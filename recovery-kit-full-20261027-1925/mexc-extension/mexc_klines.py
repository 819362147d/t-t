"""MEXC 合约 K 线（公开接口，无需 key），输出与现网 bot 一致的 bar 口径。
接口：GET https://api.mexc.com/api/v1/contract/kline/{SYM}?interval=Min1|Min5[&start=&end=]
返回 data: time(秒)/open/close/high/low/vol 数组。注意：
- 不带 start/end 时返回最近 2001 根，最后一根是未收盘当前 bar，调用方去尾。
- 带 start/end 时按窗口返回，end 用当前时间秒，避免 start-only 模式返回超出现在的窗口。
- 输出 t 为毫秒（与币安 klines 一致），已去未收盘尾巴。
"""
import urllib.request, json, time
BASE="https://api.mexc.com"
INTERVAL_MAP={"1m":"Min1","5m":"Min5"}
INTERVAL_SEC={"1m":60,"5m":300}
def fetch_mexc_klines(mexc_symbol, interval, limit=None):
    mi=INTERVAL_MAP[interval]; sec=INTERVAL_SEC[interval]
    now=int(time.time())
    # 用 start/end 窗口拉取，确保不超出现在；窗口根数 = limit+2（含去尾余量）
    n=(limit+2) if limit else 2001
    start=now-n*sec
    url=f"{BASE}/api/v1/contract/kline/{mexc_symbol}?interval={mi}&start={start}&end={now}"
    req=urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                j=json.load(r)
            break
        except Exception as e:
            if attempt==2: raise
            time.sleep(2*(attempt+1))
    if not j.get("success"):
        raise RuntimeError(f"MEXC kline failed {mexc_symbol} {j.get('code')} {j.get('message')}")
    d=j.get("data") or {}
    times=d.get("time") or []
    bars=[]
    for i,t in enumerate(times):
        bars.append({"t": int(t)*1000, "o": float(d["open"][i]), "h": float(d["high"][i]),
                     "l": float(d["low"][i]), "c": float(d["close"][i]), "v": float(d["vol"][i])})
    bars.sort(key=lambda b: b["t"])
    # 去重 + 去未收盘尾巴（最后一根收盘时间 > now）
    seen={}; 
    for b in bars: seen[b["t"]]=b
    bars=[seen[t] for t in sorted(seen)]
    if bars and bars[-1]["t"]+sec*1000>now*1000:
        bars=bars[:-1]
    if limit: bars=bars[-limit:]
    if len(bars)<2: raise RuntimeError("klines too short")
    return bars
def mexc_symbol_from_bot(sym):
    # bot 币种如 TESLAUSDT -> MEXC TESLA_USDT；指数如 SPX500USDT -> SPX500_USDT
    return sym[:-4]+"_USDT" if sym.endswith("USDT") else sym
