#!/usr/bin/env python3
"""从多币种账本取最新一轮候选的前 N 条信号（按 |prob-0.5| 从大到小）。
只做信号筛选，不下单、不碰密钥。供 MEXC 实盘交易脚本调用。
"""
import json
import sys
from collections import defaultdict

LEDGER = "/home/hatch/workspace/goals/tradingview-binance-indicator-monitoring-with-wecom-alerts/hidden_files/multicoin-alerts.jsonl"


def latest_top(ledger=LEDGER, n=3, exclude=()):
    rows = [json.loads(l) for l in open(ledger) if l.strip()]
    by_t = defaultdict(list)
    for r in rows:
        by_t[r.get("t")].append(r)
    last_t = max(by_t)
    held = set(exclude)
    cands = [r for r in by_t[last_t] if r.get("sym") not in held]
    cands.sort(key=lambda r: abs(r["prob"] - 0.5), reverse=True)
    return last_t, cands[:n]


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    t, top = latest_top(n=n)
    print("latest_t:", t, "count:", len(top))
    for r in top:
        print(r["sym"], r["dir"], "prob=", r["prob"], "close=", r["close"])
