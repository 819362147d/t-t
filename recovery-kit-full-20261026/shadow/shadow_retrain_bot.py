#!/usr/bin/env python3
# 10 分钟事件合约预报【影子 v4·月度重训版】：记账 + Bark 推送、不交易。
# 2026-10-26 用户要求：触发记账时经 Bark 推送（标题带“影子v4”以区分现网预报）。
#
# 定位: 「月度滚动重训 + 影子先行」计划的第一版影子线 (2026-10-23 用户批准)。
#   - 模型: 重训版 LightGBM lightgbm_10min_retrain202610.txt (最新 350 天
#     窗口 2025-11-08..2026-10-23, FEAT3R 特征, 与现网同一套参数/协议,
#     见 train_retrain.py / model_10min_meta_retrain202610.json);
#     GBM 缺失时降级 LR 备用 lr_10min_retrain202610.pkl (口径同现网)。
#   - 取数/特征管线与 event_10min_bot.py 逐行共用 (klines/compute_features),
#     候选时刻一致: 最新已收盘 5m bar 的收盘时刻。
#   - 触发口径与现网完全一致: GBM prob_up >= 0.55 看涨 / <= 0.45 看跌;
#     同标的同方向 4 小时冷却去重 (state 只记本影子自己的触发时刻,
#     不读现网状态)。
#   - 记账: append 到 shadow4-alerts.jsonl (字段 t/sym/dir/prob/close/model)。
#
# 本脚本触发记账时调用 Bark 推送（复用 event_10min_bot.send_bark，
# 标题带“影子v4”）；不写微信 outbox、不写 live-alerts.jsonl，
# 不修改现网 event_10min_bot.py / 现网模型 / 任何现网状态文件。
# 降级: 数据拉取失败/特征非有限/模型缺失 -> 该标的跳过并记 errors,
# 绝不用假数据凑数。
#
# 用法: python3 shadow_retrain_bot.py [--dry-run]

import json
import os
import pickle
import sys
import time
from datetime import datetime, timedelta, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from event_10min_bot import klines, compute_features, FEATURE_NAMES, send_bark  # noqa: E402
# FEAT3 管线与现网逐行共用 (只计算; 不调用其推送/记账函数)

try:
    import lightgbm as lgb
except ImportError:
    lgb = None

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
K1M_LIMIT, K5M_LIMIT = 500, 1500  # 与现网一致
GBM_FILE = os.path.join(HERE, "lightgbm_10min_retrain202610.txt")
LR_FILE = os.path.join(HERE, "lr_10min_retrain202610.pkl")
LEDGER = os.path.join(HERE, "shadow4-alerts.jsonl")
STATE = os.path.join(HERE, "shadow_retrain_state.json")
LONG_THR, SHORT_THR = 0.55, 0.45            # GBM: 与现网一致
LR_LONG_THR, LR_SHORT_THR = 0.5749, 0.4309  # LR 备用: 与现网一致
COOLDOWN_SEC = 4 * 3600                     # 同标的同方向 4 小时去重 (同现网)


def log(msg):
    sys.stderr.write("[shadow_retrain_bot] %s\n" % msg)
    sys.stderr.flush()


def load_models():
    """加载重训版主模型 (GBM); 缺失则 LR 备用; 都没有返回 (None, err)。"""
    if lgb is not None and os.path.exists(GBM_FILE):
        try:
            return ("gbm", lgb.Booster(model_file=GBM_FILE)), None
        except Exception as e:  # noqa: BLE001
            log("重训 GBM 加载失败: %s, 尝试 LR 备用" % e)
    if os.path.exists(LR_FILE):
        try:
            with open(LR_FILE, "rb") as fh:
                return ("lr", pickle.load(fh)), None
        except Exception as e:  # noqa: BLE001
            log("重训 LR 加载失败: %s" % e)
    return None, "重训版模型文件缺失, 跳过本轮"


def predict(model, feats):
    kind, m = model
    x = np.array([[feats[k] for k in FEATURE_NAMES]], dtype=float)
    if kind == "gbm":
        return float(m.predict(x)[0]), LONG_THR, SHORT_THR
    sc, lr = m["scaler"], m["model"]
    return float(lr.predict_proba(sc.transform(x))[0, 1]), LR_LONG_THR, LR_SHORT_THR


def record_alert(t_ms, sym, direction, prob, close, kind):
    with open(LEDGER, "a") as fh:
        fh.write(json.dumps({"t": t_ms, "sym": sym, "dir": direction,
                             "prob": prob, "close": close,
                             "model": "retrain202610_" + kind}) + "\n")


def format_v4_alert(sym, direction, prob, close, feats, ts_ms, lo_thr, sh_thr):
    """Bark 文案：与现网 format_alert 同格式，标题标明影子 v4，阈值用本线实际值。"""
    d = "看涨 🟢" if direction == "UP" else "看跌 🔴"
    # 写死东八区，不依赖系统时区（定时任务环境的 TZ 可能不是北京时间）
    bj = timezone(timedelta(hours=8))
    ts = datetime.fromtimestamp(ts_ms / 1000, tz=bj).strftime("%m-%d %H:%M")
    return ("【影子v4·10分钟预报】%s · %s\n"
            "上涨概率 %.3f（阈值 %.2f/%.2f）\n"
            "rsi1m %.1f · rsi5m %.1f · boll%%1m %.2f · 价格 %s\n"
            "未来10分钟二元口径 · %s\n"
            "⚠️ 影子线对照信号，不构成交易建议" % (
                sym, d, prob, lo_thr, sh_thr,
                feats["rsi14_1m"], feats["rsi14_5m"],
                feats["boll_pctB_1m"], close, ts))


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def main():
    dry_run = "--dry-run" in sys.argv
    now = int(time.time())
    model, err = load_models()
    result = {"ts": now, "model": "shadow_retrain",
              "model_kind": model[0] if model else None,
              "forecasts": [], "recorded": [], "errors": []}
    if model is None:
        result["errors"].append(err)
        log(err)
        print(json.dumps(result, ensure_ascii=False))
        return

    # 拉 K 线 (双标的: 各自需要对方的 5m 做跨标的动量, 与现网一致)
    data = {}
    for sym in SYMBOLS:
        try:
            data[sym] = (klines(sym, "1m", K1M_LIMIT),
                         klines(sym, "5m", K5M_LIMIT))
        except Exception as e:  # noqa: BLE001
            msg = "%s K 线拉取失败: %s" % (sym, e)
            log(msg)
            result["errors"].append(msg)
    if len(data) < 2:
        print(json.dumps(result, ensure_ascii=False))
        return

    state = load_state()
    for sym in SYMBOLS:
        other = SYMBOLS[1] if sym == SYMBOLS[0] else SYMBOLS[0]
        k1m, k5m = data[sym]
        try:
            t_ms, close, feats = compute_features(k1m, k5m, data[other][1])
            if feats is None:
                raise RuntimeError("特征计算失败（数据不足或非有限值）")
            prob, lo_thr, sh_thr = predict(model, feats)
        except Exception as e:  # noqa: BLE001
            msg = "%s 计算失败: %s" % (sym, e)
            log(msg)
            result["errors"].append(msg)
            continue

        direction = ("UP" if prob >= lo_thr else
                     ("DOWN" if prob <= sh_thr else None))
        fc = {"symbol": sym, "t": t_ms, "close": round(close, 2),
              "prob_up": round(prob, 4), "direction": direction,
              "recorded": False}
        if direction:
            key = "%s_%s" % (sym, direction)
            if now - int(state.get(key, 0)) > COOLDOWN_SEC:
                fc["recorded"] = True
                if not dry_run:
                    record_alert(t_ms, sym, direction, round(prob, 4),
                                 round(close, 2), model[0])
                    d = "看涨" if direction == "UP" else "看跌"
                    fc["bark_ok"] = send_bark(
                        "影子v4预报 · %s %s" % (sym, d),
                        format_v4_alert(sym, direction, prob, round(close, 2),
                                        feats, t_ms, lo_thr, sh_thr))
                    state[key] = now
                result["recorded"].append(
                    {"symbol": sym, "t": t_ms, "dir": direction,
                     "prob_up": round(prob, 4),
                     "bark_ok": fc.get("bark_ok")})
            else:
                fc["note"] = "4 小时同向冷却中, 不重复记账"
        result["forecasts"].append(fc)
    if not dry_run:
        json.dump(state, open(STATE, "w"))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
