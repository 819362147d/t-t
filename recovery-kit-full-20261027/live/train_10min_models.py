#!/usr/bin/env python3
"""10 分钟事件合约部署模型训练：在 350 天全量数据上重训。

主模型: LightGBM_reg（正则参数与 v3 报告一致：num_leaves=15,
min_child_samples=1000, lr=0.05, feature_fraction=0.8, bagging 0.8/5）。
轮数确定：先用最后 15%（时间顺序）做 early stopping 定 best_iter，
再在全量数据上 refit。输出 lightgbm_10min.txt。
备用模型: LR（StandardScaler + LogisticRegression(max_iter=2000)，与 v3 一致），
输出 lr_10min.pkl（含 scaler）。
元信息: model_10min_meta.json。
只训练、不交易。"""

import json
import pickle

import numpy as np
import lightgbm as lgb
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression

HF = "/home/hatch/workspace/goals/tradingview-binance-indicator-monitoring-with-wecom-alerts/hidden_files"
D1M = HF + "/mf_data_1m"

LONG_THR, SHORT_THR = 0.55, 0.45  # v3 验证段选定的固定阈值


def load(sym):
    d = np.load(f"{D1M}/FEAT3_{sym}.npz")
    return (d["F"].astype(np.float64), d["y"].astype(int),
            d["t"].astype(np.int64), [str(x) for x in d["names"]])


def main():
    Fb, yb, tb, names = load("BTCUSDT")
    Fe, ye, te, names_e = load("ETHUSDT")
    assert names == names_e, "feature name mismatch"
    assert len(names) == 31, f"expected 31 features, got {len(names)}"
    F = np.vstack([Fb, Fe])
    y = np.concatenate([yb, ye])
    t = np.concatenate([tb, te])
    order = np.argsort(t, kind="stable")  # 时间顺序（双标的交错）
    F, y, t = F[order], y[order], t[order]
    n = len(y)
    print(f"n={n} base_up={y.mean():.4f} t0={t[0]} t1={t[-1]}", flush=True)

    params = dict(objective="binary", num_leaves=15, learning_rate=0.05,
                  min_child_samples=1000, feature_fraction=0.8,
                  bagging_fraction=0.8, bagging_freq=5,
                  verbose=-1, num_threads=2, seed=42,
                  deterministic=True)

    # 1) early stopping 定轮数（最后 15% 时间顺序验证，与 v3 协议一致）
    cut = int(n * 0.85)
    dtr = lgb.Dataset(F[:cut], label=y[:cut])
    des = lgb.Dataset(F[cut:], label=y[cut:], reference=dtr)
    probe = lgb.train(params, dtr, num_boost_round=500, valid_sets=[des],
                      callbacks=[lgb.early_stopping(50, verbose=False)])
    best_iter = int(probe.best_iteration)
    print(f"early-stopping best_iter={best_iter}", flush=True)

    # 2) 全量 refit
    dall = lgb.Dataset(F, label=y)
    bst = lgb.train(params, dall, num_boost_round=best_iter)
    bst.save_model(HF + "/lightgbm_10min.txt")
    print("saved lightgbm_10min.txt", flush=True)

    # 3) LR 备用（全量）
    sc = StandardScaler().fit(F)
    lr = LogisticRegression(max_iter=2000).fit(sc.transform(F), y)
    with open(HF + "/lr_10min.pkl", "wb") as f:
        pickle.dump({"scaler": sc, "model": lr, "feature_names": names}, f)
    print("saved lr_10min.pkl", flush=True)

    # 4) 全量训练集上的阈值表现（仅记录，非样本外口径）
    p = bst.predict(F)
    for name, mask in [("long", p >= LONG_THR), ("short", p <= SHORT_THR)]:
        m = mask
        wr = float(y[m].mean()) if m.sum() else float("nan")
        print(f"train(in-sample) {name}: thr={LONG_THR if name=='long' else SHORT_THR} "
              f"wr={wr:.4f} n={int(m.sum())}", flush=True)

    meta = {
        "feature_names": names,
        "n_train_full": n,
        "t0_ms": int(t[0]),
        "t1_ms": int(t[-1]),
        "base_up": float(y.mean()),
        "gbm_params": {k: v for k, v in params.items() if k != "verbose"},
        "gbm_best_iter_probe": best_iter,
        "gbm_refit_rounds": best_iter,
        "long_thr": LONG_THR,
        "short_thr": SHORT_THR,
        "note": ("阈值沿用 v3 验证段选定值(0.55/0.45)；期望胜率以 v3 样本外为准 "
                 "(GBM_reg 只做多 0.6449 n=138 偏薄；稳健 K=1000 档 0.627；"
                 "做空方向约 0.57)。训练→验证衰减大，过拟合风险如实存在。"),
    }
    with open(HF + "/model_10min_meta.json", "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print("saved model_10min_meta.json", flush=True)


if __name__ == "__main__":
    main()
