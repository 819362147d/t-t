# 影子 v2·规则门控 记账 bot 上线报告（2026-10-21）

只记账、不推送、不交易、不动现网。依据：实验三报告 `opt-exp3-20261021.md` 下一步建议——唯一值得进影子的配置：现网基线模型 + 规则门控（实验三中 0.55 档 0.574、n=17,547、6/6 折跑赢基线同折，线索级）。

## 配置

- 脚本：`shadow_rulegate_bot.py`；记账文件：`shadow2-alerts.jsonl`（字段 t/sym/dir/prob/mr/close/model）；状态文件：`shadow_rulegate_state.json`（仅记已处理候选时刻，防同一根 bar 重复记账）；统计 meta：`shadow_rulegate_meta.json`。
- 模型：现网基线 LightGBM `lightgbm_10min.txt` + FEAT3 31 特征，取数/特征管线与 `event_10min_bot.py` 逐行共用（klines/compute_features），候选时刻与现网一致 = 最新已收盘 5m bar 的收盘时刻。
- 规则（实验三事前定义，见 `exp3_model_layer.py`）：mr = −(z(rsi14_5m) + z(boll_pctB_1m))/2；mr ≥ 1 规则投多、mr ≤ −1 投空、其余弃权。
- 触发：模型 prob_up ≥ 0.55（看涨）或 ≤ 0.45（看跌），且规则方向与模型方向同向，才记账。无方向冷却去重；不发 Bark、不写微信 outbox、不写 live-alerts.jsonl。

## z 统计值（固定化近似，已注明）

实验三的 z 是逐折用该折训练段 mean/std 现算的；本影子版改用全量 350 天 FEAT3 数据整体 mean/std 固定值（`mf_data_1m/FEAT3_BTCUSDT.npz` + `FEAT3_ETHUSDT.npz` 双标的合并，n=200,854 行，std 为 numpy ddof=0，与实验三代码口径一致），是对实验三逐折统计的固定化近似，不逐折滚动更新：

| 特征 | mean | std |
|---|---|---|
| rsi14_5m | 50.06318525176323 | 11.228681975663308 |
| boll_pctB_1m | 0.4992100150023892 | 0.32650280438403423 |

## 首轮自测结果（2026-10-21 12:24 CST，真实运行两轮）

- 规则逻辑合成值校验通过：深度超卖（rsi=20、boll=−0.2）→ mr=+2.41 投多；深度超买（rsi=80、boll=1.2）→ mr=−2.41 投空；均值处 → mr≈0 弃权。
- 候选时刻 t=1792556400000（2026-10-21 12:20 CST）：
  - BTCUSDT：prob_up 0.4977（模型无方向），mr −0.7053（规则弃权），未触发、未记账
  - ETHUSDT：prob_up 0.4925（模型无方向），mr −0.5338（规则弃权），未触发、未记账
- errors 为空；JSON 输出正常；第二轮复跑同根 bar 结果逐值一致、无重复记账（ledger 未生成属预期：无触发即无记账行）。
- 记账逻辑：触发+同向才 append 的路径已经代码与合成校验确认；首轮行情未触发，未产生 shadow2-alerts.jsonl 首行，后续 cron 轮次触发时自动创建。

## cron

- id：`shadow-rulegate-forecast-bot`（标题「10分钟预报影子v2（规则门控）记账」），owner `goal:tradingview-binance-indicator-monitoring-with-wecom-alerts`，每 5 分钟（Asia/Shanghai），已回查确认存在且 enabled=true，下一轮约 12:29 CST。正文写明只记账不推送、final 只写一行 status 摘要。

## 纪律与后续

- 未改动 event_10min_bot.py、shadow_allfeat_bot.py、任何现网/影子 v1 模型与阈值；未产生费用。
- 预期口径不变：规则门控样本外 +0.8 点只是线索（CI 与基线重叠、且属实验三事后拆分），影子实测可能打回 0.566 附近；攒够 ≥30 个已结算再与现网同口径对比判晋级/否决，结算口径与 live 一致（t+10 分钟 1m 收盘价方向）。
