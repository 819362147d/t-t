#!/usr/bin/env python3
"""下载 BTCUSDT/ETHUSDT 1m K 线: 2025-11-01 ~ 2026-10-16, 按月存 CSV.
输出: hidden_files/mf_data_1m/{SYM}_1m_YYYY-MM.csv (t=open ms,o,h,l,c,v)"""
import os, sys, time, json, urllib.request, urllib.parse, datetime

BASE = "https://data-api.binance.vision"
OUT = "/home/hatch/workspace/goals/tradingview-binance-indicator-monitoring-with-wecom-alerts/hidden_files/mf_data_1m"
os.makedirs(OUT, exist_ok=True)

SYMS = ["BTCUSDT", "ETHUSDT"]
START = datetime.datetime(2025, 11, 1, tzinfo=datetime.timezone.utc)
END = datetime.datetime(2026, 10, 16, 23, 59, tzinfo=datetime.timezone.utc)

def get(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "mf/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            print("  retry %d: %s" % (i + 1, e), flush=True)
            time.sleep(2 * (i + 1))
    raise RuntimeError("failed: " + url)

def month_range(y, m):
    s = datetime.datetime(y, m, 1, tzinfo=datetime.timezone.utc)
    e = s + datetime.timedelta(days=32)
    e = datetime.datetime(e.year, e.month, 1, tzinfo=datetime.timezone.utc)
    return s, e

def dl(sym, y, m):
    ms, me = month_range(y, m)
    if ms > END:
        return
    me = min(me, END + datetime.timedelta(minutes=1))
    path = "%s/%s_1m_%04d-%02d.csv" % (OUT, sym, y, m)
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        print(sym, y, m, "exists, skip", flush=True)
        return
    rows = []
    cur = int(ms.timestamp() * 1000)
    end_ms = int(me.timestamp() * 1000)
    while cur < end_ms:
        q = urllib.parse.urlencode({"symbol": sym, "interval": "1m",
                                    "startTime": cur, "endTime": end_ms, "limit": 1000})
        data = get(BASE + "/api/v3/klines?" + q)
        if not data:
            break
        for k in data:
            rows.append((k[0], k[1], k[2], k[3], k[4], k[5]))
        cur = data[-1][0] + 60000
        time.sleep(0.15)
        if len(data) < 1000:
            break
    with open(path, "w") as f:
        f.write("t,o,h,l,c,v\n")
        for r in rows:
            f.write("%d,%s,%s,%s,%s,%s\n" % r)
    print(sym, y, m, "rows:", len(rows), flush=True)

if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 else None
    y, m = 2025, 11
    while True:
        for sym in SYMS:
            if only and sym != only:
                continue
            dl(sym, y, m)
        m += 1
        if m > 12:
            m, y = 1, y + 1
        if y > 2026 or (y == 2026 and m > 10):
            break
    print("DONE", flush=True)
