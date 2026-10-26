---
id: event-10min-forecast-bot
title: 10分钟事件合约预报机器人
enabled: true
owner: goal:tradingview-binance-indicator-monitoring-with-wecom-alerts
mode: task
schedule:
  kind: interval
  timezone: Asia/Shanghai
  at: 2026-10-18T00:45:21
  every: 5m
delivery:
  - chat_id: dcdbe34c-9af1-48ff-8c90-071818baf2e6
metadata:
  tags: [cron:automatic-interval-anchor]
  originating_chat_context_json: '{"chat_id":"dcdbe34c-9af1-48ff-8c90-071818baf2e6","origin_provider":"main","chat_kind":"direct","event_kind":"message","require_mention":false,"device_id":"8dfc2762-cd8f-4454-af28-b034cd3d9227"}'
  presentation_locale: zh-CN
---
10 分钟事件合约预报巡检（只预报、不交易）。

步骤：
1. 运行 `/usr/bin/python3 ~/workspace/goals/tradingview-binance-indicator-monitoring-with-wecom-alerts/hidden_files/event_10min_bot.py`，输出 JSON。脚本计算 BTCUSDT/ETHUSDT 未来 10 分钟上涨概率；概率 ≥0.55 预报看涨、≤0.45 预报看跌，中间弃权；同标的同方向 4 小时去重开启（用户 2026-10-18 21:42 要求恢复），同方向告警冷却 4 小时后才可再次预报。alerts 非空即为新预报。脚本只经 Bark 推送到用户 iPhone（无配额限制；输出中每条 forecast 带 bark_ok 字段）。微信通道已于 2026-10-22 经用户拍板停用：脚本不再写微信 outbox（wechat_file 恒为 null），微信桥 serve 已停，不要查微信送达。
2. 送达判定：forecast.bark_ok=true 即 Bark 已送达，无需补推；bark_ok=false 时在 final message 里如实标注，由主 agent 决定是否在 Muse 聊天里补推。
3. 你的 final message 写：`alerts: [...]`（逐条：预报时间（本轮巡检的 CST 时间，精确到分钟）/标的/方向/上涨概率/核心指标值/bark_ok），以及 `status` 摘要。不要编造送达状态，以脚本输出为准。无 alerts 时 final message 只写 status，不要打扰用户。
4. 不要做交易，不要改脚本参数和阈值，不要直接给用户发微信（微信桥已停用）。

说明：模型期望胜率约 0.566（walk-forward 六折重估的诚实口径，0.60~0.65 的单次切分数字不再引用），未计手续费，不构成交易建议。
