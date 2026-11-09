"""2026 年逐月波动率档位：20/60 日波动率历史分位 → 目标波动预算"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import dynamic_rule as D
import engine3 as E

BASE = os.path.dirname(os.path.abspath(__file__))
idx, panel = D.idx, D.panel
rets = panel.pct_change()
SI = int(np.searchsorted(idx, np.datetime64("2026-01-05")))
p8 = D.p8

v20 = (rets.rolling(20).std() * np.sqrt(252)).median(axis=1)
v60 = (rets.rolling(60).std() * np.sqrt(252)).median(axis=1)

def bucket(q):
    if q < 0.25: return "低波", 0.12
    if q < 0.60: return "中波", 0.09
    if q < 0.85: return "高波", 0.06
    return "极端", 0.03

months = [SI]
for i in range(SI, len(idx) - 1):
    if idx[i + 1].month != idx[i].month:
        months.append(i + 1)
months = sorted(set([m for m in months if m < len(idx)]))

recs = []
for i in months:
    d = idx[i]
    hist20 = v20.loc[:d].tail(252).dropna()
    hist60 = v60.loc[:d].tail(252).dropna()
    c20, c60 = float(hist20.iloc[-1]), float(hist60.iloc[-1])
    q20 = float((hist20 < c20).mean()); q60 = float((hist60 < c60).mean())
    name, tv = bucket(q20)
    recs.append(dict(date=str(d)[:10], month=str(d)[:7], v20=round(c20, 4), v60=round(c60, 4),
                     q20=round(q20, 3), q60=round(q60, 3), bucket=name, target_vol=tv,
                     state=p8.loc[d].idxmax()))

print("2026 年逐月波动率档位（全资产 20 日波动率中位数 vs 过去 252 日分位）\n")
print("月份      20日波动  60日波动  20日分位  档位   目标波动  宏观状态")
for r in recs:
    print(f"{r['month']}  {r['v20']*100:8.1f}%  {r['v60']*100:7.1f}%  {r['q20']*100:6.0f}%"
          f"  {r['bucket']}  {r['target_vol']*100:5.0f}%   {r['state']}")

dist = pd.Series([r["bucket"] for r in recs]).value_counts().to_dict()
print("\n档位分布:", dist)
print("目标波动区间:", f"{min(r['target_vol'] for r in recs)*100:.0f}% ~ {max(r['target_vol'] for r in recs)*100:.0f}%")
json.dump(recs, open(os.path.join(BASE, "vol_history.json"), "w"), ensure_ascii=False, indent=2)