"""美国 CPI 与失业率：按发布滞后对齐到交易日（保守口径，避免未来函数）"""
import io, requests, pandas as pd, numpy as np
def fred(series):
    r = requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}",
                     timeout=40, headers={"User-Agent": "Mozilla/5.0"})
    d = pd.read_csv(io.StringIO(r.text)); d.columns = ["date", "value"]
    d["date"] = pd.to_datetime(d.date, errors="coerce"); d["value"] = pd.to_numeric(d.value, errors="coerce")
    return d.dropna().set_index("date").value
cpi = fred("CPIAUCSL"); une = fred("UNRATE")
# 发布滞后：CPI 月末+15天；失业率 次月第7天
cpi_rel = (cpi.index + pd.offsets.MonthBegin(1) + pd.Timedelta(days=14))
une_rel = (une.index + pd.offsets.MonthBegin(1) + pd.Timedelta(days=6))
cpi_s = pd.Series(cpi.values, index=cpi_rel).sort_index()
une_s = pd.Series(une.values, index=une_rel).sort_index()
trading = pd.read_csv("data2026/kc50.csv", parse_dates=["date"])["date"]
out = pd.DataFrame(index=trading)
out["cpi"] = cpi_s.reindex(trading, method="ffill")
out["unrate"] = une_s.reindex(trading, method="ffill")
# 同比与变化
cpi_m = cpi.copy(); cpi_yoy = (cpi_m / cpi_m.shift(12) - 1)
yoy_s = pd.Series(cpi_yoy.values, index=cpi_rel).sort_index().dropna()
out["cpi_yoy"] = yoy_s.reindex(trading, method="ffill")
out["cpi_yoy_chg3"] = out.cpi_yoy.diff(63)          # 约 3 个月变化
out["une_chg3"] = out.unrate.diff(63)
out["une_chg6"] = out.unrate.diff(126)
out.to_csv("data_signals/us_macro.csv")
print(out.dropna().head(3).to_string()); print(out.dropna().tail(3).to_string())
print("\n有效交易日", out.cpi_yoy.notna().sum(), "/", len(out))
print("CPI 同比区间 %.2f%%~%.2f%%  失业率 %.1f%%~%.1f%%" % (out.cpi_yoy.min()*100, out.cpi_yoy.max()*100, out.unrate.min(), out.unrate.max()))
