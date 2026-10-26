---
id: live-winrate-watch
title: 实盘胜率追踪（n≥30 报告）
enabled: true
owner: goal:tradingview-binance-indicator-monitoring-with-wecom-alerts
mode: task
schedule:
  kind: daily
  timezone: Asia/Shanghai
  time: 09:09:00
delivery:
  - chat_id: dcdbe34c-9af1-48ff-8c90-071818baf2e6
metadata:
  tags: [cron:flexible-time]
  originating_chat_context_json: '{"chat_id":"dcdbe34c-9af1-48ff-8c90-071818baf2e6","origin_provider":"main","chat_kind":"direct","event_kind":"message","require_mention":false,"device_id":"8dfc2762-cd8f-4454-af28-b034cd3d9227"}'
  presentation_locale: zh-CN
---
实盘胜率追踪（10 分钟预报 bot）。每天跑一次，只在有结论时打扰用户。

步骤：
1. 运行 `python3 ~/workspace/goals/tradingview-binance-indicator-monitoring-with-wecom-alerts/hidden_files/live_winrate_watch.py`，输出 JSON（n_total/n_settled/wins/win_rate）。
2. 如果脚本报错或输出解析失败：final message 只写 status 说明检查失败，不要编造数字。
3. 如果 n_settled ≥ 30 且 `~/workspace/goals/tradingview-binance-indicator-monitoring-with-wecom-alerts/hidden_files/winrate-reported-30.flag` 不存在：在主聊天报告一次真实水位（中文、简短直接）：已结算 n_settled 条、命中 wins、胜率 win_rate；对比回测期望 0.60~0.65 给一句诚实结论；说明未计手续费、不构成交易建议。报告后写入该 flag 文件（内容为报告日期），避免每天重复报告。
4. 如果 n_settled < 30 或 flag 已存在：final message 只写 status（如"n_settled=X/30，未到阈值"），不要打扰用户。

注意：ledger 从 2026-10-18 21:42 起记录（含 4 小时去重后的实盘预报）；不要回算 ledger 之前的条目。
