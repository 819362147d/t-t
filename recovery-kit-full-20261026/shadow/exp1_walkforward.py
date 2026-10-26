#!/usr/bin/env python3
"""实验一（纯离线）: 现网 LightGBM 的 walk-forward 重估 + 阈值扫描 + regime 分层。

协议:
- 数据: FEAT3_{BTC,ETH}.npz 合并按时间排序 (n=200854, 350 天, 5 分钟候选网格)。
- 6 折扩张窗口: 训练 [0, 40%+10%(k-1)), 验证紧随其后的 10% (k=1..6),
  样本外覆盖后 60% (~210 天)。训练/验证边界加 10 分钟 embargo (标签前视期)。
- 每折按现网部署协议训练: 现网参数 (num_leaves=15, min_child=1000, lr=0.05,
  ff=0.8, bag 0.8/5, seed=42); 内层最后 15% 早停定轮数, 再全量重训该折训练段。
- 评估: 双阈值触发 (看涨 p>=hi 赢=y==1; 看跌 p<=lo 赢=y==0), ties 按 y 原值计
  (与既往回测口径一致)。
输出: exp1_results.json + exp1_oos.npz (逐样本样本外预测, 供分层复用)。只读数据, 不碰现网。
"""
import json
import numpy as np
import lightgbm as lgb

HF = "/home/hatch/workspace/goals/tradingview-binance-indicator-monitoring-with-wecom-alerts/hidden_files"
D1M = HF + "/mf_data_1m"
PARAMS = dict(objective="binary", num_leaves=15, learning_rate=0.05,
              min_child_samples=1000, feature_fraction=0.8,
              bagging_fraction=0.8, bagging_freq=5,
              verbose=-1, num_threads=2, seed=42, deterministic=True)
THRESHOLDS = [0.55, 0.58, 0.60, 0.62]
EMBARGO_MS = 600_000  # 10 分钟标签前视


def load():
    Ts, Ss, Fs, Ys, Ties = [], [], [], [], []
    names = None
    for sym in ["BTCUSDT", "ETHUSDT"]:
        d = np.load("%s/FEAT3_%s.npz" % (D1M, sym))
        Ts.append(d["t"].astype(np.int64)); Ss.append(d["sym"])
        Fs.append(d["F"].astype(np.float64)); Ys.append(d["y"].astype(np.int8))
        Ties.append(d["ties"].astype(bool)); names = list(d["names"])
    t = np.concatenate(Ts); s = np.concatenate(Ss)
    F = np.vstack(Fs); y = np.concatenate(Ys); ties = np.concatenate(Ties)
    o = np.argsort(t, kind="stable")
    return t[o], s[o], F[o], y[o], ties[o], names


def train_fold(Ftr, ytr):
    inner = int(len(ytr) * 0.85)
    dtr = lgb.Dataset(Ftr[:inner], label=ytr[:inner])
    des = lgb.Dataset(Ftr[inner:], label=ytr[inner:], reference=dtr)
    probe = lgb.train(PARAMS, dtr, num_boost_round=500, valid_sets=[des],
                      callbacks=[lgb.early_stopping(50, verbose=False)])
    best = int(probe.best_iteration)
    dall = lgb.Dataset(Ftr, label=ytr)
    bst = lgb.train(PARAMS, dall, num_boost_round=best)
    return bst, best


def dual_stats(p, y, hi):
    lo = 1.0 - hi
    up = p >= hi; dn = p <= lo
    tr = up | dn
    n = int(tr.sum())
    if n == 0:
        return dict(n=0, wr=float("nan"), n_long=0, wr_long=float("nan"),
                    n_short=0, wr_short=float("nan"))
    win = ((up & (y == 1)) | (dn & (y == 0)))
    return dict(
        n=n, wr=float(win[tr].mean()),
        n_long=int(up.sum()),
        wr_long=float((y[up] == 1).mean()) if up.sum() else float("nan"),
        n_short=int(dn.sum()),
        wr_short=float((y[dn] == 0).mean()) if dn.sum() else float("nan"))


def wilson(wr, n, z=1.96):
    if n == 0 or not np.isfinite(wr):
        return [float("nan"), float("nan")]
    d = 1 + z * z / n
    c = (wr + z * z / (2 * n)) / d
    h = z * np.sqrt(wr * (1 - wr) / n + z * z / (4 * n * n)) / d
    return [float(c - h), float(c + h)]


def main():
    t, s, F, y, ties, names = load()
    n = len(y)
    print("n=%d  span_days=%.1f  base_up=%.4f  ties=%.4f" %
          (n, (t[-1] - t[0]) / 86400000, y.mean(), ties.mean()), flush=True)

    folds = []
    oos = []  # (t, s, y, p, fold)
    bounds = [0.40 + 0.10 * k for k in range(7)]
    for k in range(6):
        i_tr_end = int(n * bounds[k + 1]) if False else int(n * (0.40 + 0.10 * k))
        # 训练段终点 = 验证段起点 (扩张窗口)
        i_va_start = int(n * (0.40 + 0.10 * k))
        i_va_end = int(n * (0.50 + 0.10 * k)) if k < 5 else n
        t_va0 = t[i_va_start]
        tr_mask = np.zeros(n, bool); tr_mask[:i_va_start] = True
        tr_mask &= t <= (t_va0 - EMBARGO_MS)  # embargo: 训练标签不得探入验证段
        Ftr, ytr = F[tr_mask], y[tr_mask]
        Fva, yva = F[i_va_start:i_va_end], y[i_va_start:i_va_end]
        tva, sva = t[i_va_start:i_va_end], s[i_va_start:i_va_end]
        bst, best = train_fold(Ftr, ytr)
        p = bst.predict(Fva)
        st55 = dual_stats(p, yva, 0.55)
        days = (tva[-1] - tva[0]) / 86400000
        folds.append(dict(fold=k + 1, n_train=int(tr_mask.sum()), n_val=int(len(yva)),
                          best_iter=best, days=float(days), stats55=st55,
                          t_start=int(tva[0]), t_end=int(tva[-1])))
        oos.append(np.column_stack([tva, sva, yva, ties[i_va_start:i_va_end], p,
                                    np.full(len(yva), k + 1)]))
        print("fold%d train=%d val=%d best_iter=%d  0.55: wr=%.4f n=%d (L %.4f/%d, S %.4f/%d)"
              % (k + 1, tr_mask.sum(), len(yva), best, st55["wr"], st55["n"],
                 st55["wr_long"], st55["n_long"], st55["wr_short"], st55["n_short"]),
              flush=True)
        del bst

    O = np.vstack(oos)
    np.savez_compressed(HF + "/exp1_oos.npz", O=O, names=np.array(names))
    t_o, s_o, y_o, ties_o, p_o = O[:, 0], O[:, 1], O[:, 2], O[:, 3], O[:, 4]
    total_days = float(sum(f["days"] for f in folds))

    scan = {}
    for hi in THRESHOLDS:
        st = dual_stats(p_o, y_o, hi)
        st["per_day"] = st["n"] / total_days
        st["ci95"] = wilson(st["wr"], st["n"])
        scan[str(hi)] = st
        print("thr=%.2f pooled wr=%.4f n=%d (%.1f/day) CI95=[%.4f, %.4f] L %.4f/%d S %.4f/%d"
              % (hi, st["wr"], st["n"], st["per_day"], st["ci95"][0], st["ci95"][1],
                 st["wr_long"], st["n_long"], st["wr_short"], st["n_short"]), flush=True)

    res = dict(n_total=int(n), oos_days=total_days, folds=folds, scan=scan,
               oos_base_up=float(y_o.mean()), oos_ties=float(ties_o.mean()))
    with open(HF + "/exp1_results.json", "w") as f:
        json.dump(res, f, indent=2)
    print("saved exp1_results.json / exp1_oos.npz", flush=True)


if __name__ == "__main__":
    main()
