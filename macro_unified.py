"""复用其他任务的宏观接口，拼出 2026 年逐月宏观序列（含发布日）"""
import os, sys, json
MM = os.environ.get("MACRO_MONTHLY_DIR", "")
if MM and os.path.isdir(MM) and MM not in sys.path:
    sys.path.insert(0, MM)
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
OTHER = os.environ.get("MACRO_JSON", os.path.join(BASE, "data", "macro_2026.json"))

rows = {}

# 1) PPI / M1M2：复用 macro_monthly_report 的东财接口（实时）
try:
    import fetch_data as F
    for x in F.fetch_ppi():
        m = x["date"][:7]
        rows.setdefault(m, {})["ppi"] = x["ppi_yoy"]
        rows[m]["ppi_src"] = "东财 RPT_ECONOMY_PPI（fetch_data.fetch_ppi）"
    for x in F.fetch_m1():
        mon = x["month"].replace("月份", "").replace("年", "-").replace("月", "")
        mon = mon[:7]
        rows.setdefault(mon, {})["m1"] = x["m1_yoy"]
        rows[mon]["m2"] = x["m2_yoy"]
        rows[mon]["m_src"] = "akshare macro_china_money_supply（fetch_data.fetch_m1）"
except Exception as e:
    print("[warn] 实时接口失败，走本地:", str(e)[:80])

# 2) CPI：本地 macro_2026.json（其他任务的 2026 月度序列）
j = json.load(open(OTHER))["series"]
for k, v in j["cpi_yoy"]["values"].items():
    rows.setdefault(k, {})["cpi"] = v
    rows[k]["cpi_src"] = "本地 macro_2026.json（统计局发布值）"

# 3) PMI：macro_monthly_report/data/pmi_latest.json
p = json.load(open(os.path.join(MM, "data", "pmi_latest.json")))
for m, d in p["series"].items():
    rows.setdefault(m[:7], {})["pmi"] = d.get("pmi")
    rows[m[:7]]["pmi_src"] = "统计局发布页（pmi_fetch.py 解析）"

def release(month, kind):
    d = pd.Period(month, "M")
    if kind == "pmi":
        return (d.to_timestamp("M")).strftime("%Y-%m-%d")
    return (d + 1).to_timestamp("M").replace(day=9 if kind in ("cpi", "ppi") else 12).strftime("%Y-%m-%d")

recs = []
for m in sorted(rows):
    if not m.startswith("2026"):
        continue
    r = rows[m]
    recs.append(dict(month=m, cpi=r.get("cpi"), ppi=r.get("ppi"), pmi=r.get("pmi"),
                     m1=r.get("m1"), m2=r.get("m2"),
                     release_cpi=release(m, "cpi"), release_ppi=release(m, "ppi"),
                     release_pmi=release(m, "pmi"), release_m=release(m, "m"),
                     src="｜".join(filter(None, [r.get("cpi_src"), r.get("ppi_src"), r.get("pmi_src"), r.get("m_src")]))))

df = pd.DataFrame(recs)
df.to_csv(os.path.join(BASE, "macro2026.csv"), index=False)
json.dump(recs, open(os.path.join(BASE, "macro2026.json"), "w"), ensure_ascii=False, indent=2)
print(df[["month", "cpi", "ppi", "pmi", "m1", "m2", "release_cpi", "release_ppi"]].to_string(index=False))
print("\n月度数:", len(df), " 最新:", df["month"].iloc[-1])
print("来源:", df["src"].iloc[-1])