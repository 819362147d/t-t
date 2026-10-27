"""从 MEXC 公开合约明细生成 Stock/TradFi/PreIPO 分组（USDT 结算、state=0）。
输出 groups.json：每组 symbol 列表、杠杆桶、止盈止损价格口径。
价格口径：TP 保证金 85% / 杠杆，SL 保证金 25% / 杠杆，与现 v1 一致。
"""
import urllib.request, json, collections, sys
sys.path.insert(0, "/home/hatch/workspace/t-t-recovery/recovery-kit-full-20261027/mexc")
from mexc_trade_client import get_contract_detail
r=get_contract_detail()
assert r.get("success"), r
data=r["data"]
def has(d,p): return p in (d.get("conceptPlate") or [])
union=[d for d in data if d.get("settleCoin")=="USDT" and d.get("state")==0 and (has(d,"mc-trade-zone-Stock") or has(d,"mc-trade-zone-tradfi") or has(d,"mc-trade-zone-preipo"))]
def bucket(d):
    lev=int(d.get("maxLeverage") or 0)
    if lev>=50: return "G50"
    if lev==20: return "G20"
    return "G_other"
groups={}
for b in ["G50","G20","G_other"]:
    sub=[d for d in union if bucket(d)==b]
    groups[b]={"count":len(sub),
               "lev_dist":dict(collections.Counter(int(d.get("maxLeverage") or 0) for d in sub)),
               "symbols":[d["symbol"] for d in sorted(sub, key=lambda x: x["symbol"])]}
    lev=50 if b=="G50" else (20 if b=="G20" else None)
    if lev:
        groups[b]["tp_price_pct"]=0.85/lev
        groups[b]["sl_price_pct"]=0.25/lev
preipo=[d for d in union if has(d,"mc-trade-zone-preipo")]
groups["PREIPO"]={"count":len(preipo),
                  "symbols":[{"symbol":d["symbol"],"maxLeverage":d["maxLeverage"],"contractSize":d["contractSize"]} for d in preipo]}
groups["_meta"]={"union_usdt_count":len(union),
                 "note":"G50 含 50/100/200/1000x，统一按 50x 开仓时 TP/SL 价格口径与现 v1 相同；G20 按 20x 开仓时 TP 4.25%、SL 1.25% 价格，持仓时间与跳空风险显著高于 50x 组"}
with open("/home/hatch/workspace/mexc-extension/groups.json","w") as f:
    json.dump(groups,f,ensure_ascii=False,indent=1)
print("union",len(union))
for k in ["G50","G20","G_other","PREIPO"]:
    print(k, groups[k]["count"], groups[k].get("lev_dist",""), groups[k].get("tp_price_pct",""), groups[k].get("sl_price_pct",""))
