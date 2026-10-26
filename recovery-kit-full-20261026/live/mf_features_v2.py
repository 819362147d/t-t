#!/usr/bin/env python3
"""v2 新增特征: 复用 v1 的 FEAT npz(候选时刻/旧特征/y), 只算新指标并拼接到 25 列.
新指标: rsi14@5m, rsi14@1h, boll_pctB@5m, boll_pctB@1h, kdj_j@15m, willr14@15m,
        跨标的 1h/4h 动量, hour_sin/cos, dow_sin/cos. 严禁未来函数.
输出 /tmp/mf_data/FEAT2_{SYM}.npz"""
import sys
import numpy as np
import pandas as pd

HF = "/home/hatch/workspace/goals/tradingview-binance-indicator-monitoring-with-wecom-alerts/hidden_files"
sys.path.insert(0, HF)
from mf_features import rsi_wilder, rma, roll_mean, roll_sd  # noqa

DATA = "/tmp/mf_data"
NEW_NAMES = ["rsi14_5m", "rsi14_1h", "boll_pctB_5m", "boll_pctB_1h",
             "kdj_j_15m", "willr14_15m", "x_mom_1h", "x_mom_4h",
             "hour_sin", "hour_cos", "dow_sin", "dow_cos"]


def load_5m(sym):
    df = pd.read_csv("%s/%s_5m.csv" % (DATA, sym))
    t5 = (df["t"].values // 1000).astype(np.int64)  # 微秒->毫秒(open)
    o5 = df["o"].values; h5 = df["h"].values; l5 = df["l"].values
    c5 = df["c"].values; v5 = df["v"].values
    t5c = t5 + 300000  # 5m bar 收盘时间
    return t5c, o5, h5, l5, c5, v5


def resample(t5c, o5, h5, l5, c5, v5, ms):
    """5m -> ms 级别 K 线(只保留完整 bar). 返回收盘时间与 ohlc."""
    t5o = t5c - 300000
    grp = (t5o // ms).astype(np.int64)
    d = pd.DataFrame({"g": grp, "t": t5c, "o": o5, "h": h5,
                      "l": l5, "c": c5, "v": v5})
    per = ms // 300000
    agg = d.groupby("g").agg(t=("t", "max"), o=("o", "first"),
                             h=("h", "max"), l=("l", "min"),
                             c=("c", "last"), n=("t", "size"))
    agg = agg[agg["n"] == per].reset_index(drop=True)
    return (agg["t"].values, agg["o"].values, agg["h"].values,
            agg["l"].values, agg["c"].values)


def boll_pctb(c, n=20, k=2.0):
    s, sd = roll_mean(c, n), roll_sd(c, n)
    out = (c - (s - k * sd)) / (2 * k * sd)
    out[sd == 0] = np.nan
    return out


def kdj_j(h, l, c, n=9, k=3, d=3):
    hh = pd.Series(h).rolling(n).max().values
    ll = pd.Series(l).rolling(n).min().values
    rng = hh - ll
    rsv = np.where(rng > 0, (c - ll) / rng * 100.0, 50.0)
    kk = pd.Series(rsv).rolling(k).mean().values
    dd = pd.Series(kk).rolling(d).mean().values
    return 3 * kk - 2 * dd


def willr(h, l, c, n=14):
    hh = pd.Series(h).rolling(n).max().values
    ll = pd.Series(l).rolling(n).min().values
    rng = hh - ll
    out = np.where(rng > 0, (hh - c) / rng * -100.0, np.nan)
    return out


def build(sym, other):
    d = np.load("%s/FEAT_%s.npz" % (DATA, sym))
    tcand = d["t"]
    Fold, y, ties = d["F"], d["y"], d["ties"]
    old_names = list(d["names"])
    symid = d["sym"]

    t5c, o5, h5, l5, c5, v5 = load_5m(sym)
    ot5c, _, _, _, oc5, _ = load_5m(other)

    # 5m 口径新指标(候选时刻 t 即 5m bar 收盘, 直接按 t 对齐取值)
    r5 = rsi_wilder(c5)
    b5 = boll_pctb(c5)
    j5 = np.searchsorted(t5c, tcand, side="right") - 1

    # 1h 口径
    h1c, h1o, h1h, h1l, h1c_ = resample(t5c, o5, h5, l5, c5, v5, 3600000)
    r1h = rsi_wilder(h1c_)
    b1h = boll_pctb(h1c_)
    j1 = np.searchsorted(h1c, tcand, side="right") - 1

    # 15m 口径 KDJ / Williams
    m15c, m15o, m15h, m15l, m15c_ = resample(t5c, o5, h5, l5, c5, v5, 900000)
    kj = kdj_j(m15h, m15l, m15c_)
    wr = willr(m15h, m15l, m15c_)
    j15 = np.searchsorted(m15c, tcand, side="right") - 1

    # 跨标的动量(对方标的 5m 网格, 取 <=t 的最后 bar)
    jo = np.searchsorted(ot5c, tcand, side="right") - 1

    n = len(tcand)
    F2 = np.full((n, len(NEW_NAMES)), np.nan)
    ok = (j5 >= 20) & (j5 < len(c5)) & (j1 >= 30) & (j15 >= 30) & (jo >= 48)
    F2[ok, 0] = r5[j5[ok]]
    F2[ok, 1] = r1h[j1[ok]]
    F2[ok, 2] = b5[j5[ok]]
    F2[ok, 3] = b1h[j1[ok]]
    F2[ok, 4] = kj[j15[ok]]
    F2[ok, 5] = wr[j15[ok]]
    F2[ok, 6] = oc5[jo[ok]] / oc5[jo[ok] - 12] - 1.0
    F2[ok, 7] = oc5[jo[ok]] / oc5[jo[ok] - 48] - 1.0

    # 时间特征(UTC; 候选 t 为毫秒收盘时间)
    hr = ((tcand // 3600000) % 24).astype(float)
    dw = ((tcand // 86400000) + 3) % 7  # 1970-01-01 周四
    F2[:, 8] = np.sin(2 * np.pi * hr / 24.0)
    F2[:, 9] = np.cos(2 * np.pi * hr / 24.0)
    F2[:, 10] = np.sin(2 * np.pi * dw / 7.0)
    F2[:, 11] = np.cos(2 * np.pi * dw / 7.0)

    fin = np.all(np.isfinite(F2), axis=1)
    Fall = np.column_stack([Fold[fin], F2[fin]])
    names = old_names + NEW_NAMES
    np.savez_compressed("%s/FEAT2_%s.npz" % (DATA, sym),
                        t=tcand[fin], sym=symid[fin], F=Fall,
                        y=y[fin], ties=ties[fin],
                        names=np.array(names))
    print(sym, "v2 candidates:", int(fin.sum()), "of", n, flush=True)


if __name__ == "__main__":
    build("BTCUSDT", "ETHUSDT")
    build("ETHUSDT", "BTCUSDT")
