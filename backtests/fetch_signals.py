import os, sys, time
import akshare as ak, pandas as pd, numpy as np
BASE = os.path.dirname(os.path.abspath(__file__)); OUT = os.path.join(BASE, "data_signals")
os.makedirs(OUT, exist_ok=True)
dates = pd.read_csv(os.path.join(BASE, "data2026/kc50.csv"), parse_dates=["date"])["date"]
dates = [d.strftime("%Y%m%d") for d in dates]
print("交易日数", len(dates))
st, rk = [], []
t0 = time.time()
for i, d in enumerate(dates):
    for _ in range(3):
        try: st.append(ak.option_daily_stats_sse(date=d)); break
        except Exception: time.sleep(0.5)
    for _ in range(3):
        try: rk.append(ak.option_risk_indicator_sse(date=d)); break
        except Exception: time.sleep(0.5)
    if (i+1) % 50 == 0: print(i+1, "%.0fs" % (time.time()-t0), flush=True)
S = pd.concat(st, ignore_index=True); R = pd.concat(rk, ignore_index=True)
S.to_csv(os.path.join(OUT,"opt_stats_raw.csv"), index=False)
R.to_csv(os.path.join(OUT,"opt_risk_raw.csv"), index=False)
print("stats", S.shape, "risk", R.shape)
fw = {}
for s in ["IF0","IH0","IC0","IM0","SC0","AU0","CU0"]:
    try:
        d = ak.futures_main_sina(symbol=s); d.columns=[c.strip() for c in d.columns]
        d["日期"]=pd.to_datetime(d["日期"])
        d = d[(d["日期"]>="2026-01-01")&(d["日期"]<="2026-11-06")]
        fw[s]=d.set_index("日期"); print(s, d.shape, d.index[0].date(), d.index[-1].date())
    except Exception as e: print(s,"FAIL",str(e)[:80])
pd.concat(fw).to_csv(os.path.join(OUT,"futures_main.csv"))
