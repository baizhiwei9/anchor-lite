"""清零后的风险解除判据：期权（偏度+隐含波动）、均线、已实现波动率"""
import numpy as np, pandas as pd, long2022 as L
IDX = L.IDX
# 1) 期权：用沪深300 期权偏度与隐含波动（2022 起有数据），辅以科创50
def opt_calm(asset="hs300"):
    s = L.SIG[asset]
    z, chg = s["z_skew"], s["iv_chg20"]
    return (z < 0.5) & (chg < 0.0)
# 2) 均线：池内宽基多数站上 MA20 且 MA20 上行
def ma_calm():
    up = []
    for a in L.POOL:
        p = L.PX[a]
        up.append((p > p.rolling(20).mean()) & (p.rolling(20).mean() > p.rolling(20).mean().shift(5)))
    df = pd.concat(up, axis=1)
    return (df.sum(axis=1) >= 3)
# 3) 波动率：沪深300 20 日已实现波动回落且低于自身 60 日中位
def vol_calm():
    p = L.PX["hs300"]; r = p.pct_change()
    v20 = r.rolling(20).std() * np.sqrt(252)
    med = v20.rolling(60).median()
    return (v20 < med) & (v20 < v20.shift(10))
if __name__ == "__main__":
    O, M, V = opt_calm().reindex(IDX).fillna(False), ma_calm().reindex(IDX).fillna(False), vol_calm().reindex(IDX).fillna(False)
    for name, s in [("期权解除", O), ("均线解除", M), ("波动率解除", V)]:
        print(f"\n{name}: 累计 {int(s.sum())}/{len(s)} 天")
        print("  逐年:", {int(y): int(g.sum()) for y, g in s.groupby(s.index.year)})
        print("  首次触发:", [str(x.date()) for x in s[s].index[:3]])
    both = O & M
    print("\n期权+均线同时解除: %d 天，逐年 %s" % (int(both.sum()), {int(y): int(g.sum()) for y, g in both.groupby(both.index.year)}))
