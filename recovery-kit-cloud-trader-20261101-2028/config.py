"""Cloud Trader 参数（单一真源）。

参数沿用旧实盘系统的已定口径（见 t-t 仓库 20261027-1925 快照的
mexc_trader_daemon.py 与 event_10min_bot.py），硬规则在 risk.py 里强制执行，
改这里的参数不能绕过硬规则（比值门、爆仓线缓冲门在下单路径上逐单校验）。
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.join(HERE, "runtime")
STATE_FILE = os.path.join(RUNTIME_DIR, "paper_state.json")
LOG_FILE = os.path.join(RUNTIME_DIR, "cloud_trader.log")
MODEL_FILE = os.path.join(HERE, "models", "lightgbm_10min.txt")
LOCAL_CONFIG = os.path.join(HERE, "config_local.json")  # 不入库：只放 Bark key 等本地项

# 交易品种：MEXC 合约符号。本机符号 -> (信号符号, 跨标的动量参考符号)
# 跨标的动量口径与旧实盘一致：多币种版统一用 BTC 5m 做市场参考；
# BTC 本身沿用单币版口径，用 ETH 做参考（ETH 只拉数据、不交易）。
SYMBOLS = ["XAU_USDT", "BTC_USDT", "TSMSTOCK_USDT", "USOIL_USDT"]
CROSS_REF = {
    "BTC_USDT": "ETH_USDT",
    "TSMSTOCK_USDT": "BTC_USDT",
    "XAU_USDT": "BTC_USDT",
    "BNB_USDT": "BTC_USDT",
    "USOIL_USDT": "BTC_USDT",
    "QQQSTOCK_USDT": "BTC_USDT",
    "SPY_USDT": "BTC_USDT",
}

# K 线需求（与旧实盘一致）：1m 500 根、5m 1500 根（含指标热身余量）
K1M_LIMIT = 500
K5M_LIMIT = 1500

# 信号阈值（旧实盘 GBM_reg 口径，event_10min_bot.py）
LONG_THR = 0.55
SHORT_THR = 0.45

# 执行 / 风控（旧 daemon 口径）
LEVERAGE = 50                 # 逐仓 50x（用户硬规则）
OPEN_TYPE_ISOLATED = 1
MARGIN_PCT = 0.10             # 单笔保证金 = 权益的 10%（用户硬规则）
TP_MARGIN_PCT = 0.85          # 止盈 +85%（保证金口径）
SL_MARGIN_PCT = 0.25          # 止损 -25%（保证金口径）
TP_PCT = TP_MARGIN_PCT / LEVERAGE   # = 1.7% 价格
SL_PCT = SL_MARGIN_PCT / LEVERAGE   # = 0.5% 价格
MIN_TP_SL_RATIO = 2.0         # 硬规则：止盈距离 >= 2 x 止损距离（对即刻市价）
MIN_LIQ_BUFFER_PCT = 0.005    # 硬规则：止损线到爆仓线至少留 0.5% 价格反应空间
# 纸面/预估爆仓距离：名义强平约 2%（1/杠杆），维持保证金会把强平线拉近，
# 旧实盘 FRAX 实测约 1.05%；纸面与下单前预检按 1.05% 估算，实盘成交后用真实爆仓价复核。
EST_LIQ_PCT = 0.0105
DAILY_STOP_PCT = 0.03         # 单日累计已实现亏损达权益 3% 当天停开
FRESH_SECONDS = 240           # 只做新鲜信号：轮次年龄 <= 4 分钟
LIMIT_MAX_ATTEMPTS = 3        # 实盘限价未成交撤单重开次数（纸面不适用）
LIMIT_WAIT_SECONDS = 10

# 摩擦过滤（旧 daemon friction_check 口径，2026-11-01）
FEE_TAKER_PCT = 0.0008              # MEXC API taker 单边 0.08%
FEE_MAKER_PCT = 0.0006              # MEXC API maker 单边 0.06%（纸面记账按 taker 保守口径，不用此值）
FEE_ROUNDTRIP_PCT = 2 * FEE_TAKER_PCT   # 开 + 平 = 0.16%（名义仓位口径）
SLIP_BUFFER_PCT = 0.0005            # 止损/止盈触发成交偏差缓冲 0.05%（对交易者不利方向）
DEFAULT_SPREAD_PCT = 0.003          # 取不到 bid1/ask1 时的保守价差 0.3%
MIN_NET_RR = 2.0                    # 摩擦过滤门：净止盈/净止损 >= 2.0 才开

# 桶门（2026-11-01 用户确认上实盘）：新开仓除全部既有硬规则外，还要求信号的
# 方向对齐 mom_10m 落进该标的冻结的 D5 十分位带。口径与第六遍桶研究
# （event_optim/analyze_buckets.py）逐字一致：
#   对齐坐标 a = -dir_sign * mom_10m（dir_sign：多头 +1 / 空头 -1）；
#   带 = 该标的搜索段信号子集（prob>=0.55 / <=0.45）上 a 的 [q40, q50) 区间，
#   即 np.digitize(a, 该标的 9 条十分位边) == 4 的第 5 桶。
# 冻结前已做一致性校验：用这组带复现研究合并（7 标的）验证段 win10
# = 62.3%（n=122），与 REPORT_BUCKETS 第六节逐值吻合，重算边与研究
# 结果 JSON 的边最大差 = 0。
# 语义：mom_10m 缺失/非有限、或标的不在带表中，一律拦截（fail-closed），
# 拦截在 live_engine 里以 "BUCKET-GATE BLOCK" 逐条落日志，供前向审计。
MOM10M_D5_BANDS = {
    "XAU_USDT": (0.00032669126151425599, 0.00047623873865898503),
    "BTC_USDT": (0.00073130341310512215, 0.0010546938352966606),
    "TSMSTOCK_USDT": (0.00052549363742775417, 0.0007875143184421507),
    "USOIL_USDT": (0.00098915963739361064, 0.0012774981070419189),
}

# 纸面账户
PAPER_START_EQUITY = 100.0    # 默认纸面权益 USDT，可用 --equity 覆盖

# 循环
LOOP_SECONDS = 10             # 循环模式下平仓检查间隔（信号轮次仍按 5 分钟 K 线边界）
