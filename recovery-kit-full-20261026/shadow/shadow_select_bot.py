#!/usr/bin/env python3
# 10 分钟事件合约预报【影子 v3·精选版】：只记账、不推送、不交易。
#
# 定位: 影子 v2（规则门控, shadow_rulegate_bot.py）的子集过滤版——
# 在 v2 的「现网基线模型 + 规则门控」之上再收紧到实验一/二发现的最优
# 子集「做多 & 亚盘」，只记账到 shadow3-alerts.jsonl。
#
# 触发条件（三条全满足才记账）:
#   1. 只做多: 模型 prob_up >= 0.55（看跌一律不记, dir 恒为 UP）;
#   2. 规则同向: 均值回归规则 mr >= 1（mr 定义与 v2/实验三一致:
#      mr = -(z(rsi14_5m) + z(boll_pctB_1m)) / 2, z 统计沿用
#      shadow_rulegate_meta.json 的全量 350 天固定 mean/std）;
#   3. 时段过滤: 候选时刻（bar 时间, UTC）落在 00:00-08:00 UTC（亚盘）。
#
# 回测依据（均为事后子集、线索级, 不是定案）:
#   - 实验一 opt-exp1-20261020.md: 「做多 & 亚盘（UTC 00-08）」样本外
#     胜率 0.630（n=405）;
#   - 实验二 opt-exp2-20261020.md: 同子集 0.650（n=477）。
#
# 候选时刻与取数/特征管线与现网及 v2 一致（最新已收盘 5m bar）。
# 无方向冷却去重; 仅用 state 文件记住已处理的候选时刻防重复记账。
# 降级: 数据拉取失败/特征非有限/模型缺失 -> 该标的跳过并记 errors,
# 绝不用假数据凑数, 失败的候选不推进 state。
#
# 本脚本不调用 Bark、不写微信 outbox、不写 live-alerts.jsonl /
# shadow-alerts.jsonl / shadow2-alerts.jsonl, 不修改现网
# event_10min_bot.py / shadow_allfeat_bot.py / shadow_rulegate_bot.py
# 及任何模型。
#
# 用法: python3 shadow_select_bot.py [--dry-run]

import datetime as dt
import json
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from event_10min_bot import klines, compute_features, FEATURE_NAMES  # noqa: E402
# FEAT3 管线与现网逐行共用 (只计算; 不调用其推送/记账函数)

try:
    import lightgbm as lgb
except ImportError:
    lgb = None

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
K1M_LIMIT, K5M_LIMIT = 500, 1500  # 与现网一致
MODEL_FILE = os.path.join(HERE, "lightgbm_10min.txt")
META_FILE = os.path.join(HERE, "shadow_rulegate_meta.json")
LEDGER = os.path.join(HERE, "shadow3-alerts.jsonl")
STATE = os.path.join(HERE, "shadow_select_state.json")
LONG_THR = 0.55  # 只做多档位
ASIA_START_H, ASIA_END_H = 0, 8  # 亚盘 UTC 00:00-08:00


def log(msg):
    sys.stderr.write("[shadow_select_bot] %s\n" % msg)
    sys.stderr.flush()


def load_z_stats():
    """从 meta json 读 z 统计固定值 (全量 350 天整体 mean/std)。"""
    meta = json.load(open(META_FILE))
    zs = meta["z_stats"]
    return (float(zs["rsi14_5m"]["mean"]), float(zs["rsi14_5m"]["std"]),
            float(zs["boll_pctB_1m"]["mean"]), float(zs["boll_pctB_1m"]["std"]))


def mean_reversion(feats, zstats):
    """实验三均值回归规则: 返回 (mr, rule_dir)。
    mr = -(z(rsi14_5m) + z(boll_pctB_1m))/2; >=1 投多, <=-1 投空, 其余弃权。"""
    mu_r, sd_r, mu_b, sd_b = zstats
    z_r = (feats["rsi14_5m"] - mu_r) / sd_r
    z_b = (feats["boll_pctB_1m"] - mu_b) / sd_b
    mr = -(z_r + z_b) / 2.0
    rule_dir = "UP" if mr >= 1.0 else ("DOWN" if mr <= -1.0 else None)
    return float(mr), rule_dir


def in_asia_session(t_ms):
    """候选时刻 (bar 时间) 的 UTC 小时是否落在 00:00-08:00 (亚盘)。"""
    hour = dt.datetime.fromtimestamp(t_ms / 1000.0, tz=dt.timezone.utc).hour
    return ASIA_START_H <= hour < ASIA_END_H, hour


def record_alert(t_ms, sym, direction, prob, mr, close):
    with open(LEDGER, "a") as fh:
        fh.write(json.dumps({"t": t_ms, "sym": sym, "dir": direction,
                             "prob": prob, "mr": mr, "close": close,
                             "model": "shadow_select"}) + "\n")


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def main():
    dry_run = "--dry-run" in sys.argv
    result = {"ts": int(time.time()), "model": "shadow_select",
              "forecasts": [], "recorded": [], "errors": []}
    if lgb is None or not os.path.exists(MODEL_FILE):
        result["errors"].append("现网基线模型缺失或 lightgbm 不可用，跳过本轮")
        print(json.dumps(result, ensure_ascii=False))
        return
    try:
        zstats = load_z_stats()
    except Exception as e:  # noqa: BLE001
        result["errors"].append("z 统计 meta 读取失败: %s" % e)
        print(json.dumps(result, ensure_ascii=False))
        return
    bst = lgb.Booster(model_file=MODEL_FILE)

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
            x = np.array([[feats[k] for k in FEATURE_NAMES]], dtype=float)
            if not np.all(np.isfinite(x)):
                raise RuntimeError("特征含非有限值")
            prob = float(bst.predict(x)[0])
            mr, rule_dir = mean_reversion(feats, zstats)
            if not math.isfinite(mr):
                raise RuntimeError("mr 非有限值")
        except Exception as e:  # noqa: BLE001
            msg = "%s 计算失败: %s" % (sym, e)
            log(msg)
            result["errors"].append(msg)
            continue  # 不推进 state

        model_dir = "UP" if prob >= LONG_THR else None
        in_session, utc_hour = in_asia_session(t_ms)
        triggered = bool(model_dir == "UP" and rule_dir == "UP" and in_session)
        fc = {"symbol": sym, "t": t_ms, "close": round(close, 2),
              "prob_up": round(prob, 4), "mr": round(mr, 4),
              "model_dir": model_dir, "rule_dir": rule_dir,
              "utc_hour": utc_hour, "in_asia_session": in_session,
              "triggered": triggered}
        result["forecasts"].append(fc)

        already = int(state.get(sym, 0)) >= t_ms
        if triggered and not already and not dry_run:
            record_alert(t_ms, sym, model_dir, round(prob, 4),
                         round(mr, 4), round(close, 2))
            result["recorded"].append(
                {"symbol": sym, "t": t_ms, "dir": model_dir,
                 "prob_up": round(prob, 4), "mr": round(mr, 4)})
        if triggered and already:
            fc["note"] = "该候选已处理过，防重复记账跳过"
        if not dry_run:
            state[sym] = max(int(state.get(sym, 0)), t_ms)
    if not dry_run:
        json.dump(state, open(STATE, "w"))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
