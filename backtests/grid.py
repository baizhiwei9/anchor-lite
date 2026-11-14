"""样本内网格寻优：在 5% 回撤约束下最大化收益"""
import os, sys, json, itertools
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import engine2 as E

BASE = os.path.dirname(os.path.abspath(__file__))
IS = ("2024-01-02", "2026-04-14")
OOS = ("2018-06-01", "2023-12-29")
STRESS = {"2020-03": ("2020-02-03", "2020-04-30"), "2022": ("2022-01-04", "2022-10-31"),
          "2025-04": ("2025-03-03", "2025-05-30")}  # 2018-02 早于 HMM 可用样本，单独用风险层测试

probs, rets = E.load()
ctx = E.Ctx(probs, rets)

def ev(p, window=IS):
    d = E.run(ctx, window[0], window[1], **p)
    m = E.metrics(d)
    m["params"] = p
    return m, d

grid = dict(vol_target=[0.06, 0.08, 0.10, 0.11, 0.12],
            gross_cap=[1.10],
            dd_trigger=[None, 0.035],
            comm_cap=[None, 0.25],
            hedge_ratio=[0.0, 0.15, 0.30])

rows = []
for vals in itertools.product(*grid.values()):
    p = dict(zip(grid.keys(), vals))
    p.setdefault("dd_cut", 0.5)
    m, _ = ev(p)
    rows.append({**{k: (str(v) if v is None else v) for k, v in p.items()},
                 **{k: m[k] for k in ["cagr", "vol", "maxdd", "sharpe", "calmar", "avg_net", "turnover"]}})
df = pd.DataFrame(rows).sort_values("cagr", ascending=False)
df.to_csv(os.path.join(BASE, "grid_is.csv"), index=False)
print("grid size:", len(df))
ok = df[df["maxdd"] >= -0.05].head(12)
print("\n=== 样本内 DD<=5% 收益率前 12 ===")
print(ok.to_string(index=False))

# 对前 5 名（满足 DD 约束）做样本外与压力验证
top = ok.head(5).to_dict("records")
res = []
for r in top:
    p = dict(vol_target=float(r["vol_target"]), gross_cap=float(r["gross_cap"]),
             dd_trigger=(None if r["dd_trigger"] == "None" else float(r["dd_trigger"])),
             comm_cap=(None if r["comm_cap"] == "None" else float(r["comm_cap"])),
             hedge_ratio=float(r["hedge_ratio"]), dd_cut=0.5)
    mo, _ = ev(p, OOS)
    st = {k: ev(p, w)[0] for k, w in STRESS.items()}
    worst = min([mo["maxdd"]] + [v["maxdd"] for v in st.values()])
    res.append(dict(params=p, is_cagr=float(r["cagr"]), is_dd=float(r["maxdd"]),
                    oos_cagr=mo["cagr"], oos_dd=mo["maxdd"], oos_vol=mo["vol"],
                    stress={k: dict(cagr=v["cagr"], dd=v["maxdd"]) for k, v in st.items()},
                    worst_dd_all=worst))
out = pd.DataFrame([{**{f"p_{k}": v for k, v in x["params"].items()},
                     "is_cagr": x["is_cagr"], "is_dd": x["is_dd"], "oos_cagr": x["oos_cagr"],
                     "oos_dd": x["oos_dd"], "worst_dd_all": x["worst_dd_all"],
                     "s2020": x["stress"]["2020-03"]["dd"], "s2022": x["stress"]["2022"]["dd"],
                     "s2025": x["stress"]["2025-04"]["dd"]}
                    for x in res])
print("\n=== 前 5 名的样本外与压力验证 ===")
print(out.round(4).to_string(index=False))
out.to_csv(os.path.join(BASE, "top5_validate.csv"), index=False)
with open(os.path.join(BASE, "top5_validate.json"), "w") as fh:
    json.dump(res, fh, ensure_ascii=False, indent=2, default=str)