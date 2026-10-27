#!/usr/bin/env python3
# 多币种预报 K 线磁盘缓存（只读加速，不改模型/阈值/预报口径）
#
# 背景：event_10min_multicoin_bot.py 每 5 分钟一轮，给约 350 个币各拉
# 1m×500 根 + 5m×1500 根全量 K 线，耗时约 60-70 秒，大头是重复拉
# 全量历史。已收盘 K 线不可变（币安不修订），因此可缓存：每轮只拉
# 最新几根、与缓存合并、去重排序、去未收盘尾巴，喂给现有特征计算。
#
# 设计要点（fail-open：缓存任何问题都自动全量重拉，绝不导致无预报/错预报）：
#   * key = 币种+周期，文件 hidden_files/multicoin_klines_cache/{SYM}_{interval}.npz，
#     存最近 `limit` 根已收盘 K 线（1m=500、5m=1500，与现网请求口径一致，
#     已含 Wilder/EMA 热身余量；特征实际只需其中一小部分）。
#   * 每轮先读缓存，再用 fetch_fn 拉一小段最新 K 线（1m 取 40 根、5m 取 16 根，
#     覆盖 5 分钟轮次间隔 + 余量），合并后校验：
#       1) 合并后最新一根 == 小拉取的最新一根（拿到最新已收盘 bar）；
#       2) 全程连续（相邻 t 差值恒等于周期毫秒数），有断档即全量重拉；
#       3) 缓存 limit >= 本次请求 limit，否则全量重拉。
#     任一校验失败 -> 全量重拉并刷新缓存。
#   * 写缓存用临时文件 + fsync + os.replace 原子替换，崩溃不留半写文件；
#     文件内含 crc32 校验，静默损坏也能检出并全量重拉。
#   * 只用标准库 + numpy，不引入重型依赖。缓存读写失败只记日志不抛异常；
#     只有底层拉数本身失败时才抛异常（与改前行为一致：该币种本轮跳过）。
#   * 每个币种独立文件，单文件损坏只影响该币种。进程间理论上不重叠
#     （每 5 分钟一个进程），同币种同轮只由一个线程写，文件名含 pid/tid
#     防临时文件冲突。

import os
import threading
import zlib

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "multicoin_klines_cache")

INTERVAL_MS = {"1m": 60000, "5m": 300000}
# 小拉取窗口：覆盖一轮间隔（5 分钟）并留足余量；1m 40 根=40 分钟，5m 16 根=80 分钟。
# 若停机/漏轮导致缺口超出此窗口，合并校验会失败并自动全量重拉。
SMALL_LIMIT = {"1m": 40, "5m": 16}

_KEYS = ("t", "o", "h", "l", "c", "v")


def log(msg):
    import sys
    sys.stderr.write("[klines_cache] %s\n" % msg)
    sys.stderr.flush()


def _cache_path(symbol, interval):
    return os.path.join(CACHE_DIR, "%s_%s.npz" % (symbol, interval))


def _crc(t_arr, c_arr):
    h = zlib.crc32(t_arr.tobytes())
    h = zlib.crc32(c_arr.tobytes(), h)
    return h & 0xFFFFFFFF


def _bars_to_arrays(bars):
    t = np.array([b["t"] for b in bars], dtype=np.int64)
    o = np.array([b["o"] for b in bars], dtype=np.float64)
    h = np.array([b["h"] for b in bars], dtype=np.float64)
    l = np.array([b["l"] for b in bars], dtype=np.float64)
    c = np.array([b["c"] for b in bars], dtype=np.float64)
    v = np.array([b["v"] for b in bars], dtype=np.float64)
    return t, o, h, l, c, v


def _arrays_to_bars(t, o, h, l, c, v):
    return [{"t": int(t[i]), "o": float(o[i]), "h": float(h[i]),
             "l": float(l[i]), "c": float(c[i]), "v": float(v[i])}
            for i in range(len(t))]


def _save_cache(symbol, interval, bars, limit):
    """原子写缓存。失败只记日志，不抛异常（不影响本轮预报）。"""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        t, o, h, l, c, v = _bars_to_arrays(bars)
        tmp = os.path.join(
            CACHE_DIR, ".%s_%s.%d.%d.tmp.npz" % (
                symbol, interval, os.getpid(), threading.get_ident()))
        with open(tmp, "wb") as fh:
            np.savez(fh, t=t, o=o, h=h, l=l, c=c, v=v,
                     limit=np.int64(limit),
                     crc=np.int64(_crc(t, c)))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, _cache_path(symbol, interval))
    except Exception as e:  # noqa: BLE001
        log("写缓存失败 %s %s: %s（继续用本次拉取结果）" % (symbol, interval, e))
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:  # noqa: BLE001
            pass


def _load_cache(symbol, interval):
    """读缓存。返回 (bars, stored_limit)，任何问题返回 (None, None)。"""
    path = _cache_path(symbol, interval)
    try:
        with np.load(path) as z:
            t = z["t"]; o = z["o"]; h = z["h"]
            l = z["l"]; c = z["c"]; v = z["v"]
            limit = int(z["limit"]); crc = int(z["crc"])
            if _crc(t, c) != crc:
                log("缓存校验失败 %s %s：crc 不符，全量重拉" % (symbol, interval))
                return None, None
            if (t.dtype != np.int64 or len(t) < 2
                    or not (len(t) == len(o) == len(h) == len(l)
                            == len(c) == len(v))):
                return None, None
            if not np.all(np.diff(t) > 0):
                return None, None
            for arr in (o, h, l, c, v):
                if not np.all(np.isfinite(arr)):
                    return None, None
            return _arrays_to_bars(t, o, h, l, c, v), limit
    except Exception as e:  # noqa: BLE001
        # 缺失/损坏/格式不符：一律按缓存未命中处理（不记日志刷屏，
        # 缺失是冷启动常态；只有 crc 失败才在上面记了一条）。
        if isinstance(e, FileNotFoundError):
            return None, None
        log("读缓存失败 %s %s: %s，全量重拉" % (symbol, interval, e))
        return None, None


def cached_fetch_klines(symbol, interval, limit, fetch_fn):
    """带缓存的 K 线拉取，与 fetch_fn(symbol, interval, limit) 返回口径完全一致：
    已收盘 K 线、去重、按时间升序。fetch_fn 是现有的 fetch_with_retry。
    注意口径细节：fetch_klines(limit) 拉 limit 根（含未收盘的形成中 bar）
    再去尾，因此只返回 limit-1 根已收盘 bar；缓存合并后同样只保留最近
    limit-1 根，否则会多出一根旧 bar、轻微改变指标热身。缓存失效/
    校验失败/小拉取失败时自动全量重拉（fail-open）。"""
    step_ms = INTERVAL_MS[interval]
    keep = max(1, limit - 1)  # 与 fetch_klines 去尾后的根数一致
    cached, stored_limit = _load_cache(symbol, interval)

    if cached is None or stored_limit is None or stored_limit < limit:
        # 冷启动 / 缓存 limit 不足：全量拉取并写缓存。
        bars = fetch_fn(symbol, interval, limit)
        _save_cache(symbol, interval, bars, limit)
        return bars

    # 增量路径：只拉最新一小段，与缓存合并。
    try:
        new = fetch_fn(symbol, interval, SMALL_LIMIT[interval])
    except Exception:  # noqa: BLE001
        # 小拉取失败 -> 全量重拉（若全量也失败，异常向上传播，与改前一致）。
        bars = fetch_fn(symbol, interval, limit)
        _save_cache(symbol, interval, bars, limit)
        return bars

    if not new:
        # 小拉取返回空（不应发生）：保守起见全量重拉。
        bars = fetch_fn(symbol, interval, limit)
        _save_cache(symbol, interval, bars, limit)
        return bars

    merged = {}
    for b in cached:
        merged[b["t"]] = b
    for b in new:
        merged[b["t"]] = b
    ts = sorted(merged)
    bars = [merged[t] for t in ts]

    # 校验 1：拿到最新已收盘 bar。
    # 校验 2：全程连续无断档（闭区间 bar 不可变，连续即与全量拉取等价）。
    ok = (bars[-1]["t"] == new[-1]["t"] and len(bars) >= 2)
    if ok:
        diffs = [bars[i + 1]["t"] - bars[i]["t"]
                 for i in range(len(bars) - 1)]
        ok = all(d == step_ms for d in diffs)
    if not ok:
        log("缓存合并校验失败 %s %s（断档或未取到最新 bar），全量重拉"
            % (symbol, interval))
        bars = fetch_fn(symbol, interval, limit)
        _save_cache(symbol, interval, bars, limit)
        return bars

    # 与全量拉取同一窗口：只保留最近 keep 根（= fetch_klines 去尾后的根数）。
    bars = bars[-keep:]
    _save_cache(symbol, interval, bars, limit)
    return bars
