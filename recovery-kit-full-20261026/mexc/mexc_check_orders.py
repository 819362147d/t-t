#!/usr/bin/env python3
"""只读核查：ticker 价 + 未成交委托。密钥只从 stdin 读，不落盘、不打印。"""
import json
import sys

sys.path.insert(0, "/home/hatch/workspace/goals/tradingview-binance-indicator-monitoring-with-wecom-alerts/hidden_files")
from mexc_trade_client import get_ticker, list_open_orders, open_positions

if __name__ == "__main__":
    d = json.loads(sys.stdin.read().strip())
    ak, sk = d["access_key"], d["secret_key"]
    print("ticker A_USDT:", json.dumps(get_ticker("AUSDT"), ensure_ascii=False))
    print("open_orders:", json.dumps(list_open_orders(ak, sk), ensure_ascii=False)[:800])
    print("positions:", json.dumps(open_positions(ak, sk), ensure_ascii=False)[:600])
