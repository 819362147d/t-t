#!/usr/bin/env python3
"""v3 特征: 复用 FEAT2 npz(25 特征/候选时刻/y), 拼接 6 个 1m 粒度新特征.
新特征: rsi14_1m, rsi14_3m, boll_pctB_1m, cvd_z_1m, mom_10m, mom_30m.
候选时刻 t 为 5m bar 收盘(ms); 1m 特征取 t1c<=t 的最后已收盘 1m bar. 严禁未来函数.
输出 hidden_files/mf_data_1m/FEAT3_{SYM}.npz"""
import sys, os, glob
import numpy as np
import pandas as pd

HF = "/home/hatch/workspace/goals/tradingview-binance-indicator-monitoring-with-wecom-alerts/hidden_files"
sys.path.insert(0, HF)
from mf_features import rsi_wilder, roll_mean, roll_sd, cvd_z  # noqa
from mf_features_v2 import boll_pctb  # noqa

D1M = HF + "/mf_data_1m"
FEAT2 = "/home/hatch/workspace/goals/tradingview-binance-indicator-monitoring-with-wecom-alerts/hidden_files/retrain_work/mf_data"  # v2 npz 位置(/tmp 已被重启清空,用 workspace 备份)
NEW_NAMES = ["rsi14_1m", "rsi14_3m", "boll_pctB_1m", "cvd_z_1m", "mom_10m", "mom_30m"]


def load_1m(sym):
    files = sorted(glob.glob("%s/%s_1m_*.csv" % (D1M, sym)))
    assert files, "no 1m csv for %s" % sym
    dfs = [pd.read_csv(f) for f in files]
    df = pd.concat(dfs, ignore_index=True).drop_duplicates("t").sort_values("t").reset_index(drop=True)
    t1 = df["t"].values.astype(np.int64)          # open ms
    o = df["o"].values.astype(float); h = df["h"].values.astype(float)
    l = df["l"].values.astype(float); c = df["c"].values.astype(float)
    v = df["v"].values.astype(float)
    t1c = t1 + 60000
    del df, dfs
    return t1c, o, h, l, c, v


def build(sym):
    d = np.load("%s/FEAT2_%s.npz" % (FEAT2, sym))
    tcand = d["t"].astype(np.int64)
    Fold, y, ties, symid = d["F"], d["y"], d["ties"], d["sym"]
    old_names = list(d["names"])
    n = len(tcand)

    t1c, o1, h1, l1, c1, v1 = load_1m(sym)

    # 1m 口径指标
    r1m = rsi_wilder(c1)
    b1m = boll_pctb(c1)
    z1m = cvd_z(o1, c1, v1)
    # 3m 合成
    grp = ((t1c - 60000) // 180000).astype(np.int64)
    dd = pd.DataFrame({"g": grp, "t": t1c, "c": c1})
    agg = dd.groupby("g").agg(t=("t", "max"), c=("c", "last"), nn=("t", "size"))
    agg = agg[agg["nn"] == 3].reset_index(drop=True)
    t3c, c3 = agg["t"].values, agg["c"].values.astype(float)
    r3m = rsi_wilder(c3)
    del dd, agg

    j1 = np.searchsorted(t1c, tcand, side="right") - 1
    j3 = np.searchsorted(t3c, tcand, side="right") - 1

    F3 = np.full((n, len(NEW_NAMES)), np.nan)
    ok = (j1 >= 40) & (j1 < len(c1)) & (j3 >= 20) & (j3 < len(c3))
    jj1, jj3 = j1[ok], j3[ok]
    F3[ok, 0] = r1m[jj1]
    F3[ok, 1] = r3m[jj3]
    F3[ok, 2] = b1m[jj1]
    F3[ok, 3] = z1m[jj1]
    F3[ok, 4] = c1[jj1] / c1[jj1 - 10] - 1.0
    F3[ok, 5] = c1[jj1] / c1[jj1 - 30] - 1.0

    fin = np.all(np.isfinite(F3), axis=1)
    Fall = np.column_stack([Fold[fin], F3[fin]]).astype(np.float32)
    names = old_names + NEW_NAMES
    out = "%s/FEAT3R_%s.npz" % (D1M, sym)
    np.savez_compressed(out, t=tcand[fin], sym=symid[fin], F=Fall,
                        y=y[fin], ties=ties[fin], names=np.array(names))
    print(sym, "v3 candidates:", int(fin.sum()), "of", n, flush=True)
    del F3, Fall, r1m, b1m, z1m, r3m


if __name__ == "__main__":
    build("BTCUSDT")
    build("ETHUSDT")
