"""Anchor-Lite 数据抓取：全部数据截至 2026-04-14（模拟日的前一日），不取未来数据。"""
import sys, os, json, time
import akshare as ak
import pandas as pd

CUTOFF = "2026-04-14"
OUT = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(OUT, exist_ok=True)

def save(df, name):
    df = df.copy()
    df.to_csv(os.path.join(OUT, name + ".csv"), index=False)
    print(f"[ok] {name}: rows={len(df)} last={df.iloc[-1,0]}")

def try_(fn, name, **kw):
    try:
        d = fn(**kw)
        return d
    except Exception as e:
        print(f"[fail] {name}: {type(e).__name__} {str(e)[:120]}")
        return None

# 1) A股 ETF（新浪）
for code, name in [("sh588000", "etf_kc50"), ("sh510300", "etf_hs300"),
                   ("sh512400", "etf_nonferrous"), ("sh501018", "etf_oil"),
                   ("sh513100", "etf_nasdaq"), ("sh511010", "etf_treasury")]:
    d = try_(ak.fund_etf_hist_sina, name, symbol=code)
    if d is not None and len(d):
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
        save(d, name)

# 2) 海外指数 / 商品
d = try_(ak.index_global_hist_em, "kospi", symbol="韩国KOSPI")
if d is not None:
    d = d.rename(columns={"日期": "date", "最新价": "close", "今开": "open", "最高": "high", "最低": "low"})
    d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
    save(d[["date", "open", "high", "low", "close"]], "idx_kospi")

d = try_(ak.index_us_stock_sina, "spx", symbol=".INX")
if d is not None:
    d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
    save(d, "idx_spx")

for sym, name in [("CL", "fut_wti"), ("HG", "fut_copper")]:
    d = try_(ak.futures_foreign_hist, name, symbol=sym)
    if d is not None:
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
        save(d[["date", "open", "high", "low", "close"]], name)

# 3) 国债期货主连（T=10Y, TF=5Y, TS=2Y）
for sym, name in [("T0", "fut_t10"), ("TF0", "fut_tf5"), ("TS0", "fut_ts2")]:
    d = try_(ak.futures_main_sina, name, symbol=sym, start_date="20150101", end_date=CUTOFF)
    if d is not None:
        d = d.rename(columns={"日期": "date", "开盘价": "open", "最高价": "high", "最低价": "low", "收盘价": "close"})
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
        save(d[["date", "open", "high", "low", "close"]], name)

# 4) 中美国债收益率与期限利差
d = try_(ak.bond_zh_us_rate, "yields", start_date="20150101")
if d is not None:
    d["日期"] = pd.to_datetime(d["日期"]).dt.strftime("%Y-%m-%d")
    save(d, "yields")

# 5) 期权隐含波动率（QVIX：50ETF、科创50）
for fn, name in [(ak.index_option_50etf_qvix, "qvix_50etf"), (ak.index_option_kcb_qvix, "qvix_kcb")]:
    d = try_(fn, name)
    if d is not None:
        d.columns = [c.lower() for c in d.columns]
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
        save(d, name)

# 6) 信用利差（中债收益率曲线：AAA 中短期票据 vs 国债）
d = try_(ak.bond_china_yield, "china_yield", start_date="20230101", end_date=CUTOFF)
if d is not None:
    save(d.head(2000), "bond_china_yield_sample")