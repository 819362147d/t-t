#!/usr/bin/env python3
"""用 A 式签名打几个不同只读端点，定位是签名问题还是 assets 路径问题。
只输出 code/success，不输出敏感内容。密钥只从 stdin 读。
"""
import hashlib
import hmac
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "https://api.mexc.com"


def call(path, ak, sk, params=None):
    params = params or {}
    qs = "&".join(f"{k}={params[k]}" for k in sorted(params) if params[k] is not None)
    ts = str(int(time.time() * 1000))
    sig = hmac.new(sk.encode(), (ak + ts + qs).encode(), hashlib.sha256).hexdigest()
    url = BASE + path + ("?" + qs if qs else "")
    req = urllib.request.Request(url, headers={"ApiKey": ak, "Request-Time": ts,
                                              "Signature": sig,
                                              "Content-Type": "application/json"},
                                 method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"success": False, "code": e.code}


if __name__ == "__main__":
    d = json.loads(sys.stdin.read().strip())
    ak, sk = d["access_key"], d["secret_key"]
    for label, path, params in (
        ("open_positions", "/api/v1/private/position/open_positions", None),
        ("asset_USDT", "/api/v1/private/account/asset/USDT", None),
        ("risk_limit", "/api/v1/private/account/risk_limit", None),
    ):
        r = call(path, ak, sk, params)
        print(label, "->", json.dumps({k: r.get(k) for k in ("success", "code", "message")},
                                      ensure_ascii=False))
