"""2026 年内：静态事后最优（上限） + 可执行规则（无未来信息）"""
import os, sys, json, itertools
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import engine3 as E

BASE = os.path.dirname(os.path.abspath(__file__))
panel, rets = E.load_panel()
R = rets.fillna(0.0)
P = panel

# ---------- 1) 事后最优静态组合（上限，含未来信息） ----------
SEL = ["oil", "nasdaq", "treasury10_etf", "citybond", "dividend", "sp500", "soybean", "coal", "gold", "lowvol"]
Rs = R[SEL].values
rng = np.random.default_rng(11)
W = rng.dirichlet(np.ones(len(SEL)) * 0.55, 300000)
port = W @ Rs.T
nav = np.cumprod(1 + port, axis=1)
dd = (nav / np.maximum.accumulate(nav, axis=1) - 1).min(axis=1)
tot = nav[:, -1] - 1
vol = port.std(axis=1, ddof=1) * np.sqrt(252)
mask = dd >= -0.05
best_i = np.argsort(-tot * mask)[:5]
ceiling = [dict(weights={E.NAMES.get(a, a): round(float(x), 3) for a, x in zip(SEL, W[i]) if x > 0.02},
                total=float(tot[i]), maxdd=float(dd[i]), vol=float(vol[i])) for i in best_i]
print("事后最优静态组合（DD<=5%）：")
for c in ceiling:
    print(f"  收益 {c['total']:+.1%} DD {c['maxdd']:.2%} vol {c['vol']:.1%} {c['weights']}")

# ---------- 2) 可执行规则：逆波动 + 月度再平衡 + 波动预算 + 熔断 ----------
def rule(vol_target=0.08, dd_trigger=0.02, dd_cut=0.5, cost_bp=5.0, rebal="M", top=None):
    idx = P.index
    vol60 = (R[SEL].rolling(60).std() * np.sqrt(252)).clip(lower=0.01)
    nav = peak = 1.0
    br = 1.0
    w = pd.Series(0.0, index=SEL)
    w_prev = pd.Series(0.0, index=SEL)
    rows = []
    for i in range(61, len(idx) - 1):
        d = idx[i]
        new_month = (i == 61) or (idx[i].month != idx[i - 1].month)
        if new_month or rebal == "D":
            iv = 1.0 / vol60.iloc[i]
            w = iv / iv.sum()
        cov = R[SEL].iloc[max(0, i - 59):i + 1].cov().values * 252
        sig = float(np.sqrt(max(w.values @ cov @ w.values, 1e-8)))
        scale = float(np.clip(vol_target / sig, 0.10, 1.0))
        ww = w * scale * br
        pnl = float(np.nansum(ww.values * R[SEL].iloc[i + 1].values))
        cost = float((ww - w_prev).abs().sum()) * cost_bp / 10000.0
        ret = pnl - cost
        nav *= 1 + ret
        peak = max(peak, nav)
        dd = nav / peak - 1
        if dd_trigger is not None:
            if dd <= -dd_trigger:
                br = 1.0 - dd_cut
            elif dd >= -dd_trigger / 2:
                br = 1.0
        rows.append((idx[i + 1], ret, nav, dd, *ww.values, sig, scale, br))
        w_prev = ww
    return pd.DataFrame(rows, columns=["date", "ret", "nav", "dd"] + SEL + ["sig", "scale", "br"]).set_index("date")

rows = []
for vt in (0.04, 0.06, 0.08, 0.10, 0.12, 0.15):
    for dt in (None, 0.02, 0.03):
        d = rule(vol_target=vt, dd_trigger=dt)
        r = d["ret"].values; n = (1 + r).cumprod(); dd = n / np.maximum.accumulate(n) - 1
        rows.append(dict(vol_target=vt, dd_trigger=dt, total=float(n[-1] - 1),
                         maxdd=float(dd.min()), vol=float(r.std(ddof=1) * np.sqrt(252)),
                         avg_expo=float(d[SEL].abs().sum(axis=1).mean())))
df = pd.DataFrame(rows)
print("\n规则版（逆波动+月度再平衡）：")
print(df.round(4).to_string(index=False))
df.to_csv(os.path.join(BASE, "rule_2026.csv"), index=False)

# ---------- 3) 分半稳健性 ----------
def split_stats(d):
    r = d["ret"]
    first = r.iloc[: len(r) // 2]; second = r.iloc[len(r) // 2:]
    out = {}
    for k, s in (("h1", first), ("h2", second)):
        n = (1 + s).cumprod()
        out[k] = dict(total=float(n.iloc[-1] - 1), maxdd=float((n / n.cummax() - 1).min()))
    return out

rob = {}
for vt in (0.06, 0.08, 0.10, 0.12):
    d = rule(vol_target=vt, dd_trigger=0.02)
    rob[str(vt)] = split_stats(d)
print("\n分半稳健性（2% 熔断）：", json.dumps(rob, ensure_ascii=False, indent=1))

json.dump(dict(ceiling=ceiling, rule=rows, split=rob), open(os.path.join(BASE, "opt2026.json"), "w"),
          ensure_ascii=False, indent=2)