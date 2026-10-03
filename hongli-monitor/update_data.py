# -*- coding: utf-8 -*-
"""
红利低波均线乖离策略 · 监控数据生成器
用法: python update_data.py
输出: data.js  (window.HL_DATA = {...})  —— 供 index.html 直接读取，无需本地服务

策略规则(原帖 https://www.xueqiu.com/7734995747/411110630):
  买入: 收盘价 < MA(N)  且 中证红利低波指数 PE < 20
  卖出: 收盘价 > MA(N) × 1.07
  执行: 信号次日开盘成交，双边各 0.02% 手续费；满仓/空仓二态

数据口径: 512890 不复权原始价 + 2021-10-22 份额拆分(1:2)还原。
  该 ETF 成立以来无分红，故还原后价格序列即全收益序列。
"""
import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

BASE = Path(__file__).parent
UA = {"User-Agent": "Mozilla/5.0"}
FEE = 0.0002
SPLIT_DATE, SPLIT_RATIO = "2021-10-22", 2.0
MA_LIST = (182, 200)
K = 1.07


def http(url: str, gbk: bool = False) -> str:
    req = urllib.request.Request(url, headers=UA)
    raw = urllib.request.urlopen(req, timeout=25).read()
    return raw.decode("gbk" if gbk else "utf-8", errors="replace")


def fetch_daily(symbol="sh512890", start="2018-01-01"):
    """腾讯日线接口单次上限 800 根，按日期分段翻页取全历史(不复权)"""
    rows, end = [], dt.date.today().isoformat()
    for _ in range(15):
        url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
               f"?param={symbol},day,{start},{end},800,")
        node = json.loads(http(url))["data"][symbol]
        arr = node.get("day") or node.get("qfqday")
        if not arr:
            break
        for it in arr:
            rows.append({"date": it[0], "open": float(it[1]), "close": float(it[2]),
                         "high": float(it[3]), "low": float(it[4])})
        first = arr[0][0]
        if first <= start or len(arr) < 800:
            break
        end = (dt.date.fromisoformat(first) - dt.timedelta(days=1)).isoformat()
    seen, out = set(), []
    for r in sorted(rows, key=lambda x: x["date"]):
        if r["date"] in seen:
            continue
        seen.add(r["date"])
        out.append(r)
    return out


def fetch_quote(symbols=("sh512890", "sh000922")):
    """实时快照: 现价/涨跌幅/PE，失败不阻断"""
    try:
        txt = http("https://qt.gtimg.cn/q=" + ",".join(symbols), gbk=True)
    except Exception as e:
        return {"ok": False, "err": str(e)[:60]}
    res = {}
    for line in txt.strip().split(";"):
        if '"' not in line:
            continue
        code = line.split("=")[0].strip().replace("v_", "")
        v = line.split('"')[1].split("~")
        if len(v) < 46:
            continue
        res[code] = {"name": v[1], "price": float(v[3] or 0), "prev": float(v[4] or 0),
                     "chg_pct": float(v[32] or 0), "pe": float(v[39] or 0) if v[39] else None}
    return {"ok": True, "data": res}


def build():
    rows = fetch_daily()
    # 份额拆分还原
    for r in rows:
        if r["date"] < SPLIT_DATE:
            for k in ("open", "close", "high", "low"):
                r[k] = round(r[k] / SPLIT_RATIO, 6)

    dates = [r["date"] for r in rows]
    close = [r["close"] for r in rows]
    openp = [r["open"] for r in rows]
    n = len(rows)

    def ma(series, w):
        """均线保留 6 位小数: 4 位舍入会让 close 与 MA 的严格不等号判定出错"""
        out, s = [], 0.0
        for i, v in enumerate(series):
            s += v
            if i >= w:
                s -= series[i - w]
            out.append(round(s / w, 6) if i >= w - 1 else None)
        return out

    ma_series = {w: ma(close, w) for w in MA_LIST}

    def simulate(w):
        m = ma_series[w]
        cash, sh, pos = 1.0, 0.0, None
        eq, trades = [], []
        for i in range(n):
            if i > 0 and m[i - 1] is not None:
                sig_buy = close[i - 1] < m[i - 1]
                sig_sell = close[i - 1] > m[i - 1] * K
                if sh == 0 and sig_buy:
                    p = openp[i] * (1 + FEE)
                    sh, cash, pos = cash / p, 0.0, {"buy_date": dates[i], "buy_px": p}
                elif sh > 0 and sig_sell:
                    p = openp[i] * (1 - FEE)
                    cash = sh * p
                    pos.update({"sell_date": dates[i], "sell_px": p,
                                "ret": p / pos["buy_px"] - 1,
                                "days": (dt.date.fromisoformat(dates[i]) - dt.date.fromisoformat(pos["buy_date"])).days})
                    trades.append(pos)
                    sh, pos = 0.0, None
            eq.append(round(cash + sh * close[i], 5))
        holding = sh > 0
        if holding:
            pos.update({"sell_date": None, "sell_px": close[-1],
                        "ret": close[-1] / pos["buy_px"] - 1,
                        "days": (dt.date.fromisoformat(dates[-1]) - dt.date.fromisoformat(pos["buy_date"])).days})
            trades.append(pos)
        # 展示层统一取整（内部计算保持全精度，避免舍入影响收益率）
        clean = [{"buy_date": t["buy_date"], "buy_px": round(t["buy_px"], 4),
                  "sell_date": t.get("sell_date"), "sell_px": round(t["sell_px"], 4),
                  "ret": round(t["ret"], 4), "days": t["days"]} for t in trades]
        return {"equity": eq, "trades": clean, "holding": holding,
                "pos": clean[-1] if holding else None}

    sims = {w: simulate(w) for w in MA_LIST}
    hold = [round(c / close[0], 5) for c in close]
    start = next(i for i, v in enumerate(ma_series[MA_LIST[0]]) if v is not None)
    hold_rel = [round(v / hold[start], 5) for v in hold]

    # ---- 参数热力图: 均线周期 × 止盈偏离 ----
    hm_ma = sorted(set(list(range(150, 321, 5)) + [182]))
    hm_k = [round(1.02 + 0.01 * i, 2) for i in range(17)]      # 1.02 ~ 1.18
    cache = {w: ma_series[w] for w in MA_LIST}

    def ma_of(w):
        if w not in cache:
            cache[w] = ma(close, w)
        return cache[w]

    def cagr_of(w, k):
        m = cache[w]
        cash, sh, eq = 1.0, 0.0, 1.0
        for i in range(n):
            if i > 0 and m[i - 1] is not None:
                if sh == 0 and close[i - 1] < m[i - 1]:
                    p = openp[i] * (1 + FEE)
                    sh, cash = cash / p, 0.0
                elif sh > 0 and close[i - 1] > m[i - 1] * k:
                    p = openp[i] * (1 - FEE)
                    cash, sh = sh * p, 0.0
            eq = cash + sh * close[i]
        s0 = next(i for i, v in enumerate(m) if v is not None)
        yrs = (dt.date.fromisoformat(dates[-1]) - dt.date.fromisoformat(dates[s0])).days / 365.25
        return eq ** (1 / yrs) - 1

    grid = []
    for w in hm_ma:
        ma_of(w)
    for k in hm_k:
        grid.append([round(cagr_of(w, k), 4) for w in hm_ma])
    best = []
    for i, w in enumerate(hm_ma):
        col = [grid[j][i] for j in range(len(hm_k))]
        jm = col.index(max(col))
        best.append({"ma": w, "k": hm_k[jm], "cagr": round(col[jm], 4)})

    def stat(eq, s0):
        e = eq[s0:]
        yrs = (dt.date.fromisoformat(dates[-1]) - dt.date.fromisoformat(dates[s0])).days / 365.25
        fin = e[-1]
        dd, peak = 0.0, e[0]
        for v in e:
            peak = max(peak, v)
            dd = min(dd, v / peak - 1)
        rets = [e[i] / e[i - 1] - 1 for i in range(1, len(e)) if e[i - 1]]
        mu = sum(rets) / len(rets) if rets else 0
        sd = (sum((x - mu) ** 2 for x in rets) / len(rets)) ** 0.5 if rets else 0
        return {"total": round(fin - 1, 4), "cagr": round(fin ** (1 / yrs) - 1, 4),
                "maxdd": round(dd, 4), "vol": round(sd * 243 ** 0.5, 4),
                "sharpe": round(mu * 243 / (sd * 243 ** 0.5), 2) if sd else 0,
                "years": round(yrs, 2), "from": dates[s0]}

    stats = {w: stat(sims[w]["equity"], start) for w in MA_LIST}
    stats["hold"] = stat(hold_rel, start)

    last_i = n - 1
    latest = {"date": dates[last_i], "close": close[last_i],
              "prev_close": close[last_i - 1],
              "chg_pct": round(close[last_i] / close[last_i - 1] - 1, 4)}
    ma_now = {w: ma_series[w][last_i] for w in MA_LIST}
    for w in MA_LIST:
        latest[f"ma{w}"] = ma_now[w]
        latest[f"sell{w}"] = round(ma_now[w] * K, 4)
        latest[f"dev{w}"] = round(close[last_i] / ma_now[w] - 1, 4)
        latest[f"dev_sell{w}"] = round(close[last_i] / (ma_now[w] * K) - 1, 4)
        latest[f"ma{w}_chg20"] = round(ma_now[w] - ma_series[w][last_i - 20], 4)
        latest[f"ma{w}_slope"] = round((ma_now[w] - ma_series[w][last_i - 20]) / 20, 5)
        # 判定顺序要点（勿改回）：
        #   ① 止盈必须**最先**判，且**不能**塞进 holding 分支里 ——
        #      止盈出场当天「持仓」与「站上止盈线」同时成立，只要把 holding 放在最前面，
        #      每一次止盈出场都会被写成「继续持有」（历史重放命中 12/11 笔，与 trades 平仓笔数完全一致）。
        #   ② 持仓期只有止盈一个出口（跌破均线不卖：策略是满仓/空仓二态，持仓期不加不减）。
        #   ③ 空仓期只有「跌破均线」一个入口；空仓而价在均线上方（含站上止盈线）一律归「观望」，
        #      否则会出现「报止盈信号、卡片正文却写空仓」的自相矛盾（历史 385/447 天）。
        if close[last_i] > ma_now[w] * K:
            # 站上止盈线：持仓中 = 次日开盘清仓；空仓则只是没搭上车的强势日，归观望
            latest[f"state{w}"] = "sell" if sims[w]["holding"] else "wait"
        elif sims[w]["holding"]:
            latest[f"state{w}"] = "hold"
        elif close[last_i] < ma_now[w]:
            latest[f"state{w}"] = "buy"
        else:
            latest[f"state{w}"] = "wait"

    quote = fetch_quote()
    pe = None
    if quote.get("ok"):
        pe = (quote["data"].get("sh000922") or {}).get("pe")

    data = {
        "meta": {"code": "512890", "name": "红利低波ETF华泰柏瑞", "index": "中证红利低波动指数(H30269)",
                 "rule": f"买入 收盘价<MA(N) 且 PE<20 ｜ 卖出 收盘价>MA(N)×{K}",
                 "updated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                 "data_date": dates[last_i], "bars": n, "first_date": dates[0],
                 "split": "2021-10-22 份额拆分 1:2（已还原）；成立以来无分红"},
        "latest": latest, "stats": stats, "pe": pe, "quote": quote,
        "series": {"date": dates, "close": close, "open": openp,
                   "ma": {str(w): ma_series[w] for w in MA_LIST}},
        "equity": {"date": dates, "hold": [round(v, 5) for v in hold_rel],
                   "strat": {str(w): sims[w]["equity"] for w in MA_LIST}},
        "trades": {str(w): sims[w]["trades"] for w in MA_LIST},
        "pos": {str(w): sims[w]["pos"] for w in MA_LIST},
        "start_index": start,
        "heat": {"ma": hm_ma, "k": hm_k, "grid": grid, "best": best,
                 "cagr_at_107": {str(w): grid[hm_k.index(1.07)][i] for i, w in enumerate(hm_ma)}},
    }
    out = BASE / "data.js"
    out.write_text("window.HL_DATA=" + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";",
                   encoding="utf-8")
    print(f"[OK] {out}  {out.stat().st_size/1024:.0f} KB  数据日 {dates[last_i]}  共 {n} 根")
    for w in MA_LIST:
        st = latest[f"state{w}"]
        print(f"  MA{w}: 值 {ma_now[w]:.4f}  止盈线 {ma_now[w]*K:.4f}  偏离 {latest[f'dev{w}']*100:+.2f}%  状态 {st}"
              f"  持仓 {sims[w]['pos'] if sims[w]['holding'] else '空仓'}")
    print(f"  实时报价: {quote.get('ok')}  中证红利PE {pe}")


if __name__ == "__main__":
    build()
