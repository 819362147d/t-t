#!/usr/bin/env python3
"""增量补齐 1m K 线: 把 mf_data_1m/{SYM}_1m_2026-10.csv 从现有末尾补到当前
最后一个已收盘 1 分钟 bar。沿用 dl_1m.py 的接口/格式 (data-api.binance.vision,
t=open ms, o/h/l/c/v 原始字符串)。合并后按 t 去重排序写回, 并做连续性核对。
只动数据文件, 不碰任何现网脚本/模型。"""
import os, sys, time, json, urllib.request, urllib.parse, datetime

BASE = "https://data-api.binance.vision"
OUT = "/home/hatch/workspace/goals/tradingview-binance-indicator-monitoring-with-wecom-alerts/hidden_files/mf_data_1m"
SYMS = ["BTCUSDT", "ETHUSDT"]


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


def fmt(ms):
    return datetime.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")


def update(sym):
    path = "%s/%s_1m_2026-10.csv" % (OUT, sym)
    rows = {}
    with open(path) as f:
        header = f.readline()
        for line in f:
            line = line.strip()
            if not line:
                continue
            t = int(line.split(",", 1)[0])
            rows[t] = line
    old_max = max(rows)
    # 最后一个已收盘 bar: open < floor(now, 1min)
    end_ms = int(time.time() * 1000) // 60000 * 60000
    cur = old_max + 60000
    added = 0
    while cur < end_ms:
        q = urllib.parse.urlencode({"symbol": sym, "interval": "1m",
                                    "startTime": cur, "endTime": end_ms,
                                    "limit": 1000})
        data = get(BASE + "/api/v3/klines?" + q)
        if not data:
            break
        for k in data:
            if k[0] >= end_ms:
                break
            rows[k[0]] = "%d,%s,%s,%s,%s,%s" % (k[0], k[1], k[2], k[3], k[4], k[5])
            added += 1
        cur = data[-1][0] + 60000
        time.sleep(0.15)
        if len(data) < 1000:
            break
    ts = sorted(rows)
    with open(path, "w") as f:
        f.write(header if header.startswith("t,") else "t,o,h,l,c,v\n")
        for t in ts:
            f.write(rows[t] + "\n")
    # 连续性核对: 整个文件相邻 diff 应全为 60000
    diffs = [b - a for a, b in zip(ts, ts[1:])]
    gaps = [(ts[i], d) for i, d in enumerate(diffs) if d != 60000]
    print("%s: rows=%d added=%d range=[%s .. %s] gaps=%d" %
          (sym, len(ts), added, fmt(ts[0]), fmt(ts[-1]), len(gaps)), flush=True)
    for gt, d in gaps[:10]:
        print("   gap after %s diff=%dms" % (fmt(gt), d), flush=True)
    return len(ts), len(gaps)


if __name__ == "__main__":
    for s in SYMS:
        update(s)
    print("DONE", flush=True)
