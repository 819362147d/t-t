# 最新恢复包文件清单 MANIFEST

快照目录：recovery-kit-full-20261027-1925
快照时间：2026-10-27 约 19:25 CST（文件复制时点）
文件数（不含本清单）：24 个，总计 277062 字节

校验方法：在包根目录逐项比对下方 MD5，例如 `md5sum <文件>`。

缺失的二进制原文件说明（未编造）：
- lr_10min.pkl（二进制 LR 备用模型）：本快照未上传二进制原文件；主模型为文本 lightgbm_10min.txt，
  LR 备用 pkl 的 .b64 版见原包 recovery-kit-full-20261027/live/lr_10min.pkl.b64。
- FEAT3R BTC/ETH npz 原文件：本快照不含影子数据；原包 shadow/mf_data_1m/ 仍为 .b64.part-* 分片。
- 完整账本和完整日志未全量上传，见 README-SNAPSHOT.md：本快照只含账本尾部 200 行和日志尾部 100 行。

敏感检查说明：上传前已扫描本快照全部文件，未发现字面 API key / Secret Key / Bark 密钥值；
代码中出现的 access_key / secret_key / ApiKey 均为函数参数名、stdin 字段名或协议头名，不是密钥值。
已排除：.bak、.pid、__pycache__、K 线缓存目录、截图、完整账本、完整日志、Bark 脚本。

| 文件 | 大小（字节） | MD5 |
|---|---|---|
| `README-SNAPSHOT.md` | 4151 | `defa12b199a02f51c005d8aba4e43fc5` |
| `crons/minutely/multicoin-10min-forecast-bot__interval@5m.md` | 1977 | `7df113417f203dde3404c5dc9fb81928` |
| `mexc/mexc_top3_signals.py` | 1056 | `356dabed551750038d1462aef819bb25` |
| `mexc/mexc_trade_client.py` | 8596 | `0c177abb615e3a95b8959fc40605d3a1` |
| `mexc/mexc_trader.log.tail` | 9392 | `76f65bdff49c8e02b9ccc8106e2e501a` |
| `mexc/mexc_trader_daemon.py` | 24715 | `b988a91cf32c74014c8b7f2d4d7945a0` |
| `mexc/mexc_trader_state.json` | 43282 | `a4eec18955017688b61f61a4e3c8c973` |
| `mexc-extension/groups.json` | 11456 | `9c170b2e498264c5788db2e122134b3d` |
| `mexc-extension/groups.py` | 2168 | `0f01951413ad56001554b1d573038560` |
| `mexc-extension/mexc_klines.py` | 2385 | `ff4132c30d05f8f7385e97650ed8604d` |
| `mexc-extension/mexc_top3_signals.py` | 1056 | `356dabed551750038d1462aef819bb25` |
| `mexc-extension/mexc_trader_20x_test.log` | 512 | `426987bb338784cf6be3dca36228d761` |
| `mexc-extension/mexc_trader_daemon_v1_20x.py` | 22463 | `734827e82e0108e37ac600bdf4524cb8` |
| `mexc-extension/test_20x.py` | 656 | `c42a69ad67e49704add2e77f4381b04d` |
| `mexc-extension/test_score.py` | 1645 | `b21ff9da504881f848118229eda2f416` |
| `multicoin/MODEL-NOTES.md` | 1034 | `6032d2700f8dd2b4623867ccc2c0cf40` |
| `multicoin/event_10min_bot.py` | 18064 | `fdac378cce659f3989fd69ab91c9b34d` |
| `multicoin/event_10min_multicoin_bot.py` | 14249 | `2959427b4c631d8b6e324cb0dcce9e92` |
| `multicoin/feat_v3.py` | 3094 | `3caaff2ae7321be1f4441e2ad5dc920a` |
| `multicoin/lightgbm_10min.txt` | 68030 | `1c353499861369320fc90b96fed16967` |
| `multicoin/mf_features.py` | 6292 | `6deebd237049181c5c00fbef23e6a8af` |
| `multicoin/mf_features_v2.py` | 4802 | `b97cce680e37f10a6aa68db68164779d` |
| `multicoin/multicoin-alerts.tail.jsonl` | 17573 | `c2bf36d27119603f5ac68245465bc583` |
| `multicoin/multicoin_klines_cache.py` | 8414 | `68a08439c7bc7117f4f74dec84f7b7b6` |
