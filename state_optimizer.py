"""目标函数与约束的形式化 + 求解器（每状态内最大化收益 s.t. 路径回撤≤5%）"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import engine3 as E

BASE = os.path.dirname(os.path.abspath(__file__))
panel, rets = E.load_panel(full=True)
R = rets.fillna(0.0).values
cols = list(panel.columns)
idx = panel.index
START = "2026-01-05"
SI = int(np.searchsorted(idx, np.datetime64(START)))

p8 = pd.read_csv(os.path.join(BASE, "probs2026_8.csv"), index_col=0, parse_dates=True)
p8 = p8[~p8.index.duplicated(keep="last")].reindex(idx).ffill().fillna(1 / 8)
STATES = list(p8.columns)

SPEC = {
    "objective": "对每个状态 s：max  sum_t r_s(w)  （等价 max 该状态样本内累计收益）",
    "constraint_1": "该状态样本内组合路径最大回撤 >= -5%（逐日复利净值口径）",
    "constraint_2": "w >= 0, sum(w) = 1（ETF 现货多头，不加杠杆）",
    "constraint_3": "单资产上限 35%",
    "mixing_rule": "日频合成：w_t = Σ_s p_{s,t} * w_s ；再乘熔断系数 b_t；偏离目标超过阈值才成交",
    "breaker": "组合回撤触及 5% → b=0.5；修复到 2.5% → b=1",
}

def in_state_days(s, thr=0.5):
    m = p8[s].values >= thr
    pos = [i for i in range(SI, len(idx) - 1) if m[i]]
    return pos

def solve(Rs, cap=0.35, n=60000, seed=7):
    """Rs: T×N 状态内日收益；返回最大收益且 DD>=-5% 的权重"""
    rng = np.random.default_rng(seed)
    N = Rs.shape[1]
    W = rng.dirichlet(np.ones(N) * 0.4, size=n)
    W = np.clip(W, 0, cap)
    W = W / W.sum(axis=1, keepdims=True)
    P = W @ Rs.T                      # n × T
    nav = np.cumprod(1 + P, axis=1)
    dd = (nav / np.maximum.accumulate(nav, axis=1) - 1).min(axis=1)
    tot = nav[:, -1] - 1
    ok = dd >= -0.05
    if not ok.any():
        i = int(np.argmax(dd))
        return W[i], float(tot[i]), float(dd[i]), False
    cand = np.where(ok, tot, -9)
    i = int(np.argmax(cand))
    return W[i], float(tot[i]), float(dd[i]), True

if __name__ == "__main__":
    print("状态样本天数（概率≥0.5）：")
    days = {s: in_state_days(s) for s in STATES}
    for s, d in days.items():
        print(f"  {s}: {len(d)} 天")

    out = {}
    for s, pos in days.items():
        if len(pos) < 15:
            out[s] = dict(n=len(pos), skipped=True)
            continue
        Rs = R[pos]
        w, tot, dd, ok = solve(Rs)
        top = sorted([(cols[j], float(w[j])) for j in range(len(cols)) if w[j] > 0.02], key=lambda x: -x[1])
        out[s] = dict(n=len(pos), total=tot, maxdd=dd, feasible=ok,
                      weights={E.NAMES.get(k, k): round(v, 3) for k, v in top})
        print(f"\n{s}（{len(pos)} 天）：收益 {tot:+.1%} 回撤 {dd:.2%} 可行={ok}")
        print("   权重:", {E.NAMES.get(k, k): f"{v:.0%}" for k, v in top})
    json.dump(dict(spec=SPEC, states=out), open(os.path.join(BASE, "objective_spec.json"), "w"),
              ensure_ascii=False, indent=2, default=str)
    print("\nsaved objective_spec.json")