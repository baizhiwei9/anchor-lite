"""无上限求解：只保留 w≥0、sum=1、状态内回撤≤5%，输出集中度与流动性可实现规模"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import engine3 as E

BASE = os.path.dirname(os.path.abspath(__file__))
panel, rets = E.load_panel(full=True)
R = rets.fillna(0.0).values
cols = list(panel.columns)
idx = panel.index
SI = int(np.searchsorted(idx, np.datetime64("2026-01-05")))
p8 = pd.read_csv(os.path.join(BASE, "probs2026_8.csv"), index_col=0, parse_dates=True)
p8 = p8[~p8.index.duplicated(keep="last")].reindex(idx).ffill().fillna(1 / 8)
liq = pd.read_csv(os.path.join(BASE, "liquidity_capacity.csv")).set_index("key")

def solve_nocap(Rs, n=120000, seed=11, alpha=0.25):
    """无单资产/分组上限，仅 sum=1 与状态内回撤 >= -5%"""
    rng = np.random.default_rng(seed)
    W = rng.dirichlet(np.ones(Rs.shape[1]) * alpha, size=n)
    P = W @ Rs.T
    nav = np.cumprod(1 + P, axis=1)
    dd = (nav / np.maximum.accumulate(nav, axis=1) - 1).min(axis=1)
    tot = nav[:, -1] - 1
    ok = dd >= -0.05
    if not ok.any():
        i = int(np.argmax(dd)); return W[i], float(tot[i]), float(dd[i]), False
    i = int(np.argmax(np.where(ok, tot, -9)))
    return W[i], float(tot[i]), float(dd[i]), True

out = {}
print("状态内无上限最优权重（2026 年内，仅约束 sum=1 与回撤≤5%）\n")
for s in p8.columns:
    pos = [i for i in range(SI, len(idx) - 1) if p8[s].values[i] >= 0.5]
    if len(pos) < 15:
        out[s] = dict(n=len(pos), skipped=True); continue
    w, tot, dd, ok = solve_nocap(R[pos])
    ws = pd.Series(w, index=cols)
    top = ws.sort_values(ascending=False).head(4)
    top = top[top > 0.005]
    maxw = float(ws.max()); hhi = float((ws ** 2).sum())
    # 流动性可实现规模：权重 × 资金，需 ≤ 20日均成交额×30%
    cap_room = (liq.loc[ws.index, "amt20_yi"] * 1e8 * 0.30)
    max_capital = float((cap_room / ws.replace(0, np.nan)).min())   # 该组合能承载的最大资金
    out[s] = dict(n=len(pos), total=tot, maxdd=dd, feasible=ok, max_weight=maxw, hhi=hhi,
                  max_capital_yi=max_capital / 1e8,
                  weights={E.NAMES.get(k, k): round(float(v), 3) for k, v in top.items()})
    print(f"{s}（{len(pos)} 天）：收益 {tot:+.1%} 回撤 {dd:.2%} 可行={ok} | 最大单资产 {maxw:.0%} HHI {hhi:.2f} | 可承载资金约 {max_capital/1e8:.1f} 亿")
    print("   持仓:", {E.NAMES.get(k, k): f"{v:.0%}" for k, v in top.items()})

json.dump(out, open(os.path.join(BASE, "state_weights_nocap.json"), "w"), ensure_ascii=False, indent=2, default=str)
print("\nsaved state_weights_nocap.json")