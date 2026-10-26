#!/usr/bin/env python3
"""对比两种 MEXC 合约 API 签名方式，打到只读的 account/assets 端点看哪种通过。
密钥只从 stdin 读，不落盘、不打印。只输出 code/message，不输出敏感内容。
A: 头 ApiKey/Request-Time/Signature，sig=HMAC(secret, accessKey+ts+paramStr)
B: 头 X-MEXC-APIKEY，query 带 timestamp+signature，sig=HMAC(secret, queryString)
"""
import hashlib
import hmac
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "https://api.mexc.com"
PATH = "/api/v1/private/account/assets"


def call(url, headers):
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"success": False, "code": e.code}


def style_a(ak, sk):
    ts = str(int(time.time() * 1000))
    sig = hmac.new(sk.encode(), (ak + ts + "").encode(), hashlib.sha256).hexdigest()
    return call(BASE + PATH, {"ApiKey": ak, "Request-Time": ts, "Signature": sig,
                              "Content-Type": "application/json"})


def style_b(ak, sk):
    ts = str(int(time.time() * 1000))
    qs = f"timestamp={ts}"
    sig = hmac.new(sk.encode(), qs.encode(), hashlib.sha256).hexdigest()
    return call(f"{BASE}{PATH}?{qs}&signature={sig}",
                {"X-MEXC-APIKEY": ak, "Content-Type": "application/json"})


if __name__ == "__main__":
    d = json.loads(sys.stdin.read().strip())
    ak, sk = d["access_key"], d["secret_key"]
    for name, fn in (("A", style_a), ("B", style_b)):
        r = fn(ak, sk)
        print(name, "->", json.dumps({k: r.get(k) for k in ("success", "code", "message")},
                                     ensure_ascii=False))
