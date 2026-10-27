#!/usr/bin/env python3
"""MEXC USDT 本位永续合约交易客户端（官方 Open-API https://api.mexc.com）。
鉴权：HMAC-SHA256(secret, accessKey + timestamp + parameterString)，
请求头 ApiKey / Request-Time / Signature。GET 参数字典序拼接，POST 用 JSON 体字符串。
密钥只通过函数参数或标准输入传入：不读文件、不读环境变量、不打印、不记日志。
用法：
  python3 mexc_trade_client.py public [SYM]   # 公开接口：查合约详情（无鉴权）
  python3 mexc_trade_client.py verify          # 从 stdin 读 {"access_key":..,"secret_key":..}，
                                               # 只读调 account/assets + tiered_fee_rate 验证签名与权限
注意：order/submit 与 position/leverage 的参数表以官方文档为准，首次实盘下单前再核对一次；
仓位模式切换 path 未完全核实（疑似 /api/v1/private/position/margin_type），下单默认逐仓。
"""
import hashlib
import hmac
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "https://api.mexc.com"


def _sign(secret, access_key, timestamp, param_str):
    msg = (access_key + timestamp + param_str).encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def _request(method, path, access_key=None, secret_key=None, params=None, body=None,
             retries=3):
    """602 签名失败间歇出现（实测同一调用有时 0 有时 602，疑似服务端波动）：
    私有调用失败时用新时间戳重试，最多 3 次。"""
    params = params or {}
    last = {"success": False, "code": -1, "msg": "no attempt"}
    for _ in range(retries):
        if method == "GET" and params:
            qs = "&".join(f"{k}={params[k]}" for k in sorted(params) if params[k] is not None)
            param_str = qs
            url = BASE + path + ("?" + qs if qs else "")
            data = None
        elif method == "GET":
            param_str = ""
            url = BASE + path
            data = None
        else:
            param_str = json.dumps(body, separators=(",", ":")) if body else ""
            url = BASE + path
            data = param_str.encode() if param_str else None
        headers = {"Content-Type": "application/json"}
        if access_key and secret_key:
            ts = str(int(time.time() * 1000))
            headers["ApiKey"] = access_key
            headers["Request-Time"] = ts
            headers["Signature"] = _sign(secret_key, access_key, ts, param_str)
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                last = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            try:
                last = json.loads(e.read().decode())
            except Exception:
                last = {"success": False, "code": e.code, "msg": f"HTTP {e.code}"}
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = {"success": False, "code": -1, "msg": f"transport: {e}"}
            time.sleep(2)
            continue
        if last.get("success"):
            return last
        # 602 签名波动和 510 限频都值得退避重试，而不是立刻判失败；2026-10-27 戒 bug 时加入 510
        if last.get("code") in (602, 510):
            time.sleep(1 if last.get("code") == 602 else 3)
            continue
        return last
    return last


def mexc_symbol(sym):
    """BTCUSDT -> BTC_USDT；1000BONKUSDT -> 1000BONK_USDT。"""
    if sym.endswith("USDT"):
        return sym[:-4] + "_USDT"
    return sym


def get_contract_detail(symbol=None):
    r = _request("GET", "/api/v1/contract/detail")
    if not r.get("success"):
        return r
    data = r.get("data") or []
    if symbol:
        ms = mexc_symbol(symbol)
        for d in data:
            if d.get("symbol") == ms:
                return {"success": True, "code": 0, "data": d}
        return {"success": False, "code": -1, "msg": f"symbol not found: {ms}"}
    return r


def get_asset(ak, sk, currency="USDT"):
    """查单个币种余额。注意：复数 /account/assets 在官方 Open-API 上报 602，
    用单币种 /account/asset/{currency}（已实测通过）。"""
    return _request("GET", f"/api/v1/private/account/asset/{currency}", ak, sk)


def get_fee_tier(ak, sk):
    return _request("GET", "/api/v1/private/account/tiered_fee_rate", ak, sk)


def open_positions(ak, sk, symbol=None):
    params = {}
    if symbol:
        params["symbol"] = mexc_symbol(symbol)
    return _request("GET", "/api/v1/private/position/open_positions", ak, sk, params=params)


def set_leverage(ak, sk, symbol, leverage, open_type=1):
    body = {"symbol": mexc_symbol(symbol), "leverage": leverage, "openType": open_type}
    return _request("POST", "/api/v1/private/position/leverage", ak, sk, body=body)


def submit_market(ak, sk, symbol, side, vol, price, leverage, open_type=1,
                  stop_loss=None, take_profit=None, external_oid=None):
    """side: 1=开多，3=开空。price：市价单也必填，传当前市价。vol：张数。"""
    body = {
        "symbol": mexc_symbol(symbol),
        "price": price,
        "vol": vol,
        "side": side,
        "type": 5,
        "openType": open_type,
        "leverage": leverage,
    }
    if stop_loss is not None:
        body["stopLossPrice"] = stop_loss
    if take_profit is not None:
        body["takeProfitPrice"] = take_profit
    if external_oid:
        body["externalOid"] = external_oid
    return _request("POST", "/api/v1/private/order/submit", ak, sk, body=body)


def submit_limit(ak, sk, symbol, side, vol, price, leverage, open_type=1,
                 stop_loss=None, take_profit=None, external_oid=None):
    """限价单：side 1=开多，3=开空；type=1。price 贴着 tick 价（做多取卖一、做空取买一）。
    未成交时由调用方查单/撤单重开。"""
    body = {
        "symbol": mexc_symbol(symbol),
        "price": price,
        "vol": vol,
        "side": side,
        "type": 1,
        "openType": open_type,
        "leverage": leverage,
    }
    if stop_loss is not None:
        body["stopLossPrice"] = stop_loss
    if take_profit is not None:
        body["takeProfitPrice"] = take_profit
    if external_oid:
        body["externalOid"] = external_oid
    return _request("POST", "/api/v1/private/order/submit", ak, sk, body=body)


def get_ticker(symbol):
    """公开接口：最新行情，含 lastPrice / bid1 / ask1。无鉴权。"""
    return _request("GET", "/api/v1/contract/ticker",
                    params={"symbol": mexc_symbol(symbol)})


def get_order(ak, sk, order_id):
    return _request("GET", f"/api/v1/private/order/get/{order_id}", ak, sk)


def cancel_order(ak, sk, order_id):
    """撤单。order_id 为下单返回的订单号（int/str 均可）。"""
    body = {"orderId": str(order_id)}
    return _request("POST", "/api/v1/private/order/cancel", ak, sk, body=body)


def list_open_orders(ak, sk, symbol=None):
    """查未成交委托。symbol 为空时查全部。"""
    params = {}
    if symbol:
        params["symbol"] = mexc_symbol(symbol)
    return _request("GET", "/api/v1/private/order/list/open_orders", ak, sk,
                    params=params)


def calc_vol(detail, notional_usdt, price):
    """按名义价值算张数：vol = 名义 / (contractSize × price)，向下取整，最小 1 张。"""
    size = float(detail.get("contractSize", 0) or 0)
    if not size or not price:
        return 1
    vol = int(notional_usdt / (size * price))
    return max(1, vol)


def _read_keys_from_stdin():
    raw = sys.stdin.read().strip()
    if not raw:
        print("no keys on stdin", file=sys.stderr)
        sys.exit(2)
    d = json.loads(raw)
    return d["access_key"], d["secret_key"]


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "public":
        sym = sys.argv[2] if len(sys.argv) > 2 else None
        print(json.dumps(get_contract_detail(sym), ensure_ascii=False))
    elif cmd == "verify":
        ak, sk = _read_keys_from_stdin()
        asset = get_asset(ak, sk, "USDT")
        tier = get_fee_tier(ak, sk)
        pos = open_positions(ak, sk)
        print(json.dumps({"usdt_asset": asset, "fee_tier": tier,
                          "open_positions": pos}, ensure_ascii=False))
    else:
        print("usage: mexc_trade_client.py [public [SYM] | verify]", file=sys.stderr)
        sys.exit(2)
