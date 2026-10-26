---
id: event-contract-signal-bot
title: 事件合约信号机器人
enabled: false
owner: goal:tradingview-binance-indicator-monitoring-with-wecom-alerts
mode: task
schedule:
  kind: interval
  timezone: Asia/Shanghai
  at: 2026-10-11T13:19:01
  every: 15m
delivery:
  - chat_id: dcdbe34c-9af1-48ff-8c90-071818baf2e6
metadata:
  tags: [cron:automatic-interval-anchor]
  originating_chat_context_json: '{"chat_id":"dcdbe34c-9af1-48ff-8c90-071818baf2e6","origin_provider":"main","chat_kind":"direct","event_kind":"message","require_mention":false,"device_id":"8dfc2762-cd8f-4454-af28-b034cd3d9227"}'
  presentation_locale: zh-CN
---
事件合约信号机器人巡检（只提醒、不交易）。

步骤：
1. 运行 `python3 ~/workspace/goals/tradingview-binance-indicator-monitoring-with-wecom-alerts/hidden_files/event_bot.py`，输出 JSON。脚本已有信号去重（同标的同方向 4 小时只报一次），signals 非空即为新信号；脚本会自动把告警文本写入微信桥 outbox（字段 wechat_file 为文件名，空字符串表示未能入队）。
2. 如有 signals：对每个 signal 等待约 30 秒，然后检查 `~/workspace/wechat-bridge/outbox/sent/<wechat_file>` 是否存在——存在=微信已送达；仍在 pending/ 或 wechat_file 为空=微信未送达。
3. 你的 final message 写：`signals: [...]`（逐条：标的/方向/价格/趋势分/CVD-Z/wechat_delivered: true|false），以及 `status` 摘要。不要自己编造送达状态，以文件检查为准。
4. 不要做交易，不要改脚本参数，不要直接给用户发微信（outbox 由 serve 进程发出）。

说明：微信发送依赖 iLink 会话 token，偶尔因 token 过期发送失败（flusher 会每 10 秒重试）；final message 如实报告送达情况，由主 agent 决定是否在 Muse 聊天里补推。
