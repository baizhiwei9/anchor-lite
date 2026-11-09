"""2022-2026 宏观锚：美国 CPI/失业率/工业产出/CFNAI + 中国 PMI，按发布滞后对齐交易日"""
import io, requests, numpy as np, pandas as pd, sys
import akshare as ak

def fred(s):
    r = requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={s}", timeout=40,
                     headers={"User-Agent": "Mozilla/5.0"})
    d = pd.read_csv(io.StringIO(r.text)); d.columns = ["date", "v"]
    d["date"] = pd.to_datetime(d.date, errors="coerce"); d["v"] = pd.to_numeric(d.v, errors="coerce")
    return d.dropna().set_index("date")["v"]

# 交易日历
cal = pd.read_csv("data_long/hs300.csv", parse_dates=["date"])
cal["date"] = pd.to_datetime(cal["date"])
trading = cal[(cal.date >= "2019-12-02") & (cal.date <= "2026-11-06")]["date"].sort_values().reset_index(drop=True)

def align(series, lag_days):
    """月度数据：月末 + lag_days 视为可用"""
    rel = series.index + pd.offsets.MonthEnd(0) + pd.Timedelta(days=lag_days)
    return pd.Series(series.values, index=rel).sort_index().reindex(trading, method="ffill")

cpi = fred("CPIAUCSL"); une = fred("UNRATE"); ind = fred("INDPRO"); cfnai = fred("CFNAI")
cpi_yoy = cpi / cpi.shift(12) - 1
ind_yoy = ind / ind.shift(12) - 1
out = pd.DataFrame(index=trading)
out["cpi_yoy"] = align(cpi_yoy.dropna(), 15)
out["une"] = align(une, 6)
out["ind_yoy"] = align(ind_yoy.dropna(), 16)
out["cfnai"] = align(cfnai, 25)
# 中国 PMI
p = ak.macro_china_pmi()
p["m"] = pd.to_datetime(p["月份"].str.replace("年", "-").str.replace("月份", ""), errors="coerce")
p = p.dropna(subset=["m"]).sort_values("m").set_index("m")
pmi = pd.Series(p["制造业-指数"].values, index=p.index)
pmi_rel = pd.Series(pmi.values, index=pmi.index + pd.offsets.MonthEnd(0) + pd.Timedelta(days=1)).sort_index()
out["pmi"] = pmi_rel.reindex(trading, method="ffill")

out["une_chg3"] = out.une.diff(63)
out["ind_chg3"] = out.ind_yoy.diff(63)
out["pmi_chg3"] = out.pmi.diff(63)
out["cfnai_3m"] = out.cfnai.rolling(63).mean()
# 状态
out["us_rec"] = ((out.une_chg3 >= 0.3) | (out.ind_yoy < 0) | (out.cfnai_3m < -0.2)).astype(int)
out["cn_rec"] = (((out.pmi < 50) & (out.pmi_chg3 < 0)) | (out.pmi < 48)).astype(int)
out["us_hot"] = ((out.cpi_yoy >= 0.04) & (out.une <= 4.2)).astype(int)
out["cn_up"] = ((out.pmi >= 50.5) & (out.pmi_chg3 > 0)).astype(int)
# 增长合成分：美国（失业率下降、产出上行）与中国（PMI 上行）
us_g = np.tanh(-out.une_chg3 / 0.3) * 0.5 + np.tanh(out.ind_chg3 / 0.03) * 0.5
cn_g = np.tanh(out.pmi_chg3 / 2.0) + np.tanh((out.pmi - 50) / 2.0)
out["growth_us"] = us_g.clip(-1, 1)
out["growth_cn"] = (cn_g / 2).clip(-1, 1)
out["inflation_us"] = out.cpi_yoy
out.to_csv("data_long/macro_long.csv")
print(out.dropna().shape, out.index[0].date(), out.index[-1].date())
print("\n逐年状态计数：")
t = out.dropna()
print(t.groupby(t.index.year)[["us_rec","cn_rec","us_hot","cn_up"]].sum().to_string())
print("\n2022/2024/2026 抽样：")
print(t.loc[["2022-06-01","2024-06-03","2026-06-01"]][["cpi_yoy","une","ind_yoy","pmi","us_rec","cn_rec","us_hot"]].round(3).to_string())
