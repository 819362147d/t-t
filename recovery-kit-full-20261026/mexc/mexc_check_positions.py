#!/usr/bin/env python3
"""只读核查抹茶持仓与 USDT 余额。密钥只从 stdin 读，不落盘、不打印。"""
import json
import sys

sys.path.insert(0, "/home/hatch/workspace/goals/tradingview-binance-indicator-monitoring-with-wecom-alerts/hidden_files")
from mexc_trade_client import get_asset, open_positions

if __name__ == "__main__":
    d = json.loads(sys.stdin.read().strip())
    ak, sk = d["access_key"], d["secret_key"]
    print("positions:", json.dumps(open_positions(ak, sk), ensure_ascii=False))
    print("asset:", json.dumps(get_asset(ak, sk, "USDT"), ensure_ascii=False))
