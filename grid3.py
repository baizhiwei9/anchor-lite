"""第三轮：在均线闸门 + 快速熔断基础上，找全窗口 ≤5% 的最高收益配置，并输出风险收益前沿"""
import os, sys, json, itertools
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import engine2 as E

BASE = os.path.dirname(os.path.abspath(__file__))
IS = ("2024-01-02", "2026-04-14"); OOS = ("2018-06-01", "2023-12-29")
STRESS = {"2020-03": ("2020-02-03", "2020-04-30"), "2022": ("2022-01-04", "2022-10-31"),
          "2025-04": ("2025-03-03", "2025-05-30")}

probs, rets = E.load(); ctx = E.Ctx(probs, rets)

grid = dict(vol_target=[0.04, 0.05, 0.06, 0.07, 0.08, 0.10, 0.12],
            fast_x=[None, 0.02, 0.03],
            fast_cut=[0.4, 0.6],
            dd_trigger=[None, 0.02, 0.03],
            dd_cut=[0.5],
            comm_ma_gate=[True],
            comm_cap=[0.25],
            hedge_ratio=[0.0, 0.20])

rows = []
for vals in itertools.product(*grid.values()):
    p = dict(zip(grid.keys(), vals))
    mi = E.metrics(E.run(ctx, *IS, **p)); mo = E.metrics(E.run(ctx, *OOS, **p))
    st = {k: E.metrics(E.run(ctx, *w, **p)) for k, w in STRESS.items()}
    dds = [mi["maxdd"], mo["maxdd"]] + [v["maxdd"] for v in st.values()]
    rows.append({**{k: (str(v) if v is None else v) for k, v in p.items()},
                 "is_cagr": mi["cagr"], "is_dd": mi["maxdd"], "is_vol": mi["vol"], "is_sharpe": mi["sharpe"],
                 "is_calmar": mi["calmar"], "is_net": mi["avg_net"], "is_turn": mi["turnover"],
                 "oos_cagr": mo["cagr"], "oos_dd": mo["maxdd"], "worst_dd": min(dds),
                 "pass5": min(dds) >= -0.05,
                 "s2020": st["2020-03"]["maxdd"], "s2022": st["2022"]["maxdd"], "s2025": st["2025-04"]["maxdd"]})

df = pd.DataFrame(rows)
df.to_csv(os.path.join(BASE, "grid_frontier.csv"), index=False)
ok = df[df["pass5"]].sort_values("is_cagr", ascending=False)
print("组合数:", len(df), " 通过全窗口 ≤5%:", len(ok))
print("\n=== 通过者中收益前 10 ===")
print(ok.head(10).round(4).to_string(index=False))
print("\n=== 按波动预算看：最佳通过配置 / 最佳未通过配置 ===")
for v in sorted(df["vol_target"].unique()):
    sub = df[df["vol_target"] == v]
    passv = sub[sub["pass5"]].sort_values("is_cagr", ascending=False)
    best = passv.iloc[0] if len(passv) else None
    fall = sub.sort_values("is_cagr", ascending=False).iloc[0]
    print(f"vol={v}: 通过{'有' if best is not None else '无'}",
          (f"IS {best['is_cagr']:.3f}/dd {best['is_dd']:.3f} OOS {best['oos_cagr']:.3f}/dd {best['oos_dd']:.3f} worst {best['worst_dd']:.3f}"
           if best is not None else ""),
          f"|| 最高收益 {fall['is_cagr']:.3f} worst_dd {fall['worst_dd']:.3f}")