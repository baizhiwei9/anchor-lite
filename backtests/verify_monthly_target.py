"""验证“月度目标”口径：目标权重在月内是否恒定、何时被改写（只有熔断）、日频只做阈值/熔断检查"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import sim2026b as S

cols, idx, R = S.cols, S.idx, S.R
mix = S.MIXES["55% 国债 + 15% 城投 + 15% 原油 + 10% 纳指 + 5% 豆粕"]
w0 = np.zeros(len(cols))
for k, v in mix.items():
    w0[cols.index(k)] = v

def trace(trig=0.03, cut=0.5):
    w = w0.copy(); br = prev_br = 1.0; nav = peak = 1.0
    rows = []
    for i in range(S.WARM, len(idx) - 1):
        target = w0 * br
        rebal = (idx[i + 1].month != idx[i].month) or (br != prev_br)
        if rebal:
            w = target.copy()
        rows.append(dict(date=idx[i + 1], month=str(idx[i + 1])[:7], target=target.copy(), br=br,
                         rebal=rebal, dev=float(np.abs(target - w).max())))
        pnl = float(np.nansum(w * R[i + 1]))
        nav *= 1 + pnl
        peak = max(peak, nav)
        dd = nav / peak - 1
        if dd <= -trig:
            br = 1 - cut
        elif dd >= -trig / 2:
            br = 1.0
        prev_br = br
        if pnl > -1:
            w = w * (1 + R[i + 1]) / (1 + pnl)
    df = pd.DataFrame(rows).set_index("date")
    return df

d = trace()
# 每个月的目标权重是否恒定（忽略熔断造成的比例变化）
by_m = d.groupby("month")
rec = []
for m, g in by_m:
    brs = g["br"].unique()
    base = g["target"].values[-1] / g["br"].values[-1] if g["br"].values[-1] else g["target"].values[-1]
    consistent = all(np.allclose(g["target"].values[j], base * g["br"].values[j], atol=1e-9) for j in range(len(g)))
    rec.append(dict(month=m, days=len(g), breaker_values=[float(x) for x in brs],
                    target_constant_ex_breaker=bool(consistent),
                    intra_month_changes=int((g["br"].diff().fillna(0) != 0).sum())))
out = pd.DataFrame(rec)
print(out.to_string(index=False))
print("\n总交易日:", len(d), " 月内目标改变次数:", int(out["intra_month_changes"].sum()),
      " 每月第一个交易日的目标（示例）:")
first = d.groupby("month").first()
print(first[["br"]].assign(**{c: first["target"].apply(lambda v, j=j: round(float(v[j]), 3))
                              for j, c in enumerate(cols) if np.abs(first['target'].apply(lambda v, j=j: v[j])).max() > 0.001}).to_string())
json.dump(dict(monthly=rec, n_days=int(len(d)), intra=int(out["intra_month_changes"].sum())),
          open(os.path.join(S.BASE, "monthly_target_check.json"), "w"), ensure_ascii=False, indent=2)