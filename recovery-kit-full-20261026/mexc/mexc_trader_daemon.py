#!/usr/bin/env python3
"""MEXC 实盘交易守护进程：按多币种 10 分钟预报每轮前三开仓。
密钥只从 stdin 读一次进内存，不落盘、不进日志、不进环境变量。
每轮（5 分钟）取账本最新一轮候选，按 |prob-0.5| 取前三（取前 5 备选，跳过抹茶未上架/
杠杆不支持的币），只做新鲜信号（轮次年龄 <=4 分钟），同币不重复开仓。
参数：本金=账户 USDT 可用余额（实测 2.0575），杠杆 35x 逐仓（用户 2026-10-26 20:39 定 35x），单笔保证金 5% 本金；
止盈 +50% / 止损 -2.4%（下单时附带；+50% 基本碰不到，实际等于不要止盈；-2.4% 刚好在 35x 强平约 2.5% 之内，止损先触发），不限时（只靠止盈/止损平仓，不到时平仓）；单日累计亏损达本金 3% 当天停手。
开仓用限价单贴 tick 价（做多卖一/做空买一），未成交撤单重开最多 3 次；资金耗尽时等平仓释放后再开，不报缺资金错。
平仓经 Bark 推送（开仓 Bark 已省略）；状态写 mexc_trader_state.json（无密钥），日志 mexc_trader.log。
用法：python3 mexc_trader_daemon.py [--dry-run] [--once]
  启动时从 stdin 读 {"access_key":..,"secret_key":..}；--dry-run 只选信号不下单。
"""
import json
import os
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mexc_trade_client import (calc_vol, cancel_order, get_asset,
                               get_contract_detail, get_ticker, mexc_symbol,
                               open_positions, submit_limit)
from mexc_top3_signals import latest_top

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "mexc_trader_state.json")
LOG_FILE = os.path.join(HERE, "mexc_trader.log")
BARK = "/home/hatch/workspace/custom-connectors/custom.bark-push/bin/bark_send.py"

LEVERAGE = 50  # 用户 2026-10-26 21:25 由 80x 降回 50x（80x 下小币最高杠杆不够，全跳过）；50x 下强平约 2%
MARGIN_PCT = 0.05
TP_PCT = 0.01  # 用户 2026-10-26 21:20 定止盈 50%（保证金口径）；50x 下 50% 保证金 = 1% 价格（80x 时为 0.625%，随杠杆换算）
SL_PCT = 0.015  # 用户 2026-10-26 21:26 改为 1.5%（价格口径，50x 下 = 75% 保证金，离强平约 2% 有 0.5% 余量）
HOLD_SECONDS = None  # 不限时：只靠止盈/止损，不到时平仓
DAILY_STOP_PCT = 0.03
FRESH_SECONDS = 240
LOOP_SECONDS = 10  # 用户 2026-10-26 20:54 批准提速：30秒→10秒扫一次账本，信号到下单快约 20 秒
MAX_MARGIN_USE_PCT = 0.50
LIMIT_MAX_ATTEMPTS = 3   # 限价单未成交时撤单重开的最大次数
LIMIT_WAIT_SECONDS = 10  # 挂限价单后等成交的秒数


def _is_insufficient_funds(r):
    """判断下单失败是否因资金不足（保证金/余额不够）。
    抹茶资金不足的错误码未完全核实，以返回 message 的关键词为准。"""
    msg = str(r.get("message") or r.get("msg") or "").lower()
    for kw in ("insufficient", "not enough", "资金不足", "余额不足", "保证金不足"):
        if kw in msg:
            return True
    return False


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + msg
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def bark(title, body):
    try:
        subprocess.run(["python3", BARK, title, body], timeout=15,
                       capture_output=True)
    except Exception as e:
        log(f"bark failed: {e}")


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"traded_rounds": [], "open_trades": {}, "closed_trades": [],
            "day": time.strftime("%Y-%m-%d"), "day_start_equity": None,
            "day_realized": 0.0}


def save_state(s):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(s, f, ensure_ascii=False, indent=1)
    except Exception as e:
        log(f"save state failed: {e}")


class Trader:
    def __init__(self, ak, sk, dry_run=False):
        self.ak, self.sk = ak, sk
        self.dry_run = dry_run
        self.state = load_state()
        self.detail_map = {}
        self.capital = 0.0
        self._refresh_details()

    def _refresh_details(self):
        r = get_contract_detail()
        if r.get("success"):
            self.detail_map = {d["symbol"]: d for d in r.get("data") or []}
            log(f"contract detail loaded: {len(self.detail_map)} symbols")
        else:
            log(f"contract detail failed: {r.get('code')} {r.get('message')}")

    def detail_for(self, sym):
        ms = mexc_symbol(sym)
        d = self.detail_map.get(ms)
        if d is None:
            self._refresh_details()
            d = self.detail_map.get(ms)
        return ms, d

    def sync_capital(self):
        if self.dry_run:
            self.capital = 2.0575
            return
        r = get_asset(self.ak, self.sk, "USDT")
        if r.get("success"):
            d = r["data"]
            # 本金口径用权益（总账户价值），不用可用余额：可用余额会随保证金占用而缩水，
            # 导致 50% 总预算上限掉到已用保证金之下、再也开不了新单（2026-10-26 20:45 轮教训：
            # 已用 0.7542 > 50%×可用 1.2884=0.6442，4 个候选全被跳过）。
            self.capital = float(d.get("equity", 0) or 0)
            eq = float(d.get("equity", 0) or 0)
            today = time.strftime("%Y-%m-%d")
            if self.state["day"] != today:
                self.state.update({"day": today, "day_start_equity": eq,
                                   "day_realized": 0.0})
                save_state(self.state)
            elif self.state.get("day_start_equity") is None:
                self.state["day_start_equity"] = eq
                save_state(self.state)
            log(f"capital equity={self.capital:.4f} available={float(d.get('availableBalance', 0) or 0):.4f}")
        else:
            log(f"get_asset failed: {r.get('code')} {r.get('message')}")

    def margin_in_use(self):
        used = 0.0
        for t in self.state["open_trades"].values():
            used += t.get("margin", 0.0)
        return used

    def daily_stopped(self):
        return self.state.get("day_realized", 0.0) <= -DAILY_STOP_PCT * self.capital

    def maybe_open(self, round_t, cands):
        if self.daily_stopped():
            log("daily stop hit, skip opening")
            return
        # 阶段一：主线程做纯计算，定出最多 3 个开仓计划（含保证金预算）
        held = {t["symbol"] for t in self.state["open_trades"].values()}
        plans = []
        planned_margin = 0.0
        skipped_margin = 0
        for c in cands:
            if len(plans) >= 3:
                break
            sym, direction, prob, ref = c["sym"], c["dir"], c["prob"], c["close"]
            if sym in held:
                log(f"skip {sym}: already held")
                continue
            ms, det = self.detail_for(sym)
            if det is None:
                log(f"skip {sym}: not listed on MEXC")
                continue
            if LEVERAGE > int(det.get("maxLeverage", 0) or 0):
                log(f"skip {sym}: maxLeverage {det.get('maxLeverage')} < {LEVERAGE}")
                continue
            price_scale = int(det.get("priceScale", 0) or 0)
            ref_r = round(float(ref), price_scale)
            notional = MARGIN_PCT * self.capital * LEVERAGE
            vol = calc_vol(det, notional, ref_r)
            margin = vol * float(det["contractSize"]) * ref_r / LEVERAGE
            if self.margin_in_use() + planned_margin + margin > MAX_MARGIN_USE_PCT * self.capital:
                log(f"skip {sym}: margin budget exceeded")
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
                          "oid": f"muse-{round_t}-{sym}"})
            planned_margin += margin
            log(f"plan {sym} {'多' if side == 1 else '空'} prob={prob} ref={ref_r} "
                f"vol={vol} margin~{margin:.4f} tp={tp} sl={sl}")
        if not plans:
            # 资金耗尽：不等报错，等平仓释放保证金后再开（用户 2026-10-26 20:24 要求）
            if skipped_margin > 0:
                log(f"资金已用完（{skipped_margin} 个信号因保证金不足跳过），等平仓释放后再开")
            return
        if self.dry_run:
            log(f"dry-run: would open {len(plans)} positions")
            return
        # 阶段二：3 个开仓并行执行（各币独立，互不干扰）
        # 限价单贴着 tick 价：做多卖一、做空买一；未成交就撤单重开，最多 3 次
        def _execute(plan):
            sym = plan["symbol"]
            price_scale = plan["price_scale"]
            filled = False
            for attempt in range(1, LIMIT_MAX_ATTEMPTS + 1):
                # 本轮已过新鲜期就不再试
                if (time.time() * 1000 - plan["round_t"]) / 1000 > FRESH_SECONDS:
                    plan["stale"] = True
                    break
                # 取最新 tick 价
                tk = get_ticker(sym)
                tkdata = (tk.get("data") or {}) if tk.get("success") else {}
                last = tkdata.get("lastPrice") or plan["ref"]
                px_raw = tkdata.get("ask1") if plan["side"] == 1 else tkdata.get("bid1")
                try:
                    px = round(float(px_raw or last), price_scale)
                except Exception:
                    px = plan["ref"]
                # 止盈止损按最新价重算
                if plan["side"] == 1:
                    tp = round(px * (1 + TP_PCT), price_scale)
                    sl = round(px * (1 - SL_PCT), price_scale)
                else:
                    tp = round(px * (1 - TP_PCT), price_scale)
                    sl = round(px * (1 + SL_PCT), price_scale)
                oid = f"{plan['oid']}-a{attempt}"
                r = submit_limit(self.ak, self.sk, sym, plan["side"], plan["vol"], px,
                                 LEVERAGE, open_type=1, stop_loss=sl, take_profit=tp,
                                 external_oid=oid)
                if not r.get("success"):
                    # 资金不足：不等了，等平仓释放后再开
                    if _is_insufficient_funds(r):
                        plan["funds_exhausted"] = True
                        break
                    # TP 挂得太远可能被交易所拒，退回只挂止损再试一次
                    plan["tp_retry"] = f"{r.get('code')} {r.get('message')}"
                    r = submit_limit(self.ak, self.sk, sym, plan["side"], plan["vol"], px,
                                     LEVERAGE, open_type=1, stop_loss=sl, external_oid=oid)
                    if not r.get("success"):
                        if _is_insufficient_funds(r):
                            plan["funds_exhausted"] = True
                            break
                        plan["submit_error"] = f"{r.get('code')} {r.get('message')}"
                        break
                # 下单返回 data 可能是订单号本身（int/str），也可能是含 orderId 的 dict
                data = r.get("data")
                order_id = data.get("orderId") if isinstance(data, dict) else data
                # 等一会看是否成交
                time.sleep(LIMIT_WAIT_SECONDS)
                # 查持仓确认是否成交
                try:
                    pp = open_positions(self.ak, self.sk, sym)
                except Exception:
                    pp = {"success": False}
                pos = None
                if pp.get("success"):
                    for p in pp.get("data") or []:
                        if p.get("symbol") == plan["mexc_symbol"]:
                            pos = p
                            break
                if pos is not None:
                    filled = True
                    plan["entry"] = pos.get("openAvgPrice") or px
                    plan["margin"] = pos.get("im") or plan["margin"]
                    plan["order_id"] = order_id
                    plan["tp"] = tp
                    plan["sl"] = sl
                    plan["pos_check"] = json.dumps(pp, ensure_ascii=False)[:300]
                    break
                # 未成交：撤单重开
                try:
                    cancel_order(self.ak, self.sk, order_id)
                except Exception as e:
                    plan["cancel_error"] = str(e)
                log(f"cancel {sym} attempt {attempt} (not filled at {px}), retry at tick")
            plan["filled"] = filled
            return plan
        with ThreadPoolExecutor(max_workers=3) as ex:
            results = list(ex.map(_execute, plans))
        # 阶段三：主线程统一落账本、记状态
        for plan in results:
            sym = plan["symbol"]
            if plan.get("funds_exhausted"):
                log(f"{sym}: 资金不足，等平仓释放后再开")
                continue
            if plan.get("stale"):
                log(f"{sym}: 本轮已过新鲜期，停止重试")
                continue
            if plan.get("tp_retry"):
                log(f"submit {sym} with TP failed: {plan['tp_retry']}, retried without TP")
            if plan.get("submit_error"):
                log(f"submit {sym} failed: {plan['submit_error']}")
                continue
            if not plan.get("filled"):
                log(f"submit {sym}: {LIMIT_MAX_ATTEMPTS} 次限价均未成交，放弃本轮")
                continue
            trade = {"symbol": sym, "mexc_symbol": plan["mexc_symbol"],
                     "side": plan["side"], "dir": plan["dir"],
                     "prob": plan["prob"], "ref": plan["ref"], "vol": plan["vol"],
                     "margin": plan["margin"], "tp": plan["tp"], "sl": plan["sl"],
                     "order_id": plan["order_id"],
                     "entry": plan.get("entry") or plan["ref"],
                     "opened_at": time.time(),
                     "round_t": round_t, "status": "open"}
            self.state["open_trades"][plan["oid"]] = trade
            save_state(self.state)
            # 开仓 Bark 已按用户要求（2026-10-26 20:20）省略，省推送；平仓通知仍保留
            log(f"opened {sym} order={plan['order_id']} entry={plan.get('entry')}")
            log(f"position check {sym}: {plan.get('pos_check')}")

    def manage_exits(self):
        if not self.state["open_trades"]:
            return
        try:
            pos = open_positions(self.ak, self.sk) if not self.dry_run else {"success": True, "data": []}
        except Exception as e:
            log(f"open_positions error: {e}")
            return
        live = {}
        if pos.get("success"):
            for p in pos.get("data") or []:
                live[p.get("symbol")] = p
        now = time.time()
        for key, t in list(self.state["open_trades"].items()):
            ms = t["mexc_symbol"]
            p = live.get(ms)
            if p is None:
                self._close_trade(key, t, "止盈/止损触发", None, now)
                continue
            if HOLD_SECONDS is not None and now - t["opened_at"] >= HOLD_SECONDS:
                self._close_trade(key, t, "满10分钟", p, now)

    def _close_trade(self, key, t, reason, pos, now):
        sym = t["symbol"]
        if self.dry_run:
            log(f"would close {sym} ({reason})")
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
            body_extra = {}
            pid = pos.get("positionId")
            r = submit_market(self.ak, self.sk, sym, side, t["vol"], price,
                              LEVERAGE, open_type=1)
            if not r.get("success"):
                log(f"close {sym} failed: {r.get('code')} {r.get('message')}")
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
        save_state(self.state)
        msg = f"{reason}，平仓价 {close_price}" if close_price else f"{reason}"
        if pnl is not None:
            msg += f"，盈亏约 {pnl:+.4f} USDT"
        bark(f"实盘平仓 · {sym}", msg)
        log(f"closed {sym} ({reason}) pnl={pnl}")

    def run_once(self):
        try:
            round_t, cands = latest_top(n=5)
        except Exception as e:
            log(f"read ledger failed: {e}")
            return
        now_ms = int(time.time() * 1000)
        age = (now_ms - round_t) / 1000
        if round_t not in self.state["traded_rounds"] and age <= FRESH_SECONDS:
            log(f"fresh round t={round_t} age={age:.0f}s cands={len(cands)}")
            self.maybe_open(round_t, cands)
            self.state["traded_rounds"].append(round_t)
            save_state(self.state)
        elif round_t in self.state["traded_rounds"]:
            pass
        elif getattr(self, "_last_stale_t", None) != round_t:
            log(f"round t={round_t} age={age:.0f}s stale, skip")
            self._last_stale_t = round_t
        self.manage_exits()

    def run(self, once=False):
        self.sync_capital()
        log(f"trader started dry_run={self.dry_run} capital={self.capital:.4f} "
            f"leverage={LEVERAGE}x open={len(self.state['open_trades'])}")
        while True:
            try:
                self.run_once()
            except Exception:
                log("loop error:\n" + traceback.format_exc())
            if once:
                return
            time.sleep(LOOP_SECONDS)


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    once = "--once" in sys.argv
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
    Trader(ak, sk, dry_run=dry).run(once=once)
