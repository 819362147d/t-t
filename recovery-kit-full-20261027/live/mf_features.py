#!/usr/bin/env python3
"""单标的特征构建: 5m K线 -> 15m重采样 -> 指标 -> 5分钟候选矩阵.
用法: mf_features.py BTCUSDT ; 输出 /tmp/mf_data/FEAT_{SYM}.npz
指标实现与 event_bot.py 逐行对应(降级模式,无OI). 严禁未来函数."""
import sys
import numpy as np
import pandas as pd

ATR_P, ATR_M, KER_L = 12, 3.0, 10
CVD_Z_LEN = 20

def rma(vals, n):
    vals = np.asarray(vals, dtype=float)
    out = np.full(len(vals), np.nan)
    out[n - 1] = np.mean(vals[:n])
    for i in range(n, len(vals)):
        out[i] = (out[i - 1] * (n - 1) + vals[i]) / n
    return out

def roll_mean(v, n):
    return pd.Series(v).rolling(n).mean().values

def roll_sd(v, n):
    return pd.Series(v).rolling(n).std(ddof=0).values

def ema(v, span):
    return pd.Series(v).ewm(span=span, adjust=False).mean().values

def compute_trend(o, h, l, c):
    """复刻 event_bot.trend(): ATR Trailing Stop + KER -> trend_score, pos"""
    n = len(c)
    tr = np.empty(n)
    tr[0] = h[0] - l[0]
    tr[1:] = np.maximum(h[1:] - l[1:],
                        np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    atr = rma(tr, ATR_P)
    stop = np.zeros(n)
    pos = np.zeros(n)
    for i in range(n):
        a = atr[i]
        if np.isnan(a):
            continue
        s = ATR_M * a
        p = stop[i - 1] if i else 0.0
        ci = c[i]
        cim1 = c[i - 1] if i else 0.0
        if ci > p and cim1 > p:
            stop[i] = max(p, ci - s)
        elif ci < p and cim1 < p:
            stop[i] = min(p, ci + s)
        elif ci > p:
            stop[i] = ci - s
        else:
            stop[i] = ci + s
        if i and not np.isnan(atr[i - 1]):
            if c[i - 1] < stop[i - 1] and ci > stop[i]:
                pos[i] = 1
            elif c[i - 1] > stop[i - 1] and ci < stop[i]:
                pos[i] = -1
            else:
                pos[i] = pos[i - 1]
    ts = np.full(n, np.nan)
    for i in range(KER_L, n):
        if np.isnan(atr[i]):
            continue
        change = abs(c[i] - c[i - KER_L])
        vol = np.sum(np.abs(c[i - KER_L + 1:i + 1] - c[i - KER_L:i]))
        ker = change / vol if vol else 0.0
        d = 1.0 if c[i] > c[i - KER_L] else (-1.0 if c[i] < c[i - KER_L] else 0.0)
        dist = (c[i] - stop[i]) / atr[i] if atr[i] else 0.0
        atr_ts = pos[i] * min(abs(dist) / ATR_M, 1.0)
        ts[i] = atr_ts * 0.5 + (ker * d) * 0.5
    return ts, pos, atr

def cvd_z(o, c, v, length=CVD_Z_LEN):
    """复刻 event_bot.cvd_flow(): delta-volume近似 + z-score(length)"""
    n = len(c)
    up, dn = c > o, c < o
    dv = np.empty(n)
    dv[up] = v[up]
    dv[dn] = -v[dn]
    tie = ~(up | dn)
    pu = np.zeros(n, bool); pdn = np.zeros(n, bool)
    pu[1:] = c[1:] > c[:-1]; pdn[1:] = c[1:] < c[:-1]
    m = tie & pu;  dv[m] = v[m]
    m = tie & pdn; dv[m] = -v[m]
    m = tie & ~(pu | pdn); dv[m] = v[m]
    chg = np.empty(n); chg[0] = 0.0; chg[1:] = dv[1:] - dv[:-1]
    mu, sd = roll_mean(chg, length), roll_sd(chg, length)
    z = (chg - mu) / sd
    z[sd == 0] = 0.0
    return z

def rsi_wilder(c, n=14):
    d = np.empty(len(c)); d[0] = 0.0; d[1:] = c[1:] - c[:-1]
    ag, al = rma(np.clip(d, 0, None), n), rma(np.clip(-d, 0, None), n)
    r = 100 - 100 / (1 + ag / al)
    r[al == 0] = 100.0
    return r

def build(sym):
    df = pd.read_csv("/tmp/mf_data/%s_5m.csv" % sym)
    t5 = (df["t"].values // 1000).astype(np.int64)  # bulk文件为微秒, 转毫秒
    o5 = df["o"].values; h5 = df["h"].values
    l5 = df["l"].values; c5 = df["c"].values; v5 = df["v"].values
    n5 = len(df)
    # 5m -> 15m (只保留完整的3根5m合成的15m bar)
    grp = (t5 // 900000).astype(np.int64)
    d = pd.DataFrame({"g": grp, "t": t5, "o": o5, "h": h5, "l": l5, "c": c5, "v": v5})
    agg = d.groupby("g").agg(t=("t", "max"), o=("o", "first"), h=("h", "max"),
                             l=("l", "min"), c=("c", "last"), v=("v", "sum"),
                             n=("t", "size"))
    agg = agg[agg["n"] == 3].reset_index(drop=True)
    t15c = agg["t"].values + 300000
    o15, h15 = agg["o"].values, agg["h"].values
    l15, c15 = agg["l"].values, agg["c"].values
    v15 = agg["v"].values
    n15 = len(agg)

    ts15, pos15, _ = compute_trend(o15, h15, l15, c15)
    cvz15 = cvd_z(o15, c15, v15)
    cvz5 = cvd_z(o5, c5, v5)
    rsi = rsi_wilder(c15)
    macd = ema(c15, 12) - ema(c15, 26)
    mhist = macd - ema(macd, 9)
    s20, sd20 = roll_mean(c15, 20), roll_sd(c15, 20)
    pctB = (c15 - (s20 - 2 * sd20)) / (4 * sd20)
    tr15 = np.empty(n15); tr15[0] = h15[0] - l15[0]
    tr15[1:] = np.maximum(h15[1:] - l15[1:],
                          np.maximum(np.abs(h15[1:] - c15[:-1]), np.abs(l15[1:] - c15[:-1])))
    atr14 = rma(tr15, 14)
    atr_norm = atr14 / c15
    vmu, vsd = roll_mean(v15, 20), roll_sd(v15, 20)
    volz = (v15 - vmu) / vsd
    volz[vsd == 0] = 0.0
    mom1h = np.full(n15, np.nan); mom1h[4:] = c15[4:] / c15[:-4] - 1
    mom4h = np.full(n15, np.nan); mom4h[16:] = c15[16:] / c15[:-16] - 1

    t5c = t5 + 300000
    j = np.searchsorted(t15c, t5c, side="right") - 1  # <=t 的最后已收盘15m bar
    idx = np.arange(n5)
    valid = (j >= 60) & (j < n15) & (idx <= n5 - 3)
    jj, ii = j[valid], idx[valid]
    fl = ((pos15[jj] == 1) & (pos15[jj - 1] == -1)).astype(float)
    fs = ((pos15[jj] == -1) & (pos15[jj - 1] == 1)).astype(float)
    F = np.column_stack([
        ts15[jj], np.abs(ts15[jj]), cvz5[ii], cvz15[jj], rsi[jj], mhist[jj],
        pctB[jj], atr_norm[jj], volz[jj], mom1h[jj], mom4h[jj], fl, fs,
    ])
    out = (c5[ii + 2] > c5[ii]).astype(np.int8)
    ties = (c5[ii + 2] == c5[ii])
    fin = np.all(np.isfinite(F), axis=1)
    names = ["trend_score", "abs_trend_score", "cvd_z_5m", "cvd_z_15m", "rsi14",
             "macd_hist", "boll_pctB", "atr_norm", "vol_z", "mom_1h", "mom_4h",
             "flip_long", "flip_short"]
    np.savez_compressed("/tmp/mf_data/FEAT_%s.npz" % sym,
                        t=t5c[ii][fin],
                        sym=np.full(fin.sum(), 0 if sym == "BTCUSDT" else 1, np.int8),
                        F=F[fin], y=out[fin], ties=ties[fin],
                        names=np.array(names))
    print(sym, "candidates:", int(fin.sum()), flush=True)
    # 释放
    del df, d, agg, F, out

if __name__ == "__main__":
    build(sys.argv[1])
