---
id: monthly-model-retrain
title: 10分钟预报模型月度滚动重训（影子先行）
enabled: true
owner: goal:tradingview-binance-indicator-monitoring-with-wecom-alerts
mode: task
schedule:
  kind: monthly
  timezone: Asia/Shanghai
  time: 03:09:00
  dom: [17]
timeout_secs: 3600
delivery:
  - chat_id: dcdbe34c-9af1-48ff-8c90-071818baf2e6
metadata:
  tags: [cron:flexible-time]
  originating_chat_context_json: '{"chat_id":"dcdbe34c-9af1-48ff-8c90-071818baf2e6","origin_provider":"main","chat_kind":"direct","event_kind":"message","require_mention":false,"device_id":"8dfc2762-cd8f-4454-af28-b034cd3d9227"}'
  presentation_locale: zh-CN
---
10 分钟事件合约预报模型【月度滚动重训】（影子先行；用户 2026-10-23 批准的维护型优化）。工作目录：~/workspace/goals/tradingview-binance-indicator-monitoring-with-wecom-alerts/hidden_files/

完整流程（严格按序，任何一步失败就停并在汇报里如实说明，不要带病推进）：
1. 补数据：把 mf_data_1m/ 的 BTCUSDT/ETHUSDT 1m K 线补到当前最后一个已收盘 bar。沿用 dl_1m.py 的接口（https://data-api.binance.vision 公开接口、无需 key）与 CSV 格式（t=open ms,o,h,l,c,v 按月分文件）；dl_1m_update.py 是当月追加的参考实现，跨月时按同样格式新建月份文件并合并去重。补完核对连续性（相邻 diff 应全为 60000ms，无整段缺口）。
2. 重建特征：把 retrain_build_5m.py 的窗口起点改为「数据末日 − 349 天」（保持最新 350 天窗口），依次运行 retrain_build_5m.py、retrain_feat_v1.py（两标的）、retrain_feat_v2.py、retrain_feat_v3.py，产出新的 FEAT3R_{SYM}.npz（输出到 mf_data_1m/，带本次月份后缀备份旧 FEAT3R 不要直接覆盖丢失）。断言：特征维度 31、特征名与顺序与现网 FEAT3 完全一致；记录行数与候选时间范围。
3. 重训：复制 train_retrain.py 的协议（与现网同一套参数：num_leaves=15、min_child_samples=1000、lr=0.05、feature_fraction=0.8、bagging 0.8/5、seed=42；最后 15% 早停定轮数后全量 refit；LR 备用同口径），产出带新月份后缀的模型文件 lightgbm_10min_retrainYYYYMM.txt + lr_10min_retrainYYYYMM.pkl + model_10min_meta_retrainYYYYMM.json。旧月份的重训模型文件原样留档，不删除、不覆盖。
4. 样本外自检：按 exp1_walkforward.py 的 walk-forward 协议评估新模型（0.55/0.45 双向触发档），与现网基线 0.566（实验一六折 walk-forward，n=26,926）对比，记录胜率与 n。walk-forward 不达基线也要如实汇报，不许挑口径。
5. 轮换影子线：把 shadow_retrain_bot.py 的 GBM_FILE / LR_FILE 常量改为新月份模型文件（旧版模型留档），手动跑一次 shadow_retrain_bot.py 验证（两标的 prob 正常输出、无 errors、model_kind=gbm），影子账本 shadow4-alerts.jsonl 继续累积不清空；在汇报中注明轮换时间点，方便后续按段结算。
6. 汇报：在主聊天给用户一份结论式汇报（中文、简短）：新模型 walk-forward 胜率（带 n）vs 基线 0.566；影子 v4 至今的记账/已结算 n 与胜率、与现网同期对比（结算口径参照 live_winrate_watch.py：入场价=记账 close、出场=10 分钟后 1m 收盘）；是否建议换现网，给出明确建议和依据。

铁律：不许自动替换现网模型（lightgbm_10min.txt / lr_10min.pkl）、不许改现网脚本 event_10min_bot.py 与现网 cron；换现网必须等用户明确批准后另行执行。本任务只做：补数据→重建特征→重训→自检→轮换影子→汇报。
