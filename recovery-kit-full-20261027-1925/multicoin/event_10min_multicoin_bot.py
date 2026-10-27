#!/usr/bin/env python3
# 10 分钟事件合约预报机器人（多币种版，只预报、不交易、不下单）
#
# 复用现网 event_10min_bot.py 的全部核心逻辑（单点真源，不复制特征代码）：
#   模型 lightgbm_10min.txt（LR 备用 lr_10min.pkl），FEAT3 31 特征，
#   阈值 0.55/0.45（LR 备用档与现网一致），不去重（用户 2026-10-26 确认取消 4 小时去重）。
# 覆盖币安全部 USDT 永续币种，排除现网已单独推送的 BTCUSDT/ETHUSDT；
# 跨标的动量统一用 BTCUSDT 5m 做市场参考（模型在 BTC/ETH 对上训练）。
# 传输层例外：不复用 klines() 的 urllib 单次连接（521 币种下实测大面积断连），
# 改用线程内复用的隧道长连接 fetch_klines()，URL/分页/去尾语义与 klines() 一致。
# K 线缓存（2026-10-26）：multicoin_klines_cache.py，磁盘缓存已收盘 K 线，
# 每轮只拉最新几根（1m 40 根 / 5m 16 根）合并，校验失败自动全量重拉；
# 返回口径与直接拉取逐根一致（fetch_klines 去尾后为 limit-1 根），
# 模型/阈值/去重/推送/记账口径均未动。
# 推送：每轮最多一条 Bark 汇总（触发才推，无触发静默），绝不逐条推送。
# 记账：multicoin-alerts.jsonl（字段与现网一致 t/sym/dir/prob/close），
#   与 live-alerts.jsonl 完全分开；不去重，不再读写 multicoin_state.json。
# 降级：单币种 K 线/特征失败只跳过该币种，绝不编造概率或预报；
#   模型加载失败或 BTC 参考数据缺失则整轮跳过、不推送。
#
# 用法: python3 event_10min_multicoin_bot.py [--dry-run]
#   --dry-run: 只打印完整预报 JSON，不推送、不写状态、不记账。

import base64
import http.client
import json
import os
import sys
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlparse

# 小币种常有平价/零波动区间，特征计算里的 numpy 除零警告是预期的，
# compute_features 已校验有限性；这里只压警告不改逻辑，避免每轮刷屏。
warnings.filterwarnings("ignore", category=RuntimeWarning)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from event_10min_bot import (  # noqa: E402
    compute_features, load_models, predict, send_bark,
)
from multicoin_klines_cache import cached_fetch_klines  # noqa: E402

# --- K 线拉取：与 event_10min_bot.klines() 同语义，但走长连接 ---
# 实测结论（2026-10-26）：klines() 每次新建 HTTPS 连接，521 币种 × 3 请求
# 在出口代理下大面积 "Remote end closed connection"（首轮 dry-run：174/521
# 失败、耗时 356 秒）。改走线程内复用的隧道长连接后，150 次并发调用
# 0 失败、9.6 秒。URL、分页、去重、去未收盘尾巴的语义与 klines() 完全一致。
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


def fetch_klines(symbol, interval, limit):
    """与 event_10min_bot.klines() 同语义：分页拉取、去重排序、去掉未收盘尾巴。"""
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
    seen = {}
    for b in out:
        seen[b["t"]] = b
    out = [seen[t] for t in sorted(seen)]
    if len(out) < 2:
        raise RuntimeError("klines too short")
    return out[:-1]

UNIVERSE_PATH = os.path.expanduser(
    "~/workspace/usdt-perps/binance_usdt_perps.json")
MEXC_PATH = os.path.expanduser(
    "~/workspace/usdt-perps/mexc_usdt_perps.json")
LEDGER = os.path.join(HERE, "multicoin-alerts.jsonl")
EXCLUDE = {"BTCUSDT", "ETHUSDT"}  # 现网已单独推送，禁止重复
REF_SYMBOL = "BTCUSDT"  # 跨标的动量参考
MAX_WORKERS = 16  # 保持 16：子代理复核，32 线程两次测量 36.1s/46.6s 不稳定、相对 16 线程（48.2s）省多少不可信，不采纳 32
DIGEST_MAX_LINES = 10
K1M_LIMIT, K5M_LIMIT = 500, 1500  # 与现网一致


def log(msg):
    sys.stderr.write("[multicoin_bot] %s\n" % msg)
    sys.stderr.flush()


def load_universe():
    with open(UNIVERSE_PATH) as f:
        rows = json.load(f)
    # 用户 2026-10-26 20:54 批准提速：只扫抹茶上架的币（交易端只能开抹茶有的币，
    # 币安独有 33 个 base 不用白算）。按 base 求交集，521 → 约 488。
    try:
        with open(MEXC_PATH) as f:
            mrows = json.load(f)
        mexc_bases = {r["base"] for r in mrows}
        syms = sorted({r["symbol"] for r in rows
                       if r["base"] in mexc_bases} - EXCLUDE)
    except Exception:
        syms = sorted({r["symbol"] for r in rows} - EXCLUDE)
    return syms


def fetch_with_retry(sym, interval, limit, attempts=3):
    """fetch_klines() 外再包一层，应对 429/5xx 瞬时限流。
    HTTP 400/404 是确定性失败（现货无此标的），直接失败不重试。"""
    last = None
    for i in range(attempts):
        try:
            return fetch_klines(sym, interval, limit)
        except Exception as e:  # noqa: BLE001
            last = e
            msg = str(e)
            if "HTTP 400" in msg or "HTTP 404" in msg:
                break
            if i < attempts - 1:
                time.sleep(2 * (i + 1))
    raise last


def fetch_and_features(sym, k5m_ref):
    """单币种：拉 K 线 + 算特征。返回 (sym, (t, close, feats) 或 None, err)。
    K 线走磁盘缓存：命中时只拉最新几根（1m 40 根 / 5m 16 根），
    缓存失效自动全量重拉，返回口径与直接拉取完全一致。"""
    try:
        k1m = cached_fetch_klines(sym, "1m", K1M_LIMIT, fetch_with_retry)
        k5m = cached_fetch_klines(sym, "5m", K5M_LIMIT, fetch_with_retry)
    except Exception as e:  # noqa: BLE001
        return sym, None, "%s K 线拉取失败: %s" % (sym, e)
    try:
        t_ms, close, feats = compute_features(k1m, k5m, k5m_ref)
    except Exception as e:  # noqa: BLE001
        return sym, None, "%s 特征计算异常: %s" % (sym, e)
    if feats is None:
        return sym, None, "%s 特征计算失败（数据不足或非有限值），跳过" % sym
    return sym, (t_ms, close, feats), None


def fmt_price(c):
    """按量级自适应小数位（小币种 0.00001235 不能像现网那样 round 到 2 位）。"""
    if c >= 1000:
        return "%.2f" % c
    if c >= 100:
        return "%.3f" % c
    if c >= 1:
        return "%.4f" % c
    if c >= 0.01:
        return "%.6f" % c
    return "%.8f" % c


def build_digest(alerts, t_ms):
    """汇总 Bark：标题带条数，正文最多 10 条按 |prob-0.5| 从强到弱。"""
    n = len(alerts)
    title = "10分钟预报 · 多币种 %d 条" % n
    ranked = sorted(alerts, key=lambda a: abs(a["prob_up"] - 0.5),
                    reverse=True)
    lines = []
    for a in ranked[:DIGEST_MAX_LINES]:
        d = "看涨" if a["direction"] == "UP" else "看跌"
        lines.append("%s %s prob %.3f 价格 %s" % (
            a["symbol"], d, a["prob_up"], fmt_price(a["close"])))
    if n > DIGEST_MAX_LINES:
        lines.append("另有 %d 条未列出" % (n - DIGEST_MAX_LINES))
    bj = timezone(timedelta(hours=8))
    ts = datetime.fromtimestamp(t_ms / 1000, tz=bj).strftime("%H:%M")
    lines.append("未来10分钟二元口径 · %s" % ts)
    lines.append("⚠️ 仅研究提醒，不构成交易建议")
    return title, "\n".join(lines)


def record_alert(t_ms, sym, direction, prob, close):
    try:
        with open(LEDGER, "a") as f:
            f.write(json.dumps({"t": t_ms, "sym": sym, "dir": direction,
                                "prob": round(prob, 4), "close": close}) + "\n")
    except Exception as e:  # noqa: BLE001
        log("记账失败: %s" % e)


def main():
    dry_run = "--dry-run" in sys.argv
    t0 = time.time()
    now = int(t0)
    model, err = load_models()
    result = {"ts": now, "model": model[0] if model else None,
              "universe": 0, "n_scored": 0, "forecasts": [], "alerts": [],
              "digest": None, "bark_ok": None, "errors": [], "n_errors": 0,
              "elapsed_sec": 0.0}
    if model is None:
        result["errors"].append(err)
        result["n_errors"] = 1
        log(err)
        result["elapsed_sec"] = round(time.time() - t0, 1)
        print(json.dumps(result, ensure_ascii=False))
        return

    try:
        universe = load_universe()
    except Exception as e:  # noqa: BLE001
        result["errors"].append("币种全集加载失败: %s" % e)
        result["n_errors"] = 1
        log("币种全集加载失败: %s" % e)
        result["elapsed_sec"] = round(time.time() - t0, 1)
        print(json.dumps(result, ensure_ascii=False))
        return
    result["universe"] = len(universe)

    # BTC 5m 参考只拉一次（跨标的动量）；失败则整轮跳过。同样走磁盘缓存。
    try:
        k5m_ref = cached_fetch_klines(REF_SYMBOL, "5m", K5M_LIMIT,
                                      fetch_with_retry)
    except Exception as e:  # noqa: BLE001
        result["errors"].append("%s 参考 K 线拉取失败: %s" % (REF_SYMBOL, e))
        result["n_errors"] = 1
        log("%s 参考 K 线拉取失败: %s" % (REF_SYMBOL, e))
        result["elapsed_sec"] = round(time.time() - t0, 1)
        print(json.dumps(result, ensure_ascii=False))
        return

    # 并行：拉 K 线 + 算特征；预测放主线程做（LightGBM 单行预测极快，
    # 也避免多线程并发调用 Booster 的任何不确定性）。
    scored = {}
    errors = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(fetch_and_features, sym, k5m_ref): sym
                for sym in universe}
        for fut in as_completed(futs):
            sym, payload, ferr = fut.result()
            if ferr:
                errors.append(ferr)
            else:
                scored[sym] = payload
    result["n_scored"] = len(scored)
    result["n_errors"] = len(errors)

    # 不去重（用户 2026-10-26 确认取消 4 小时去重）：只要过阈值就触发，
    # 不再读 multicoin_state.json。
    candidate_t = None
    new_alerts = []
    for sym in sorted(scored):
        t_ms, close, feats = scored[sym]
        if candidate_t is None:
            candidate_t = t_ms
        try:
            prob, lo_thr, sh_thr = predict(model, feats)
        except Exception as e:  # noqa: BLE001
            errors.append("%s 预测异常: %s" % (sym, e))
            result["n_errors"] = len(errors)
            continue
        direction = ("UP" if prob >= lo_thr
                     else ("DOWN" if prob <= sh_thr else None))
        fc = {"symbol": sym, "close": close,
              "prob_up": round(prob, 4), "direction": direction,
              "alerted": False}
        if direction:
            fc["alerted"] = True
            new_alerts.append({"symbol": sym, "direction": direction,
                               "prob_up": round(prob, 4), "close": close,
                               "t": t_ms})
        if dry_run or fc["alerted"]:
            result["forecasts"].append(fc)
    result["alerts"] = new_alerts

    # 汇总 Bark：有新触发才推一封；dry-run 只生成文本不发送。
    if new_alerts:
        title, text = build_digest(new_alerts, candidate_t or now * 1000)
        result["digest"] = {"title": title, "text": text}
        if not dry_run:
            ok = send_bark(title, text)
            result["bark_ok"] = ok
            if ok:
                for a in new_alerts:
                    record_alert(a["t"], a["symbol"], a["direction"],
                                 a["prob_up"], a["close"])
            else:
                log("Bark 汇总推送失败，本轮触发未记账（下一轮会重推）")
                errors.append("Bark 汇总推送失败")
                result["n_errors"] = len(errors)

    # live 模式输出截断 errors，避免失败币种多时刷屏；dry-run 全量输出。
    if not dry_run and len(errors) > 20:
        result["errors"] = errors[:20]
    else:
        result["errors"] = errors
    result["elapsed_sec"] = round(time.time() - t0, 1)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
