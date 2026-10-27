# 多币种 10 分钟预报模型说明（快照时点：2026-10-27）

- 脚本：event_10min_multicoin_bot.py，只预报、不交易、不下单；核心特征逻辑复用 event_10min_bot.py。
- 模型：lightgbm_10min.txt（本快照含文本版），FEAT3 31 特征；LR 备用 lr_10min.pkl 未以二进制上传，
  原包 live/lr_10min.pkl.b64 为其 base64 版。
- 阈值：0.55 / 0.45，不去重。
- 宇宙：币安 USDT 永续与 MEXC 上架 base 的交集，排除 BTCUSDT / ETHUSDT，约 488 个；
  每轮实际评分约 335-337 个，其余因币安 K 线拉取失败（HTTP 400）跳过，属已知常态。
- 跨标的动量参考：BTCUSDT 5m。
- 记账：multicoin-alerts.jsonl，字段 t / sym / dir / prob / close；每轮最多一条 Bark 汇总，触发才推。
- 运行：用 bot-venv 的 python 运行，不要用系统 python（系统里没有 lightgbm / sklearn）。
- 本快照只含关键脚本和模型文本，不含 K 线缓存目录和完整账本；完整账本以实盘运行目录为准。
