#!/usr/bin/env python3
"""重训专用: 由 mf_data_1m 的 1m CSV 聚合出最新 350 天窗口的 5m CSV。
窗口: 2025-11-08 00:00 UTC 起 (与原 FEAT3 的 2025-11-01..2026-10-16 同为
350 天, 整体后移 7 天) 到 1m 数据末尾。只保留 5 根 1m 齐全的 5m bar。
输出: retrain_work/mf_data/{SYM}_5m.csv, t 为微秒 (与原 mf_data 备份一致,
mf_features.py 读取时 //1000 转毫秒)。不覆盖任何原文件。"""
import glob, os, datetime
import numpy as np
import pandas as pd

HF = "/home/hatch/workspace/goals/tradingview-binance-indicator-monitoring-with-wecom-alerts/hidden_files"
D1M = HF + "/mf_data_1m"
OUT = HF + "/retrain_work/mf_data"
os.makedirs(OUT, exist_ok=True)

WIN_START = int(datetime.datetime(2025, 11, 8, tzinfo=datetime.timezone.utc).timestamp() * 1000)


def fmt(ms):
    return datetime.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")


def build(sym):
    files = sorted(glob.glob("%s/%s_1m_*.csv" % (D1M, sym)))
    dfs = [pd.read_csv(f) for f in files]
    df = pd.concat(dfs, ignore_index=True).drop_duplicates("t").sort_values("t")
    df = df[df["t"] >= WIN_START].reset_index(drop=True)
    df["g"] = (df["t"] // 300000).astype(np.int64)
    agg = df.groupby("g").agg(o=("o", "first"), h=("h", "max"), l=("l", "min"),
                              c=("c", "last"), v=("v", "sum"), n=("t", "size"))
    agg = agg[agg["n"] == 5].reset_index()
    t_open_ms = agg["g"].values * 300000
    out = pd.DataFrame({"t": t_open_ms * 1000, "o": agg["o"].values,
                        "h": agg["h"].values, "l": agg["l"].values,
                        "c": agg["c"].values, "v": agg["v"].values})
    path = "%s/%s_5m.csv" % (OUT, sym)
    out.to_csv(path, index=False)
    print("%s: 5m bars=%d range=[%s .. %s] -> %s" %
          (sym, len(out), fmt(t_open_ms[0]), fmt(t_open_ms[-1]), path), flush=True)


if __name__ == "__main__":
    build("BTCUSDT")
    build("ETHUSDT")
    print("DONE", flush=True)
