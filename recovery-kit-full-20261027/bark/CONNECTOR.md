# Bark Push

- `custom.bark-push` 把 Muse 接到 Bark iPhone 推送（https://api.day.app）。用注册的 `api_key` 凭证认证，只调用 `api.day.app`。

- 推送脚本在 `~/workspace/custom-connectors/custom.bark-push/bin/bark_send.py`，用法见下文"推送"一节。

## 接入

- 用 `credentials.request_api_access` 注册，参数如下。重连时原样复用，placement 建好后改不了，key 也读不回来：

```json
{
  "provider": "bark-push",
  "auth_scheme": "api_key",
  "placement": "url_path_segment:/{}",
  "api_hosts": ["api.day.app"]
}
```

## 推送

- 运行下面的命令推送到用户 iPhone。推送级别为 timeSensitive，中文直接写：

```text
python3 ~/workspace/custom-connectors/custom.bark-push/bin/bark_send.py "标题" "正文"
```

- 成功返回 `{"code":200,"message":"success"}`。返回 400 多半是 key 无效或凭证没换上，先看"已知坑"。

## 凭证

- 注册的连接持有凭证。helper 通过 `/opt/muse/skills/custom-connector-setup/bin/dynamic_credentials.py` 拿 `hsurr:`  surrogate，Muse 只在发往已注册 host 的已批准请求上把 surrogate 换成真凭证。

- 不要把 key、token、密码、cookie 写进文档、helper、命令行参数、环境变量或日志。也不要让用户在聊天里粘贴。

- 发认证请求时禁用重定向。

- 遇到 401 或 403 先检查请求本身。确认 helper 带上了凭证、host 已注册、placement 对得上。

- 只有当确认带了凭证的请求仍被拒绝时，才用上面的参数加 `"reconnect": true` 调 `credentials.request_api_access` 重连。

## 已知坑

- 不要用 `dynamic_credentials.url_with_surrogate_path_segment()`。它会对 surrogate 做 percent-encoding（`hsurr:` 变成 `hsurr%3A`），Sentinel 认不出字面 `hsurr:` 前缀就不做凭证替换，Bark 会返回 400 `failed to get device token`。正确做法：用 `dynamic_credential_entry("custom.bark-push")` 取 surrogate 后直接字符串替换进 URL，不编码。surrogate 只含 `[a-z0-9:]`，在 path 里不编码是安全的。实现见 `bin/bark_send.py` 的 `bark_url()`（2026-10-18 实测）。

- 凭证替换只发生在出境到已注册 host 时。声称接通前，先用一次真实推送验证。

## 出错恢复

- `400 failed to get device token` 表示 Bark 收到的 key 无效。先确认 helper 发出的 URL 里是字面 `hsurr:`（未被编码），再确认用户填的 key 正确。

- 推送超时或失败后不要直接重发。先看 Bark App 是否收到，避免重复推送。

## 限制

- Bark 免费版推送频率有限制，短时间大量推送会被限流。10 分钟预报 bot 每 5 分钟最多 2 条，在限额内。

- 只做推送，不读消息，不支持回复。

## 相关

- 调用方：10 分钟事件合约预报机器人 `~/workspace/goals/tradingview-binance-indicator-monitoring-with-wecom-alerts/hidden_files/event_10min_bot.py` 的 `send_bark()`。

- 用 `muse.skill_search` 找其他 skill，用 `muse.connector_search` 找其他 connector。
