"""债券腿切换：美衰退→美债，中衰退/A股风险高→中债"""
import pandas as pd, numpy as np
def build():
    m = pd.read_csv("data_long/macro_long.csv", parse_dates=["date"]).set_index("date")
    px = pd.read_csv("data_long/hs300.csv", parse_dates=["date"]).set_index("date")["close"]
    idx = m.index.intersection(px.index)
    m = m.loc[idx]; px = px.reindex(idx).ffill()
    ret20 = px / px.shift(20) - 1
    vol20 = px.pct_change().rolling(20).std() * np.sqrt(252)
    # A股风险状态：滞后与冷却，避免日频抖动（后续由期权信号替换）
    risk = []
    on = False; hold = 0
    for dt in idx:
        r_, v_ = ret20.get(dt, np.nan), vol20.get(dt, np.nan)
        if np.isnan(r_): risk.append(0); continue
        if not on:
            if (r_ <= -0.08) or (v_ >= 0.30): on = True; hold = 8
        else:
            hold -= 1
            if hold <= 0 and r_ >= -0.02 and v_ < 0.25: on = False
        risk.append(int(on))
    risk_high = pd.Series(risk, index=idx)
    us_rec, cn_rec = m.us_rec.fillna(0), m.cn_rec.fillna(0)
    us = pd.Series(np.where((cn_rec == 1) | (risk_high == 1), 0.0,
                    np.where(us_rec == 1, 0.6, 0.3)), index=idx)
    cn = 1 - us
    out = pd.DataFrame({"us_bond": us, "cn_bond": cn, "us_rec": us_rec, "cn_rec": cn_rec,
                        "risk_high": risk_high, "ret20": ret20, "vol20": vol20})
    return out
if __name__ == "__main__":
    o = build(); o.to_csv("data_long/bond_alloc.csv")
    print("交易日", len(o))
    print("\n逐年美债/中债平均占比（债券腿内部）：")
    g = o.groupby(o.index.year)[["us_bond", "cn_bond"]].mean() * 100
    print(g.round(1).to_string())
    sw = o.us_bond.diff().abs() > 1e-9
    print("\n切换次数:", int(sw.sum()))
    print("主要切换日（前 12 次）：")
    print(o.loc[sw, ["us_bond", "us_rec", "cn_rec", "risk_high"]].head(12).to_string())
    print("\n2022/2023 抽样：")
    print(o.loc[["2022-06-01","2023-03-01","2023-09-01","2024-06-03","2026-06-01"]][["us_bond","us_rec","cn_rec","risk_high"]].to_string())
