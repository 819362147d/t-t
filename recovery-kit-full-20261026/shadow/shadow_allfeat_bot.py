#!/usr/bin/env python3
# 10 分钟事件合约预报【影子版·全组合】：只记账、不推送、不交易。
#
# 模型: lightgbm_shadow_allfeat.txt（FEAT3 31 特征 + 实验二 13 个新特征，
#       350 天全量重训，协议与现网一致；训练脚本 train_shadow_allfeat.py）。
# 与现网 event_10min_bot.py 完全独立：不读现网状态、不写 live-alerts.jsonl、
# 不调用 Bark/微信。本脚本的输出只有 shadow-alerts.jsonl + stdout JSON。
#
# 数据源（2026-10-20 本 VM 实测）:
#   现货全字段 K 线: data-api.binance.vision /api/v3/klines（与现网同源）。
#   合约 K 线/标记价/资金费率/OI: fapi.binance.com 被地域封锁（HTTP 451），
#   改走 www.binance.com 网关的同一组 fapi 端点（实测 HTTP 200、数据真实）。
#
# OI 对齐（关键，实测结论）: Binance Vision metrics 存档 create_time X 的值
#   == REST openInterestHist 标签 X+300000 的值（2026-10-19 全天 288/288 点
#   精确一致；旧窗口残差集中于存档 2026-10-15 损坏段）。实验二的 OI 索引
#   （create_time<=t 的最近值、-12/-288 格偏移）因此等价于实盘数列
#   A(X) := REST(X+300000)。A(t) 在 t+300000 时刻才可得，且该格点有约
#   1-2 分钟发布延迟，故本脚本每轮处理的候选时刻 = 最新已收盘 5m bar 再往前
#   一根的收盘时刻（滞后两 tick 记账），与回测逐点同语义。结算口径与 live_winrate_watch.py 一致（入场=候选
#   时刻现货收盘价，出场=t+10min 的 1m 收盘价）。
#
# 记账: 每个候选时刻、每个标的、每档（0.55/0.58/0.60 对称双向）触发即
#   append 一行到 shadow-alerts.jsonl（无去重冷却，与现网 4h 冷却口径不同，
#   对比时注意）。字段与 live-alerts.jsonl 对齐（t/sym/dir/prob/close），
#   另加 band 与 model 字段供分档结算。
# 降级: 任一数据源失败/特征非有限/OI 格点缺失 → 该候选跳过并记日志，
#   绝不用假数据凑数。状态文件 shadow_allfeat_state.json 记录已处理候选，
#   失败的候选会在下一轮自动重试（回溯上限 12 个候选 = 1 小时）。
#
# 用法: python3 shadow_allfeat_bot.py [--dry-run]

import json
import math
import os
import sys
import time
import urllib.request

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from event_10min_bot import compute_features, FEATURE_NAMES  # noqa: E402
# FEAT3 管线与现网逐行共用（只计算、不推送；不调用其推送/记账函数）

try:
    import lightgbm as lgb
except ImportError:
    lgb = None

SPOT = "https://data-api.binance.vision"
FAPI = "https://www.binance.com"  # fapi.binance.com 被 451 封锁，www 网关实测可通
SYMBOLS = ["BTCUSDT", "ETHUSDT"]
BANDS = [0.55, 0.58, 0.60]
MODEL_FILE = os.path.join(HERE, "lightgbm_shadow_allfeat.txt")
LEDGER = os.path.join(HERE, "shadow-alerts.jsonl")
STATE = os.path.join(HERE, "shadow_allfeat_state.json")
MAX_BACKLOG = 12  # 每轮最多回溯处理的候选数（1 小时）
NEW_NAMES = ["tbr_5m", "tbr_1m", "tbr5_dev288",
             "basis_fut", "basis_mark", "basis_fut_dev288",
             "fund_last", "fund_ma3",
             "oi_chg_1h", "oi_chg_24h",
             "qvol_z288", "body_ratio_5m", "range_5m"]
ALL_NAMES = FEATURE_NAMES + NEW_NAMES


def log(msg):
    sys.stderr.write("[shadow_allfeat_bot] %s\n" % msg)
    sys.stderr.flush()


def fetch_json(url, timeout=20, retries=2):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    last = None
    for _ in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2)
    raise last


def spot_klines(symbol, interval, limit):
    """data-api.binance.vision 现货全字段 K 线，只保留已收盘 bar。
    limit>1000 时分页（与现网 klines() 同逻辑）。返回 dict 列表（含 qv/tbv）。"""
    out = []
    end = None
    remaining = limit
    while remaining > 0:
        n = min(remaining, 1000)
        url = "%s/api/v3/klines?symbol=%s&interval=%s&limit=%d" % (
            SPOT, symbol, interval, n)
        if end is not None:
            url += "&endTime=%d" % end
        raw = fetch_json(url)
        bars = [{"t": int(k[0]), "o": float(k[1]), "h": float(k[2]),
                 "l": float(k[3]), "c": float(k[4]), "v": float(k[5]),
                 "qv": float(k[7]), "tbv": float(k[9])} for k in raw]
        if not bars:
            break
        out = bars + out
        remaining -= len(bars)
        end = bars[0]["t"] - 1
        if len(bars) < n:
            break
    seen = {}
    for b in out:
        seen[b["t"]] = b
    out = [seen[t] for t in sorted(seen)]
    step = 60000 if interval == "1m" else 300000
    now_ms = int(time.time() * 1000)
    return [b for b in out if b["t"] + step <= now_ms]  # 去掉未收盘的最后一根


def fut_klines(symbol, kind, limit=1000):
    """合约/标记价 5m K 线（www 网关），只保留已收盘 bar。
    kind: klines|markPriceKlines。返回 {open_time: close}。"""
    url = "%s/fapi/v1/%s?symbol=%s&interval=5m&limit=%d" % (
        FAPI, kind, symbol, limit)
    raw = fetch_json(url)
    now_ms = int(time.time() * 1000)
    out = {}
    for k in raw:
        t = int(k[0])
        if t + 300000 <= now_ms:
            out[t] = float(k[4])
    return out


def funding_rates(symbol, limit=100):
    """资金费率历史（www 网关）：返回 (fundingTime, rate) 升序列表。"""
    url = "%s/fapi/v1/fundingRate?symbol=%s&limit=%d" % (FAPI, symbol, limit)
    raw = fetch_json(url)
    return sorted((int(x["fundingTime"]), float(x["fundingRate"]))
                  for x in raw)


def oi_series(symbol, limit=500):
    """OI 5m 数列（www 网关 openInterestHist）：返回 {label_ms: sumOpenInterest}。
    实盘存档等价数列 A(X) = 本字典中标签 X+300000 的值（见文件头说明）。"""
    url = ("%s/futures/data/openInterestHist?symbol=%s&period=5m&limit=%d"
           % (FAPI, symbol, limit))
    raw = fetch_json(url)
    return {int(x["timestamp"]): float(x["sumOpenInterest"]) for x in raw}


def roll_mean_last(vals, w=288):
    return float(np.mean(vals[-w:]))


def compute_new_features(sym, k5m_t, k1m_t, fut_c, mark_c, funding, oi, t_c):
    """在候选时刻 t_c 上计算 13 个新特征（与 exp2_features.py 逐项同语义）。
    k5m_t/k1m_t 已截断到收盘<=t_c（最后一根即候选 bar）。数据不足返回 None。"""
    n5 = len(k5m_t)
    if n5 < 289 or not k1m_t:
        return None
    o5 = np.array([b["o"] for b in k5m_t])
    h5 = np.array([b["h"] for b in k5m_t])
    l5 = np.array([b["l"] for b in k5m_t])
    c5 = np.array([b["c"] for b in k5m_t])
    v5 = np.array([b["v"] for b in k5m_t])
    qv5 = np.array([b["qv"] for b in k5m_t])
    tb5 = np.array([b["tbv"] for b in k5m_t])
    j = n5 - 1
    if k5m_t[j]["t"] + 300000 != t_c or k1m_t[-1]["t"] + 60000 != t_c:
        return None

    # G1 taker-buy
    tbr5 = tb5 / np.where(v5 > 0, v5, np.nan)
    tbr_5m = float(tbr5[j])
    tbr5_dev288 = tbr_5m - roll_mean_last(tbr5)
    b1 = k1m_t[-1]
    tbr_1m = float(b1["tbv"] / b1["v"]) if b1["v"] > 0 else float("nan")

    # G2 基差（合约/标记价收盘 vs 现货收盘，按 open_time 对齐；只需尾部 288 根）
    opens288 = [b["t"] for b in k5m_t[-288:]]
    try:
        fut_ser = np.array([fut_c[t] for t in opens288], dtype=float)
        mark_last = mark_c[k5m_t[j]["t"]]
    except KeyError:
        return None
    basis_ser = fut_ser / c5[-288:] - 1.0
    basis_fut = float(basis_ser[-1])
    basis_mark = float(mark_last / c5[j] - 1.0)
    basis_fut_dev288 = basis_fut - roll_mean_last(basis_ser)

    # G3 资金费率（calc_time<=t_c 的最近一档；ma3 = 最近 3 档均值）
    past = [r for (ft, r) in funding if ft <= t_c]
    if len(past) < 3:
        return None
    fund_last = float(past[-1])
    fund_ma3 = float(np.mean(past[-3:]))

    # G4 OI（存档等价数列 A(X)=REST(X+300000)，索引偏移与 exp2 一致）
    def A(x):
        v = oi.get(x + 300000)
        return v if (v is not None and v > 0) else None
    a_now, a_1h, a_24h = A(t_c), A(t_c - 3600000), A(t_c - 86400000)
    if a_now is None or a_1h is None or a_24h is None:
        return None
    oi_chg_1h = a_now / a_1h - 1.0
    oi_chg_24h = a_now / a_24h - 1.0

    # G5 微观结构（现货 5m quote 量/实体比/振幅；滚动 std ddof=1 与 pandas 一致）
    qwin = qv5[-288:]
    qm = float(np.mean(qwin))
    qs = float(np.std(qwin, ddof=1))
    if qs <= 0:
        return None
    qvol_z288 = float((qv5[j] - qm) / qs)
    rng_bar = h5[j] - l5[j]
    body_ratio_5m = (float(abs(c5[j] - o5[j]) / rng_bar)
                     if rng_bar > 0 else float("nan"))
    range_5m = float(rng_bar / c5[j])

    f = {"tbr_5m": tbr_5m, "tbr_1m": tbr_1m, "tbr5_dev288": float(tbr5_dev288),
         "basis_fut": basis_fut, "basis_mark": basis_mark,
         "basis_fut_dev288": float(basis_fut_dev288),
         "fund_last": fund_last, "fund_ma3": fund_ma3,
         "oi_chg_1h": float(oi_chg_1h), "oi_chg_24h": float(oi_chg_24h),
         "qvol_z288": qvol_z288, "body_ratio_5m": body_ratio_5m,
         "range_5m": range_5m}
    if not all(math.isfinite(v) for v in f.values()):
        return None
    return f


def record_alert(t_ms, sym, direction, prob, close, band):
    with open(LEDGER, "a") as fh:
        fh.write(json.dumps({"t": t_ms, "sym": sym, "dir": direction,
                             "prob": prob, "close": close, "band": band,
                             "model": "shadow_allfeat"}) + "\n")


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def main():
    dry_run = "--dry-run" in sys.argv
    result = {"ts": int(time.time()), "model": "shadow_allfeat",
              "forecasts": [], "recorded": [], "errors": []}
    if lgb is None or not os.path.exists(MODEL_FILE):
        result["errors"].append("影子模型缺失或 lightgbm 不可用，跳过本轮")
        print(json.dumps(result, ensure_ascii=False))
        return
    bst = lgb.Booster(model_file=MODEL_FILE)

    # ---- 拉数据（双标的现货 + 合约侧） ----
    data = {}
    for sym in SYMBOLS:
        try:
            data[sym] = {
                "k1m": spot_klines(sym, "1m", 500),
                "k5m": spot_klines(sym, "5m", 1500),
                "fut": fut_klines(sym, "klines"),
                "mark": fut_klines(sym, "markPriceKlines"),
                "funding": funding_rates(sym),
                "oi": oi_series(sym),
            }
        except Exception as e:  # noqa: BLE001
            msg = "%s 数据拉取失败: %s" % (sym, e)
            log(msg)
            result["errors"].append(msg)
    if len(data) < 2:
        print(json.dumps(result, ensure_ascii=False))
        return

    # 候选时刻：最新已收盘 5m bar 的收盘 - 600000（滞后两 tick 记账）。
    # 原因：A(t) 需要的 REST 标签 t+300000 是当前周期起始点，而该格点有约
    # 1-2 分钟发布延迟（实测：周期开始后 ~1.5 分钟时仍缺失、~1.8 分钟已出现），
    # 滞后两 tick 后所需标签已发布 5 分钟以上，稳定可得；特征语义不变。
    last_close = data[SYMBOLS[0]]["k5m"][-1]["t"] + 300000
    cur_cand = last_close - 600000

    state = load_state()
    for sym in SYMBOLS:
        other = SYMBOLS[1] if sym == SYMBOLS[0] else SYMBOLS[0]
        d, do = data[sym], data[other]
        last_done = int(state.get(sym, 0))
        if last_done <= 0:
            cands = [cur_cand]
        else:
            cands = []
            tt = last_done + 300000
            while tt <= cur_cand and len(cands) < MAX_BACKLOG:
                cands.append(tt)
                tt += 300000
            if tt <= cur_cand:  # 回溯超限：只处理最近 MAX_BACKLOG 个
                cands = [cur_cand - 300000 * i
                         for i in range(MAX_BACKLOG - 1, -1, -1)]
        for t_c in cands:
            try:
                k5 = [b for b in d["k5m"] if b["t"] + 300000 <= t_c]
                k1 = [b for b in d["k1m"] if b["t"] + 60000 <= t_c]
                k5o = [b for b in do["k5m"] if b["t"] + 300000 <= t_c]
                if not k5 or k5[-1]["t"] + 300000 != t_c:
                    raise RuntimeError("5m 截断后候选 bar 不对齐")
                t_ms, close, f31 = compute_features(k1, k5, k5o)
                if f31 is None or t_ms != t_c:
                    raise RuntimeError("FEAT3 计算失败或时刻不一致")
                f13 = compute_new_features(sym, k5, k1, d["fut"], d["mark"],
                                           d["funding"], d["oi"], t_c)
                if f13 is None:
                    raise RuntimeError("新特征计算失败（数据不足/格点缺失）")
                feats = dict(f31)
                feats.update(f13)
                x = np.array([[feats[k] for k in ALL_NAMES]], dtype=float)
                if not np.all(np.isfinite(x)):
                    raise RuntimeError("特征含非有限值")
                prob = float(bst.predict(x)[0])
            except Exception as e:  # noqa: BLE001
                msg = "%s 候选 %d 失败: %s" % (sym, t_c, e)
                log(msg)
                result["errors"].append(msg)
                continue  # 不推进 state，下一轮重试
            fc = {"symbol": sym, "t": t_c, "close": round(close, 2),
                  "prob_up": round(prob, 4), "bands": []}
            for band in BANDS:
                direction = ("UP" if prob >= band else
                             ("DOWN" if prob <= 1.0 - band else None))
                if direction:
                    fc["bands"].append({"band": band, "dir": direction})
                    if not dry_run:
                        record_alert(t_c, sym, direction, round(prob, 4),
                                     round(close, 2), band)
                    result["recorded"].append(
                        {"symbol": sym, "t": t_c, "band": band,
                         "dir": direction, "prob_up": round(prob, 4)})
            result["forecasts"].append(fc)
            if not dry_run:
                state[sym] = t_c
    if not dry_run:
        json.dump(state, open(STATE, "w"))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
