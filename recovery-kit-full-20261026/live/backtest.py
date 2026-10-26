#!/usr/bin/env python3
# 历史回测：原样复刻 event_bot.py 的指标与信号逻辑（降级模式：纯 CVD，无 OI）
# 只读分析，不交易。成本口径：双边手续费 0.1% + 双边滑点 0.04%（均为估计值）
import json, math, os, sys, time, urllib.request, urllib.error

BASE = "https://data-api.binance.vision"
HERE = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(HERE, "backtest-report-20261017.md")

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
TREND_TF, FLOW_TF = "15m", "5m"
ATR_P, ATR_M, KER_L = 12, 3.0, 10
CVD_Z_LEN = 20
FLOW_LONG_T, FLOW_SHORT_T = 0.35, -0.35
TREND_SCORE_T = 0.5
COOLDOWN_MS = 4 * 3600 * 1000
FEE_RT, SLIP_RT = 0.001, 0.0004   # 双边
WARMUP_MS = 48 * 3600 * 1000      # 前 48 小时信号丢弃（指标热身）
DAYS = 180

# ---------- 以下指标函数逐字复制自 event_bot.py（仅改名/去无关代码） ----------
def rma(vals, n):
    out, s = [], sum(vals[:n]) / n
    out += [float("nan")] * (n - 1) + [s]
    for v in vals[n:]:
        s = (s * (n - 1) + v) / n
        out.append(s)
    return out

def sma(vals, n):
    return [sum(vals[i - n + 1:i + 1]) / n if i >= n - 1 else float("nan")
            for i in range(len(vals))]

def stdev(vals, n):
    out = []
    for i in range(len(vals)):
        if i < n - 1:
            out.append(float("nan")); continue
        w = vals[i - n + 1:i + 1]
        m = sum(w) / n
        out.append(math.sqrt(sum((x - m) ** 2 for x in w) / n))
    return out

def trend(bars):
    n = len(bars)
    o = [b["o"] for b in bars]; h = [b["h"] for b in bars]
    l = [b["l"] for b in bars]; c = [b["c"] for b in bars]
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1]))
                          for i in range(1, n)]
    atr = rma(tr, ATR_P)
    stop, pos = [0.0] * n, [0.0] * n
    for i in range(n):
        if math.isnan(atr[i]):
            continue
        s = ATR_M * atr[i]
        p = stop[i-1] if i else 0.0
        if c[i] > p and (c[i-1] if i else 0) > p:
            stop[i] = max(p, c[i] - s)
        elif c[i] < p and (c[i-1] if i else 0) < p:
            stop[i] = min(p, c[i] + s)
        elif c[i] > p:
            stop[i] = c[i] - s
        else:
            stop[i] = c[i] + s
        if i and not math.isnan(atr[i-1]):
            if c[i-1] < stop[i-1] and c[i] > stop[i]:
                pos[i] = 1
            elif c[i-1] > stop[i-1] and c[i] < stop[i]:
                pos[i] = -1
            else:
                pos[i] = pos[i-1]
    out = []
    for i in range(n):
        if math.isnan(atr[i]) or i < KER_L:
            out.append(None); continue
        change = abs(c[i] - c[i-KER_L])
        vol = sum(abs(c[j] - c[j-1]) for j in range(i-KER_L+1, i+1))
        ker = change / vol if vol else 0.0
        d = 1.0 if c[i] > c[i-KER_L] else (-1.0 if c[i] < c[i-KER_L] else 0.0)
        ker_d = ker * d
        dist = (c[i] - stop[i]) / atr[i] if atr[i] else 0.0
        atr_ts = pos[i] * min(abs(dist) / ATR_M, 1.0)
        ts = atr_ts * 0.5 + ker_d * 0.5
        out.append({"t": bars[i]["t"], "close": c[i], "atr": atr[i],
                    "stop": stop[i], "pos": pos[i], "ker": ker,
                    "trend_score": ts})
    return out

def cvd_flow(bars):
    n = len(bars)
    dv = []
    for i, b in enumerate(bars):
        if b["c"] > b["o"]:
            dv.append(b["v"])
        elif b["c"] < b["o"]:
            dv.append(-b["v"])
        elif i and b["c"] > bars[i-1]["c"]:
            dv.append(b["v"])
        elif i and b["c"] < bars[i-1]["c"]:
            dv.append(-b["v"])
        else:
            dv.append(b["v"])
    chg = [0.0] + [dv[i] - dv[i-1] for i in range(1, n)]
    mu, sd = sma(chg, CVD_Z_LEN), stdev(chg, CVD_Z_LEN)
    return [(chg[i] - mu[i]) / sd[i] if sd[i] else 0.0
            for i in range(n) if not (math.isnan(mu[i]) or math.isnan(sd[i]))]
# ---------- 指标函数复制结束 ----------

TF_MS = {"15m": 15*60*1000, "5m": 5*60*1000}

def fetch_klines(symbol, interval, start_ms, end_ms, missing):
    """分页拉取，失败重试 3 次；记录缺失区间。返回 bars 列表。"""
    step = TF_MS[interval]
    bars, cur = [], start_ms
    while cur < end_ms:
        url = (f"{BASE}/api/v3/klines?symbol={symbol}&interval={interval}"
               f"&startTime={cur}&endTime={end_ms}&limit=1000")
        raw = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    raw = json.loads(r.read())
                break
            except Exception as e:
                if attempt == 2:
                    missing.append({"symbol": symbol, "interval": interval,
                                    "from": cur, "to": end_ms, "error": str(e)[:120]})
                    return bars
                time.sleep(2 * (attempt + 1))
        if not raw:
            missing.append({"symbol": symbol, "interval": interval,
                            "from": cur, "to": end_ms, "error": "empty response"})
            return bars
        for k in raw:
            bars.append({"t": k[0], "o": float(k[1]), "h": float(k[2]),
                         "l": float(k[3]), "c": float(k[4]), "v": float(k[5])})
        cur = raw[-1][0] + step
        time.sleep(0.15)  # 限频保护
    # 去掉未收盘的最后一根（与线上 bot 的 out[:-1] 一致）
    if bars and bars[-1]["t"] + step > end_ms:
        bars = bars[:-1]
    return bars

def backtest_symbol(symbol, start_ms, end_ms, missing):
    b15 = fetch_klines(symbol, "15m", start_ms, end_ms, missing)
    b5 = fetch_klines(symbol, "5m", start_ms, end_ms, missing)
    if len(b15) < 100 or len(b5) < 100:
        return [], {"bars15": len(b15), "bars5": len(b5), "error": "数据不足"}
    tb = trend(b15)
    fz = cvd_flow(b5)
    # cvd_flow 返回长度 n-19，fz[j] 对应 b5[j+19]
    zmap = {b5[j + 19]["t"]: fz[j] for j in range(len(fz))}
    warmup_end = b15[0]["t"] + WARMUP_MS
    last_sig = {}
    trades = []
    for i in range(1, len(b15)):
        t, prev = tb[i], tb[i-1]
        if t is None or prev is None:
            continue
        if t["t"] < warmup_end:
            continue
        # 15m 收盘时刻 T 对应的最后一根已收盘 5m：open = T - 5min
        z = zmap.get(t["t"] - TF_MS["5m"])
        if z is None:
            continue
        sig = None
        if prev["pos"] == -1 and t["pos"] == 1 \
                and t["trend_score"] > TREND_SCORE_T and z > FLOW_LONG_T:
            sig = "LONG"
        elif prev["pos"] == 1 and t["pos"] == -1 \
                and t["trend_score"] < -TREND_SCORE_T and z < FLOW_SHORT_T:
            sig = "SHORT"
        if not sig:
            continue
        key = (symbol, sig)
        if t["t"] - last_sig.get(key, 0) <= COOLDOWN_MS:
            continue
        last_sig[key] = t["t"]
        j = i + 4  # +60 分钟出场
        if j >= len(b15):
            continue  # 尾部无法评分，丢弃
        entry, exitp = t["close"], b15[j]["c"]
        gross = (exitp / entry - 1) if sig == "LONG" else ((entry - exitp) / entry)
        net = gross - FEE_RT - SLIP_RT
        trades.append({"symbol": symbol, "side": sig, "entry_t": t["t"],
                       "entry": entry, "exit": exitp,
                       "trend_score": round(t["trend_score"], 3),
                       "cvd_z": round(z, 3),
                       "gross": gross, "net": net})
    return trades, {"bars15": len(b15), "bars5": len(b5)}

def stats(trades):
    n = len(trades)
    if not n:
        return {"n": 0}
    nets = [t["net"] for t in trades]
    wins = sum(1 for x in nets if x > 0)
    cum, peak, mdd = 0.0, 0.0, 0.0
    for x in nets:
        cum += 100 * x
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    return {"n": n, "wins": wins, "winrate": wins / n,
            "avg_net": sum(nets) / n, "total_net": sum(100 * x for x in nets),
            "max_dd": mdd,
            "best": max(nets), "worst": min(nets)}

def main():
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - DAYS * 24 * 3600 * 1000
    missing = []
    all_trades, info = [], {}
    for sym in SYMBOLS:
        print(f"fetch+replay {sym} ...", flush=True)
        tr, inf = backtest_symbol(sym, start_ms, end_ms, missing)
        all_trades += tr
        info[sym] = inf
        print(f"  {sym}: {len(tr)} signals, bars15={inf.get('bars15')}, bars5={inf.get('bars5')}", flush=True)
    all_trades.sort(key=lambda t: t["entry_t"])
    json.dump(all_trades, open(os.path.join(HERE, "backtest-trades-20261017.json"), "w"),
              indent=1)
    # 月度拆分
    months = {}
    for t in all_trades:
        m = time.strftime("%Y-%m", time.localtime(t["entry_t"] / 1000))
        months.setdefault(m, []).append(t)
    overall = stats(all_trades)
    per_sym = {s: stats([t for t in all_trades if t["symbol"] == s]) for s in SYMBOLS}
    per_month = {m: stats(v) for m, v in sorted(months.items())}

    def f(x):
        return f"{x*100:+.2f}%"
    L = []
    L.append("# 信号规则历史回测报告（2026-10-17）\n")
    L.append("## 方法")
    L.append(f"- 标的：BTCUSDT、ETHUSDT；数据源：https://data-api.binance.vision（现货 K 线）")
    L.append(f"- 回测区间：{time.strftime('%Y-%m-%d', time.localtime(start_ms))} ~ "
             f"{time.strftime('%Y-%m-%d', time.localtime(end_ms))}（约 {DAYS} 天）")
    L.append("- 指标与触发条件逐字复刻 `event_bot.py`（15m ATR Trailing Stop + KER → TrendScore；"
             "5m CVD 近似 → z-score(20)；降级模式，无 OI）")
    L.append("- 触发：15m 方向翻转 且 |TrendScore|>0.5 且同向 |CVD-Z 5m|>0.35；"
             "同标的同方向 4 小时去重")
    L.append("- 入场：信号 15m 收盘价；出场：+60 分钟（4 根 15m 后）收盘价")
    L.append("- 成本：双边手续费 0.1% + 双边滑点 0.04%（均为估计值）；本金 $100、单利口径（每单固定 $100 名义）")
    L.append("- 线上 bot 每次只取最近 300 根重算，回测为全序列一次计算并丢弃前 48 小时热身信号；"
             "两者在热身后收敛，差异可忽略")
    L.append("- 缺失数据区间见文末；尾部不足 60 分钟的信号无法评分，已丢弃\n")
    L.append("## 总体")
    if overall["n"]:
        L.append(f"- 样本数 n = {overall['n']}")
        L.append(f"- 胜率 = {overall['wins']}/{overall['n']} = {overall['winrate']*100:.1f}%")
        L.append(f"- 单均净收益 = {f(overall['avg_net'])}")
        L.append(f"- 总净收益（$100 本金单利）= ${overall['total_net']:+.2f}")
        L.append(f"- 最大回撤 = ${overall['max_dd']:.2f}")
        L.append(f"- 单笔最好 {f(overall['best'])} / 最差 {f(overall['worst'])}\n")
    else:
        L.append("- 无样本\n")
    L.append("## 分标的")
    for s in SYMBOLS:
        st = per_sym[s]
        if st["n"]:
            L.append(f"- {s}：n={st['n']}，胜率 {st['winrate']*100:.1f}%，"
                     f"单均 {f(st['avg_net'])}，总净 ${st['total_net']:+.2f}，最大回撤 ${st['max_dd']:.2f}")
        else:
            L.append(f"- {s}：无样本")
    L.append("\n## 按月")
    for m, st in per_month.items():
        L.append(f"- {m}：n={st['n']}，胜率 {st['winrate']*100:.1f}%，"
                 f"单均 {f(st['avg_net'])}，总净 ${st['total_net']:+.2f}")
    L.append("\n## 数据缺失区间")
    if missing:
        for g in missing:
            L.append(f"- {g['symbol']} {g['interval']}: "
                     f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(g['from']/1000))} ~ "
                     f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(g['to']/1000))}（{g['error']}）")
    else:
        L.append("- 无（分页拉取全部成功）")
    L.append("\n## 局限性（必读）")
    L.append("- 样本局限：n 较小（尤其分标的/分月后），统计效力有限；胜率与收益的置信区间宽")
    L.append("- 区间偏差：回测区间为单一段连续行情，结论不一定外推到其他市场环境（趋势市/震荡市/极端波动）")
    L.append("- 降级模式：线上与回测均未使用 OI，仅 CVD 近似；若未来接入 OI，触发条件会变化")
    L.append("- 成本为估计值：真实滑点随波动率和流动性变化；未计入资金费率（永续）与冲击成本")
    L.append("- 单利口径：未复利；最大回撤基于 $100 固定名义逐单累加曲线")
    L.append("- 历史表现不代表未来表现\n")
    open(REPORT, "w").write("\n".join(L))
    print("report written:", REPORT)
    print(json.dumps({"overall": overall,
                      "per_symbol": {k: {kk: (round(vv, 6) if isinstance(vv, float) else vv)
                                          for kk, vv in v.items()} for k, v in per_sym.items()},
                      "missing": len(missing)}, indent=1))

if __name__ == "__main__":
    main()
