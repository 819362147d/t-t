"""影子测试（不写账本、不下单）：用 MEXC K 线 + 现网模型，对样本标的算 10 分钟上涨概率。
参考仍用币安 BTCUSDT 5m（现网口径），以便先验证特征/模型能跑通；股票类用 BTC 做参考是否合理，另行验证。
"""
import sys, json
sys.path.insert(0, "/home/hatch/workspace/bot-run")
sys.path.insert(0, "/home/hatch/workspace/mexc-extension")
from mexc_klines import fetch_mexc_klines
from event_10min_bot import compute_features, load_models, predict, klines as binance_klines
model, err = load_models()
print("model:", model[0] if model else None, "err:", err)
if not model: raise SystemExit(1)
k5m_ref = binance_klines("BTCUSDT", "5m", 1500)
print("BTC ref bars:", len(k5m_ref), "last t close:", k5m_ref[-1])
samples = [
    ("TESLA_USDT","G50"),("NVIDIA_USDT","G50"),("NAS100_USDT","G50"),
    ("SPX500_USDT","G50"),("ANTHROPIC_USDT","G50/PreIPO"),
    ("KIMISTOCK_USDT","G20/PreIPO"),("PYPLSTOCK_USDT","G20"),
]
for sym, grp in samples:
    try:
        k1m = fetch_mexc_klines(sym, "1m", 500)
        k5m = fetch_mexc_klines(sym, "5m", 1500)
        t_ms, close, feats = compute_features(k1m, k5m, k5m_ref)
        if feats is None:
            print(sym, grp, "FEATURES_NONE bars", len(k1m), len(k5m))
            continue
        prob, _lt, _st = predict(model, feats)
        print(sym, grp, "t_ms", t_ms, "close", close, "prob_up", round(float(prob),4),
              "dir", "UP" if float(prob)>=0.55 else ("DOWN" if float(prob)<=0.45 else "HOLD"),
              "bars1m/5m", len(k1m), len(k5m))
    except Exception as e:
        print(sym, grp, "ERR", type(e).__name__, e)
