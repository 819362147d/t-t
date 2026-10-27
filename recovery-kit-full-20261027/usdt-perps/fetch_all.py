import json, urllib.request

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

out = {}

# --- Binance USDT-margined perpetuals (vision mirror first, fapi fallback) ---
bn = None
for base in ["https://data-api.binance.vision", "https://fapi.binance.com"]:
    try:
        bn = get(base + "/fapi/v1/exchangeInfo")
        print("binance ok via", base)
        break
    except Exception as e:
        print("binance fail via", base, e)
bn_syms = []
if bn:
    for s in bn["symbols"]:
        if (s.get("contractType") == "PERPETUAL" and s.get("quoteAsset") == "USDT"
                and s.get("status") == "TRADING"):
            bn_syms.append({"exchange": "binance", "symbol": s["symbol"], "base": s["baseAsset"]})
out["binance"] = bn_syms

# --- OKX USDT-margined swaps ---
okx = get("https://www.okx.com/api/v5/public/instruments?instType=SWAP")
ok_syms = []
for s in okx["data"]:
    if s.get("settleCcy") == "USDT" and s.get("state") == "live":
        ok_syms.append({"exchange": "okx", "symbol": s["instId"], "base": s["ctValCcy"] if s.get("ctValCcy") else s["instId"].split("-")[0]})
out["okx"] = ok_syms
print("okx ok, count", len(ok_syms))

# --- MEXC USDT-margined contracts ---
mx = get("https://contract.mexc.com/api/v1/contract/detail")
mx_syms = []
for s in mx["data"]:
    if s.get("quoteCoin") == "USDT" and s.get("state") == 1:
        mx_syms.append({"exchange": "mexc", "symbol": s["symbol"], "base": s["baseCoin"]})
out["mexc"] = mx_syms
print("mexc ok, count", len(mx_syms))

for k, v in out.items():
    print(k, len(v))
    with open(f"/home/hatch/workspace/usdt-perps/{k}_usdt_perps.json", "w") as f:
        json.dump(v, f, indent=1)

# combined by base
from collections import defaultdict
bases = defaultdict(set)
for k, v in out.items():
    for s in v:
        bases[s["base"]].add(k)
rows = sorted(bases.items())
with open("/home/hatch/workspace/usdt-perps/combined_by_base.csv", "w") as f:
    f.write("base,binance,okx,mexc\n")
    for b, ex in rows:
        f.write(f"{b},{int('binance' in ex)},{int('okx' in ex)},{int('mexc' in ex)}\n")
print("unique bases:", len(rows))
print("on all 3:", sum(1 for _, ex in rows if len(ex) == 3))
print("only binance:", sum(1 for _, ex in rows if ex == {"binance"}))
print("only okx:", sum(1 for _, ex in rows if ex == {"okx"}))
print("only mexc:", sum(1 for _, ex in rows if ex == {"mexc"}))
