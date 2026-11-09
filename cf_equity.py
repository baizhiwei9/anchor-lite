"""T3：股票篮上限测算（月度调仓，只用当日及以前信息）"""
import numpy as np, pandas as pd, dyn3 as D3
panel, rets = D3.panel, D3.rets
idx = panel.index; R = rets.fillna(0)
START = pd.Timestamp("2026-01-05"); END = pd.Timestamp("2026-11-06")
dates = idx[(idx >= START) & (idx <= END)]
POOL = ["kc50", "cyb", "zz500", "zz1000", "hs300", "nasdaq", "nikkei", "sp500", "hstech",
        "semiconductor", "newenergy", "military"]

def simulate(sleeve=0.45, mode="eq", n_top=2, cash_asset="treasury10_etf"):
    nav = 1.0; w = None; peak = 1.0; dds = []; wlog = []
    last_m = None
    for i, dt in enumerate(dates[:-1]):
        if dt.month != last_m:
            last_m = dt.month
            if mode == "eq":
                sel = POOL[:5]
                w = pd.Series(0.0, index=panel.columns)
                for c in sel: w[c] = sleeve / len(sel)
            elif mode == "mom":
                mom = {c: float(panel[c].loc[:dt].iloc[-1] / panel[c].loc[:dt].iloc[-21] - 1) for c in POOL}
                top = sorted(mom, key=mom.get, reverse=True)[:n_top]
                w = pd.Series(0.0, index=panel.columns)
                for c in top: w[c] = sleeve / len(top)
            w[cash_asset] = 1 - sleeve
            wlog.append((str(dt)[:10], list(w[w > 0].index), sleeve))
        r = float((w * R.loc[dt]).sum())
        nav *= 1 + r; peak = max(peak, nav); dds.append(nav / peak - 1)
    n = np.array(dds); ret = np.diff(np.concatenate([[1.0], []]))  # placeholder
    return nav, min(dds)

for sleeve in [0.45, 0.55]:
    for mode, n in [("eq", 0), ("mom", 2), ("mom", 3)]:
        nav, dd = simulate(sleeve, mode, n)
        # 7 月前净值
        nav7 = 1.0; w = None; last_m = None
        for dt in dates[:-1]:
            if dt.month != last_m:
                last_m = dt.month
                if mode == "eq":
                    sel = POOL[:5]; w = pd.Series(0.0, index=panel.columns)
                    for c in sel: w[c] = sleeve / len(sel)
                else:
                    mom = {c: float(panel[c].loc[:dt].iloc[-1] / panel[c].loc[:dt].iloc[-21] - 1) for c in POOL}
                    top = sorted(mom, key=mom.get, reverse=True)[:n]
                    w = pd.Series(0.0, index=panel.columns)
                    for c in top: w[c] = sleeve / len(top)
                w["treasury10_etf"] = 1 - sleeve
            nav *= 1 + float((w * R.loc[dt]).sum())
            if dt <= pd.Timestamp("2026-06-30"): nav7 *= 1 + float((w * R.loc[dt]).sum())
        print(f"股票{sleeve:.0%} {mode}{n}  全年 {nav-1:+.1%} 回撤 {dd:.2%}  7月前 {(nav7-1):+.1%}")
