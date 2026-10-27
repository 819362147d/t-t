#!/usr/bin/env python3
"""MEXC 实盘交易守护进程 v2：极限优化版（2026-10-27）。

相对 v1（mexc_trader_daemon.py）的改动只针对延迟与 TP/SL 重复 bug，
风控参数一字未动：
  杠杆 50x 逐仓、止盈 +85% / 止损 -25%（保证金口径，50x 下 = 1.7% / 0.5% 价格，
  比值 3.4 倍，满足“止盈距离 ≥2 倍止损距离”）、止损到爆仓线至少留 0.5% 价格空间、
  单笔保证金 5% 本金、总占用 ≤50%、单日累计亏损达本金 3% 停手、
  只做 ≤4 分钟新鲜信号、同币不重复开仓、资金不足等平仓。
  TP_MARGIN_PCT=0.85 为用户 2026-10-27 11:51 所定（v1 文件当日已同步改成 0.85，
  11:54 GRT 首单实测 tp +1.7% / sl -0.5%）。

延迟实测（2026-10-27，见 OPTIMIZATION.md）：
  round_t → daemon 看到：均值 206.6s（n=73，186–240s），大头在上游：
    cron 每 5 分钟锚点 18:22:20，收盘后约 140s 才启动 bot；
    多币种 bot 单轮 elapsed 47.8s（dry-run 实测，488 宇宙/已评分 330）。
  daemon 内 plan → opened：中位 13s、p90 27s、max 47s；
    每次 attempt ≈ ticker ~0.9s + submit ~1–2s + 固定 sleep 10s
    + 持仓查询 ~1s + 撤单 ~1s ≈ 14–17s，3 次重试即 47s，
    且 ex.map 要等最慢的币才统一记 opened。
  v2 目标：账本写入 → 开单 <5s；round_t → 开单要 <60s 还须同时改上游
  （cron 重锚到收盘后 ~10s 启动：约 48s bot + ~3s daemon ≈ 51s），
  只换 daemon 不改上游，round_t → 开单仍约 200s。

v2 改动一览（每处省多少秒见 OPTIMIZATION.md）：
  1. 轮询 10s → 1s + 账本 mtime 触发：mtime 没变不读账本、不调 API；
     manage_exits 仍每 10s 一次（不增加 API 频率）。
  2. latest_top 全量读（7573 行约 0.03s）→ tail 读最后 128KB（约 0.003s），
     首行残行丢弃；异常回落全量读。实测省 0.027s，可忽略，但防账本变大。
  3. 限价单 10s 等待 + 最多 3 次撤单重开 → 市价单一次成交：
     submit_market（type=5）下单时同单附带 TP/SL；成交确认改 0.5s 轮询
     open_positions（最多 5s），首轮询到即确认。滑点取舍：市价吃 taker 费
     （实收约 0.06%–0.08%，maker 为 0）且薄盘可能滑过止损距离；
     spread >0.25%（止损距离的一半）时回落限价单只试一次（2s 轮询），
     不成交即撤，不再反复重开。
  4. HTTP keep-alive：每线程复用一条到 api.mexc.com 的 HTTPS 连接
     （走环境里的 egress proxy 隧道，与多币种 bot 同款做法），
     失败自动回落 urllib 版客户端函数；ticker/contract detail 只在启动拉一次，
     此后缓存（缺失才补拉，且 60s 内同一币种只补拉一次）。
  5. 逐单落账：as_completed，哪个币先成交先写 state，不等最慢的币。
  6. 资金同步每轮一次（60s TTL 缓存），不再只用启动时权益。

TP/SL 重复 bug 的根因与修复（用户 2026-10-27 11:48：同一币种止盈止损
不要重复设置，重复会卡 bug 导致止不掉）：
  根因 A——重试循环每次都带 TP/SL 重新下单：attempt 1–3 各发一个带
    stopLossPrice/takeProfitPrice 的限价单；若 attempt 1 已成交但持仓查询
    恰好失败/超时，attempt 2 会再发一单，同币出现两套 TP/SL。
  根因 B——撤单不核实：v1 调 cancel_order 后不看结果直接重开；
    510 限流时撤单常失败，旧单带着 TP/SL 还挂着，新单又挂一套。
  根因 C——TP 失败静默降级：submit 带 TP 失败（510 限流 21 次、
    2030 oid 超长 3 次、700007 3 次）后立即用同一个 oid 只挂 SL 重试；
    第一次提交可能服务端已成功，等于同币第二套止损；且复用 oid 本身
    也可能被拒。2030 的直接原因是 oid "muse-<13位>-<币种>-a1" 超 32 字符
    （如 VELODROMEUSDT 为 36 字符）。
  根因 D——06:29 会话双进程并发：2026-10-27 06:00 与 07:00 两小时日志
    每行恰好重复一次（73/146 与 17/34，其余时段 0 次），即两个守护进程
    同时在跑、同时读同一 state、同时给同一币种下单挂 TP/SL。
  修复（v2）：
  - TP/SL 只在开仓那一单里附带一次，同一持仓绝不重发：state 里每单记
    tp_sl_set / tp_missing / attach_oid / attach_attempts；已持有币种
    （open_trades 或正在开仓的 in-flight 集合）直接跳过。
  - 下单前先查持仓：该币已有持仓 → 视为已成交收编，不再下单；
    若 state 里没这单，记 tp_missing=True 并 Bark 一次，不补挂 TP/SL
    （本客户端没有独立改 TP/SL 的接口，TP/SL 只能随开仓单附带，
    事后补挂无安全做法，只能人工处理）。
  - 撤单必须核实：cancel 后用 list_open_orders 轮询确认订单消失
    （最多 2s）；撤不掉就不发下一单，记 cancel_unconfirmed 并 Bark。
  - TP 提交失败不降级：510 只等 1s 重试一次（新 oid、同样带 TP/SL）；
    仍失败则放弃该币、不开裸单（不开没有止盈的仓），记 tp_missing。
  - oid 缩短到 ≤32 字符：m2 + round_t 后 8 位 + 币种（+r1 表示重试）。
  - 单进程锁：启动时检查 v1 进程与 v2 pidfile，v1 还在跑就拒绝启动
    （须先停 v1），杜绝根因 D 的双进程并发。
  - 补上 v1 缺失的 submit_market import（v1 的 _close_trade 用到了它，
    HOLD_SECONDS=None 时走不到，但一旦启用定时平仓就会 NameError）。

用法与 v1 相同（密钥只从 stdin 读一次进内存，不落盘）：
  python3 mexc_trader_daemon_v2.py [--dry-run] [--once]
  dry-run 时 state/log 一律写 /tmp（绝不碰 live 的 state/log），
  可用环境变量覆盖：MEXC_V2_LEDGER（测试账本）、MEXC_V2_STATE。
切换到 v2 的步骤（须用户点头）：先停 v1（确认 ps 里无 mexc_trader_daemon.py），
再以前台 exec 起 v2（stdin 重发 key），首单盯日志确认市价成交与 TP/SL。
"""
import base64
import hashlib
import hmac
import http.client
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mexc_trade_client import (calc_vol, cancel_order, get_asset,
                               get_contract_detail, get_ticker, mexc_symbol,
                               open_positions, submit_limit, submit_market,
                               get_order, list_open_orders)
from mexc_top3_signals import latest_top as latest_top_full

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.environ.get(
    "MEXC_V2_LEDGER",
    os.path.join(HERE, "multicoin-alerts.jsonl"))
DEFAULT_STATE = os.path.join(HERE, "mexc_trader_state.json")
DEFAULT_LOG = os.path.join(HERE, "mexc_trader_v2.log")
BARK = "/home/hatch/workspace/custom-connectors/custom.bark-push/bin/bark_send.py"
PIDFILE = os.path.join(HERE, "mexc_trader_v2.pid")

LEVERAGE = 50
MARGIN_PCT = 0.05
TP_MARGIN_PCT = 0.85  # 用户 2026-10-27 11:51 定：止盈 +85%（保证金口径）
SL_MARGIN_PCT = 0.25  # 止损 -25%（保证金口径），不变
TP_PCT = TP_MARGIN_PCT / LEVERAGE  # 50x 下 = 1.7% 价格
SL_PCT = SL_MARGIN_PCT / LEVERAGE  # 50x 下 = 0.5% 价格
MIN_LIQ_BUFFER_PCT = 0.005
HOLD_SECONDS = None  # 不限时：只靠止盈/止损，不到时平仓
DAILY_STOP_PCT = 0.03
FRESH_SECONDS = 240
LOOP_SECONDS = 1  # v2：1s 轮询 + mtime 触发（v1 为 10s）
MAX_MARGIN_USE_PCT = 0.50
POLL_INTERVAL = 0.5  # 成交确认轮询间隔（v1 为固定 sleep 10s）
POLL_DEADLINE = 5.0  # 成交确认最长等待
SPREAD_LIMIT_PCT = 0.0025  # spread 超过止损距离一半 → 改限价单只试一次
RATE_LIMIT_BACKOFF = 1.0  # 510 限流后重试前等待
TAIL_BYTES = 131072


def _is_insufficient_funds(r):
    msg = str(r.get("message") or r.get("msg") or "").lower()
    for kw in ("insufficient", "not enough", "资金不足", "余额不足", "保证金不足"):
        if kw in msg:
            return True
    return False


def _is_rate_limited(r):
    return str(r.get("code")) == "510" or "too frequent" in str(
        r.get("message") or r.get("msg") or "").lower()


# ---------------------------------------------------------------- HTTP keep-alive
# 每线程一条到 api.mexc.com 的持久 HTTPS 连接（走环境 egress proxy 隧道，
# 做法与多币种 bot 的 _vision_conn 相同）；任何异常回落 urllib 版函数。
_TLS = threading.local()
_MEXC_HOST = "api.mexc.com"


def _sign(secret, access_key, timestamp, param_str):
    msg = (access_key + timestamp + param_str).encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def _new_mexc_conn():
    proxy = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY") or ""
    p = urlparse(proxy)
    if p.hostname:
        headers = {}
        if p.username:
            creds = base64.b64encode(
                ("%s:%s" % (p.username, p.password or "")).encode()).decode()
            headers["Proxy-Authorization"] = "Basic %s" % creds
        conn = http.client.HTTPSConnection(p.hostname, p.port or 3128, timeout=10)
        conn.set_tunnel(_MEXC_HOST, 443, headers=headers or None)
        return conn
    return http.client.HTTPSConnection(_MEXC_HOST, timeout=10)


def _mexc_conn():
    c = getattr(_TLS, "conn", None)
    if c is None:
        c = _TLS.conn = _new_mexc_conn()
    return c


def _fast_request(method, path, ak=None, sk=None, params=None, body=None):
    """返回与 mexc_trade_client._request 同形 dict；失败抛异常由调用方回落。"""
    params = params or {}
    if method == "GET" and params:
        qs = "&".join(f"{k}={params[k]}" for k in sorted(params)
                      if params[k] is not None)
        param_str, url = qs, path + ("?" + qs if qs else "")
        data = None
    elif method == "GET":
        param_str, url, data = "", path, None
    else:
        param_str = json.dumps(body, separators=(",", ":")) if body else ""
        url, data = path, param_str.encode() if param_str else None
    headers = {"Content-Type": "application/json"}
    if ak and sk:
        ts = str(int(time.time() * 1000))
        headers["ApiKey"] = ak
        headers["Request-Time"] = ts
        headers["Signature"] = _sign(sk, ak, ts, param_str)
    last = None
    for _ in range(2):  # 连接断了丢弃重建，再试一次
        try:
            conn = _mexc_conn()
            conn.request(method, url, body=data, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            return json.loads(raw.decode())
        except Exception as e:  # noqa: BLE001
            last = e
            _TLS.conn = None
    raise last


def get_ticker_fast(sym):
    try:
        r = _fast_request("GET", "/api/v1/contract/ticker",
                          params={"symbol": mexc_symbol(sym)})
        if isinstance(r, dict) and r.get("success"):
            return r
    except Exception:  # noqa: BLE001
        pass
    return get_ticker(sym)


def open_positions_fast(ak, sk, sym=None):
    try:
        params = {"symbol": mexc_symbol(sym)} if sym else {}
        r = _fast_request("GET", "/api/v1/private/position/open_positions",
                          ak, sk, params=params)
        if isinstance(r, dict) and r.get("success") is not False:
            return r
    except Exception:  # noqa: BLE001
        pass
    return open_positions(ak, sk, sym)


def list_open_orders_fast(ak, sk, sym=None):
    try:
        params = {"symbol": mexc_symbol(sym)} if sym else {}
        r = _fast_request("GET", "/api/v1/private/order/list/open_orders",
                          ak, sk, params=params)
        if isinstance(r, dict):
            return r
    except Exception:  # noqa: BLE001
        pass
    return list_open_orders(ak, sk, sym)


def submit_market_fast(ak, sk, sym, side, vol, price, leverage,
                       stop_loss=None, take_profit=None, external_oid=None):
    body = {"symbol": mexc_symbol(sym), "price": price, "vol": vol,
            "side": side, "type": 5, "openType": 1, "leverage": leverage}
    if stop_loss is not None:
        body["stopLossPrice"] = stop_loss
    if take_profit is not None:
        body["takeProfitPrice"] = take_profit
    if external_oid:
        body["externalOid"] = external_oid
    try:
        r = _fast_request("POST", "/api/v1/private/order/submit",
                          ak, sk, body=body)
        if isinstance(r, dict):
            return r
    except Exception:  # noqa: BLE001
        pass
    return submit_market(ak, sk, sym, side, vol, price, leverage,
                         open_type=1, stop_loss=stop_loss,
                         take_profit=take_profit, external_oid=external_oid)


def get_asset_fast(ak, sk):
    try:
        r = _fast_request("GET", "/api/v1/private/account/asset/USDT", ak, sk)
        if isinstance(r, dict) and r.get("success"):
            return r
    except Exception:  # noqa: BLE001
        pass
    return get_asset(ak, sk, "USDT")


# ---------------------------------------------------------------- 账本 tail 读
def latest_top_tail(ledger=LEDGER, n=5):
    """只读账本尾部找最新一轮（v1 全量读 7573 行约 0.03s，tail 约 0.003s）。"""
    with open(ledger, "rb") as f:
        f.seek(0, 2)
        size, pos, data = f.tell(), f.tell(), b""
        while pos > 0 and data.count(b"\n") < 600:
            step = min(TAIL_BYTES, pos)
            pos -= step
            f.seek(pos)
            data = f.read(step) + data
    lines = data.decode(errors="ignore").splitlines()
    if pos > 0 and lines:
        lines = lines[1:]  # 丢弃可能被截断的首行
    rows = [json.loads(x) for x in lines if x.strip()]
    if not rows:
        raise ValueError("ledger tail empty")
    last_t = max(r["t"] for r in rows)
    cands = [r for r in rows if r["t"] == last_t]
    cands.sort(key=lambda r: abs(r["prob"] - 0.5), reverse=True)
    return last_t, cands[:n]


def latest_top(n=5):
    try:
        return latest_top_tail(n=n)
    except Exception:  # noqa: BLE001
        return latest_top_full(n=n)


def _short_oid(round_t, sym, retry=False):
    """≤32 字符的外部订单号（v1 的 muse-<13位>-<币种>-a1 最长 36 字符，报 2030）。"""
    oid = "m2%d%s" % (round_t % 100000000, sym)
    if retry:
        oid += "r1"
    assert len(oid) <= 32, oid
    return oid


# ---------------------------------------------------------------- 进程
class Trader:
    def __init__(self, ak, sk, dry_run=False):
        self.ak, self.sk = ak, sk
        self.dry_run = dry_run
        # dry-run 绝不碰 live state/log：写 /tmp
        if dry_run:
            self.state_file = os.environ.get(
                "MEXC_V2_STATE", "/tmp/mexc_trader_v2_dryrun_state.json")
            self.log_file = "/tmp/mexc_trader_v2_dryrun.log"
        else:
            self.state_file = os.environ.get("MEXC_V2_STATE", DEFAULT_STATE)
            self.log_file = DEFAULT_LOG
        self.state = self._load_state()
        self.detail_map = {}
        self.capital = 0.0
        self._last_capital_sync = 0.0
        self._last_ledger_mtime = None
        self._last_exit_check = 0.0
        self._detail_miss_until = {}
        self._inflight = set()
        self._refresh_details()

    # -- 日志/状态 --
    def log(self, msg):
        line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + msg
        print(line, flush=True)
        try:
            with open(self.log_file, "a") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def bark(self, title, body):
        try:
            subprocess.run(["python3", BARK, title, body], timeout=15,
                           capture_output=True)
        except Exception as e:  # noqa: BLE001
            self.log(f"bark failed: {e}")

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return {"traded_rounds": [], "open_trades": {}, "closed_trades": [],
                "day": time.strftime("%Y-%m-%d"), "day_start_equity": None,
                "day_realized": 0.0}

    def _save_state(self):
        if self.dry_run:
            return  # dry-run 不写任何 state（即使 /tmp 也不写，保持零副作用）
        try:
            with open(self.state_file, "w") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=1)
        except Exception as e:  # noqa: BLE001
            self.log(f"save state failed: {e}")

    # -- 合约详情（启动拉一次，此后缓存） --
    def _refresh_details(self):
        r = get_contract_detail()
        if r.get("success"):
            self.detail_map = {d["symbol"]: d for d in r.get("data") or []}
            self.log(f"contract detail loaded: {len(self.detail_map)} symbols")
        else:
            self.log(f"contract detail failed: {r.get('code')} {r.get('message')}")

    def detail_for(self, sym):
        ms = mexc_symbol(sym)
        d = self.detail_map.get(ms)
        if d is None:
            now = time.time()
            if now > self._detail_miss_until.get(ms, 0):
                self._detail_miss_until[ms] = now + 60
                self._refresh_details()
                d = self.detail_map.get(ms)
        return ms, d

    def sync_capital(self):
        if self.dry_run:
            self.capital = 2.0575
            return
        if time.time() - self._last_capital_sync < 60 and self.capital:
            return
        self._last_capital_sync = time.time()
        r = get_asset_fast(self.ak, self.sk)
        if r.get("success"):
            d = r["data"]
            self.capital = float(d.get("equity", 0) or 0)
            eq = float(d.get("equity", 0) or 0)
            today = time.strftime("%Y-%m-%d")
            if self.state["day"] != today:
                self.state.update({"day": today, "day_start_equity": eq,
                                   "day_realized": 0.0})
                self._save_state()
            elif self.state.get("day_start_equity") is None:
                self.state["day_start_equity"] = eq
                self._save_state()
            self.log(f"capital equity={self.capital:.4f} "
                     f"available={float(d.get('availableBalance', 0) or 0):.4f}")
        else:
            self.log(f"get_asset failed: {r.get('code')} {r.get('message')}")

    def margin_in_use(self):
        return sum(t.get("margin", 0.0)
                   for t in self.state["open_trades"].values())

    def daily_stopped(self):
        return self.state.get("day_realized", 0.0) <= -DAILY_STOP_PCT * self.capital

    def _position_of(self, sym):
        """返回该币的交易所持仓 dict，或 None（查不到/无持仓）。"""
        try:
            pp = open_positions_fast(self.ak, self.sk, sym)
        except Exception as e:  # noqa: BLE001
            self.log(f"open_positions {sym} error: {e}")
            return None
        if not pp.get("success"):
            return None
        ms = mexc_symbol(sym)
        for p in pp.get("data") or []:
            if p.get("symbol") == ms:
                return p
        return None

    def _order_still_open(self, sym, order_id, oid):
        try:
            oo = list_open_orders_fast(self.ak, self.sk, sym)
        except Exception:  # noqa: BLE001
            return None  # 查不到 = 未知，不当作已撤
        if not oo.get("success"):
            return None
        for o in oo.get("data") or []:
            if str(o.get("orderId")) == str(order_id) or \
                    o.get("externalOid") == oid:
                return True
        return False

    # -- 开仓 --
    def maybe_open(self, round_t, cands):
        if self.daily_stopped():
            self.log("daily stop hit, skip opening")
            return
        held = {t["symbol"] for t in self.state["open_trades"].values()}
        plans, planned_margin, skipped_margin = [], 0.0, 0
        for c in cands:
            if len(plans) >= 3:
                break
            sym, direction, prob, ref = c["sym"], c["dir"], c["prob"], c["close"]
            if sym in held or sym in self._inflight:
                self.log(f"skip {sym}: already held")
                continue
            ms, det = self.detail_for(sym)
            if det is None:
                self.log(f"skip {sym}: not listed on MEXC")
                continue
            if LEVERAGE > int(det.get("maxLeverage", 0) or 0):
                self.log(f"skip {sym}: maxLeverage {det.get('maxLeverage')} < {LEVERAGE}")
                continue
            price_scale = int(det.get("priceScale", 0) or 0)
            ref_r = round(float(ref), price_scale)
            notional = MARGIN_PCT * self.capital * LEVERAGE
            vol = calc_vol(det, notional, ref_r)
            margin = vol * float(det["contractSize"]) * ref_r / LEVERAGE
            if self.margin_in_use() + planned_margin + margin > MAX_MARGIN_USE_PCT * self.capital:
                self.log(f"skip {sym}: margin budget exceeded")
                skipped_margin += 1
                continue
            if direction == "UP":
                side, tp = 1, round(ref_r * (1 + TP_PCT), price_scale)
                sl = round(ref_r * (1 - SL_PCT), price_scale)
            else:
                side, tp = 3, round(ref_r * (1 - TP_PCT), price_scale)
                sl = round(ref_r * (1 + SL_PCT), price_scale)
            plans.append({"symbol": sym, "mexc_symbol": ms, "side": side,
                          "dir": direction, "prob": prob, "ref": ref_r,
                          "vol": vol, "margin": margin, "tp": tp, "sl": sl,
                          "price_scale": price_scale, "round_t": round_t,
                          "oid": _short_oid(round_t, sym)})
            planned_margin += margin
            self.log(f"plan {sym} {'多' if side == 1 else '空'} prob={prob} ref={ref_r} "
                     f"vol={vol} margin~{margin:.4f} tp={tp} sl={sl}")
        if not plans:
            if skipped_margin > 0:
                self.log(f"资金已用完（{skipped_margin} 个信号因保证金不足跳过），等平仓释放后再开")
            return
        if self.dry_run:
            self.log(f"dry-run: would open {len(plans)} positions")
            return
        for p in plans:
            self._inflight.add(p["symbol"])
        try:
            with ThreadPoolExecutor(max_workers=3) as ex:
                futs = {ex.submit(self._execute, plan): plan for plan in plans}
                for fut in as_completed(futs):
                    plan = futs[fut]
                    self._inflight.discard(plan["symbol"])
                    try:
                        result = fut.result()
                    except Exception:
                        self.log(f"execute {plan['symbol']} crashed:\n"
                                 + traceback.format_exc())
                        continue
                    self._record_fill(result)
        finally:
            for p in plans:
                self._inflight.discard(p["symbol"])

    def _record_fill(self, plan):
        sym = plan["symbol"]
        if plan.get("funds_exhausted"):
            self.log(f"{sym}: 资金不足，等平仓释放后再开")
            return
        if plan.get("stale"):
            self.log(f"{sym}: 本轮已过新鲜期，停止重试")
            return
        if plan.get("cancel_unconfirmed"):
            self.log(f"{sym}: 撤单未确认，本轮不再下单，等人工处理")
            return
        if plan.get("submit_error"):
            self.log(f"submit {sym} failed: {plan['submit_error']}")
            return
        if plan.get("tp_missing") and not plan.get("filled"):
            self.log(f"{sym}: TP 未能挂上，放弃开仓（不开裸单）")
            return
        if not plan.get("filled"):
            self.log(f"{sym}: 未成交，放弃本轮")
            return
        trade = {"symbol": sym, "mexc_symbol": plan["mexc_symbol"],
                 "side": plan["side"], "dir": plan["dir"],
                 "prob": plan["prob"], "ref": plan["ref"], "vol": plan["vol"],
                 "margin": plan["margin"], "tp": plan["tp"], "sl": plan["sl"],
                 "order_id": plan.get("order_id"),
                 "entry": plan.get("entry") or plan["ref"],
                 "opened_at": time.time(),
                 "round_t": plan["round_t"], "status": "open",
                 "tp_sl_set": bool(plan.get("tp_sl_set")),
                 "tp_missing": bool(plan.get("tp_missing")),
                 "attach_oid": plan.get("oid"),
                 "attach_attempts": plan.get("attach_attempts", 0)}
        self.state["open_trades"][plan["oid"]] = trade
        self._save_state()  # 逐单落账，不等其他币
        self.log(f"opened {sym} order={plan.get('order_id')} "
                 f"entry={plan.get('entry')} tp_sl_set={trade['tp_sl_set']}")
        if plan.get("adopted"):
            self.log(f"{sym}: 持仓已存在，收编入账，不再下单挂 TP/SL")

    def _execute(self, plan):
        """单币开仓：市价单一次成交 + TP/SL 同单附带一次。绝不重复挂 TP/SL。"""
        sym, ms = plan["symbol"], plan["mexc_symbol"]
        price_scale = plan["price_scale"]
        plan["attach_attempts"] = 0
        # 0. 下单前先查持仓：已有持仓 → 收编，绝不再下单（根因 A 的反向保护）
        if not self.dry_run:
            pos0 = self._position_of(sym)
            if pos0 is not None:
                plan.update({"filled": True, "adopted": True,
                             "tp_sl_set": False, "tp_missing": True,
                             "entry": pos0.get("openAvgPrice") or plan["ref"],
                             "margin": pos0.get("im") or plan["margin"],
                             "order_id": pos0.get("positionId")})
                self.log(f"{sym}: 下单前已发现持仓，收编不重开 "
                         f"(tp_missing=True，等人工确认交易所 TP/SL)")
                try:
                    self.bark("实盘注意 · %s" % sym,
                              "开仓前已发现持仓，已收编不再下单；"
                              "TP/SL 未重复挂，请人工确认交易所止盈止损是否在位")
                except Exception:
                    pass
                return plan
        # 1. 一次 ticker 定价（v1 每 attempt 各取一次）
        tk = get_ticker_fast(sym)
        tkdata = (tk.get("data") or {}) if tk.get("success") else {}
        try:
            last = float(tkdata.get("lastPrice") or plan["ref"])
            bid = float(tkdata.get("bid1") or last)
            ask = float(tkdata.get("ask1") or last)
        except (TypeError, ValueError):
            last = bid = ask = float(plan["ref"])
        mid = (bid + ask) / 2 if bid and ask else last
        spread = (ask - bid) / mid if mid else 0.0
        px = round(last, price_scale)
        # TP/SL 只算一次，以提交价为准（v1 每 attempt 重算 = 多套 TP/SL 的来源之一）
        if plan["side"] == 1:
            tp = round(px * (1 + TP_PCT), price_scale)
            sl = round(px * (1 - SL_PCT), price_scale)
        else:
            tp = round(px * (1 - TP_PCT), price_scale)
            sl = round(px * (1 + SL_PCT), price_scale)
        plan["tp"], plan["sl"] = tp, sl
        use_limit = spread > SPREAD_LIMIT_PCT
        if use_limit:
            self.log(f"{sym}: spread {spread:.2%} > {SPREAD_LIMIT_PCT:.2%}，"
                     f"改限价单只试一次（市价滑点会吃掉止损距离）")
        # 2. 下单：TP/SL 同单附带，只发一次；510 才重试一次（新 oid，照样带 TP/SL）
        oid = plan["oid"]
        r = self._submit_once(plan, px, tp, sl, oid, use_limit)
        plan["attach_attempts"] += 1
        if not r.get("success"):
            if _is_insufficient_funds(r):
                plan["funds_exhausted"] = True
                return plan
            if _is_rate_limited(r):
                time.sleep(RATE_LIMIT_BACKOFF)
                oid2 = _short_oid(plan["round_t"], sym, retry=True)
                plan["oid"] = oid2
                r = self._submit_once(plan, px, tp, sl, oid2, use_limit)
                plan["attach_attempts"] += 1
                oid = oid2
            if not r.get("success"):
                # 不降级成只有 SL：不开裸单（TP 缺失的仓违反 85%/25% 风控设计）
                plan["submit_error"] = f"{r.get('code')} {r.get('message')}"
                plan["tp_missing"] = True
                self.log(f"{sym}: TP/SL 附带提交失败，放弃开仓 "
                         f"(不降级只挂 SL): {plan['submit_error']}")
                return plan
        data = r.get("data")
        order_id = data.get("orderId") if isinstance(data, dict) else data
        plan["order_id"] = order_id
        # 3. 成交确认：0.5s 轮询持仓（v1 固定 sleep 10s），最多 5s
        pos, deadline = None, time.time() + POLL_DEADLINE
        while time.time() < deadline:
            pos = self._position_of(sym)
            if pos is not None:
                break
            time.sleep(POLL_INTERVAL)
            if (time.time() * 1000 - plan["round_t"]) / 1000 > FRESH_SECONDS:
                plan["stale"] = True
                break
        if pos is not None:
            plan.update({"filled": True, "tp_sl_set": True,
                         "entry": pos.get("openAvgPrice") or px,
                         "margin": pos.get("im") or plan["margin"]})
            self._liq_check(plan, pos, px)
            return plan
        # 4. 未确认成交：先查该单是否还挂着；挂着就撤并核实，撤不掉绝不重开
        still = self._order_still_open(sym, order_id, oid)
        if still:
            try:
                cancel_order(self.ak, self.sk, order_id)
            except Exception as e:  # noqa: BLE001
                self.log(f"cancel {sym} error: {e}")
            gone = False
            for _ in range(4):
                time.sleep(POLL_INTERVAL)
                if self._order_still_open(sym, order_id, oid) is False:
                    gone = True
                    break
            if not gone:
                plan["cancel_unconfirmed"] = True
                self.log(f"{sym}: 撤单未确认（订单仍挂着），本轮不再下单")
                try:
                    self.bark("实盘注意 · %s" % sym,
                              "撤单未确认，订单可能仍挂着 TP/SL，"
                              "本轮已停手，等人工处理")
                except Exception:
                    pass
                return plan
            self.log(f"cancel {sym} (not filled), abort round")
            return plan
        if still is None:
            # 查不到订单状态：不重开、不假设，等人工确认（重开可能双仓双 TP/SL）
            plan["fill_unknown"] = True
            self.log(f"{sym}: 提交成功但成交状态查不到，不重开，等人工确认")
            try:
                self.bark("实盘注意 · %s" % sym,
                          "下单返回成功但持仓/订单状态查不到，"
                          "未重开，等人工确认是否已成交")
            except Exception:
                pass
            return plan
        self.log(f"{sym}: 订单已不在挂单且无持仓，未成交，放弃本轮")
        return plan

    def _submit_once(self, plan, px, tp, sl, oid, use_limit):
        sym = plan["symbol"]
        if use_limit:
            # 限价只试一次：贴 tick（多卖一/空买一），TP/SL 同单附带
            tk = get_ticker_fast(sym)
            tkdata = (tk.get("data") or {}) if tk.get("success") else {}
            try:
                px_raw = tkdata.get("ask1") if plan["side"] == 1 else tkdata.get("bid1")
                px = round(float(px_raw or px), plan["price_scale"])
            except (TypeError, ValueError):
                pass
            return submit_limit(self.ak, self.sk, sym, plan["side"],
                                plan["vol"], px, LEVERAGE, open_type=1,
                                stop_loss=sl, take_profit=tp,
                                external_oid=oid)
        return submit_market_fast(self.ak, self.sk, sym, plan["side"],
                                  plan["vol"], px, LEVERAGE,
                                  stop_loss=sl, take_profit=tp,
                                  external_oid=oid)

    def _liq_check(self, plan, pos, px):
        """爆仓线检查（与 v1 同口径）：止损线须在爆仓线前且至少留 0.5% 空间。"""
        sym = plan["symbol"]
        liq = None
        for _k in ("liquidatePrice", "liquidationPrice", "liqPrice",
                   "estLiquidatePrice"):
            if pos.get(_k) is not None:
                try:
                    liq = float(pos.get(_k))
                    break
                except (TypeError, ValueError):
                    pass
        if not liq:
            self.log(f"{sym}: 未取到爆仓价，跳过爆仓线检查")
            return
        entry = float(plan.get("entry") or px)
        sl = plan["sl"]
        if plan["side"] == 3:
            buf = (liq - sl) / entry
        else:
            buf = (sl - liq) / entry
        plan["liq_price"] = liq
        plan["liq_buffer"] = buf
        if buf < MIN_LIQ_BUFFER_PCT:
            self.log(f"WARN {sym}: 止损离爆仓线仅 {buf:.2%}（<{MIN_LIQ_BUFFER_PCT:.2%}），"
                     f"止损 {sl} 爆仓价 {liq}")
            try:
                self.bark("止损离爆仓线太近",
                          f"{sym} 止损 {sl} 离爆仓价 {liq} 仅 {buf:.2%}，"
                          f"小于 {MIN_LIQ_BUFFER_PCT:.2%}，注意盯盘")
            except Exception:
                pass

    # -- 平仓 --
    def manage_exits(self):
        if not self.state["open_trades"]:
            return
        try:
            pos = open_positions_fast(self.ak, self.sk) \
                if not self.dry_run else {"success": True, "data": []}
        except Exception as e:  # noqa: BLE001
            self.log(f"open_positions error: {e}")
            return
        live = {}
        if pos.get("success"):
            for p in pos.get("data") or []:
                live[p.get("symbol")] = p
        now = time.time()
        for key, t in list(self.state["open_trades"].items()):
            p = live.get(t["mexc_symbol"])
            if p is None:
                # 交易所已无此持仓 = 止盈/止损已触发；只记账，绝不重发 TP/SL
                self._close_trade(key, t, "止盈/止损触发", None, now)
                continue
            if HOLD_SECONDS is not None and now - t["opened_at"] >= HOLD_SECONDS:
                self._close_trade(key, t, "满10分钟", p, now)

    def _close_trade(self, key, t, reason, pos, now):
        sym = t["symbol"]
        if self.dry_run:
            self.log(f"would close {sym} ({reason})")
            return
        close_price = None
        if pos is not None:
            side = 4 if t["side"] == 1 else 2
            price = pos.get("markPrice") or pos.get("fairPrice") or t["ref"]
            try:
                price = float(price)
            except Exception:
                price = t["ref"]
            det = self.detail_map.get(t["mexc_symbol"]) or {}
            price = round(price, int(det.get("priceScale", 0) or 0))
            r = submit_market(self.ak, self.sk, sym, side, t["vol"], price,
                              LEVERAGE, open_type=1)
            if not r.get("success"):
                self.log(f"close {sym} failed: {r.get('code')} {r.get('message')}")
                return
            close_price = price
        entry = t.get("entry", t["ref"])
        pnl = None
        if close_price is not None:
            d = 1 if t["side"] == 1 else -1
            det = self.detail_map.get(t["mexc_symbol"]) or {}
            size = float(det.get("contractSize", 0) or 0)
            pnl = d * (close_price - entry) * t["vol"] * size
        t.update({"status": "closed", "close_reason": reason,
                  "close_price": close_price, "pnl": pnl, "closed_at": now})
        self.state["closed_trades"].append(t)
        del self.state["open_trades"][key]
        if pnl is not None:
            self.state["day_realized"] = self.state.get("day_realized", 0.0) + pnl
        self._save_state()
        msg = f"{reason}，平仓价 {close_price}" if close_price else f"{reason}"
        if pnl is not None:
            msg += f"，盈亏约 {pnl:+.4f} USDT"
        self.bark(f"实盘平仓 · {sym}", msg)
        self.log(f"closed {sym} ({reason}) pnl={pnl}")

    # -- 主循环 --
    def run_once(self):
        try:
            mtime = os.stat(LEDGER).st_mtime_ns
        except Exception as e:  # noqa: BLE001
            self.log(f"ledger stat failed: {e}")
            return
        if mtime == self._last_ledger_mtime:
            return  # 账本没变：不读、不调 API（v1 每 10s 全量读一次）
        self._last_ledger_mtime = mtime
        try:
            round_t, cands = latest_top(n=5)
        except Exception as e:  # noqa: BLE001
            self.log(f"read ledger failed: {e}")
            return
        now_ms = int(time.time() * 1000)
        age = (now_ms - round_t) / 1000
        if round_t not in self.state["traded_rounds"] and age <= FRESH_SECONDS:
            self.log(f"fresh round t={round_t} age={age:.0f}s cands={len(cands)}")
            self.sync_capital()
            self.maybe_open(round_t, cands)
            if not self.dry_run:
                self.state["traded_rounds"].append(round_t)
                self._save_state()
        elif round_t in self.state["traded_rounds"]:
            pass
        elif getattr(self, "_last_stale_t", None) != round_t:
            self.log(f"round t={round_t} age={age:.0f}s stale, skip")
            self._last_stale_t = round_t
        if time.time() - self._last_exit_check >= 10:
            self._last_exit_check = time.time()
            self.manage_exits()

    def run(self, once=False):
        self.sync_capital()
        self.log(f"trader v2 started dry_run={self.dry_run} "
                 f"capital={self.capital:.4f} leverage={LEVERAGE}x "
                 f"open={len(self.state['open_trades'])} "
                 f"TP={TP_MARGIN_PCT:.0%} SL={SL_MARGIN_PCT:.0%}")
        while True:
            try:
                self.run_once()
            except Exception:
                self.log("loop error:\n" + traceback.format_exc())
            if once:
                return
            time.sleep(LOOP_SECONDS)


def _check_single_flight(dry_run):
    """防双进程：v1 还在跑或 v2 pidfile 的进程还活着就拒绝启动。"""
    try:
        out = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True,
                             text=True, timeout=5).stdout
    except Exception:
        out = ""
    mine = os.getpid()
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if pid == mine or "grep" in parts[1]:
            continue
        if "mexc_trader_daemon.py" in parts[1] and \
                "mexc_trader_daemon_v2" not in parts[1]:
            return "v1 守护进程仍在跑（PID %d），先停 v1 再起 v2" % pid
    if os.path.exists(PIDFILE):
        try:
            old = int(open(PIDFILE).read().strip())
            os.kill(old, 0)
            return "v2 已在跑（PID %d），拒绝重复启动" % old
        except Exception:
            pass
    return None


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    once = "--once" in sys.argv
    block = _check_single_flight(dry)
    if block and not dry:
        print("refuse to start: " + block, file=sys.stderr)
        sys.exit(3)
    if block and dry:
        print("note (dry-run, not blocking): " + block, file=sys.stderr)
    ak = sk = None
    if not dry:
        raw = ""
        for _ in range(5):
            try:
                raw = sys.stdin.read().strip()
            except Exception:
                raw = ""
            if raw:
                break
            time.sleep(2)
        if not raw:
            print("no keys on stdin", file=sys.stderr)
            sys.exit(2)
        d = json.loads(raw)
        ak, sk = d["access_key"], d["secret_key"]
    trader = Trader(ak, sk, dry_run=dry)
    if not dry:
        try:
            with open(PIDFILE, "w") as f:
                f.write(str(os.getpid()))
        except Exception:
            pass
    try:
        trader.run(once=once)
    finally:
        if not dry:
            try:
                os.unlink(PIDFILE)
            except Exception:
                pass
