#!/usr/bin/env python3
# DRAFT（草稿，未接入巡检）: 事件合约信号机器人 — 模拟成交记账（paper ledger）
#
# 背景：只给信号、不记账，永远验证不了信号（用户验证原则：先看真实历史结果再谈实盘，
# 必须扣掉手续费和滑点）。本脚本是 Size/Fills/Risk 的最小种子：每个信号按规则记一笔
# 模拟成交，之后自动结算盈亏。
#
# 启用方式（需用户确认后由主 agent 执行，本文件当前不被任何 cron 调用）：
#   1) event-contract-signal-bot 的 cron 定义步骤 1 后加：
#        信号非空时，对每条 signal 执行
#        python3 paper_ledger.py record '<signal JSON>'
#   2) data-night-signal-recap（2026-10-16 09:00）的步骤里加：
#        python3 paper_ledger.py settle && python3 paper_ledger.py report
#
# 口径（全部显式、可调）：
#   - 入场价 = 信号 JSON 里的 close（即触发信号的那根已收盘 15m K 线收盘价；
#     cron 在 :04/:19/:34/:49 运行，入场相对墙钟有数分钟滞后，这是确定性定义）
#   - 出场价 = 信号发出后第一个开盘时间 >= ts+3600 的 5m K 线收盘价（对应打分卡 +60min 观察窗）
#   - 本金口径 NOTIONAL_USD = 100（美元）
#   - 双边手续费 FEE_RT = 0.001（Binance U 本位合约 taker 0.05% x 2，按吃单计）
#   - 双边滑点 SLIP_RT = 0.0004（0.02% x 2，估计值，数据夜可调大）
#   - 净收益率 = 方向 x (出场-入场)/入场 - 手续费 - 滑点
import json, math, os, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "paper_ledger.jsonl")
BASE = "https://data-api.binance.vision"  # 与 event_bot.py 同一数据源

NOTIONAL_USD = 100.0
FEE_RT = 0.001    # 双边手续费
SLIP_RT = 0.0004  # 双边滑点（估计）
EXIT_AFTER_SEC = 3600   # +60min 出场（与 CPI 打分卡观察窗一致）
SETTLE_AFTER_SEC = 70 * 60  # 信号 70 分钟后才可结算

def klines(symbol, interval, limit=100, start_ms=None, end_ms=None):
    url = f"{BASE}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    if start_ms:
        url += f"&startTime={start_ms}"
    if end_ms:
        url += f"&endTime={end_ms}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = json.loads(r.read())
    return [{"t": k[0], "c": float(k[4])} for k in raw][:-1]  # 只用已收盘

def read_ledger():
    rows = []
    if os.path.exists(LEDGER):
        for line in open(LEDGER):
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows

def write_ledger(rows):
    with open(LEDGER, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

def record(sig):
    """记一笔模拟成交。sig: event_bot.py 输出的 signal dict（含 symbol/signal/close/ts 等）。"""
    rows = read_ledger()
    rows.append({
        "id": f"{sig['symbol']}_{sig['signal']}_{sig.get('ts', int(time.time()))}",
        "ts": sig.get("ts", int(time.time())),
        "symbol": sig["symbol"],
        "side": sig["signal"],           # LONG / SHORT
        "entry": float(sig["close"]),
        "trend_score": sig.get("trend_score"),
        "cvd_z_5m": sig.get("cvd_z_5m"),
        "notional_usd": NOTIONAL_USD,
        "fee_rt": FEE_RT, "slip_rt": SLIP_RT,
        "exit": None, "exit_ts": None, "net_ret": None, "pnl_usd": None,
    })
    write_ledger(rows)
    print(json.dumps({"recorded": rows[-1]["id"], "ledger": LEDGER}, ensure_ascii=False))

def settle(now=None):
    """结算所有已过观察窗、尚未结算的记录。返回结算条数。"""
    now = now or int(time.time())
    rows = read_ledger()
    n = 0
    for r in rows:
        if r["exit"] is not None:
            continue
        if now - r["ts"] < SETTLE_AFTER_SEC:
            continue
        # 按目标时间窗口定向取 K 线（默认 limit 取的是最近 N 根，会漏掉旧窗口）
        target_ms = (r["ts"] + EXIT_AFTER_SEC) * 1000
        try:
            bars = klines(r["symbol"], "5m", limit=20,
                          start_ms=target_ms - 5 * 60 * 1000,
                          end_ms=target_ms + 30 * 60 * 1000)
        except Exception as e:
            print(json.dumps({"skip": r["id"], "reason": f"klines failed: {e}"},
                             ensure_ascii=False))
            continue
        hit = next((b for b in bars if b["t"] >= target_ms), None)
        if not hit:
            print(json.dumps({"skip": r["id"], "reason": "exit bar not available yet"},
                             ensure_ascii=False))
            continue
        d = 1.0 if r["side"] == "LONG" else -1.0
        gross = d * (hit["c"] - r["entry"]) / r["entry"]
        net = gross - r["fee_rt"] - r["slip_rt"]
        r["exit"] = hit["c"]
        r["exit_ts"] = hit["t"] // 1000
        r["net_ret"] = round(net, 6)
        r["pnl_usd"] = round(net * r["notional_usd"], 4)
        n += 1
    write_ledger(rows)
    print(json.dumps({"settled": n, "ledger": LEDGER}, ensure_ascii=False))

def report():
    """输出汇总：信号数、胜率、平均净收益率、累计盈亏、分标的。"""
    rows = [r for r in read_ledger() if r["net_ret"] is not None]
    if not rows:
        print(json.dumps({"trades": 0, "note": "no settled trades yet"}, ensure_ascii=False))
        return
    wins = [r for r in rows if r["net_ret"] > 0]
    by_sym = {}
    for r in rows:
        by_sym.setdefault(r["symbol"], []).append(r)
    out = {
        "trades": len(rows),
        "win_rate": round(len(wins) / len(rows), 4),
        "avg_net_ret": round(sum(r["net_ret"] for r in rows) / len(rows), 6),
        "total_pnl_usd": round(sum(r["pnl_usd"] for r in rows), 2),
        "notional_usd_each": NOTIONAL_USD,
        "assumptions": {"fee_rt": FEE_RT, "slip_rt": SLIP_RT,
                        "exit_rule": "+60min 5m close", "entry_rule": "signal bar close"},
        "by_symbol": {s: {"trades": len(v),
                          "win_rate": round(sum(1 for r in v if r["net_ret"] > 0) / len(v), 4),
                          "total_pnl_usd": round(sum(r["pnl_usd"] for r in v), 2)}
                      for s, v in by_sym.items()},
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "record":
        record(json.loads(sys.argv[2]))
    elif cmd == "settle":
        settle()
    elif cmd == "report":
        report()
    else:
        sys.exit(f"unknown cmd: {cmd} (record|settle|report)")
