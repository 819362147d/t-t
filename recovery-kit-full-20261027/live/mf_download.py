#!/usr/bin/env python3
"""下载 BTCUSDT/ETHUSDT 5m K线: 月度包(2025-11~2026-09) + 日包(2026-10-01~16).
顺序下载、限频友好. 输出 /tmp/mf_data/{SYM}_5m.csv"""
import os, time, csv, zipfile, io, urllib.request

OUT = "/tmp/mf_data"
SYMS = ["BTCUSDT", "ETHUSDT"]
MONTHS = ["2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04",
          "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]
DAYS = ["2026-10-%02d" % d for d in range(1, 17)]

def fetch(url, retries=3):
    for a in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read()
        except Exception:
            if a == retries - 1:
                raise
            time.sleep(2 * (a + 1))

def rows_from_zip(data):
    z = zipfile.ZipFile(io.BytesIO(data))
    rows = []
    for line in z.read(z.namelist()[0]).decode().splitlines():
        p = line.split(",")
        rows.append((int(float(p[0])), float(p[1]), float(p[2]),
                     float(p[3]), float(p[4]), float(p[5])))
    return rows

os.makedirs(OUT, exist_ok=True)
for sym in SYMS:
    urls = (["https://data.binance.vision/data/spot/monthly/klines/%s/5m/%s-5m-%s.zip" % (sym, sym, m) for m in MONTHS] +
            ["https://data.binance.vision/data/spot/daily/klines/%s/5m/%s-5m-%s.zip" % (sym, sym, d) for d in DAYS])
    allrows = []
    for u in urls:
        allrows.extend(rows_from_zip(fetch(u)))
        time.sleep(0.2)
    allrows.sort()
    ded, last = [], None
    for r in allrows:
        if r[0] != last:
            ded.append(r)
            last = r[0]
    with open("%s/%s_5m.csv" % (OUT, sym), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "o", "h", "l", "c", "v"])
        w.writerows(ded)
    print(sym, len(ded), "bars", flush=True)
print("download done", flush=True)
