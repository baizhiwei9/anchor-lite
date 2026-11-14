"""四维度状态标签：需求通胀(CPI)｜供给冲击(PPI+油价)｜增长(PMI+M1)｜流动性(基差+QVIX)"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import akshare as ak
import engine3 as E

BASE = os.path.dirname(os.path.abspath(__file__))
mac = pd.read_csv(os.path.join(BASE, "macro2026.csv"))
panel, rets = E.load_panel(full=True)
idx = panel.index
oil = panel["oil"]

# 股指基差（IF 与沪深300）
i300 = ak.stock_zh_index_daily(symbol="sh000300"); i300["date"] = pd.to_datetime(i300["date"])
fut = ak.futures_main_sina(symbol="IF0", start_date="20250101", end_date="20261106")
fut["日期"] = pd.to_datetime(fut["日期"])
b = fut.merge(i300[["date", "close"]], left_on="日期", right_on="date", how="inner")
b["basis"] = (b["收盘价"] / b["close"] - 1) * 100
basis = b.set_index("日期")["basis"].sort_index()

qv = pd.read_csv(os.path.join(BASE, "data2026", "qvix_300etf.csv"), parse_dates=["date"]).set_index("date")["close"]
qv = qv[~qv.index.duplicated(keep="last")].sort_index().reindex(panel.index).ffill()

def asof(d, col, rel):
    s = mac[(pd.to_datetime(mac[rel]) <= d) & mac[col].notna()]
    return float(s[col].iloc[-1]) if len(s) else np.nan

def classify(d):
    cpi = asof(d, "cpi", "release_cpi")
    ppi = asof(d, "ppi", "release_ppi")
    pmi = asof(d, "pmi", "release_pmi")
    m1 = asof(d, "m1", "release_m")
    oil60 = float(oil[oil.index <= d].iloc[-1] / oil[oil.index <= d].iloc[-61] - 1)
    bs = float(basis[basis.index <= d].tail(20).mean())
    q = qv[qv.index <= d]
    qp = float((q.tail(252) < q.iloc[-1]).mean()) if len(q) > 60 else np.nan

    if np.isnan(cpi):
        demand = "数据不足"
    elif cpi < 1.0:
        demand = "需求弱(CPI<1)"
    elif cpi <= 2.0:
        demand = "需求温和"
    else:
        demand = "需求过热"

    if np.isnan(ppi):
        supply = "数据不足"
    elif ppi < 0:
        supply = "无供给冲击"
    elif ppi <= 3.0:
        supply = "供给温和上行" + ("(油↑)" if oil60 > 0 else "(油↓)")
    else:
        supply = "供给强冲击" + ("(油↑)" if oil60 > 0 else "(油↓)")

    if np.isnan(pmi) or np.isnan(m1):
        growth = "数据不足"
    elif pmi >= 50.5 and m1 >= 5.0:
        growth = "增长偏强"
    elif pmi < 49.5 or m1 < 4.0:
        growth = "增长偏弱"
    else:
        growth = "增长中性"

    liq = "流动性紧(深贴水/高波动)" if (bs <= -1.5 or qp >= 0.85) else "流动性中性"

    return dict(date=str(d)[:10], cpi=cpi, ppi=ppi, pmi=pmi, m1=m1,
                oil60=round(oil60, 3), basis20=round(bs, 2), qvix_pct=None if np.isnan(qp) else round(qp, 2),
                demand=demand, supply=supply, growth=growth, liq=liq,
                label=f"{demand}｜{supply}｜{growth}｜{liq}")

if __name__ == "__main__":
    starts = [idx[int(np.searchsorted(idx, np.datetime64("2026-01-05")))]]
    for i in range(len(idx) - 1):
        if idx[i] >= pd.Timestamp("2026-01-05") and idx[i + 1].month != idx[i].month:
            starts.append(idx[i + 1])
    starts = sorted(set([s for s in starts if s <= pd.Timestamp("2026-11-06")]))
    recs = [classify(s) for s in starts]
    df = pd.DataFrame(recs)
    print(df[["date", "cpi", "ppi", "pmi", "m1", "oil60", "basis20", "qvix_pct", "label"]].to_string(index=False))
    print("\n当期（2026-11-06）标签：", recs[-1]["label"])
    json.dump(recs, open(os.path.join(BASE, "macro4_labels.json"), "w"), ensure_ascii=False, indent=2)
    print("saved macro4_labels.json，共", len(recs), "个月")