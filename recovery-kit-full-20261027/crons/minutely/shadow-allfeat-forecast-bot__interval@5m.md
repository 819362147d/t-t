---
id: shadow-allfeat-forecast-bot
title: 10分钟预报影子版（全组合）记账
enabled: true
owner: goal:tradingview-binance-indicator-monitoring-with-wecom-alerts
mode: task
schedule:
  kind: interval
  timezone: Asia/Shanghai
  at: 2026-10-20T22:04:26
  every: 5m
delivery:
  - chat_id: dcdbe34c-9af1-48ff-8c90-071818baf2e6
metadata:
  tags: [cron:automatic-interval-anchor]
  originating_chat_context_json: '{"origin_provider":"main","chat_kind":"direct","event_kind":"message","require_mention":false}'
  presentation_locale: zh-CN
---
10 分钟事件合约预报【影子版·全组合】记账巡检（只记账、不推送、不交易）。

步骤：
1. 运行 `/usr/bin/python3 ~/workspace/goals/tradingview-binance-indicator-monitoring-with-wecom-alerts/hidden_files/shadow_allfeat_bot.py`，输出 JSON。脚本用全组合模型（FEAT3 + 13 个新特征，lightgbm_shadow_allfeat.txt）对 BTCUSDT/ETHUSDT 计算未来 10 分钟上涨概率，候选时刻为最新已收盘 5m bar 再往前一根（滞后两 tick，因 OI 格点发布延迟）；概率触发 0.55/0.58/0.60 任一档（对称双向）即 append 到同目录 shadow-alerts.jsonl 记账，无去重冷却。
2. final message 只写一行 status 摘要：候选时刻/两标的 prob_up/触发档位/errors 有无。不要向用户推送任何消息（不发 Bark、不写微信 outbox、不在聊天里发预报），不要修改现网 event_10min_bot.py、现网模型或任何阈值。
3. errors 非空时如实写明失败原因（数据源失败/格点缺失属预期降级，脚本下一轮会自动重试未推进的候选），不要编造概率或记账结果。
