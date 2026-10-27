#!/usr/bin/env python3
"""影子版（全组合）预报胜率追踪：给 shadow-alerts.jsonl 逐条结算，按档汇总。

结算口径与 live_winrate_watch.py 完全一致：入场价 = 记账时的现货收盘价；
出场价 = 入场 10 分钟后那根 1m K 线收盘价；看涨命中当且仅当 出场 > 入场，
看跌反之。只结算 t < now-12min 的条目。
结果增量写入 shadow-alerts-scored.json（key 为 ledger 行号）。
汇总按 band（0.55/0.58/0.60）分别输出，并给总计；与现网 live-alerts 的
同档数字同场对比时注意：影子无 4h 冷却去重、候选滞后两 tick 记账。"""
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "shadow-alerts.jsonl")
SCORED = os.path.join(HERE, "shadow-alerts-scored.json")
BASE = "https://data-api.binance.vision"


def log(msg):
    sys.stderr.write("[shadow_winrate_watch] %s\n" % msg)
    sys.stderr.flush()


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
        except Exception as e:  # noqa: BLE001
            log("fetch 失败 %s %d: %s" % (sym, ts_ms, e))
            time.sleep(2)
    return None


def summarize(alerts, scored, pred):
    sel = [(i, a) for i, a in enumerate(alerts) if pred(a)]
    settled = [scored[str(i)] for i, _ in sel if str(i) in scored]
    wins = sum(s["hit"] for s in settled)
    return {
        "n_total": len(sel),
        "n_settled": len(settled),
        "wins": wins,
        "win_rate": round(wins / len(settled), 4) if settled else None,
    }


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
            continue
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

    out = {"overall": summarize(alerts, scored, lambda a: True), "bands": {}}
    for band in (0.55, 0.58, 0.60):
        out["bands"][str(band)] = summarize(
            alerts, scored, lambda a, b=band: a.get("band") == b)
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
