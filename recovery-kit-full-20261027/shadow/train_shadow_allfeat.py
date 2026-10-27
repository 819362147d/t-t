#!/usr/bin/env python3
"""影子版全组合模型训练：在 350 天全量数据上重训（FEAT3 31 + 实验二 13 个新特征）。

协议与现网 train_10min_models.py 逐项一致：同样的 LightGBM 参数
（num_leaves=15, min_child_samples=1000, lr=0.05, feature_fraction=0.8,
bagging 0.8/5, seed=42, deterministic）；同样的定轮数方式（时间顺序最后 15%
做 early stopping 定 best_iter，再全量 refit）。
特征 = exp2_wf.py 'all' 配置：FEAT3 (mf_data_1m/FEAT3_*.npz) 列 + exp2_feat_*.npz
的 G 全 13 列（顺序与 exp2_wf 一致）。
输出 lightgbm_shadow_allfeat.txt + model_shadow_allfeat_meta.json。
只训练、不交易、不推送。"""

import json

import numpy as np
import lightgbm as lgb

HF = "/home/hatch/workspace/goals/tradingview-binance-indicator-monitoring-with-wecom-alerts/hidden_files"
D1M = HF + "/mf_data_1m"


def load(sym):
    d = np.load("%s/FEAT3_%s.npz" % (D1M, sym))
    e = np.load("%s/exp2_feat_%s.npz" % (HF, sym))
    assert np.array_equal(e["t"].astype(np.int64), d["t"].astype(np.int64)), sym
    F = np.column_stack([d["F"].astype(np.float64), e["G"].astype(np.float64)])
    names = [str(x) for x in d["names"]] + [str(x) for x in e["names"]]
    return F, d["y"].astype(int), d["t"].astype(np.int64), names


def main():
    Fb, yb, tb, names = load("BTCUSDT")
    Fe, ye, te, names_e = load("ETHUSDT")
    assert names == names_e, "feature name mismatch"
    assert len(names) == 44, "expected 44 features, got %d" % len(names)
    F = np.vstack([Fb, Fe])
    y = np.concatenate([yb, ye])
    t = np.concatenate([tb, te])
    order = np.argsort(t, kind="stable")
    F, y, t = F[order], y[order], t[order]
    n = len(y)
    print("n=%d base_up=%.4f t0=%d t1=%d" % (n, y.mean(), t[0], t[-1]), flush=True)
    assert np.all(np.isfinite(F)), "训练特征含非有限值"

    params = dict(objective="binary", num_leaves=15, learning_rate=0.05,
                  min_child_samples=1000, feature_fraction=0.8,
                  bagging_fraction=0.8, bagging_freq=5,
                  verbose=-1, num_threads=2, seed=42,
                  deterministic=True)

    # 1) early stopping 定轮数（最后 15% 时间顺序验证，与现网协议一致）
    cut = int(n * 0.85)
    dtr = lgb.Dataset(F[:cut], label=y[:cut])
    des = lgb.Dataset(F[cut:], label=y[cut:], reference=dtr)
    probe = lgb.train(params, dtr, num_boost_round=500, valid_sets=[des],
                      callbacks=[lgb.early_stopping(50, verbose=False)])
    best_iter = int(probe.best_iteration)
    print("early-stopping best_iter=%d" % best_iter, flush=True)

    # 2) 全量 refit
    dall = lgb.Dataset(F, label=y)
    bst = lgb.train(params, dall, num_boost_round=best_iter)
    bst.save_model(HF + "/lightgbm_shadow_allfeat.txt")
    print("saved lightgbm_shadow_allfeat.txt", flush=True)

    # 3) 全量训练集上的分档表现（仅记录，样本内口径、非样本外）
    p = bst.predict(F)
    insample = {}
    for band in (0.55, 0.58, 0.60):
        up = p >= band
        dn = p <= 1.0 - band
        tr = up | dn
        win = ((up & (y == 1)) | (dn & (y == 0)))
        insample[str(band)] = {
            "n": int(tr.sum()),
            "wr": float(win[tr].mean()) if tr.sum() else float("nan"),
        }
        print("train(in-sample) band=%.2f wr=%.4f n=%d"
              % (band, insample[str(band)]["wr"], insample[str(band)]["n"]),
              flush=True)

    meta = {
        "feature_names": names,
        "n_train_full": n,
        "t0_ms": int(t[0]),
        "t1_ms": int(t[-1]),
        "base_up": float(y.mean()),
        "gbm_params": {k: v for k, v in params.items() if k != "verbose"},
        "gbm_best_iter_probe": best_iter,
        "gbm_refit_rounds": best_iter,
        "bands": [0.55, 0.58, 0.60],
        "insample_band_stats": insample,
        "note": ("影子版全组合（FEAT3+13 新特征），训练协议与现网一致；样本外期望"
                 "以实验二 walk-forward 为准：0.55 档 0.566（n=26,926）、0.58 档 "
                 "0.589（n=6,617）、0.60 档约 0.596（n=2,304），均为线索级、CI 与"
                 "基线重叠。OI 特征语义：Vision metrics 存档 create_time X 的值 "
                 "== REST openInterestHist 标签 X+300000 的值（2026-10-19 全天 "
                 "288/288 点精确验证）；影子实盘按 A(X)=REST(X+300000) 复刻，"
                 "故每轮计算候选时刻为最新已收盘 bar 再往前一根（滞后两 tick "
                 "记账，含 OI 最新格点约 1-2 分钟发布延迟的余量）。"
                 "存档 OI 在 2026-10-15 约 11:45–15:10 UTC 有损坏段（与已知 0 值"
                 "坏 tick 同窗，BTC 偏离 ≤3%、ETH ≤23%），该窗候选点的回测 OI "
                 "特征受污染，样本占比小但如实记录。"),
    }
    with open(HF + "/model_shadow_allfeat_meta.json", "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print("saved model_shadow_allfeat_meta.json", flush=True)


if __name__ == "__main__":
    main()
