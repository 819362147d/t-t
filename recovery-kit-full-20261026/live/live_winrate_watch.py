#!/usr/bin/env python3
"""实盘预报胜率追踪：给 live-alerts.jsonl 里的预报逐条结算，输出汇总。

结算口径：入场价 = 预报时的价格；出场价 = 入场 10 分钟后那根 1m K 线收盘价；
看涨命中当且仅当 出场价 > 入场价，看跌反之。只结算 t < now-12min 的条目。
结果增量写入 live-alerts-scored.json（key 为 ledger 行号）。
"""
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "live-alerts.jsonl")
SCORED = os.path.join(HERE, "live-alerts-scored.json")
BASE = "https://data-api.binance.vision"


def log(msg):
    sys.stderr.write("[live_winrate_watch] %s\n" % msg)
    sys.stderr.flush()


def fetch_close_1m(sym, ts_ms):
    """取 ts_ms 所在分钟那根 1m K 线的收盘价。"""
    url = ("%s/api/v3/klines?symbol=%s&interval=1m&limit=1&startTime=%d"
           % (BASE, sym, ts_ms))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                raw = json.loads(r.read())
            if raw:
                return float(raw[0][4])
        except Exception as e:  # noqa: BLE001
            log("fetch 失败 %s %d: %s" % (sym, ts_ms, e))
            time.sleep(2)
    return None


def main():
    alerts = []
    if os.path.exists(LEDGER):
        with open(LEDGER) as f:
            for line in f:
                line = line.strip()
                if line:
                    alerts.append(json.loads(line))
    scored = {}
    if os.path.exists(SCORED):
        scored = json.load(open(SCORED))

    now = int(time.time() * 1000)
    changed = False
    for i, a in enumerate(alerts):
        key = str(i)
        if key in scored:
            continue
        if a["t"] > now - 12 * 60 * 1000:
            continue  # 10 分钟窗口还没关，多留 2 分钟余量
        exit_close = fetch_close_1m(a["sym"], a["t"] + 10 * 60 * 1000)
        if exit_close is None:
            continue
        if a["dir"] == "UP":
            hit = 1 if exit_close > a["close"] else 0
        else:
            hit = 1 if exit_close < a["close"] else 0
        scored[key] = {"hit": hit, "exit": round(exit_close, 2)}
        changed = True

    if changed:
        json.dump(scored, open(SCORED, "w"))

    n_total = len(alerts)
    settled = [scored[k] for k in scored]
    n_settled = len(settled)
    wins = sum(s["hit"] for s in settled)
    print(json.dumps({
        "n_total": n_total,
        "n_settled": n_settled,
        "wins": wins,
        "win_rate": round(wins / n_settled, 4) if n_settled else None,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
