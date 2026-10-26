#!/usr/bin/env python3
"""fail-open 校验：缓存损坏 / 断档时自动全量重拉，结果仍与直接拉取一致。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import event_10min_multicoin_bot as bot
from multicoin_klines_cache import (
    CACHE_DIR, _cache_path, _load_cache, _save_cache, cached_fetch_klines)
import numpy as np


def direct(sym, interval, limit):
    return bot.fetch_with_retry(sym, interval, limit)


def main():
    ok_all = True
    # 1) 损坏文件：写垃圾进缓存，load 应返回 None，cached_fetch 应全量重拉且结果正确
    sym, interval, limit = "AUSDT", "1m", 500
    cached_fetch_klines(sym, interval, limit, bot.fetch_with_retry)  # 确保有缓存
    p = _cache_path(sym, interval)
    with open(p, "wb") as fh:
        fh.write(b"garbage-not-a-npz")
    b, _ = _load_cache(sym, interval)
    print("损坏文件 load 返回 None:", b is None)
    got = cached_fetch_klines(sym, interval, limit, bot.fetch_with_retry)
    ref = direct(sym, interval, limit)
    same = got == ref or (len(got) == len(ref) and all(
        got[i] == ref[i] for i in range(min(len(got), len(ref)))))
    print("损坏后全量重拉结果与直接拉取一致:", same, "len", len(got), len(ref))
    ok_all &= (b is None and same)

    # 2) 断档：删掉缓存中间一段 bar，合并校验应失败并全量重拉
    sym, interval, limit = "RVNUSDT", "5m", 1500
    bars = cached_fetch_klines(sym, interval, limit, bot.fetch_with_retry)
    cut = bars[:500] + bars[600:]  # 中间挖掉 100 根
    _save_cache(sym, interval, cut, limit)
    got = cached_fetch_klines(sym, interval, limit, bot.fetch_with_retry)
    ref = direct(sym, interval, limit)
    same = got == ref or (len(got) == len(ref) and all(
        got[i] == ref[i] for i in range(min(len(got), len(ref)))))
    print("断档后全量重拉结果与直接拉取一致:", same, "len", len(got), len(ref))
    ok_all &= same

    # 3) 缓存文件缺失：删文件，走冷启动
    sym, interval, limit = "CELOUSDT", "1m", 500
    cached_fetch_klines(sym, interval, limit, bot.fetch_with_retry)
    os.remove(_cache_path(sym, interval))
    got = cached_fetch_klines(sym, interval, limit, bot.fetch_with_retry)
    ref = direct(sym, interval, limit)
    same = got == ref or (len(got) == len(ref) and all(
        got[i] == ref[i] for i in range(min(len(got), len(ref)))))
    print("缺失后冷启动结果与直接拉取一致:", same, "len", len(got), len(ref))
    ok_all &= same
    print("FAIL-OPEN 全部通过" if ok_all else "FAIL-OPEN 有失败")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
