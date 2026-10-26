"""Close all open MEXC positions (manual close-all). Usage: python3 close_all.py (keys via stdin JSON)."""
import sys, json, time
sys.path.insert(0, '.')
from mexc_trade_client import open_positions, submit_market, get_contract_detail
from mexc_trader_daemon import load_state, save_state, bark, log

def main():
    keys = json.load(sys.stdin)
    ak, sk = keys["access_key"], keys["secret_key"]
    st = load_state()
    live = {p["symbol"]: p for p in open_positions(ak, sk).get("data", [])}
    detail = get_contract_detail()
    dmap = {d["symbol"]: d for d in detail.get("data", [])}
    total_pnl = 0.0
    for key, t in list(st["open_trades"].items()):
        ms = t["mexc_symbol"]
        p = live.get(ms)
        if p is None:
            log(f"close-all {t['symbol']}: not live, dropping from state")
            st["closed_trades"].append({**t, "status": "closed", "close_reason": "手动全平（仓位已不在）"})
            del st["open_trades"][key]
            continue
        side = 4 if t["side"] == 1 else 2
        price = float(p.get("markPrice") or p.get("fairPrice") or t["ref"])
        det = dmap.get(ms) or {}
        price = round(price, int(det.get("priceScale", 0) or 0))
        r = submit_market(ak, sk, t["symbol"], side, t["vol"], price, t.get("leverage", 35), open_type=1)
        if not r.get("success"):
            log(f"close-all {t['symbol']} FAILED: {r.get('code')} {r.get('message')}")
            continue
        entry = t.get("entry", t["ref"])
        d = 1 if t["side"] == 1 else -1
        size = float(det.get("contractSize", 0) or 0)
        pnl = d * (price - entry) * t["vol"] * size
        total_pnl += pnl
        t.update({"status": "closed", "close_reason": "手动全平", "close_price": price, "pnl": pnl, "closed_at": int(time.time())})
        st["closed_trades"].append(t)
        del st["open_trades"][key]
        st["day_realized"] = st.get("day_realized", 0.0) + pnl
        log(f"closed {t['symbol']} pnl={pnl:+.4f}")
        bark(f"实盘平仓 · {t['symbol']}", f"手动全平，平仓价 {price}，盈亏约 {pnl:+.4f} USDT")
    save_state(st)
    print(json.dumps({"closed_pnl_total": round(total_pnl, 4), "open_remaining": len(st["open_trades"])}))

if __name__ == "__main__":
    main()
