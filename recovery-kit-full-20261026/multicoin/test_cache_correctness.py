#!/usr/bin/env python3
"""缓存一致性校验：cached_fetch_klines 与直接 fetch_klines 逐根比对。
用法：python3 test_cache_correctness.py
对每个 (symbol, interval)：先冷调用填充缓存，再热调用走合并路径，
最后直接全量拉取，三方比对。热调用结果应与直接全量完全一致
（除非恰好跨过收盘边界导致直接拉取多出一根新 bar，此时只允许多一根
且旧 bar 必须逐根相同）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import event_10min_multicoin_bot as bot
from multicoin_klines_cache import cached_fetch_klines

CASES = [
    ("BTCUSDT", "5m", 1500),   # 参考
    ("AUSDT", "1m", 500),
    ("AUSDT", "5m", 1500),
    ("RVNUSDT", "1m", 500),
    ("CELOUSDT", "5m", 1500),
    ("PNUTUSDT", "1m", 500),
    ("DEXEUSDT", "5m", 1500),
]


def bars_equal(a, b):
    if len(a) != len(b):
        return False, "长度不同 %d vs %d" % (len(a), len(b))
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return False, "第 %d 根不同 %s vs %s" % (i, x, y)
    return True, "完全一致"


def main():
    n_ok = 0
    for sym, interval, limit in CASES:
        # 冷调用：全量拉取 + 写缓存
        cold = cached_fetch_klines(sym, interval, limit, bot.fetch_with_retry)
        # 热调用：小拉取 + 合并
        warm = cached_fetch_klines(sym, interval, limit, bot.fetch_with_retry)
        # 直接全量
        direct = bot.fetch_with_retry(sym, interval, limit)
        ok, msg = bars_equal(warm, direct)
        detail = ""
        if not ok:
            # 容忍恰好跨过收盘边界：warm 的每根都必须在 direct 里逐值相同，
            # 且 direct 最多比 warm 多 1 根（新收盘的 bar）。
            dmap = {b["t"]: b for b in direct}
            all_match = all(dmap.get(b["t"]) == b for b in warm)
            tail_extra = len(direct) - len(warm)
            warm_last = warm[-1]["t"] if warm else None
            direct_last = direct[-1]["t"] if direct else None
            step = 60000 if interval == "1m" else 300000
            boundary = (all_match and 0 <= tail_extra <= 1
                        and direct_last - warm_last in (0, step))
            detail = ("  [边界容忍] 旧 bar 全相同=%s, direct 多 %d 根, "
                      "末根差 %s ms" % (
                          all_match, tail_extra,
                          direct_last - warm_last if warm_last else None))
            if boundary:
                ok, msg = True, "仅差一根新收盘 bar（边界），旧 bar 全相同"
        # 冷调用也应与直接拉取一致（同上容忍）
        ok_c, msg_c = bars_equal(cold, direct)
        print("%-10s %-3s limit=%4d 冷=%s 热=%s %s%s" % (
            sym, interval, limit, msg_c, msg,
            "PASS" if (ok and ok_c) else "FAIL", detail if not ok else ""))
        if ok and ok_c:
            n_ok += 1
    print("通过 %d/%d" % (n_ok, len(CASES)))
    return 0 if n_ok == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
