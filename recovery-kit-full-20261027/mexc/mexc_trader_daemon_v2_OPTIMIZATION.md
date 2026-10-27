# MEXC 实盘守护进程 v2：极限优化说明（2026-10-27）

文件：`hidden_files/mexc_trader_daemon_v2.py`（新文件，live 的 v1 未动）
参数：50x 逐仓、止盈 +85% / 止损 -25% 保证金口径（= 1.7% / 0.5% 价格，比值 3.4 倍）、
止损到爆仓线留 0.5%、单笔 5%、总占用 ≤50%、单日亏 3% 停手、新鲜 ≤4 分钟、
同币不重复开仓、资金不足等平仓。TP 0.85 为用户 2026-10-27 11:51 所定。

## 1. 分段延迟实测表（全部 2026-10-27 当天重测，非旧数字）

| 段 | 实测 | 说明 |
|---|---|---|
| 多币种 bot 单轮 elapsed | **47.8s** | dry-run 实测：宇宙 488、已评分 330、触发 9 条、errors 158 |
| cron 相位延迟 | **约 140s** | cron 锚点 18:22:20 每 5 分钟，跑在 :22:20/:27:20…，5m K 线收盘在 :25/:30…，收盘后约 140s bot 才启动 |
| 账本写入 → daemon 看到 | 0–10s（均值 ~5s） | v1 LOOP_SECONDS=10 轮询 |
| round_t → daemon 首次看到（fresh age） | **均值 206.6s（n=73，186–240s）** | = 相位 140s + bot 47.8s + 轮询 0–10s + 零头 |
| latest_top 全量读 7573 行 | 0.026–0.031s | tail 读 0.0026–0.0032s，省 0.027s（可忽略） |
| get_ticker 单次 | 中位 0.92s（5 次 0.76–1.21s） | 每次新建 HTTPS 连接 |
| get_contract_detail | 2.62s（1179 币种） | 启动时一次，v1 已缓存 |
| plan → opened（有开单的轮） | **中位 13s、p90 27s、max 47s**（n=47；另 26/73 轮无开单） | 每次 attempt ≈ ticker 1 + submit 1–2 + 固定 sleep 10 + 持仓查询 1 + 撤单 1 ≈ 14–17s |
| 最慢实例 06:48 HUMA | 47s | 3 次 attempt（两次未成交撤单重开）+ SUSDT 同轮 510 两次 |
| round_t → 开单合计 | 典型约 220s（206.6+13），最差约 287s | 最新实测：11:54 GRT，fresh age 229s + 12s = **241s** |
| TP 提交失败 | 27 次：510 限流 ×21、2030 oid 超长 ×3、700007 ×3 | 每次失败触发一次无 TP 重试（多一次往返） |

结论：**最大头在上游（相位 140s + bot 48s ≈ 188s，占 85%），daemon 内只占约 13–47s**。
只换 daemon，round_t → 开单仍约 200s；要进 60s 必须同时改上游（见 §4）。

## 2. TP/SL 重复 bug：根因（实测证据）与修复点

- 根因 A——重试循环每次都带 TP/SL 重下单：attempt 1–3 各发一个带
  stopLossPrice/takeProfitPrice 的限价单；attempt 1 已成交但持仓查询恰好
  失败/超时，attempt 2 再发一单，同币两套 TP/SL。证据：06:48 HUMA 3 次
  attempt、06:38 MTL 2 次 attempt。
- 根因 B——撤单不核实：v1 调 cancel_order 后不看结果直接重开；510 限流时
  撤单常失败，旧单带 TP/SL 还挂着，新单又挂一套。
- 根因 C——TP 失败静默降级：带 TP 提交失败后立即用同一 oid 只挂 SL 重试
  （27 次）；第一次提交可能服务端已成功，等于同币第二套止损；复用 oid
  本身也可能被拒。2030 的直接原因是 oid `muse-<13位>-<币种>-a1` 超 32 字符
  （VELODROMEUSDT 为 36 字符）。
- 根因 D——双进程并发：2026-10-27 06:00 与 07:00 两小时日志每行恰好重复
  一次（73/146 与 17/34，其余时段 0 次），即 06:29 会话有两个守护进程同时
  在跑、同时读同一 state、同时给同一币种下单挂 TP/SL。
- 另有两个隐患一并修了：v1 `_close_trade` 调用了未 import 的 `submit_market`
  （HOLD_SECONDS=None 时走不到，启用定时平仓即 NameError）；日志第 30–38 行
  有一次 `AttributeError: 'int' object has no attribute 'get'`（旧版解析
  下单返回 data 为 int 时崩溃，v1 后续版已兼容，v2 沿用兼容写法）。

修复点（v2 内）：
1. TP/SL 只在开仓那一单里附带一次；state 每单记 `tp_sl_set / tp_missing /
   attach_oid / attach_attempts`；已持有或正在开仓的币直接跳过。
2. 下单前先查持仓：已有持仓 → 收编入账，不再下单；state 里没这单则记
   `tp_missing=True` 并 Bark 一次，不补挂（本客户端没有独立改 TP/SL 的接口，
   TP/SL 只能随开仓单附带，事后补挂无安全做法，只能人工确认）。
3. 撤单必须核实：cancel 后用 list_open_orders 轮询确认消失（最多 2s）；
   撤不掉就不发下一单，记 `cancel_unconfirmed` 并 Bark。
4. TP 提交失败不降级：510 只等 1s 重试一次（新 oid、照样带 TP/SL）；
   仍失败则放弃该币、不开裸单，记 `tp_missing`。
5. oid 缩短到 ≤32 字符：`m2 + round_t 后 8 位 + 币种`（重试加 `r1`）。
6. 单进程锁：启动时检查 v1 进程与 v2 pidfile，v1 还在跑就拒绝启动。

## 3. v2 改了哪几处、每处预计省多少秒

| # | 改动 | 预计省时（每轮/每单） |
|---|---|---|
| 1 | 市价单一次成交 + 0.5s 轮询 open_positions（最多 5s），取代限价 10s 等待 + 3 次撤单重开 | 每单省 ~9s；3 次重试场景省 ~28s（47s → ~4s） |
| 2 | LOOP 10s → 1s + 账本 mtime 触发（mtime 不变不读账本、不调 API；manage_exits 仍每 10s） | 省 0–9s，均值 ~4.5s |
| 3 | latest_top 全量读 → tail 读最后 128KB（首行残行丢弃，异常回落全量） | 省 0.027s（可忽略，防账本变大） |
| 4 | HTTP keep-alive（每线程复用一条到 api.mexc.com 的连接，走环境 proxy 隧道；失败回落 urllib） | 每调用省约 0.3–0.7s（估计，待首单实测）；每单 3–4 次调用 → 省 ~1–2s |
| 5 | 逐单落账（as_completed，哪个币先成交先写 state，不等最慢的币） | 06:48 场景：MASK 不再等 HUMA 的 47s，各自 ~3s |
| 6 | ticker 每单只取一次；contract detail 启动拉一次后缓存（缺失 60s 内只补拉一次）；资金同步每轮一次（60s TTL） | 零星 ~1–2s，防 2.6s 补拉卡住开单 |
| 7 | spread >0.25%（止损距离一半）回落限价单只试一次 | 不省时间，防市价滑点吃掉止损距离 |

合计 daemon 侧：中位 13s → **约 2–3s**；p90 27s → **约 3–4s**；max 47s → **约 4–6s**
（含一次 510 重试）。账本写入 → 开单 <5s 可达成。

风险点：
- 市价单滑点：小币种盘口薄，市价可能偏离 last 0.5% 以上，50x 下 0.1%
  价格 = 5% 保证金；已加 spread guard（>0.25% 改限价一次）。taker 费实收
  约 0.06%–0.08%（maker 为 0），每单名义约 5 USDT，约多 0.003–0.004 USDT。
- keep-alive 是新代码，未实盘验证：失败自动回落 urllib 版函数；首单需盯日志。
- 510 限流是交易所侧：并行 3 单 + 0.5s 轮询可能加重，轮询上限 5 次、
  失败退避 1s；若 510 频繁，首选降轮询频率而非加重试。
- FRESH_SECONDS=240 未动：若上游不改，信号仍 3 分多钟才到，v2 只解决 daemon 侧。

## 4. round_t → 开单 <60s 的唯一路径（须改上游，v2 不含）

只换 v2 不够：206.6s 的 fresh age 里 188s 在上游。需两步一起做：
1. cron 重锚：multicoin cron 从 `18:22:20 每 5 分钟` 改到收盘后约 10s 启动
   （如锚点改到 :00:10 / :05:10…），省约 130s 相位延迟。
2. bot elapsed 48s 不动，则 round_t → 开单 ≈ 10 + 48 + 3 ≈ **61s**；
   再想压需优化 bot（提高并发、跳过 400 币种缓存黑名单——32 线程曾测不稳定，
   未采纳，需单独验证）。
信号 10 分钟窗口的意义：现在 t+220s 开单已用掉 37% 的窗口；改后 t+61s
只用掉 10%。

## 5. 切换到 v2 还差什么（只差用户点头 + 一次重启）

1. 用户点头切 v2（本文件即请示依据）。
2. 先停 v1：确认 `ps` 里无 `mexc_trader_daemon.py`（v2 启动时也会自查，
   v1 在跑则拒绝启动并退出码 3）。
3. 前台 exec 起 v2，stdin 重发 key（key 只放进程内存、不落盘；v2 与 v1
   共用同一 state 文件，6 单持仓与已平 70 单直接继承，同币不重开）。
4. 首轮盯 v2 日志 `mexc_trader_v2.log`：确认市价成交约 2–3s、TP/SL 单次挂载
   （`tp_sl_set=True`）、无 `tp_missing / cancel_unconfirmed`。
5. 回滚：停 v2、起 v1 即可（state 格式兼容，多出的字段 v1 会忽略；
   但 v1 仍有 TP/SL 重复 bug，回滚只作应急）。

注意：live v1 当前正在跑（2026-10-27 11:53:48 重启，PID 9684，TP 已是 85%），
v2 未启动、未下过任何单。dry-run 验证见 §6。

## 6. dry-run 验证（2026-10-27 11:58 CST，实测）

- `python3 -m py_compile mexc_trader_daemon_v2.py`：通过（COMPILE_OK）。
- `--dry-run --once`（默认账本）：当时最新轮 t=1793073300000 age=191s，
  plan ILVUSDT 多 prob=0.5561 ref=3.02 vol=1703 margin~0.1029
  tp=3.071 sl=3.005，输出 `dry-run: would open 1 positions`。
  TP/SL 验算：3.02×1.017=3.071 ✓、3.02×0.995=3.005 ✓（85%/25% 口径）。
- 新鲜账本 dry-run（/tmp 测试账本，最新轮 t 改写为当前，age=3s）：
  同样 plan ILVUSDT 并 would open 1 单；单进程锁正常提示
  `v1 守护进程仍在跑（PID 9684）`（dry-run 下只提示不拦截）。
- keep-alive 自测：`get_ticker_fast("BTCUSDT")` 三次调用 0.69s / 0.32s / 0.12s，
  连接复用确认；相对 urllib 中位 0.92s，后续每次调用省约 0.6–0.8s。
- oid 长度：VELODROMEUSDT 23 字符、1000BONKUSDT 22、GRTUSDT 17，重试 +r1 后 25，
  均 ≤32（v1 最长 36 字符报 2030）。
- 密钥扫描：v2 文件不含任何 key/secret，只有参数名；grep 通过。
- 零副作用：两次 dry-run 后 live 的 daemon/state/log mtime 未变
  （daemon 03:51:49、state 03:54:06、log 03:54:08 UTC），
  /tmp 下也未产生任何 state 文件（dry-run 不写 state）；未下过任何单。
