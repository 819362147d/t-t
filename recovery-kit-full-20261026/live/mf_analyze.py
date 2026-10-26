#!/usr/bin/env python3
"""多指标10分钟二元预测分析: 单指标排名 / LR权重寻优(做多/做空/双向) / 样本外验证 / 原规则基线.
输出 ~/workspace/goals/tradingview-binance-indicator-monitoring-with-wecom-alerts/hidden_files/multifactor-10min-report-20261017.md"""
import time
import numpy as np
import pandas as pd

HF = "/home/hatch/workspace/goals/tradingview-binance-indicator-monitoring-with-wecom-alerts/hidden_files"
d1 = np.load("/tmp/mf_data/FEAT_BTCUSDT.npz")
d2 = np.load("/tmp/mf_data/FEAT_ETHUSDT.npz")
names = list(d1["names"])
t = np.concatenate([d1["t"], d2["t"]])
sym = np.concatenate([d1["sym"], d2["sym"]])
F = np.vstack([d1["F"], d2["F"]])
y = np.concatenate([d1["y"], d2["y"]]).astype(int)
ties = np.concatenate([d1["ties"], d2["ties"]])
o = np.argsort(t)
t, sym, F, y, ties = t[o], sym[o], F[o], y[o], ties[o]
n = len(y)
SYMNAME = {0: "BTCUSDT", 1: "ETHUSDT"}

def ts(x):
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(x / 1000))

t0, t1 = t[0], t[-1]
tcut = t0 + 0.7 * (t1 - t0)
tr = t < tcut
va = ~tr
base_rate = y.mean()
tie_rate = ties.mean()
n_tr, n_va = int(tr.sum()), int(va.sum())

# ---------- 1. 单指标分位数胜率排名(全样本) ----------
singles = []
detail_tables = {}
for k, nm in enumerate(names):
    v = F[:, k]
    if nm in ("flip_long", "flip_short"):
        m1 = v == 1
        w1 = y[m1].mean() if m1.sum() else float("nan")
        w0 = y[~m1].mean()
        singles.append((nm, w1, int(m1.sum()), "==1" if w1 >= w0 else "==0", max(w1, w0)))
        detail_tables[nm] = [("==1", int(m1.sum()), w1), ("==0", int((~m1).sum()), w0)]
        continue
    try:
        q = pd.qcut(v, 10, labels=False, duplicates="drop")
    except Exception:
        continue
    rows = []
    for g in range(10):
        m = q == g
        if m.sum():
            rows.append((g, int(m.sum()), y[m].mean()))
    detail_tables[nm] = rows
    best = max(rows, key=lambda r: r[2])
    worst = min(rows, key=lambda r: r[2])
    # 取偏离0.5最大的一端
    pick = best if abs(best[2] - 0.5) >= abs(worst[2] - 0.5) else worst
    singles.append((nm, pick[2], pick[1], "decile%d" % pick[0], pick[2]))
singles.sort(key=lambda r: r[1], reverse=True)

# ---------- 2. 逻辑回归(IRLS, numpy) ----------
mu = F[tr].mean(0)
sd = F[tr].std(0)
sd[sd == 0] = 1
Zs = (F - mu) / sd
Xtr = np.column_stack([np.ones(n_tr), Zs[tr]])
ytr = y[tr]
Xva = np.column_stack([np.ones(n_va), Zs[va]])
yva = y[va]

def fit_lr(X, y, l2=1.0, iters=100):
    w = np.zeros(X.shape[1])
    I = np.eye(X.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-X @ w))
        W = p * (1 - p)
        H = (X.T * W) @ X + l2 * I
        g = X.T @ (y - p) - l2 * w
        st = np.linalg.solve(H, g)
        w += st
        if np.abs(st).max() < 1e-8:
            break
    return w

w = fit_lr(Xtr, ytr)
coef = w[1:]
coef_rank = sorted([(names[k], coef[k]) for k in range(len(names))],
                   key=lambda r: abs(r[1]), reverse=True)
s_tr = 1 / (1 + np.exp(-Xtr @ w))
s_va = 1 / (1 + np.exp(-Xva @ w))

def sweep_long(s, y, n_min):
    thrs = np.quantile(s, np.linspace(0.01, 0.99, 150))
    best = None
    for thr in thrs:
        m = s >= thr
        cnt = int(m.sum())
        if cnt < n_min:
            continue
        wr = float(y[m].mean())
        if best is None or wr > best[1]:
            best = (thr, wr, cnt)
    return best

def sweep_short(s, y, n_min):
    thrs = np.quantile(s, np.linspace(0.01, 0.99, 150))
    best = None
    for thr in thrs:
        m = s <= thr
        cnt = int(m.sum())
        if cnt < n_min:
            continue
        wr = float(1 - y[m].mean())
        if best is None or wr > best[1]:
            best = (thr, wr, cnt)
    return best

N_MIN = 1500
bl = sweep_long(s_tr, ytr, N_MIN)
bs = sweep_short(s_tr, ytr, N_MIN)
# 验证
def val_long(thr):
    m = s_va >= thr
    return (float(yva[m].mean()), int(m.sum())) if m.sum() else (float("nan"), 0)
def val_short(thr):
    m = s_va <= thr
    return (float(1 - yva[m].mean()), int(m.sum())) if m.sum() else (float("nan"), 0)
vl = val_long(bl[0]) if bl else (float("nan"), 0)
vs = val_short(bs[0]) if bs else (float("nan"), 0)

# 双向: 单阈值准确率
thrs = np.quantile(s_tr, np.linspace(0.05, 0.95, 120))
ba = None
for thr in thrs:
    acc = float((((s_tr >= thr).astype(int)) == ytr).mean())
    if ba is None or acc > ba[1]:
        ba = (thr, acc)
va_acc = float((((s_va >= ba[0]).astype(int)) == yva).mean())

# 双向: 双阈值(hi做多/lo做空, 中间弃权)
his = np.quantile(s_tr, np.linspace(0.50, 0.99, 14))
los = np.quantile(s_tr, np.linspace(0.01, 0.50, 14))
bd = None
for hi in his:
    for lo in los:
        if lo >= hi:
            continue
        take = (s_tr >= hi) | (s_tr <= lo)
        cnt = int(take.sum())
        if cnt < N_MIN:
            continue
        pred = (s_tr >= hi).astype(int)
        acc = float((pred[take] == ytr[take]).mean())
        if bd is None or acc > bd[2]:
            bd = (hi, lo, acc, cnt)
take_va = (s_va >= bd[0]) | (s_va <= bd[1])
pred_va = (s_va >= bd[0]).astype(int)
vd_acc = float((pred_va[take_va] == yva[take_va]).mean())
vd_n = int(take_va.sum())

# ---------- 3. 原规则基线(15m对齐候选 + 4h去重, 10分钟口径) ----------
al = (t % 900000 == 0)
base = {}
for s in (0, 1):
    idx = np.where((sym == s) & al)[0]
    lastL = lastS = -10 ** 18
    yL, yS = [], []
    for i in idx:
        f = F[i]
        tss, cvz, fl, fs = f[0], f[2], f[11], f[12]
        if fl == 1 and tss > 0.5 and cvz > 0.35 and t[i] - lastL > 4 * 3600 * 1000:
            lastL = t[i]; yL.append(y[i])
        if fs == 1 and tss < -0.5 and cvz < -0.35 and t[i] - lastS > 4 * 3600 * 1000:
            lastS = t[i]; yS.append(y[i])
    yL = np.array(yL); yS = np.array(yS)
    base[SYMNAME[s]] = {
        "long_n": len(yL), "long_wr": float(yL.mean()) if len(yL) else float("nan"),
        "short_n": len(yS), "short_wr": float(1 - yS.mean()) if len(yS) else float("nan"),
    }

# ---------- 4. 写报告 ----------
L = []
L.append("# 多指标 10 分钟二元预测回测报告（2026-10-17）")
L.append("")
L.append("## 数据与口径")
L.append("- 标的: BTCUSDT + ETHUSDT, Binance 现货 5m K 线(原生 5m 包; 全部所需指标均为 5m/15m 口径, 与 1m 重采样数学等价)")
L.append("- 区间: %s ~ %s (UTC)" % (ts(t0), ts(t1)))
L.append("- 候选时刻: 每 5 分钟一个(5m 边界对齐), 双标的合并")
L.append("- 总样本 n=%d (训练前70%% n=%d, 验证后30%% n=%d)" % (n, n_tr, n_va))
L.append("- 目标: outcome=1 若 close[t+10m] > close[t], 否则 0; 平局计 0, 平局比例 %.4f%%" % (tie_rate * 100))
L.append("- 基准上涨概率(全样本): %.4f" % base_rate)
L.append("- 资金费率: fapi/v1/fundingRate 在本区域返回 404(期货 API 被禁), 缺失, 仅用现货指标")
L.append("- 费用: 未计(胜率口径); 指标实现与 event_bot.py 逐行对应, 降级模式无 OI, 全程无未来函数")
L.append("")
L.append("## 单指标胜率排名(全样本, 按最优分组胜率排序)")
L.append("")
L.append("| 排名 | 指标 | 最优分组 | 分组胜率 | 分组 n |")
L.append("|---|---|---|---|---|")
for i, (nm, wr, cnt, grp, _) in enumerate(singles, 1):
    L.append("| %d | %s | %s | %.4f | %d |" % (i, nm, grp, wr, cnt))
L.append("")
L.append("### 各指标十分位明细(胜率 = 该组上涨比例)")
for nm, rows in detail_tables.items():
    L.append("- %s: " % nm + ", ".join("g%s n=%d wr=%.3f" % (g, c, wr) for g, c, wr in rows))
L.append("")
L.append("## 逻辑回归权重(训练段标准化特征, |系数|排序)")
L.append("")
L.append("| 指标 | 系数 |")
L.append("|---|---|")
for nm, c in coef_rank:
    L.append("| %s | %+.4f |" % (nm, c))
L.append("")
L.append("## 组合寻优: 训练 vs 样本外验证")
L.append("")
L.append("### 只做多(信号分>=阈值 则押涨)")
if bl:
    L.append("- 最优阈值 %.4f | 训练胜率 %.4f (n=%d) | 验证胜率 %.4f (n=%d)" % (bl[0], bl[1], bl[2], vl[0], vl[1]))
else:
    L.append("- 训练段无满足 n>=%d 的阈值" % N_MIN)
L.append("### 只做空(信号分<=阈值 则押跌)")
if bs:
    L.append("- 最优阈值 %.4f | 训练胜率 %.4f (n=%d) | 验证胜率 %.4f (n=%d)" % (bs[0], bs[1], bs[2], vs[0], vs[1]))
else:
    L.append("- 训练段无满足 n>=%d 的阈值" % N_MIN)
L.append("### 双向-单阈值(分>=阈值押涨否则押跌, 准确率)")
L.append("- 最优阈值 %.4f | 训练准确率 %.4f | 验证准确率 %.4f" % (ba[0], ba[1], va_acc))
L.append("### 双向-双阈值(>=hi押涨 / <=lo押跌 / 中间弃权)")
L.append("- hi=%.4f lo=%.4f | 训练准确率 %.4f (n=%d) | 验证准确率 %.4f (n=%d)" % (bd[0], bd[1], bd[2], bd[3], vd_acc, vd_n))
L.append("")
L.append("## 对照基线: 原双指标规则在 10 分钟口径下的胜率")
L.append("(15m 翻转 + |TrendScore|>0.5 + |CVD-Z 5m|>0.35, 15m 对齐候选, 同标的同方向 4h 去重)")
L.append("")
for s in ("BTCUSDT", "ETHUSDT"):
    b = base[s]
    L.append("- %s: LONG n=%d 胜率=%.4f | SHORT n=%d 胜率=%.4f" % (s, b["long_n"], b["long_wr"], b["short_n"], b["short_wr"]))
L.append("")
L.append("## 结论")
best_val = max([(vl[0], "只做多", vl[1]), (vs[0], "只做空", vs[1]), (vd_acc, "双向双阈值", vd_n), (va_acc, "双向单阈值", n_va)], key=lambda r: r[0])
L.append("- 最高样本外胜率: %.4f (%s, n=%d)" % (best_val[0], best_val[1], best_val[2]))
L.append("- 权重最大的三个指标: %s" % ", ".join("%s(%+.3f)" % (nm, c) for nm, c in coef_rank[:3]))
L.append("")
L.append("## 局限性")
L.append("- 过拟合风险: 阈值在训练段上按胜率最大挑选, 即使有样本外验证, 多重比较仍会夸大训练胜率; 以验证段为准")
L.append("- 未计费用: 事件合约有价差/手续费, 胜率需高于盈亏平衡点才有意义(赔付率未知, 无法换算期望收益)")
L.append("- 胜率 != 期望收益: 二元期权赔付不对称, 高胜率也可能期望为负")
L.append("- 样本外仅 30%% 时间窗口, 市场机制变化(波动率/趋势性)会使权重失效")
L.append("- 平局计为失败(0), 若真实合约平局退款则实际胜率口径不同")
L.append("- 资金费率缺失; 降级模式无 OI, 与线上机器人一致但信息有损")
open(HF + "/multifactor-10min-report-20261017.md", "w").write("\n".join(L) + "\n")
print("report written")
print("n=%d tr=%d va=%d base=%.4f tie=%.4f" % (n, n_tr, n_va, base_rate, tie_rate))
print("best_val:", best_val)
print("top3 coef:", coef_rank[:3])
print("baseline:", base)
