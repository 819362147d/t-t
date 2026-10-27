#!/usr/bin/env python3
""" profiling：拆解暖缓存一轮的耗时（拉数+缓存 vs 特征计算 vs 预测）。"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import event_10min_multicoin_bot as bot
from multicoin_klines_cache import cached_fetch_klines
from concurrent.futures import ThreadPoolExecutor, as_completed


def main():
    t0 = time.time()
    model, err = bot.load_models()
    t_model = time.time() - t0
    universe = bot.load_universe()
    t_univ = time.time() - t0 - t_model

    t1 = time.time()
    k5m_ref = cached_fetch_klines("BTCUSDT", "5m", bot.K5M_LIMIT,
                                  bot.fetch_with_retry)
    t_ref = time.time() - t1

    # 阶段 A：拉数+缓存（含小拉取、合并、读写缓存），不含特征
    def fetch_only(sym):
        try:
            k1m = cached_fetch_klines(sym, "1m", bot.K1M_LIMIT,
                                      bot.fetch_with_retry)
            k5m = cached_fetch_klines(sym, "5m", bot.K5M_LIMIT,
                                      bot.fetch_with_retry)
            return sym, (k1m, k5m), None
        except Exception as e:  # noqa: BLE001
            return sym, None, str(e)

    t2 = time.time()
    fetched = {}
    with ThreadPoolExecutor(max_workers=bot.MAX_WORKERS) as ex:
        futs = {ex.submit(fetch_only, s): s for s in universe}
        for fut in as_completed(futs):
            sym, payload, ferr = fut.result()
            if payload:
                fetched[sym] = payload
    t_fetch = time.time() - t2

    # 阶段 B：特征计算
    t3 = time.time()
    n_feat = 0
    for sym, (k1m, k5m) in fetched.items():
        t_ms, close, feats = bot.compute_features(k1m, k5m, k5m_ref)
        if feats is not None:
            n_feat += 1
    t_feat = time.time() - t3

    # 阶段 C：预测（主线程单行 LightGBM）
    t4 = time.time()
    for sym, (k1m, k5m) in fetched.items():
        t_ms, close, feats = bot.compute_features(k1m, k5m, k5m_ref)
        if feats is not None:
            bot.predict(model, feats)
    t_pred = time.time() - t4

    print("模型加载 %.1fs | 宇宙加载 %.1fs | BTC参考 %.1fs" % (
        t_model, t_univ, t_ref))
    print("阶段A 拉数+缓存 %.1fs（%d 币）" % (t_fetch, len(fetched)))
    print("阶段B 特征计算 %.1fs（%d 币有特征）" % (t_feat, n_feat))
    print("阶段C 预测 %.1fs（含重复算特征，仅作参考）" % t_pred)
    print("合计约 %.1fs" % (t_model + t_univ + t_ref + t_fetch + t_feat))


if __name__ == "__main__":
    main()
