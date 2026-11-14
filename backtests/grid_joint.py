"""联合约束网格：要求样本内 + 样本外 + 三个压力窗口的最大回撤全部 ≤5%"""
import os, sys, json, itertools
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import engine2 as E

BASE = os.path.dirname(os.path.abspath(__file__))
IS = ("2024-01-02", "2026-04-14"); OOS = ("2018-06-01", "2023-12-29")
STRESS = {"2020-03": ("2020-02-03", "2020-04-30"), "2022": ("2022-01-04", "2022-10-31"),
          "2025-04": ("2025-03-03", "2025-05-30")}

probs, rets = E.load(); ctx = E.Ctx(probs, rets)

grid = dict(vol_target=[0.03, 0.04, 0.05, 0.06, 0.07, 0.08],
            dd_trigger=[None, 0.02, 0.03],
            dd_cut=[0.4, 0.6],
            comm_cap=[0.15, 0.25],
            hedge_ratio=[0.0, 0.30])

rows = []
for vals in itertools.product(*grid.values()):
    p = dict(zip(grid.keys(), vals))
    d_is = E.run(ctx, *IS, **p); m_is = E.metrics(d_is)
    d_oos = E.run(ctx, *OOS, **p); m_oos = E.metrics(d_oos)
    st = {k: E.metrics(E.run(ctx, *w, **p)) for k, w in STRESS.items()}
    dds = [m_is["maxdd"], m_oos["maxdd"]] + [v["maxdd"] for v in st.values()]
    rows.append({**{k: (str(v) if v is None else v) for k, v in p.items()},
                 "is_cagr": m_is["cagr"], "is_dd": m_is["maxdd"], "is_vol": m_is["vol"],
                 "is_sharpe": m_is["sharpe"], "is_calmar": m_is["calmar"],
                 "is_net": m_is["avg_net"], "is_turn": m_is["turnover"],
                 "oos_cagr": m_oos["cagr"], "oos_dd": m_oos["maxdd"],
                 "worst_dd": min(dds), "pass5": min(dds) >= -0.05,
                 "s2020": st["2020-03"]["maxdd"], "s2022": st["2022"]["maxdd"], "s2025": st["2025-04"]["maxdd"]})

df = pd.DataFrame(rows)
df.to_csv(os.path.join(BASE, "grid_joint.csv"), index=False)
ok = df[df["pass5"]].sort_values("is_cagr", ascending=False)
print("总组合:", len(df), " 通过全部 ≤5% 的:", len(ok))
print(ok.head(15).round(4).to_string(index=False))
print("\n=== 边界：最好的 5 组通不过的（看差多少）===")
print(df.sort_values("is_cagr", ascending=False).head(5).round(4).to_string(index=False))