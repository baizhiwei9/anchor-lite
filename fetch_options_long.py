"""2022-2026 期权：每日统计(PCR/OI) + 风险指标(25Δ偏度、ATM IV)，逐日提取后落盘"""
import sys, os, time, numpy as np, pandas as pd
import akshare as ak

BASE = os.path.dirname(os.path.abspath(__file__))
dates = pd.read_csv(os.path.join(BASE, "data_long/macro_long.csv"), parse_dates=["date"])["date"]
dates = [d for d in dates if d >= pd.Timestamp("2022-01-04")]
STAT, RISK = os.path.join(BASE, "data_long/opt_stats_long.csv"), os.path.join(BASE, "data_long/opt_skew_long.csv")
done = set()
if os.path.exists(RISK):
    done = set(pd.read_csv(RISK, usecols=["date"])["date"].astype(str))
print("待抓取", len(dates), "已存在", len(done), flush=True)

rows_s, rows_k = [], []
for i, dt in enumerate(dates):
    ds = dt.strftime("%Y%m%d")
    if str(dt.date()) in done: continue
    # 日统计
    for _ in range(2):
        try:
            s = ak.option_daily_stats_sse(date=ds); break
        except Exception:
            s = None; time.sleep(0.4)
    if s is not None and len(s):
        s = s.rename(columns={"交易日": "date", "合约标的代码": "code", "认购成交量": "cvol",
                              "认沽成交量": "pvol", "未平仓认购合约数": "coi", "未平仓认沽合约数": "poi"})
        s["date"] = pd.to_datetime(s["date"]); s["code"] = s["code"].astype(str)
        rows_s.append(s[["date", "code", "cvol", "pvol", "coi", "poi"]])
    # 风险指标 → 偏度
    r = None
    for _ in range(2):
        try:
            r = ak.option_risk_indicator_sse(date=ds); break
        except Exception:
            r = None; time.sleep(0.4)
    if r is not None and len(r):
        r["code"] = r["CONTRACT_ID"].str[:6]; r["cp"] = r["CONTRACT_ID"].str[6]
        r["exp"] = r["CONTRACT_ID"].str[7:11]
        r["iv"] = pd.to_numeric(r["IMPLC_VOLATLTY"], errors="coerce")
        r["delta"] = pd.to_numeric(r["DELTA_VALUE"], errors="coerce")
        r = r[(r.iv > 0.02) & (r.iv < 3.0)]
        out = []
        for code, g in r.groupby("code"):
            em = ds[2:6]
            cur = g[g.exp >= em]
            if cur.empty: cur = g
            p = cur[(cur.cp == "P") & (cur.delta.between(-0.40, -0.15))].iv
            c = cur[(cur.cp == "C") & (cur.delta.between(0.15, 0.40))].iv
            a = cur[cur.delta.abs().between(0.40, 0.60)].iv
            if len(p) and len(c):
                out.append(dict(date=dt.date(), code=code, iv_put25=p.median(), iv_call25=c.median(),
                                iv_atm=a.median() if len(a) else np.nan, np25=len(p), nc25=len(c)))
        if out:
            d_ = pd.DataFrame(out); d_["skew"] = d_.iv_put25 - d_.iv_call25; rows_k.append(d_)
    if rows_s and (i % 40 == 0):
        pd.DataFrame(pd.concat(rows_s)).drop_duplicates(["date", "code"]).to_csv(STAT, index=False)
    if rows_k and (i % 40 == 0):
        pd.DataFrame(pd.concat(rows_k)).drop_duplicates(["date", "code"]).to_csv(RISK, index=False)
    if (i + 1) % 50 == 0:
        open(os.path.join(BASE, "fetch_progress.txt"), "w").write(f"{i+1}/{len(dates)} {ds}\n")
        print(i + 1, ds, flush=True)

if rows_s: pd.DataFrame(pd.concat(rows_s)).drop_duplicates(["date", "code"]).to_csv(STAT, index=False)
if rows_k: pd.DataFrame(pd.concat(rows_k)).drop_duplicates(["date", "code"]).to_csv(RISK, index=False)
open(os.path.join(BASE, "fetch_done.flag"), "w").write("ok\n")
print("DONE", flush=True)
