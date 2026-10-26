---
id: shadow-retrain-forecast-bot
title: 10分钟预报影子v4（月度重训版）记账
enabled: true
owner: goal:tradingview-binance-indicator-monitoring-with-wecom-alerts
mode: task
schedule:
  kind: interval
  timezone: Asia/Shanghai
  at: 2026-10-23T23:24:29
  every: 5m
delivery:
  - chat_id: dcdbe34c-9af1-48ff-8c90-071818baf2e6
metadata:
  tags: [cron:automatic-interval-anchor]
  originating_chat_context_json: '{"chat_id":"dcdbe34c-9af1-48ff-8c90-071818baf2e6","origin_provider":"main","chat_kind":"direct","event_kind":"message","require_mention":false,"device_id":"8dfc2762-cd8f-4454-af28-b034cd3d9227"}'
  presentation_locale: zh-CN
---
10 分钟事件合约预报【影子 v4·月度重训版】记账巡检（记账 + Bark 推送、不交易；2026-10-26 用户要求 v4 触发时经 Bark 推送）。

步骤：
1. 运行 `/usr/bin/python3 ~/workspace/goals/tradingview-binance-indicator-monitoring-with-wecom-alerts/hidden_files/shadow_retrain_bot.py`，输出 JSON。脚本用重训版模型（lightgbm_10min_retrain202610.txt，最新 350 天窗口重训，参数/协议与现网一致；缺失时降级 lr_10min_retrain202610.pkl）对 BTCUSDT/ETHUSDT 计算未来 10 分钟上涨概率，取数/特征管线与现网 event_10min_bot.py 共用，候选时刻一致（最新已收盘 5m bar）。触发口径与现网完全一致：prob_up ≥0.55 看涨 / ≤0.45 看跌，同标的同方向 4 小时冷却去重（用影子自己的 shadow_retrain_state.json，不读现网状态）；触发才 append 到同目录 shadow4-alerts.jsonl 记账（字段 t/sym/dir/prob/close/model），并经 Bark 推送到用户 iPhone（标题带“影子v4”以区分现网预报，输出中每条 recorded 带 bark_ok 字段；bark_ok=false 只记日志，脚本不补推）。
2. final message 只写一行 status 摘要：候选时刻/两标的 prob_up/是否触发记账/bark_ok 有无/errors 有无。不要在聊天里发预报（Bark 已送达无需补推；bark_ok=false 时在 final message 里如实标注，由主 agent 决定是否在 Muse 聊天里补推），不写微信 outbox，不要修改现网 event_10min_bot.py、现网模型（lightgbm_10min.txt / lr_10min.pkl）、现网账本（live-alerts.jsonl）或任何阈值。
3. errors 非空时如实写明失败原因（数据源失败属预期降级，脚本下一轮处理新候选），不要编造概率或记账结果。
