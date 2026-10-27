#!/usr/bin/env python3
"""同一只读端点连打 5 次，看 602 是否间歇出现。密钥只从 stdin 读。"""
import json
import sys

sys.path.insert(0, "/home/hatch/workspace/goals/tradingview-binance-indicator-monitoring-with-wecom-alerts/hidden_files")
from mexc_trade_client import get_asset, open_positions

if __name__ == "__main__":
    d = json.loads(sys.stdin.read().strip())
    ak, sk = d["access_key"], d["secret_key"]
    for i in range(5):
        r = get_asset(ak, sk, "USDT")
        print(f"asset try {i+1} -> success={r.get('success')} code={r.get('code')} msg={r.get('message')}")
    r = open_positions(ak, sk)
    print("positions ->", json.dumps(r, ensure_ascii=False)[:200])
