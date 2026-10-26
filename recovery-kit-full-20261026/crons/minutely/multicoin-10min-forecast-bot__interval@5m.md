---
id: multicoin-10min-forecast-bot
title: 10分钟预报 · 多币种 Bark 通知
enabled: true
owner: goal:tradingview-binance-indicator-monitoring-with-wecom-alerts
mode: task
schedule:
  kind: interval
  timezone: Asia/Shanghai
  at: 2026-10-26T18:22:20
  every: 5m
delivery:
  - chat_id: dcdbe34c-9af1-48ff-8c90-071818baf2e6
metadata:
  tags: [cron:automatic-interval-anchor]
  originating_chat_context_json: '{"chat_id":"f6bd2c6b-9833-47c7-99d2-bf81621d0f9a","origin_provider":"main","chat_kind":"direct","event_kind":"message","require_mention":false,"device_id":"8dfc2762-cd8f-4454-af28-b034cd3d9227"}'
  presentation_locale: zh-CN
---
10 分钟事件合约预报【多币种版】巡检（只预报、不交易）。

步骤：
1. 运行 `/usr/bin/python3 ~/workspace/goals/tradingview-binance-indicator-monitoring-with-wecom-alerts/hidden_files/event_10min_multicoin_bot.py`，输出 JSON。脚本用现网基线模型（lightgbm_10min.txt + FEAT3 31 特征）对币安 521 个 USDT 永续币种（已排除现网单独推送的 BTCUSDT/ETHUSDT）计算未来 10 分钟上涨概率，跨标的动量统一用 BTCUSDT 5m；阈值 0.55/0.45、不去重（用户 2026-10-26 确认取消 4 小时去重）；新触发记账到 multicoin-alerts.jsonl；每轮最多只发一条 Bark 汇总（标题「10分钟预报 · 多币种 N 条」，正文最多 10 条按 |prob-0.5| 排序），无触发不推送。注意：约 170 个永续币种在现货无对应标的，脚本会记 errors 跳过，属预期情况（现货 K 线是现网模型的数据口径），不是故障。
2. final message 只写一行 status 摘要：候选时刻/已评分币种数/新触发数/Bark 是否发送/errors 有无。不要在聊天里发预报正文，不要修改现网 event_10min_bot.py、现网/多币种模型或任何阈值。
3. errors 非空时如实写明失败原因（数据源失败属预期降级，脚本下一轮处理新候选），不要编造概率或记账结果。
