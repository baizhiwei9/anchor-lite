"""稳健性：成本 10bp、参数邻域、月度回撤口径、期权对冲增量、以及基准对照"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import engine2 as E

BASE = os.path.dirname(os.path.abspath(__file__))
IS = ("2024-01-02", "2026-04-14"); OOS = ("2018-06-01", "2023-12-29")
STRESS = {"2020-03": ("2020-02-03", "2020-04-30"), "2022": ("2022-01-04", "2022-10-31"),
          "2025-04": ("2025-03-03", "2025-05-30")}
probs, rets = E.load(); ctx = E.Ctx(probs, rets)

BEST = dict(vol_target=0.07, dd_trigger=0.02, dd_cut=0.5, comm_ma_gate=True,
            comm_cap=0.25, hedge_ratio=0.0, fast_x=None)
SAFE = dict(vol_target=0.05, dd_trigger=0.02, dd_cut=0.5, comm_ma_gate=True,
            comm_cap=0.25, hedge_ratio=0.0)
LOOSE = dict(vol_target=0.12, comm_ma_gate=True, comm_cap=0.25, hedge_ratio=0.0)
DELIV = dict(vol_target=0.10, comm_ma_gate=True, comm_cap=0.25, hedge_ratio=0.0)  # 上一份报告的基准

def full(p, label):
    mi = E.metrics(E.run(ctx, *IS, **p)); mo = E.metrics(E.run(ctx, *OOS, **p))
    st = {k: E.metrics(E.run(ctx, *w, **p)) for k, w in STRESS.items()}
    return dict(label=label, params={k: (str(v) if v is None else v) for k, v in p.items()},
                is_cagr=mi["cagr"], is_dd=mi["maxdd"], is_vol=mi["vol"], is_sharpe=mi["sharpe"],
                is_calmar=mi["calmar"], is_month_dd=mi["month_dd"], is_net=mi["avg_net"],
                is_turn=mi["turnover"], is_sv_days=mi["sv_days"],
                oos_cagr=mo["cagr"], oos_dd=mo["maxdd"], oos_vol=mo["vol"], oos_sharpe=mo["sharpe"],
                worst=min([mi["maxdd"], mo["maxdd"]] + [v["maxdd"] for v in st.values()]),
                s2020=st["2020-03"]["maxdd"], s2022=st["2022"]["maxdd"], s2025=st["2025-04"]["maxdd"])

rows = [full(BEST, "5%约束·最优"), full(SAFE, "5%约束·保守"),
        full(DELIV, "上一版基准(10%波动)"), full(LOOSE, "放开到12%波动")]
# 稳健性
rows.append(full({**BEST, "cost_bp": 10}, "最优·成本10bp"))
rows.append(full({**BEST, "hedge_ratio": 0.20}, "最优·加期权尾部对冲20%"))
rows.append(full({**BEST, "vol_target": 0.06}, "最优·波动预算-1pct"))
rows.append(full({**BEST, "vol_target": 0.08}, "最优·波动预算+1pct"))
rows.append(full({**BEST, "comm_cap": 0.30}, "最优·商品上限放到30%"))

df = pd.DataFrame(rows)
df.to_csv(os.path.join(BASE, "robust.csv"), index=False)
print(df[["label", "is_cagr", "is_dd", "is_month_dd", "is_vol", "is_sharpe", "oos_cagr", "oos_dd", "worst",
          "s2020", "s2022", "s2025"]].round(4).to_string(index=False))

# 基准：买入持有
def bh(col, w=(1.0,), legs=None, a=IS, b=None):
    b = b or a
    r = rets.loc[a[0]:a[1], [x for x in (legs or [col])]]
    pass

def bh_series(legs, weights, a, b):
    idx = [d for d in rets.loc[a:b].index if d in probs.index]
    rr = rets.loc[idx, legs].fillna(0).values
    port = rr @ np.array(weights)
    nav = np.cumprod(1 + port)
    dd = nav / np.maximum.accumulate(nav) - 1
    n = len(port)
    return dict(cagr=float(nav[-1] ** (252 / n) - 1), maxdd=float(dd.min()),
                vol=float(port.std(ddof=1) * np.sqrt(252)))

bench = {
    "沪深300ETF": bh_series(["hs300"], [1.0], *IS),
    "60/40(沪深300+国债期货多)": bh_series(["hs300", "t10"], [0.6, 0.4], *IS),
    "等权四资产(300/科创50/原油/有色)": bh_series(["hs300", "kc50", "oil", "nonferrous"], [0.25] * 4, *IS),
}
bench_oos = {
    "沪深300ETF": bh_series(["hs300"], [1.0], *OOS),
    "60/40(沪深300+国债期货多)": bh_series(["hs300", "t10"], [0.6, 0.4], *OOS),
    "等权四资产(300/科创50/原油/有色)": bh_series(["hs300", "kc50", "oil", "nonferrous"], [0.25] * 4, *OOS),
}
print("\n=== 买入持有基准（样本内）===")
print(pd.DataFrame(bench).T.round(4).to_string())
print("\n=== 买入持有基准（样本外）===")
print(pd.DataFrame(bench_oos).T.round(4).to_string())
json.dump(dict(rows=rows, bench_is=bench, bench_oos=bench_oos),
          open(os.path.join(BASE, "robust.json"), "w"), ensure_ascii=False, indent=2, default=str)