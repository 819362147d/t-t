#!/usr/bin/env python3
"""通用影子账本结算：与 live_winrate_watch.py 同口径。
入场价 = 记录 close；出场价 = 入场 10 分钟后那根 1m K 线收盘价；
看涨命中当且仅当出场 > 入场，看跌反之；只结算 t < now-12min 的条目。
用法：python3 settle_ledger.py <账本.jsonl>
"""
import json
import sys
import time
import urllib.request

BASE = "https://data-api.binance.vision"


def fetch_close_1m(sym, ts_ms):
    url = ("%s/api/v3/klines?symbol=%s&interval=1m&limit=1&startTime=%d"
           % (BASE, sym, ts_ms))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                raw = json.loads(r.read())
            if raw:
                return float(raw[0][4])
        except Exception:
            time.sleep(2)
    return None


def main():
    path = sys.argv[1]
    alerts = [json.loads(l) for l in open(path) if l.strip()]
    now = int(time.time() * 1000)
    wins = settled = 0
    for a in alerts:
        if a["t"] > now - 12 * 60 * 1000:
            continue
        exit_close = fetch_close_1m(a["sym"], a["t"] + 10 * 60 * 1000)
        if exit_close is None:
            continue
        settled += 1
        if a["dir"] == "UP":
            wins += 1 if exit_close > a["close"] else 0
        else:
            wins += 1 if exit_close < a["close"] else 0
    print(json.dumps({
        "ledger": path.split("/")[-1],
        "n_total": len(alerts),
        "n_settled": settled,
        "wins": wins,
        "win_rate": round(wins / settled, 4) if settled else None,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
