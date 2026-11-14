"""诊断：滤波 vs 平滑概率（未来信息）、状态切换滞后、概率加权的退化程度"""
import os, sys, json
import numpy as np, pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
probs = pd.read_csv(os.path.join(BASE, "probs_filtered.csv"), index_col=0, parse_dates=True)
sm = pd.read_csv(os.path.join(BASE, "probs_smoothed_last_window.csv"), index_col=0, parse_dates=True)
rets = pd.read_csv(os.path.join(BASE, "returns.csv"), index_col=0, parse_dates=True)

f = probs.loc[sm.index]
af, asm = f.idxmax(1), sm.idxmax(1)

sw_f = af[af != af.shift()].index
sw_s = asm[asm != asm.shift()].index
out = dict(n_days=int(len(f)), switches_filtered=int(len(sw_f) - 1), switches_smoothed=int(len(sw_s) - 1),
           switch_dates_filtered=[str(d.date()) for d in sw_f[1:]],
           switch_dates_smoothed=[str(d.date()) for d in sw_s[1:]])

# 最近一次切换的滞后
if len(sw_f) > 1 and len(sw_s) > 1:
    last = af.iloc[-1]
    df_ = [d for d in sw_f if af.loc[d] == last]
    ds_ = [d for d in sw_s if asm.loc[d] == last]
    if df_ and ds_:
        out["last_state"] = last
        out["last_switch_filtered"] = str(df_[-1].date())
        out["last_switch_smoothed"] = str(ds_[-1].date())
        out["lag_days_last"] = int(np.busday_count(ds_[-1].date(), df_[-1].date()))

# 平均绝对概率差
out["mean_abs_diff"] = float((f - sm).abs().mean().mean())
out["max_abs_diff"] = float((f - sm).abs().max().max())

# 未来信息检验：概率 vs 其后 20 日"该状态受益资产"的收益
oil = rets["_oil_px"]; hs = rets["_hs300_px"]
fwd_oil = (oil.shift(-20) / oil - 1)
fwd_eq = (hs.shift(-20) / hs - 1)
idx = f.index
pair = pd.DataFrame({
    "f_inf": f["通胀交易"], "s_inf": sm["通胀交易"],
    "f_low": f["低波趋势"], "s_low": sm["低波趋势"],
    "f_cri": f["高波危机"], "s_cri": sm["高波危机"],
    "fwd_oil": fwd_oil.reindex(idx), "fwd_eq": fwd_eq.reindex(idx),
}).dropna()

def ic(x, y):
    return float(np.corrcoef(x, y)[0, 1])

out["ic"] = {
    "filtered_inf_vs_fwd_oil": ic(pair["f_inf"], pair["fwd_oil"]),
    "smoothed_inf_vs_fwd_oil": ic(pair["s_inf"], pair["fwd_oil"]),
    "filtered_low_vs_fwd_eq": ic(pair["f_low"], pair["fwd_eq"]),
    "smoothed_low_vs_fwd_eq": ic(pair["s_low"], pair["fwd_eq"]),
    "filtered_cri_vs_fwd_eq": ic(pair["f_cri"], pair["fwd_eq"]),
    "smoothed_cri_vs_fwd_eq": ic(pair["s_cri"], pair["fwd_eq"]),
}
# 方向命中率（>0.5 视为看多/看空）
def hit(p, y, direction=1):
    m = p > 0.5
    if m.sum() < 5:
        return None
    return float(((y[m] > 0) == (direction > 0)).mean()), int(m.sum())
out["hit_inf_oil"] = hit(pair["f_inf"], pair["fwd_oil"])
out["hit_inf_oil_sm"] = hit(pair["s_inf"], pair["fwd_oil"])
out["hit_cri_eq"] = hit(pair["f_cri"], pair["fwd_eq"], -1)
out["hit_cri_eq_sm"] = hit(pair["s_cri"], pair["fwd_eq"], -1)

# 概率集中度：当期最大概率与 0/1 化程度
out["p_max_share"] = float((f.max(1) > 0.85).mean())
out["sm_max_share"] = float((sm.max(1) > 0.85).mean())

with open(os.path.join(BASE, "diag_out.json"), "w") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=2, default=str)
print(json.dumps(out, ensure_ascii=False, indent=1, default=str))