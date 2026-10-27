---
id: shadow-select-forecast-bot
title: 10分钟预报影子v3（精选版）记账
enabled: true
owner: goal:tradingview-binance-indicator-monitoring-with-wecom-alerts
mode: task
schedule:
  kind: interval
  timezone: Asia/Shanghai
  at: 2026-10-21T22:59:05
  every: 5m
delivery:
  - chat_id: dcdbe34c-9af1-48ff-8c90-071818baf2e6
metadata:
  tags: [cron:automatic-interval-anchor]
  originating_chat_context_json: '{"chat_id":"dcdbe34c-9af1-48ff-8c90-071818baf2e6","origin_provider":"main","chat_kind":"direct","event_kind":"message","require_mention":false,"device_id":"8dfc2762-cd8f-4454-af28-b034cd3d9227"}'
  presentation_locale: zh-CN
---
10 分钟事件合约预报【影子 v3·精选版】记账巡检（只记账、不推送、不交易）。

步骤：
1. 运行 `/usr/bin/python3 ~/workspace/goals/tradingview-binance-indicator-monitoring-with-wecom-alerts/hidden_files/shadow_select_bot.py`，输出 JSON。脚本用现网基线模型（lightgbm_10min.txt + FEAT3 31 特征）对 BTCUSDT/ETHUSDT 计算未来 10 分钟上涨概率，候选时刻与现网一致（最新已收盘 5m bar）；再算实验三事前定义的均值回归规则 mr = −(z(rsi14_5m) + z(boll_pctB_1m))/2（z 用 shadow_rulegate_meta.json 里全量 350 天固定 mean/std，是对实验三逐折统计的固定化近似）。仅当三条同时满足才 append 到同目录 shadow3-alerts.jsonl 记账（字段 t/sym/dir/prob/mr/close，dir 恒为 UP）：(a) 只做多，模型 prob_up ≥0.55；(b) 规则同向，mr ≥ 1；(c) 候选时刻（bar 时间，UTC）落在 00:00–08:00（亚盘）。无方向冷却去重，仅防同一候选重复记账。
2. final message 只写一行 status 摘要：候选时刻/两标的 prob_up 与 mr/时段判断/是否触发记账/errors 有无。不要向用户推送任何消息（不发 Bark、不写微信 outbox、不在聊天里发预报），不要修改现网 event_10min_bot.py、shadow_allfeat_bot.py、shadow_rulegate_bot.py、现网/影子模型或任何阈值。
3. errors 非空时如实写明失败原因（数据源失败属预期降级，脚本下一轮处理新候选），不要编造概率或记账结果。
