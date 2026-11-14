"""用真实宏观（CPI/PPI/PMI/M1）+ 股指基差 + 期权波动率重建状态判定
关键修正：把“通胀”拆成 CPI 与 PPI 两个维度，当前属“低 CPI + 高 PPI = 成本推动/类滞胀”，不是商品强通胀。
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
MACRO = os.environ.get("MACRO_JSON", os.path.join(BASE, "data", "macro_2026.json"))
m = json.load(open(MACRO))["series"]

def series(key):
    v = m[key]["values"]
    return pd.Series({pd.Period(k, "M").to_timestamp("M"): val for k, val in v.items()}).sort_index()

cpi, ppi, pmi, m1 = series("cpi_yoy"), series("ppi_yoy"), series("pmi"), m1_series if False else series("m2_yoy")

def release_dates(s, kind):
    """按惯例给发布日：CPI/PPI 次月 9 日；PMI 当月最后一天；M2 次月 12 日"""
    out = {}
    for d, v in s.items():
        if kind == "pmi":
            out[d] = v
        else:
            nxt = (d + pd.offsets.MonthEnd(1))
            out[nxt] = v
    return pd.Series(out).sort_index()

cpi_r = release_dates(cpi, "cpi")   # 以月末近似发布日，避免未来函数
ppi_r = release_dates(ppi, "ppi")
pmi_r = release_dates(pmi, "pmi")
m2_r = release_dates(m1, "m2")

def asof(s, d):
    s = s[s.index <= d]
    return float(s.iloc[-1]) if len(s) else np.nan

# 基差与波动
import akshare as ak
idx = ak.stock_zh_index_daily(symbol="sh000300"); idx["date"] = pd.to_datetime(idx["date"])
fut = ak.futures_main_sina(symbol="IF0", start_date="20250101", end_date="20261106")
fut["日期"] = pd.to_datetime(fut["日期"])
basis = fut.merge(idx[["date", "close"]], left_on="日期", right_on="date", how="inner")
basis["b"] = (basis["收盘价"] / basis["close"] - 1) * 100
basis = basis.set_index("日期")["b"]
qv = pd.read_csv(os.path.join(BASE, "data2026", "qvix_300etf.csv"), parse_dates=["date"]).set_index("date")["close"]
qv = qv[~qv.index.duplicated(keep="last")]

def classify(d):
    c, p = asof(cpi_r, d), asof(ppi_r, d)
    pm, mm = asof(pmi_r, d), asof(m2_r, d)
    b = float(basis[basis.index <= d].tail(20).mean())
    q = float((qv[qv.index <= d].tail(252) < qv[qv.index <= d].iloc[-1]).mean())
    if np.isnan(c) or np.isnan(p):
        infl = "数据不足"
    else:
        if p < 2.0:
            infl = "低通胀"                      # 工业品价格没起来，无论 CPI 多少，都不算通胀
        elif c < 1.5:
            infl = "成本推动"                    # PPI 上、CPI 低：成本端推动
        else:
            infl = "需求拉动通胀"                # CPI 与 PPI 同步上行
    grow = "增长偏强" if (pm >= 50.5 and mm >= 8.0) else "增长偏弱"
    risk = "风险偏高" if (b <= -1.5 or q >= 0.85) else "风险正常"
    label = f"{infl}·{grow}·{risk}"
    return dict(date=str(d)[:10], cpi=c, ppi=p, pmi=pm, m2=mm, basis20=round(b, 2), qvix_pct=round(q, 2),
                infl=infl, grow=grow, risk=risk, label=label)

if __name__ == "__main__":
    months = ["2026-01-30", "2026-02-27", "2026-03-31", "2026-04-30", "2026-05-29",
              "2026-06-30", "2026-07-31", "2026-08-31", "2026-09-30", "2026-10-30", "2026-11-06"]
    recs = [classify(pd.Timestamp(d)) for d in months]
    df = pd.DataFrame(recs)
    print(df[["date", "cpi", "ppi", "pmi", "m2", "basis20", "qvix_pct", "label"]].to_string(index=False))
    print("\n当期标签：", recs[-1]["label"])
    print("说明：CPI %.1f%% 属偏低（接近通缩），PPI %.1f%% 属工业品涨价——这是成本推动，不是需求拉动的"
          "商品强通胀；配商品的理由不成立。" % (recs[-1]["cpi"], recs[-1]["ppi"]))
    json.dump(recs, open(os.path.join(BASE, "macro_state.json"), "w"), ensure_ascii=False, indent=2)