"""抓取 2026 年内扩展资产池（2026-01-02 ~ 2026-11-06）"""
import os, sys
import akshare as ak
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "data2026")
os.makedirs(OUT, exist_ok=True)
START, END = "2026-01-02", "2026-11-06"

CODES = {
    # A股宽基
    "sh510300": "hs300", "sh510500": "zz500", "sh512100": "zz1000", "sh588000": "kc50",
    "sz159915": "cyb", "sh510880": "dividend", "sh512890": "lowvol",
    # 行业/风格
    "sh512480": "semiconductor", "sh512660": "military", "sh512010": "pharma",
    "sh512000": "broker", "sh512800": "bank", "sh515220": "coal", "sh515210": "steel",
    "sh512690": "liquor", "sz159928": "consumer", "sh516160": "newenergy",
    # 海外宽基
    "sh513100": "nasdaq", "sh513500": "sp500", "sh513520": "nikkei", "sh513130": "hstech",
    "sh513050": "china_internet", "sz159941": "nasdaq2",
    # 商品
    "sh501018": "oil", "sh518880": "gold", "sh512400": "nonferrous", "sz159985": "soybean",
    "sh515790": "solar", "sh159981": "energychem",
    # 债券（现券类 ETF）
    "sh511010": "treasury_etf", "sh511260": "treasury10_etf", "sh511020": "treasury_active",
    "sh511220": "citybond", "sh511030": "corp_bond",
}

rows = []
for code, name in CODES.items():
    try:
        d = ak.fund_etf_hist_sina(symbol=code)
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
        d = d[(d["date"] >= START) & (d["date"] <= END)].drop_duplicates("date").sort_values("date")
        if len(d) == 0:
            print(f"[skip-empty] {code} {name}"); continue
        d.to_csv(os.path.join(OUT, name + ".csv"), index=False)
        rows.append(dict(code=code, name=name, rows=len(d), first=d["date"].iloc[0], last=d["date"].iloc[-1],
                         close_last=float(d["close"].iloc[-1]), amt_avg=float(pd.to_numeric(d["amount"], errors="coerce").mean())))
    except Exception as e:
        print(f"[fail] {code} {name}: {type(e).__name__} {str(e)[:80]}")

df = pd.DataFrame(rows).sort_values("name")
df.to_csv(os.path.join(OUT, "_coverage.csv"), index=False)
print(df.to_string(index=False))
print("\n成功:", len(df), "只")