"""各状态最优权重（含分组约束）→ 状态 / 因子暴露 / 持仓权重 对照表"""
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
STATES = list(p8.columns)
expo = pd.read_csv(os.path.join(BASE, "factor_exposure_20261106.csv"), index_col=0)
expo = expo[[c for c in cols if c in expo.columns]].T  # asset × factor

COMMOD = ["oil", "soybean", "coal", "gold", "nonferrous"]
BOND = ["treasury10_etf", "citybond", "treasury_etf", "treasury_active", "corp_bond"]
OVERSEAS = ["nasdaq", "sp500", "nikkei", "hstech", "china_internet"]
GROUP = {}
for c in COMMOD: GROUP[c] = "commodity"
for c in BOND: GROUP[c] = "bond"
for c in OVERSEAS: GROUP[c] = "overseas"
for c in cols:
    GROUP.setdefault(c, "a_share")

def solve(Rs, cap=0.35, comm_max=0.40, bond_min=0.20, ovs_max=0.35, n=80000, seed=5):
    rng = np.random.default_rng(seed)
    N = len(cols)
    W = rng.dirichlet(np.ones(N) * 0.35, size=n)
    W = np.clip(W, 0, cap)
    W /= W.sum(axis=1, keepdims=True)
    gi = np.array([GROUP[c] for c in cols])
    ok = np.ones(n, dtype=bool)
    ok &= W[:, gi == "commodity"].sum(axis=1) <= comm_max
    ok &= W[:, gi == "bond"].sum(axis=1) >= bond_min
    ok &= W[:, gi == "overseas"].sum(axis=1) <= ovs_max
    if ok.sum() < 100:
        return None
    Wk = W[ok]
    P = Wk @ Rs.T
    nav = np.cumprod(1 + P, axis=1)
    dd = (nav / np.maximum.accumulate(nav, axis=1) - 1).min(axis=1)
    tot = nav[:, -1] - 1
    m = dd >= -0.05
    if not m.any():
        i = int(np.argmax(dd)); return Wk[i], float(tot[i]), float(dd[i]), False
    cand = np.where(m, tot, -9)
    i = int(np.argmax(cand))
    return Wk[i], float(tot[i]), float(dd[i]), True

out = {}
print("状态样本与最优权重（2026 年内，含分组约束：单资产≤35%、商品≤40%、债券≥20%、海外≤35%）\n")
for s in STATES:
    pos = [i for i in range(SI, len(idx) - 1) if p8[s].values[i] >= 0.5]
    if len(pos) < 15:
        out[s] = dict(n=len(pos), skipped=True)
        continue
    r = solve(R[pos])
    if r is None:
        out[s] = dict(n=len(pos), infeasible=True); continue
    w, tot, dd, feasible = r
    ws = pd.Series(w, index=cols)
    gsum = {g: float(ws[[c for c in cols if GROUP[c] == g]].sum()) for g in ("a_share", "overseas", "commodity", "bond")}
    top = list(ws.sort_values(ascending=False).head(6).index)
    key = list(dict.fromkeys(["oil", "soybean"] + top))
    fac = (ws[expo.index].values[:, None] * expo.values).sum(axis=0)
    out[s] = dict(n=len(pos), total=tot, maxdd=dd, feasible=feasible,
                  groups={k: round(v, 3) for k, v in gsum.items()},
                  weights={E.NAMES.get(c, c): round(float(ws[c]), 3) for c in key},
                  exposure={f: round(float(fac[i]), 2) for i, f in enumerate(expo.columns)
                            if f in ("MOM_60D", "VOL_60", "BETA_60_HS300", "MAXDD_120", "TREND_MA60")})
    print(f"{s}（{len(pos)} 天）：收益 {tot:+.1%} 回撤 {dd:.2%} 可行={feasible}")
    print(f"   分组：{out[s]['groups']}")
    print(f"   持仓：{ {k: f'{v:.0%}' for k, v in out[s]['weights'].items()} }")
    print(f"   组合因子暴露：{out[s]['exposure']}\n")

json.dump(out, open(os.path.join(BASE, "state_weights.json"), "w"), ensure_ascii=False, indent=2, default=str)
print("saved state_weights.json")