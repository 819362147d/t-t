#!/usr/bin/env python3
# 事件合约信号机器人（只提醒、不交易）
# 指标1: ATR Trailing Stop + KER（趋势过滤，15m）— 完整复刻用户 Pine v5 逻辑
# 指标2: MTF CVD Flow（资金流触发，5m）— OI 不可用（本区域 Binance 期货 API 被禁），
#        降级为纯 CVD 近似（K 线量能按阴阳线归属），z-score(20) 标准化
# 数据源: https://data-api.binance.vision（现货 K 线，本机实测可用）
import json, math, os, sys, time, urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://data-api.binance.vision"
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "event_bot_state.json")

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
TREND_TF, FLOW_TF = "15m", "5m"
ATR_P, ATR_M, KER_L = 12, 3.0, 10
CVD_Z_LEN = 20
FLOW_LONG_T, FLOW_SHORT_T = 0.35, -0.35
TREND_SCORE_T = 0.5
COOLDOWN_SEC = 4 * 3600  # 同标的同方向 4 小时只报一次

def klines(symbol, interval, limit=300):
    url = f"{BASE}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = json.loads(r.read())
    out = []
    for k in raw:
        out.append({"t": k[0], "o": float(k[1]), "h": float(k[2]),
                    "l": float(k[3]), "c": float(k[4]), "v": float(k[5])})
    return out[:-1]  # 只用已收盘的 K 线

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
    """复刻 Pine: ATR Trailing Stop + KER，返回每根 bar 的 dict"""
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
    """CVD 近似 + z-score，返回每根 bar 的 cvdZ（None 表数据不足）"""
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

BRIDGE = os.path.expanduser("~/workspace/wechat-bridge")

def queue_wechat(text):
    """把告警写入微信桥 outbox，由 serve 进程 200ms 内发出。
    返回 outbox 文件名（入队成功）或空字符串。注意：入队≠送达，
    token 过期时 flusher 会报 ret=-2 并每 10 秒重试。"""
    try:
        ctx = json.load(open(os.path.join(BRIDGE, "state", "last_context.json")))
        to_user, token = ctx.get("to_user_id"), ctx.get("context_token")
        if not to_user or not token:
            return ""
        import uuid
        pend = os.path.join(BRIDGE, "outbox", "pending")
        os.makedirs(pend, exist_ok=True)
        name = uuid.uuid4().hex + ".json"
        json.dump({"to_user_id": to_user, "text": text, "context_token": token},
                  open(os.path.join(pend, name), "w"), ensure_ascii=False)
        return name
    except Exception:
        return ""

def format_alert(sig):
    d = "做多 🟢" if sig["signal"] == "LONG" else "做空 🔴"
    # 写死东八区，不依赖系统时区（定时任务环境的 TZ 可能不是北京时间）
    bj = timezone(timedelta(hours=8))
    ts = datetime.now(tz=bj).strftime("%m-%d %H:%M")
    url = "https://apps.apple.com/app/binance-buy-bitcoin-crypto/id1436799971"
    return (f"【事件合约信号】{sig['symbol']} · {d}\n"
            f"15m 趋势翻转 | TrendScore {sig['trend_score']} | 资金流 CVD-Z {sig['cvd_z_5m']}\n"
            f"价格 {sig['close']} · {ts}\n"
            f"👉 {url}\n"
            f"⚠️ 仅研究提醒，不构成交易建议")

def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}

def save_state(s):
    json.dump(s, open(STATE, "w"))

def main():
    now = int(time.time())
    state = load_state()
    signals, status = [], {}
    for sym in SYMBOLS:
        tb = trend(klines(sym, TREND_TF))
        fz = cvd_flow(klines(sym, FLOW_TF))
        t, z = tb[-1], fz[-1]
        if t is None or z is None:
            status[sym] = {"error": "数据不足"}; continue
        prev = tb[-2]
        sig = None
        # 做多：15m 刚翻多 且 趋势分>阈值 且 资金流>阈值
        if prev and prev["pos"] == -1 and t["pos"] == 1 \
                and t["trend_score"] > TREND_SCORE_T and z > FLOW_LONG_T:
            sig = "LONG"
        elif prev and prev["pos"] == 1 and t["pos"] == -1 \
                and t["trend_score"] < -TREND_SCORE_T and z < FLOW_SHORT_T:
            sig = "SHORT"
        key = f"{sym}_{sig}" if sig else None
        st = {"symbol": sym, "close": round(t["close"], 2),
              "pos": "LONG" if t["pos"] == 1 else ("SHORT" if t["pos"] == -1 else "FLAT"),
              "trend_score": round(t["trend_score"], 3),
              "cvd_z_5m": round(z, 3), "signal": sig,
              "note": "OI不可用，资金流仅CVD近似（降级）"}
        status[sym] = st
        if sig and now - state.get(key, 0) > COOLDOWN_SEC:
            state[key] = now
            st["wechat_file"] = queue_wechat(format_alert(st))
            signals.append(st)
    save_state(state)
    print(json.dumps({"ts": now, "signals": signals, "status": status},
                     ensure_ascii=False))

if __name__ == "__main__":
    main()
