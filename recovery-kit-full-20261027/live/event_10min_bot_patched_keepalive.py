#!/usr/bin/env python3
# DRAFT ONLY — 未上线，等用户点头才替换 event_10min_bot.py。
# 改动唯一：klines() 取数从 urllib 单次连接改走长连接隧道（与多币种 bot 同款传输层，
# 2026-10-26 多币种首跑/18:20/18:40 轮已验证）；模型、阈值、去重、Bark、账本、状态文件全不动。
# 10 分钟事件合约监控预报机器人（只预报、不交易、不下单）
#
# 模型: LightGBM_reg（31 特征，输出未来 10 分钟上涨概率），在 350 天全量数据上训练，
#       存 hidden_files/lightgbm_10min.txt；LR 备用模型 hidden_files/lr_10min.pkl。
# 特征管线与回测（mf_features.py / mf_features_v2.py / feat_v3.py）逐行对应，
# 全程只用已收盘 K 线，无未来函数。
# 推送：主通道 Bark（custom.bark-push，直推用户 iPhone，无配额限制）；
# 微信 outbox（queue_wechat）保留为备用，送达失败由主流程在聊天补推。
# 降级：K 线拉取失败 / 模型缺失 / 特征非有限值 → 不预报、只记日志，绝不输出假信号。
#
# 用法: python3 event_10min_bot.py [--dry-run]
#   --dry-run: 只打印预报 JSON，不写微信 outbox、不更新去重状态。

import base64
import http.client
import json
import math
import os
import pickle
import subprocess
import sys
import threading
import time
import urllib.request
from urllib.parse import quote, urlparse
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from mf_features import (  # noqa: E402
    rma, roll_mean, roll_sd, ema, compute_trend, cvd_z, rsi_wilder,
)
from mf_features_v2 import boll_pctb, kdj_j, willr  # noqa: E402
from event_bot import queue_wechat  # noqa: E402

try:
    import lightgbm as lgb
except ImportError:
    lgb = None

BASE = "https://data-api.binance.vision"
SYMBOLS = ["BTCUSDT", "ETHUSDT"]
STATE = os.path.join(HERE, "event_10min_bot_state.json")

LONG_THR, SHORT_THR = 0.55, 0.45      # GBM_reg：v3 验证段选定
LR_LONG_THR, LR_SHORT_THR = 0.5749, 0.4309  # LR 备用：v2 验证段选定
COOLDOWN_SEC = 4 * 3600  # 同标的同方向 4 小时去重（用户 2026-10-18 21:42 要求恢复）
K1M_LIMIT, K5M_LIMIT = 500, 1500  # 5m 取 1500 根：保证 1h 指标 Wilder 热身充分收敛

FEATURE_NAMES = [
    "trend_score", "abs_trend_score", "cvd_z_5m", "cvd_z_15m", "rsi14",
    "macd_hist", "boll_pctB", "atr_norm", "vol_z", "mom_1h", "mom_4h",
    "flip_long", "flip_short", "rsi14_5m", "rsi14_1h", "boll_pctB_5m",
    "boll_pctB_1h", "kdj_j_15m", "willr14_15m", "x_mom_1h", "x_mom_4h",
    "hour_sin", "hour_cos", "dow_sin", "dow_cos", "rsi14_1m", "rsi14_3m",
    "boll_pctB_1m", "cvd_z_1m", "mom_10m", "mom_30m",
]


def log(msg):
    sys.stderr.write("[event_10min_bot] %s\n" % msg)
    sys.stderr.flush()


BARK_SEND = os.path.expanduser(
    "~/workspace/custom-connectors/custom.bark-push/bin/bark_send.py")

LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "live-alerts.jsonl")


def record_alert(t_ms, sym, direction, prob, close):
    """实盘预报记账（JSONL），供胜率追踪用；失败只记日志。"""
    try:
        with open(LEDGER, "a") as f:
            f.write(json.dumps({"t": t_ms, "sym": sym, "dir": direction,
                                "prob": prob, "close": close}) + "\n")
    except Exception as e:  # noqa: BLE001
        log("记账失败: %s" % e)


OUTBOX_STALE_SEC = 2 * 3600  # 备用通道消息超过 2 小时未发出即视为过期


def sweep_stale_outbox():
    """把微信 outbox 里超过 2 小时还没发出去的备份消息移到 quarantined/。

    背景：iLink 配额耗尽（ret=-2）时 Go 端 flusher 每 10 秒无限重试且只记日志，
    旧预报会永远堆积并刷屏 serve.log。10 分钟预报过期即无价值，直接隔离，
    不删除（quarantined/ 保留原文件，可追溯）。
    失败只记日志，绝不影响主流程。
    """
    try:
        pend = os.path.expanduser("~/workspace/wechat-bridge/outbox/pending")
        quar = os.path.expanduser("~/workspace/wechat-bridge/outbox/quarantined")
        if not os.path.isdir(pend):
            return 0
        os.makedirs(quar, exist_ok=True)
        now = time.time()
        moved = 0
        for name in os.listdir(pend):
            if not name.endswith(".json"):
                continue
            path = os.path.join(pend, name)
            try:
                if now - os.path.getmtime(path) < OUTBOX_STALE_SEC:
                    continue
                os.rename(path, os.path.join(quar, name))
                moved += 1
            except OSError:
                continue
        if moved:
            log("outbox 过期清理: %d 条 >2h 未发出，已移入 quarantined/" % moved)
        return moved
    except Exception as e:  # noqa: BLE001
        log("outbox 过期清理失败: %s" % e)
        return 0


def send_bark(title, text):
    """经 Bark 推送到用户 iPhone；返回 True/False，失败只记日志不抛异常。"""
    try:
        p = subprocess.run([sys.executable, BARK_SEND, title, text],
                           capture_output=True, text=True, timeout=30)
        if p.returncode != 0:
            log("Bark 推送失败: %s" % (p.stderr or "").strip()[:200])
            return False
        resp = json.loads(p.stdout or "{}")
        if resp.get("code") != 200:
            log("Bark 推送返回异常: %s" % (p.stdout or "").strip()[:200])
            return False
        return True
    except Exception as e:  # noqa: BLE001
        log("Bark 推送异常: %s" % e)
        return False


_VISION_HOST = "data-api.binance.vision"
_TLS = threading.local()


def _new_vision_conn():
    proxy = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY") or ""
    p = urlparse(proxy)
    if p.hostname:
        headers = {}
        if p.username:
            creds = base64.b64encode(
                ("%s:%s" % (p.username, p.password or "")).encode()).decode()
            headers["Proxy-Authorization"] = "Basic %s" % creds
        conn = http.client.HTTPSConnection(p.hostname, p.port or 3128,
                                           timeout=20)
        conn.set_tunnel(_VISION_HOST, 443, headers=headers or None)
        return conn
    return http.client.HTTPSConnection(_VISION_HOST, timeout=20)


def _vision_conn():
    c = getattr(_TLS, "conn", None)
    if c is None:
        c = _TLS.conn = _new_vision_conn()
    return c


def _get_klines_page(symbol, interval, limit, end=None):
    path = ("/api/v3/klines?symbol=%s&interval=%s&limit=%d"
            % (quote(symbol, safe=""), interval, limit))
    if end is not None:
        path += "&endTime=%d" % end
    last = None
    for _ in range(2):  # 连接断了就丢弃重建，再试一次
        try:
            conn = _vision_conn()
            conn.request("GET", path, headers={"User-Agent": "Mozilla/5.0"})
            resp = conn.getresponse()
            raw = resp.read()
            if resp.status != 200:
                raise RuntimeError("HTTP %d" % resp.status)
            return json.loads(raw)
        except Exception as e:  # noqa: BLE001
            last = e
            _TLS.conn = None
    raise last


def klines(symbol, interval, limit):
    """只返回已收盘 K 线（去尾），与原 klines() 一致；失败抛异常。
    limit<=1000 单次请求；更大时自动分页（endTime 回溯）。
    传输层改走线程内复用的隧道长连接（与多币种 bot 的 fetch_klines 同款）。"""
    out = []
    end = None
    remaining = limit
    while remaining > 0:
        n = min(remaining, 1000)
        raw = _get_klines_page(symbol, interval, n, end)
        bars = [{"t": k[0], "o": float(k[1]), "h": float(k[2]),
                 "l": float(k[3]), "c": float(k[4]), "v": float(k[5])}
                for k in raw]
        if not bars:
            break
        out = bars + out
        remaining -= len(bars)
        end = bars[0]["t"] - 1
        if len(bars) < n:
            break
    # 去重 + 按时间排序
    seen = {}
    for b in out:
        seen[b["t"]] = b
    out = [seen[t] for t in sorted(seen)]
    if len(out) < 2:
        raise RuntimeError("klines too short")
    return out[:-1]  # 去掉未收盘的最后一根


def resample_5m(t5c, o5, h5, l5, c5, v5, ms):
    """复刻 mf_features_v2.resample：5m -> ms 级别（只保留完整 bar）。"""
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


def synth_15m(t5, o5, h5, l5, c5, v5):
    """复刻 mf_features.build 的 5m -> 15m 合成（只保留完整的 3 根组）。"""
    grp = (t5 // 900000).astype(np.int64)
    d = pd.DataFrame({"g": grp, "t": t5, "o": o5, "h": h5,
                      "l": l5, "c": c5, "v": v5})
    agg = d.groupby("g").agg(t=("t", "max"), o=("o", "first"), h=("h", "max"),
                             l=("l", "min"), c=("c", "last"), v=("v", "sum"),
                             n=("t", "size"))
    agg = agg[agg["n"] == 3].reset_index(drop=True)
    t15c = agg["t"].values + 300000
    return (t15c, agg["o"].values, agg["h"].values, agg["l"].values,
            agg["c"].values, agg["v"].values)


def compute_features(k1m, k5m, k5m_other):
    """对单个标的计算 31 维特征向量（取最后已收盘 5m bar 为候选时刻 t）。
    返回 (t_ms, close, dict特征名->值)；数据不足返回 (None, None, None)。"""
    t5 = np.array([k["t"] for k in k5m], dtype=np.int64)
    o5 = np.array([k["o"] for k in k5m]); h5 = np.array([k["h"] for k in k5m])
    l5 = np.array([k["l"] for k in k5m]); c5 = np.array([k["c"] for k in k5m])
    v5 = np.array([k["v"] for k in k5m])
    t5c = t5 + 300000
    t = int(t5c[-1])

    # --- 15m 合成 + 趋势/动量类 ---
    t15c, o15, h15, l15, c15, v15 = synth_15m(t5, o5, h5, l5, c5, v5)
    n15 = len(c15)
    j = int(np.searchsorted(t15c, t, side="right") - 1)
    if j < 60 or j >= n15:
        return None, None, None
    ts15, pos15, _ = compute_trend(o15, h15, l15, c15)
    cvz15 = cvd_z(o15, c15, v15)
    rsi = rsi_wilder(c15)
    macd = ema(c15, 12) - ema(c15, 26)
    mhist = macd - ema(macd, 9)
    s20, sd20 = roll_mean(c15, 20), roll_sd(c15, 20)
    pctB = (c15 - (s20 - 2 * sd20)) / (4 * sd20)
    tr15 = np.empty(n15); tr15[0] = h15[0] - l15[0]
    tr15[1:] = np.maximum(h15[1:] - l15[1:],
                          np.maximum(np.abs(h15[1:] - c15[:-1]),
                                     np.abs(l15[1:] - c15[:-1])))
    atr14 = rma(tr15, 14)
    atr_norm = atr14 / c15
    vmu, vsd = roll_mean(v15, 20), roll_sd(v15, 20)
    volz = (v15 - vmu) / vsd
    mom1h = c15[j] / c15[j - 4] - 1.0
    mom4h = c15[j] / c15[j - 16] - 1.0
    fl = 1.0 if (pos15[j] == 1 and pos15[j - 1] == -1) else 0.0
    fs = 1.0 if (pos15[j] == -1 and pos15[j - 1] == 1) else 0.0

    # --- 5m 直接 ---
    cvz5 = cvd_z(o5, c5, v5)
    r5 = rsi_wilder(c5)
    b5 = boll_pctb(c5)

    # --- 1h / 15m(kdj) 重采样 ---
    h1c, _, _, _, h1c_ = resample_5m(t5c, o5, h5, l5, c5, v5, 3600000)
    r1h = rsi_wilder(h1c_); b1h = boll_pctb(h1c_)
    j1 = int(np.searchsorted(h1c, t, side="right") - 1)
    m15c, _, m15h, m15l, m15c_ = resample_5m(t5c, o5, h5, l5, c5, v5, 900000)
    kj = kdj_j(m15h, m15l, m15c_)
    wr = willr(m15h, m15l, m15c_)
    j15k = int(np.searchsorted(m15c, t, side="right") - 1)

    # --- 跨标的动量 ---
    ot5 = np.array([k["t"] for k in k5m_other], dtype=np.int64)
    oc5 = np.array([k["c"] for k in k5m_other])
    ot5c = ot5 + 300000
    jo = int(np.searchsorted(ot5c, t, side="right") - 1)

    # --- 时间特征（UTC） ---
    hr = float((t // 3600000) % 24)
    dw = float(((t // 86400000) + 3) % 7)

    # --- 1m / 3m ---
    t1 = np.array([k["t"] for k in k1m], dtype=np.int64)
    o1 = np.array([k["o"] for k in k1m]); c1 = np.array([k["c"] for k in k1m])
    v1 = np.array([k["v"] for k in k1m])
    t1c = t1 + 60000
    r1m = rsi_wilder(c1); b1m = boll_pctb(c1); z1m = cvd_z(o1, c1, v1)
    grp3 = ((t1c - 60000) // 180000).astype(np.int64)
    dd = pd.DataFrame({"g": grp3, "t": t1c, "c": c1})
    agg3 = dd.groupby("g").agg(t=("t", "max"), c=("c", "last"), nn=("t", "size"))
    agg3 = agg3[agg3["nn"] == 3].reset_index(drop=True)
    t3c, c3 = agg3["t"].values, agg3["c"].values.astype(float)
    r3m = rsi_wilder(c3)
    j1m = int(np.searchsorted(t1c, t, side="right") - 1)
    j3 = int(np.searchsorted(t3c, t, side="right") - 1)

    # --- 有效性门槛（与回测一致） ---
    if not (j1m >= 40 and j3 >= 20 and j1 >= 30 and j15k >= 30 and jo >= 48):
        return None, None, None

    f = {
        "trend_score": ts15[j], "abs_trend_score": abs(ts15[j]),
        "cvd_z_5m": cvz5[-1], "cvd_z_15m": cvz15[j],
        "rsi14": rsi[j], "macd_hist": mhist[j], "boll_pctB": pctB[j],
        "atr_norm": atr_norm[j], "vol_z": volz[j],
        "mom_1h": mom1h, "mom_4h": mom4h,
        "flip_long": fl, "flip_short": fs,
        "rsi14_5m": r5[-1], "rsi14_1h": r1h[j1],
        "boll_pctB_5m": b5[-1], "boll_pctB_1h": b1h[j1],
        "kdj_j_15m": kj[j15k], "willr14_15m": wr[j15k],
        "x_mom_1h": oc5[jo] / oc5[jo - 12] - 1.0,
        "x_mom_4h": oc5[jo] / oc5[jo - 48] - 1.0,
        "hour_sin": math.sin(2 * math.pi * hr / 24.0),
        "hour_cos": math.cos(2 * math.pi * hr / 24.0),
        "dow_sin": math.sin(2 * math.pi * dw / 7.0),
        "dow_cos": math.cos(2 * math.pi * dw / 7.0),
        "rsi14_1m": r1m[j1m], "rsi14_3m": r3m[j3],
        "boll_pctB_1m": b1m[j1m], "cvd_z_1m": z1m[j1m],
        "mom_10m": c1[j1m] / c1[j1m - 10] - 1.0,
        "mom_30m": c1[j1m] / c1[j1m - 30] - 1.0,
    }
    vals = [f[k] for k in FEATURE_NAMES]
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in vals):
        return None, None, None
    return t, float(c5[-1]), f


def load_models():
    """加载主模型（GBM_reg）；缺失则尝试 LR 备用；都没有则返回 (None, err)。"""
    gbm_path = os.path.join(HERE, "lightgbm_10min.txt")
    lr_path = os.path.join(HERE, "lr_10min.pkl")
    if lgb is not None and os.path.exists(gbm_path):
        try:
            return ("gbm", lgb.Booster(model_file=gbm_path)), None
        except Exception as e:  # noqa: BLE001
            log("GBM 模型加载失败: %s，尝试 LR 备用" % e)
    if os.path.exists(lr_path):
        try:
            with open(lr_path, "rb") as fh:
                return ("lr", pickle.load(fh)), None
        except Exception as e:  # noqa: BLE001
            log("LR 模型加载失败: %s" % e)
    return None, "模型文件缺失，跳过本轮预报"


def predict(model, feats):
    kind, m = model
    x = np.array([[feats[k] for k in FEATURE_NAMES]], dtype=float)
    if kind == "gbm":
        return float(m.predict(x)[0]), LONG_THR, SHORT_THR
    sc, lr = m["scaler"], m["model"]
    return float(lr.predict_proba(sc.transform(x))[0, 1]), LR_LONG_THR, LR_SHORT_THR


def format_alert(sym, direction, prob, close, feats, ts_ms):
    d = "看涨 🟢" if direction == "UP" else "看跌 🔴"
    # 写死东八区，不依赖系统时区（定时任务环境的 TZ 可能不是北京时间）
    bj = timezone(timedelta(hours=8))
    ts = datetime.fromtimestamp(ts_ms / 1000, tz=bj).strftime("%m-%d %H:%M")
    return ("【10分钟事件合约预报】%s · %s\n"
            "上涨概率 %.3f（阈值 %.2f/%.2f）\n"
            "rsi1m %.1f · rsi5m %.1f · boll%%1m %.2f · 价格 %s\n"
            "未来10分钟二元口径 · %s\n"
            "⚠️ 仅研究提醒，不构成交易建议" % (
                sym, d, prob, LONG_THR, SHORT_THR,
                feats["rsi14_1m"], feats["rsi14_5m"],
                feats["boll_pctB_1m"], close, ts))


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def save_state(s):
    json.dump(s, open(STATE, "w"))


def main():
    dry_run = "--dry-run" in sys.argv
    now = int(time.time())
    # 每轮先清理微信备用通道里过期的堆积消息（>2h 未发出），防止配额耗尽时
    # 无限重试刷屏；Bark 主通道不受影响。
    if not dry_run:
        sweep_stale_outbox()
    model, err = load_models()
    result = {"ts": now, "model": model[0] if model else None,
              "forecasts": [], "alerts": [], "errors": []}
    if model is None:
        result["errors"].append(err)
        log(err)
        print(json.dumps(result, ensure_ascii=False))
        return

    # 拉 K 线（双标的：各自需要对方的 5m 做跨标的动量）
    data = {}
    for sym in SYMBOLS:
        try:
            k1m = klines(sym, "1m", K1M_LIMIT)
            k5m = klines(sym, "5m", K5M_LIMIT)
            data[sym] = (k1m, k5m)
        except Exception as e:  # noqa: BLE001
            msg = "%s K 线拉取失败: %s" % (sym, e)
            log(msg)
            result["errors"].append(msg)

    state = load_state()
    for sym in SYMBOLS:
        if sym not in data:
            continue
        other = SYMBOLS[1] if sym == SYMBOLS[0] else SYMBOLS[0]
        if other not in data:
            result["errors"].append("%s 缺少 %s 跨标的动量数据" % (sym, other))
            continue
        k1m, k5m = data[sym]
        t_ms, close, feats = compute_features(k1m, k5m, data[other][1])
        if feats is None:
            msg = "%s 特征计算失败（数据不足或非有限值），跳过" % sym
            log(msg)
            result["errors"].append(msg)
            continue
        prob, lo_thr, sh_thr = predict(model, feats)
        direction = "UP" if prob >= lo_thr else ("DOWN" if prob <= sh_thr else None)
        fc = {"symbol": sym, "close": round(close, 2),
              "prob_up": round(prob, 4), "direction": direction,
              "rsi14_1m": round(feats["rsi14_1m"], 2),
              "rsi14_5m": round(feats["rsi14_5m"], 2),
              "boll_pctB_1m": round(feats["boll_pctB_1m"], 3)}
        result["forecasts"].append(fc)
        if direction:
            key = "%s_%s" % (sym, direction)
            fc["alerted"] = False
            if now - state.get(key, 0) > COOLDOWN_SEC:
                text = format_alert(sym, direction, prob, round(close, 2), feats, t_ms)
                fc["alerted"] = True
                if dry_run:
                    fc["alert_text"] = text
                else:
                    fc["wechat_file"] = None  # 微信通道已停用（用户 2026-10-22 拍板），只走 Bark
                    d = "看涨" if direction == "UP" else "看跌"
                    fc["bark_ok"] = send_bark(
                        "10分钟预报 · %s %s" % (sym, d), text)
                    record_alert(t_ms, sym, direction, round(prob, 4),
                                 round(close, 2))
                    state[key] = now
                result["alerts"].append({"symbol": sym, "direction": direction,
                                        "prob_up": round(prob, 4)})
    if not dry_run:
        save_state(state)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
