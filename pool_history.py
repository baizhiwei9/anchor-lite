"""2026 年逐月动态资产池：状态 → 候选池 → 入池/出池"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import dynamic_rule as D
import engine3 as E

BASE = os.path.dirname(os.path.abspath(__file__))
idx, p8, panel = D.idx, D.p8, D.panel
SI = int(np.searchsorted(idx, np.datetime64("2026-01-05")))

# 每月首个交易日
months = []
for i in range(SI, len(idx) - 1):
    if idx[i + 1].month != idx[i].month:
        months.append(i + 1)     # 次月首个交易日作为结算日
months = [i for i in months if i < len(idx)]
months = [SI] + months  # 加入 1 月首个交易日作为初始池

recs = []
prev = None
for i in months:
    d = idx[i]
    st = p8.loc[d].idxmax(); prob = float(p8.loc[d].max())
    pool = D.build_pool(d, st)
    chg_in = [c for c in pool if prev is None or c not in prev]
    chg_out = [c for c in (prev or []) if c not in pool]
    recs.append(dict(date=str(d)[:10], month=str(d)[:7], state=st, prob=round(prob, 3),
                     pool=[E.NAMES.get(c, c) for c in pool],
                     n=len(pool),
                     enter=[E.NAMES.get(c, c) for c in chg_in],
                     exit=[E.NAMES.get(c, c) for c in chg_out]))
    prev = pool

df = pd.DataFrame([{k: v for k, v in r.items() if k not in ("pool", "enter", "exit")} for r in recs])
print("2026 年逐月：状态 → 候选池（含流动性≥1亿元与 60 日动量排序）\n")
for r in recs:
    print(f"{r['month']}  {r['state']}（{r['prob']:.0%}）  {r['n']} 只：{'、'.join(r['pool'])}")
    if r["enter"] or r["exit"]:
        print(f"        进入：{'、'.join(r['enter']) or '—'} ｜ 退出：{'、'.join(r['exit']) or '—'}")

stats = dict(n_months=len(recs), avg_pool=float(np.mean([r["n"] for r in recs])),
             churn=float(np.mean([len(r["enter"]) for r in recs[1:]])),
             states=sorted(set(r["state"] for r in recs)))
print(f"\n月份数 {stats['n_months']}，平均池子 {stats['avg_pool']:.1f} 只，月均换出 {stats['churn']:.1f} 只")
print("出现过的状态:", "、".join(stats["states"]))
json.dump(dict(monthly=recs, stats=stats), open(os.path.join(BASE, "pool_history.json"), "w"),
          ensure_ascii=False, indent=2)