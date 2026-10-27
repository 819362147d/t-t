# 全系统一键恢复包 · 使用指南（RESTORE-FULL）

**打包时间：2026-10-27 约 12:00 CST。适用对象：一个全新的 Muse 实例，拿到本 zip 就能把整条系统恢复起来。**

本包覆盖：10 分钟事件合约预报（现网 BTC/ETH）＋ 多币种预报（488 个币）＋ 影子线 v1–v4 ＋ 月度重训流水线 ＋ 抹茶 MEXC U 本位小资金实盘守护进程 ＋ Bark 推送。**只预报的线不交易；唯一会下单的是抹茶守护进程，见第 7 节。**

---

## 0. 打包时的系统快照（先读这段，别拿旧数字当现状）

- **现网与影子线正常在跑**：2026-10-26 18:15–23:00 的币安 K 线断流（云端出口代理截断 Python urllib 大响应）已于 23:00 恢复。修法草案仍在包内：`live/event_10min_bot_patched_keepalive.py`（只换传输层，不改模型/阈值）。**铁律：未经用户明确点头，不要用草案替换现网 `event_10min_bot.py`。**恢复后先跑一次现网脚本看是否仍断流，再按用户决定处理。
- **多币种正常**：宇宙 488（币安永续 ∩ 抹茶上架，剔 BTC/ETH），每 5 分钟一轮，Bark 汇总推送。账本每 5 分钟新增，包内行数是打包时刻快照，恢复后多几行属正常。
- **抹茶实盘已重启**：守护进程在 2026-10-27 07:10 第三次 VM 重启后停了约 4 小时 44 分；用户 11:53 新建一组 key 发截图（90 天有效期、IP 未绑定），只读验证通过（权益 1.9099、可用 1.3823），守护进程 11:53:48 重启（新参数 50x、止盈 +85%/止损 -25% 已生效）。重启时交易所实查只剩 SUN 一单在手，其余 5 单（METIS/MASK/HUMA/S/RENDER）已在停机期间被止盈/止损触发平掉，守护进程 11:54:03–08 同步清掉 5 单旧账。`mexc/mexc_trader_state.json` 打包时状态：day 2026-10-27、day_start_equity 1.87500164、day_realized 0.0、open_trades 2 单（SUN 空 + GRT 多）、closed_trades 70、traded_rounds 66。**恢复时先查交易所实际持仓再决定是否重启守护进程，不要凭 state 文件假设。**用户 11:46 截图：权益 1.91、可用保证金 1.38、钱包 1.81、未实现 +0.096；1.38 闲置是因守护进程停后无人执行，并非保证金上限卡住。
- **抹茶参数（用户 2026-10-27 11:51 刚定）**：杠杆 50x 逐仓、止盈 **+85%** / 止损 -25%（保证金口径，50x 下 = 1.7% / 0.5% 价格，比值 3.4 倍），止损线到爆仓线至少留 0.5% 价格空间，单笔保证金 5% 权益、总占用 ≤50% 权益、单日亏 3% 权益停手、10 秒扫一次账本、只做 ≤4 分钟新鲜信号前三。首单新参数订单：GRT 多 11:54:01 开仓 entry 0.02149、tp 0.02186、sl 0.02138、保证金约 0.0992。**注意：SUN 一单是 11:51 之前按旧止盈 +60% 挂的单（entry 0.01632、tp 0.016124、sl 0.016402），新 85% 只适用于之后新开的单；不要拿 SUN 的 tp 当新参数。**守护进程文件头部的 docstring 还写着旧参数（35x 等），以文件内 `TP_MARGIN_PCT = 0.85` 等常量为准。旧 key（2026-10-26 建的那组与 06:27 那组）在抹茶侧仍有效，应在 API 管理里删掉、只留 11:53 的新 key；新 key 有效期约至 2027-01-25。
- **用户 11:48 补充两点**：同一币种止盈止损只设置一次、不要重复设置（重复会卡 bug 导致止不掉）；信号到开单延迟约 3.5 分钟（fresh round age 186–197 秒 + plan 到 opened 13–47 秒），要求极限优化。优化版已打进包（`mexc/mexc_trader_daemon_v2.py` + `mexc/mexc_trader_daemon_v2_OPTIMIZATION.md`，参数与 v1 一致：50x、止盈 +85%/止损 -25%）；**用户 2026-10-27 12:01 决定先留着 v1 不切，v2 只作备选留档，现网仍跑 v1——切 v2 须用户点头，不要自行替换。**
- **胜率快照（打包时实测重跑，与 settle_ledger.py 同口径：入场价 = 记录 close，出场价 = 入场 10 分钟后那根 1m K 线收盘价；引用前必须重跑结算脚本，不得直接沿用）**：现网 0.6118（85 条已结算/胜 52，BTC 43/27、ETH 42/25）；影子 v1 全组合 0.6113（674/412，BTC 313/204、ETH 361/208）；影子 v2 规则门控 0.6192（344/213，BTC 162/105、ETH 182/108）；影子 v3 精选 0.5000（38/19，已判负、不换线）；影子 v4 月度重训 0.5882（34/20，已满 30 条判不换线）。walk-forward 六折真实水位 0.566（n=26,926）是唯一可引用的回测口径，0.60~0.65 的单次切分数字已作废。多币种账本 7,582 行（打包时），未结算胜率——不去重、跨币种，胜率不能直接与 0.566 比。
- **换现网铁律**：影子线再好也不许自动替换现网模型，换现网必须用户明确批准。下次月度重训 2026-11-17 03:09。

---

## 1. 解压与落点

```bash
# 1. 解压到工作区（zip 内根目录就是 recovery-kit-full-20261027/）
cd ~/workspace/goals/tradingview-binance-indicator-monitoring-with-wecom-alerts/files
unzip recovery-kit-full-20261027.zip

# 2. 按下表把各目录放回原位（GOAL 就是该 goal 的目录）
GOAL=~/workspace/goals/tradingview-binance-indicator-monitoring-with-wecom-alerts
cp recovery-kit-full-20261027/live/*        $GOAL/hidden_files/
cp recovery-kit-full-20261027/multicoin/*   $GOAL/hidden_files/
cp recovery-kit-full-20261027/shadow/*      $GOAL/hidden_files/
cp recovery-kit-full-20261027/shadow/mf_data_1m/* $GOAL/hidden_files/mf_data_1m/
cp recovery-kit-full-20261027/mexc/*        $GOAL/hidden_files/
cp -r recovery-kit-full-20261027/crons/*    $GOAL/crons/   # 按 minutely/daily/monthly/disabled 原结构整目录复制
cp -r recovery-kit-full-20261027/usdt-perps/* ~/workspace/usdt-perps/
cp -r recovery-kit-full-20261027/bark/*    ~/workspace/custom-connectors/custom.bark-push/
cp -r recovery-kit-full-20261027/docs/*     $GOAL/hidden_files/
```

目录对照表：

| 包内目录 | 放回位置 | 内容 |
|---|---|---|
| `live/` | `$GOAL/hidden_files/` | 现网脚本、模型、账本、状态、结算脚本 |
| `multicoin/` | `$GOAL/hidden_files/` | 多币种脚本、缓存模块、账本、状态 |
| `shadow/` | `$GOAL/hidden_files/` | 影子 v1–v4、重训流水线、FEAT3R 特征产物 |
| `mexc/` | `$GOAL/hidden_files/` | 抹茶交易客户端、守护进程、信号筛选、全平脚本、state |
| `bark/` | `~/workspace/custom-connectors/custom.bark-push/` | bark_send.py、CONNECTOR.md、connector.yaml |
| `usdt-perps/` | `~/workspace/usdt-perps/` | 三所 U 本位合约币种名单 |
| `crons/` | `$GOAL/crons/` | 8 个在用 cron 全文（见第 5 节） |
| `docs/` | `$GOAL/hidden_files/`（参考） | 优化实验报告、影子线报告 |

### 从 GitHub 恢复时（二进制文件的 .b64 分片解码）

GitHub 上传工具不接受二进制文件，所以 GitHub 副本里的 4 个二进制文件存的是 base64 文本版，zip 副本里是原文件。GitHub 恢复时按下面步骤还原（本地 zip 不需要这一步）：

```bash
cd $GOAL/hidden_files
# 1. 两个 .pkl：单个 .b64 文件，直接解码
base64 -d recovery-kit-full-20261027/live/lr_10min.pkl.b64 > lr_10min.pkl
base64 -d recovery-kit-full-20261027/shadow/lr_10min_retrain202610.pkl.b64 > lr_10min_retrain202610.pkl
# 2. 两个 .npz：各 5 个 2MB 分片，按 00→04 顺序拼起来再解码
cat recovery-kit-full-20261027/shadow/mf_data_1m/FEAT3R_BTCUSDT.npz.b64.part-* | base64 -d > mf_data_1m/FEAT3R_BTCUSDT.npz
cat recovery-kit-full-20261027/shadow/mf_data_1m/FEAT3R_ETHUSDT.npz.b64.part-* | base64 -d > mf_data_1m/FEAT3R_ETHUSDT.npz
# 3. 核对 MD5（与 MANIFEST.md 表中原文件一致才算还原成功）
md5sum lr_10min.pkl lr_10min_retrain202610.pkl mf_data_1m/FEAT3R_BTCUSDT.npz mf_data_1m/FEAT3R_ETHUSDT.npz
```

---

## 2. 依赖

- Python 3.12（打包环境版本；3.10+ 一般可用，以实测为准）
- `pip install lightgbm==4.7.0 numpy==1.26.4 pandas==2.1.4 scikit-learn==1.9.1`
- 其余全部是标准库（urllib / json / hashlib / hmac / threading 等），无需再装。
- 网络：需能访问 `data-api.binance.vision`（公开行情，无 key）和 `api.mexc.com`（实盘，需 key）。

---

## 3. 包里没有的东西（必须用户重录，绝不要编造或从别处找）

| 缺失项 | 如何恢复 | 注意 |
|---|---|---|
| **Bark key** | 用户在 Bark App 里看 key，经 Secure Vault 重新录入自定义连接器 `custom.bark-push`（host `api.day.app`，placement `url_path_segment:/{}`）。key 不可读回、不可恢复 | 录完先跑一次 `bark_send.py "测试" "恢复包验证"`，收到手机推送才算通。`bark_send.py` 本身不含 key，运行时从 Vault 注入 |
| **抹茶 MEXC API key** | 用户在抹茶后台建 key（只需合约交易权限，不要开提现；有效期 90 天，当前在用的 11:53 新 key 约 2027-01-25 到期，须续期/重建；注意 2026-10-26 建的旧 key 与 06:27 那组在抹茶侧仍有效，应在 API 管理里删掉、只留新 key）。**密钥只放常驻交易进程内存、不落盘、不进文件/记忆/聊天**；启动守护进程时经 stdin 传入一次，VM 重启即失效、需用户重新提供 | 守护进程启动方式见第 7 节。nohup + stdin 传密钥行不通，必须前台 exec 起进程（yield 后自动后台化） |
| K 线磁盘缓存（约 679 个 npz，约 35MB） | 未打包，自动重建：首轮多币种脚本全量拉取后写回 `hidden_files/multicoin_klines_cache/`，之后每轮只拉最新 40 根 1m / 16 根 5m | 详见 `multicoin/multicoin_klines_cache/README.md` |
| 原始 1m K 线 CSV（mf_data_1m，约 109MB） | 未打包；月度重训时 `dl_1m_update.py` 会从 `data-api.binance.vision` 公开接口补下载 | 恢复后先跑一次 `dl_1m_update.py` 再重训 |
| 实验中间数据（exp1–exp6 的 npz、mf_data/、retrain_work/、日志 *.log） | 未打包；实验结论在 `docs/` 的报告里，数据可按报告方法重算 | 不影响线上运行 |
| 抹茶守护进程优化版 v2 | 已打进包（`mexc/mexc_trader_daemon_v2.py` + 优化说明 `mexc_trader_daemon_v2_OPTIMIZATION.md`），只作备选留档；现网仍跑 v1 | 切 v2 须用户点头，不要自行替换 live 守护进程 |

---

## 4. 恢复后验证（按顺序跑，全部通过才算恢复成功）

```bash
cd ~/workspace/goals/tradingview-binance-indicator-monitoring-with-wecom-alerts/hidden_files
python3 -c "import lightgbm, numpy, pandas, sklearn; print('deps ok')"

# 1. 现网 dry-run（只算不推不记账；如仍报 IncompleteRead，属已知断流，见第 0 节）
python3 event_10min_bot.py --dry-run 2>&1 | head -20

# 2. 多币种 dry-run（约 60 秒；只打印 JSON，不推送、不记账）
time python3 event_10min_multicoin_bot.py --dry-run | python3 -c "import json,sys; d=json.load(sys.stdin); print('universe', d['universe'], 'scored', d['n_scored'], 'alerts', len(d['alerts']))"
# 预期 universe 488、scored 约 330-350

# 3. 影子线各跑一次（只记账不推送；v4 会推 Bark，验证时注意）
python3 shadow_allfeat_bot.py | head -c 500; echo
python3 shadow_rulegate_bot.py | head -c 500; echo
python3 shadow_select_bot.py | head -c 500; echo
python3 shadow_retrain_bot.py | head -c 500; echo

# 4. 结算脚本重跑（引用胜率前必须做；逐条拉结算价，账本大时较慢）
python3 settle_ledger.py live-alerts.jsonl
python3 settle_ledger.py shadow4-alerts.jsonl

# 5. Bark 测试推送（Bark key 重录后）
python3 ~/workspace/custom-connectors/custom.bark-push/bin/bark_send.py "测试" "恢复包验证"
# 预期返回 {"code":200,"message":"success"}，且手机收到推送
```

---

## 5. Cron 重建（8 个在用，全文在包内 `crons/`）

按 `crons/` 下原路径重建（id / schedule / 正文以包内 md 文件为准，不要凭记忆改）：

| id | 频率 | 脚本 |
|---|---|---|
| `event-10min-forecast-bot` | 每 5 分钟 | `live/event_10min_bot.py`，只预报不交易，Bark 推送 |
| `multicoin-10min-forecast-bot` | 每 5 分钟 | `multicoin/event_10min_multicoin_bot.py`，每轮最多一条 Bark 汇总，不去重 |
| `shadow-allfeat-forecast-bot` | 每 5 分钟 | `shadow/shadow_allfeat_bot.py`，只记账不推送 |
| `shadow-rulegate-forecast-bot` | 每 5 分钟 | `shadow/shadow_rulegate_bot.py`，只记账不推送 |
| `shadow-select-forecast-bot` | 每 5 分钟 | `shadow/shadow_select_bot.py`，只记账不推送 |
| `shadow-retrain-forecast-bot` | 每 5 分钟 | `shadow/shadow_retrain_bot.py`，记账 + Bark 推送（标题带“影子v4”） |
| `live-winrate-watch` | 每天 09:09 | `live/live_winrate_watch.py`，n≥30 才报告一次（flag 防重复） |
| `monthly-model-retrain` | 每月 17 日 03:09 | 月度重训流水线（见 `crons/monthly/` 全文），只轮换影子、不自动换现网 |

`crons/disabled/event-contract-signal-bot` 是已停用的老 15 分钟信号机器人（回测胜率 24.1% 证伪），**不要恢复启用**。

---

## 6. 各线口径速查（恢复后不要改）

- **现网**：LightGBM（350 天重训）+ LR 备用；阈值 ≥0.55 看涨、≤0.45 看跌、中间弃权；同标的同方向 4 小时去重；只预报不交易。
- **多币种**：复用现网模型/阈值；宇宙 = 币安 USDT 永续 ∩ 抹茶上架（488 个，BTC/ETH 已排除）；跨标的动量统一用 BTC 5m；**不去重**（用户 2026-10-26 确认取消）；166 个永续在现货无对应标的会报 HTTP 400 跳过，属预期。
- **影子 v1**：全组合模型，三档 0.55/0.58/0.60，无去重，只记账。**v2**：基线 + mr 规则门控同向才记。**v3**：只做多 + mr≥1 + 亚盘 UTC 00–08，已判负。**v4**：月度重训模型，与现网同阈值同去重，触发时 Bark 推送。v1–v3 只记账不推送。
- **月度重训**：每月 17 日，补数据 → 最新 350 天重建特征 → 同参数重训 → walk-forward 对基线 0.566 → 轮换影子 v4。铁律见第 0 节。

---

## 7. 抹茶实盘守护进程（唯一会下单的部分，谨慎操作）

**启动前必读：**

1. 密钥只能用户提供，启动时经 stdin 传入守护进程内存，不写文件、不进环境变量、不进日志。VM 重启后进程即死、密钥失效，必须用户重新提供才能重起。
2. 启动前先查交易所实际持仓（`mexc_check_positions.py` 或抹茶 App），与 `mexc/mexc_trader_state.json` 核对。state 只是快照，交易所为准；有持仓在先不要凭空开新单。
3. 当前参数（2026-10-27 11:51 用户定，见 `mexc/mexc_trader_daemon.py` 头部常量）：杠杆 50x 逐仓、止盈 **+85%**（保证金口径，50x 下 = 1.7% 价格）、止损 -25%（保证金口径，50x 下 = 0.5% 价格）、止损线到爆仓线至少留 0.5% 价格反应空间、不限时、单笔保证金 5% 权益、总占用 ≤50% 权益、单日亏 3% 权益停手、10 秒扫一次账本。本金口径是权益（equity），不要改回可用余额（2026-10-26 已修过一个因此死锁的 bug）。**同一币种止盈止损只设置一次，不要重复设置。**
4. 启动命令（前台 exec，stdin 送密钥 JSON，yield 后自动后台化；不要用 nohup）：

```bash
cd ~/workspace/goals/tradingview-binance-indicator-monitoring-with-wecom-alerts/hidden_files
/usr/bin/python3 mexc_trader_daemon.py
# stdin 传入：{"access_key":"...","secret_key":"..."}
```

5. 只读验证（不下单，先跑这个确认 key 有效）：
```bash
python3 mexc_test_sign.py   # 签名 + 只读查询
python3 mexc_check_positions.py  # 查当前持仓
```
6. 手动全平仓：`python3 mexc/close_all.py`（同样经 stdin 传 key），会市价平掉 state 里的全部持仓并记账本。**全平是不可逆动作，执行前必须用户明确点头。**
7. 已知接口坑：`POST /api/v1/private/position/leverage` 报 404（路径不存在），下单自带 leverage 字段是当前 workaround，成交后务必核对持仓实际杠杆；`/account/assets`（复数）报 602，改走 `/account/asset/USDT`；手续费以实收为准（API 下单 taker 约 0.06%–0.08%，不要信 0.02% 的说法）；50x 下多数小币最高杠杆仅 20–25x，会被跳过。

---

## 8. 包内文件清单（共 115 个文件，约 15MB，不含 MANIFEST.md 本身；完整清单与 MD5 见 MANIFEST.md）

- `live/`（26 个）：现网脚本、双模型、账本、状态、结算脚本、回测文件
- `multicoin/`（8 个）：多币种脚本、缓存模块、账本、状态、测试脚本
- `shadow/`（36 个）：影子 v1–v4、重训流水线、FEAT3R 特征产物（2×6.5MB）、各账本
- `mexc/`（12 个）：交易客户端、守护进程 v1、优化版 v2 + 优化说明、信号筛选、全平脚本、state、测试脚本
- `bark/`（3 个）：bark_send.py、CONNECTOR.md、connector.yaml
- `usdt-perps/`（8 个）：三所币种名单（清洗后）+ 抓取脚本
- `crons/`（9 个）：8 个在用 cron 全文 + 1 个已停用（disabled/）
- `docs/`（14 个）：优化实验报告、影子线报告、Tardis 探针报告
- 根目录：`RESTORE-FULL.md`（本文件）、`MANIFEST.md`（文件清单 + MD5）

**GitHub 副本说明**：GitHub 上的同一目录共 127 个文件（不含 MANIFEST.md），约 33M——115 个文件与 zip 一致，另加 12 个 `.b64` 分片（2 个 .pkl 各 1 个 + 2 个 .npz 各 5 个），4 个二进制原文件在 GitHub 上以 .b64 形式存放，解码方法见第 1 节。GitHub 版 MANIFEST.md 比 zip 版多 12 行分片记录。

**不含密钥声明**：打包前已逐项检查——`mexc_trader_state.json`、各类 state json、账本、日志均不含 API key/密码/UUID；`bark_send.py` 不含 key（运行时从 Vault 注入）。如在包内发现任何疑似密钥值，请立即删除并告知用户，不要使用。

---

## 9. 恢复后不要做的事

- 不要自动把影子模型换成现网模型（换现网必须用户批准）。
- 不要恢复启用 `event-contract-signal-bot`（已证伪停用）。
- 不要在小样本上重调阈值（检出 +3 点需约 2,140 条已结算）。
- 不要把抹茶 key 写进任何文件、环境变量、聊天记录或记忆。
- 不要把本包传到公开仓库前再次检查密钥残留（GitHub 单文件上限 100MB，本包约 15MB，符合；分片后单个文件最大 2MB，符合）。
- 不要把守护进程的止盈止损对同一币种重复设置。
