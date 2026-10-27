# 影子版·全组合：实盘数据可得性实测 + 上线报告（2026-10-20 晚）

纯影子：只记账、不推送、不动现网（event_10min_bot.py / 现网模型 / 现网 cron
均未改动，已用 live-alerts.jsonl 前后 diff 验证现网账本分毫未动）。

## 1. 实盘数据可得性（本 VM 逐项实测，2026-10-20 21:44–22:00 CST）

| 数据 | 结论 | 实测细节 |
|---|---|---|
| 现货 K 线全字段（taker-buy/qv） | ✅ 可得 | data-api.binance.vision /api/v3/klines（与现网同源），每根 12 字段，qv=字段7、tbv=字段9 齐全，HTTP 200，~2s |
| OI（openInterestHist） | ✅ 可得（绕行） | fapi.binance.com 直连 **HTTP 451 地域封锁**（全部 fapi 端点同）；**www.binance.com 网关**的 /futures/data/openInterestHist 实测 200：500 点、5m 格点完整无缺口、最新点延迟约 2 分钟内。Vision metrics 当日文件不存在、昨日文件次日 06:37 UTC 才发布——OI 实盘只能走 REST，Vision 仅供历史 |
| 合约 K 线（基差分子） | ✅ 可得（绕行） | www 网关 /fapi/v1/klines 200，~1.2s |
| 标记价 K 线 | ✅ 可得（绕行） | www 网关 /fapi/v1/markPriceKlines 200，~0.7s |
| 资金费率历史 | ✅ 可得（绕行） | www 网关 /fapi/v1/fundingRate 200，8h 格点；与 exp2 存档重叠段 10/10 点精确一致（另 4 点是存档 9-30 截止后的新档）。Vision 无 fundingRate 日包（404），月包滞后一个月——实盘只能走 REST |
| fapi1/fapi2 备用域 | ❌ 不可用 | HTTP 202 空响应（WAF 拦截），无 JSON |
| data-api.binance.vision 上的 futures 路径 | ❌ 不存在 | /fapi、/futures 路径均 404（该域名仅现货） |

**一句话：13 个新特征实盘全部可得**，合约侧统一走 www.binance.com 网关
（官方 fapi 域名被地域封锁）。该网关是官网公开端点的同一组接口，返回真实
合约数据（premiumIndex 的 markPrice 与现货价、盘口量级互洽，并已用存档
逐点比对证伪造可能，见下）。风险提示：www 网关属非文档化路径，稳定性
不如官方 fapi 域名；影子脚本对每类数据都做了失败降级（拉不到就跳过该
候选并记日志、下一轮重试），不会用假数据凑特征。

## 2. OI 数列一致性（关键校验，逐点比对）

- 存档（exp2 回测所用 Vision metrics）与 REST 是同一条数列，但**标签错位
  一格**：存档 create_time X 的值 == REST 标签 X+300000 的值。
  - 新鲜全天验证：2026-10-19 日包 vs REST，H1 假设 **288/288 点精确一致**
    （H0/H2 均为 0/288）。
  - 旧窗口（10-15~10-16）500 点：414 点精确一致（82.8%）；残差全部集中在
    **2026-10-15 约 11:45–15:10 UTC**——即实验二已知的存档坏 tick 时段，
    该时段存档值本身失真（BTC 偏离 ≤3%、ETH ≤23%，REST 为现行数据库值）。
    这是实验二的一个数据质量瑕疵：该窗内候选点的回测 OI 特征受污染
    （样本占比小，不改实验二结论量级，但如实记录）。
- 影子按 **A(X) := REST(X+300000)** 复刻存档数列，索引偏移与 exp2 逐项一致。
- **特征复刻总校验**：取回测候选 2026-10-16 10:00 UTC（BTC），用 REST 历史
  数据重算全部 13 个新特征 vs exp2 存档 G 行：**11 个逐位精确一致（差 0.0），
  含 oi_chg_1h/oi_chg_24h**；tbr5_dev288、qvol_z288 仅浮点求和顺序级差异
  （≤2e-14）；fund_last 差 1.97e-06——原因是实验二的 2026-10-01~16 资金费率
  为 9-30 末档前向填充（其报告已披露），存档值是旧值、实盘 REST 是真实
  最近一档。此项不是复刻错误：实盘资金费率用真数据，反而更贴特征定义；
  训练集尾段约 4.6% 样本的 G3 两腿是填充值，属已知工件。

## 3. 影子版已上线

- **模型**：train_shadow_allfeat.py 全量 350 天重训（n=200,854，FEAT3+13，
  参数/定轮协议与现网 train_10min_models.py 一致，best_iter=45，确定性
  重跑复现一致）→ lightgbm_shadow_allfeat.txt + model_shadow_allfeat_meta.json。
  期望口径仍以实验二 walk-forward 为准：0.55 档 0.566（n=26,926）、
  0.58 档 0.589（n=6,617）、0.60 档约 0.596（n=2,304），线索级。
- **脚本**：shadow_allfeat_bot.py。FEAT3 部分直接 import 现网 compute_features
  （同管线）；每轮处理候选 = 最新已收盘 5m bar 再往前一根（滞后两 tick：
  A(t) 所需 REST 标签在周期开始后约 1–2 分钟才发布，实测 1.5 分钟时缺失、
  1.8 分钟已出现，滞后两 tick 后该标签已发布 5 分钟以上，稳定可得）。
  失败候选不推进状态、下一轮自动回溯重试（上限 12 个候选）。
- **记账**：shadow-alerts.jsonl，字段与 live-alerts.jsonl 对齐
  （t/sym/dir/prob/close）+ band + model；三档 0.55/0.58/0.60 对称双向，
  触发即记，**无 4h 冷却**（与现网账本口径差异，对比时注意：现网 0.55 档
  数字是冷却过滤后的）。记账写盘已单测验证格式。
- **结算**：shadow_winrate_watch.py（克隆现网结算口径：入场=候选时刻现货
  收盘，出场=t+10min 的 1m 收盘，t<now−12min 才结算），按 band 分别输出
  n_total/n_settled/wins/win_rate。
- **cron**：shadow-allfeat-forecast-bot，每 5 分钟，owner 同 goal，任务正文
  明确只跑脚本记账、不推送、不通知、不改现网。
- **端到端实测**：dry-run 与正式运行各一次均成功（候选 2026-10-20 21:45 CST
  收盘：BTC prob 0.4889、ETH 0.4914，未触发任何档位，errors 为空；状态文件
  双标的正常推进；现网 live-alerts.jsonl 前后 diff 为空）。

## 4. 对比与晋级纪律（不变）

- 影子与现网同场跑；晋级门槛沿用既定纪律：影子某档已结算 ≥30 条且明显
  赢现网同档，才谈换模型。0.58/0.60 档触发率低（回测口径约每日数条），
  攒够 30 条需要数周，按日用 shadow_winrate_watch.py 查即可。
- 注意两个口径差异：① 影子无冷却、同候选可三档各记一条（分开看 band）；
  ② 影子候选滞后两 tick（10 分钟），与现网账本不是同一候选时刻集合，
  对比胜率时按 band 级别比，不做逐条配对。
- OI 特征的语义提醒：存档/REST 错位一格意味着回测与影子的 oi_chg 用的都是
  「标签时刻 +5 分钟处 stamped 的 OI 快照」构造的数列（存档 create_time 为
  桶起始、值为桶末快照的口径）。影子与回测逐点同构，胜率对比有效；若日后
  想检验「严格因果版 OI」（只用候选时刻已知的快照）是否还有增益，需要
  另做一轮 walk-forward，不在本任务范围。
