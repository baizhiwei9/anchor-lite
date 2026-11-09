"""补 2020-01 ~ 2021-12 的沪深300/上证50 期权偏度，追加进 opt_skew_long.csv"""
import sys, os, time, numpy as np, pandas as pd
import akshare as ak
BASE=os.path.dirname(os.path.abspath(__file__)); P=os.path.join(BASE,"data_long/opt_skew_long.csv")
cal=pd.read_csv(os.path.join(BASE,"data_long/hs300.csv"),parse_dates=["date"])["date"]
dates=[d for d in cal if pd.Timestamp("2020-01-01")<=d<=pd.Timestamp("2021-12-31")]
old=pd.read_csv(P,parse_dates=["date"]) if os.path.exists(P) else pd.DataFrame()
have=set(old["date"].dt.date.astype(str)) if len(old) else set()
rows=[]
for i,dt in enumerate(dates):
    ds=dt.strftime("%Y%m%d")
    if str(dt.date()) in have: continue
    r=None
    for _ in range(2):
        try: r=ak.option_risk_indicator_sse(date=ds); break
        except Exception: r=None; time.sleep(0.4)
    if r is None or not len(r): continue
    r["code"]=r["CONTRACT_ID"].str[:6]; r["cp"]=r["CONTRACT_ID"].str[6]; r["exp"]=r["CONTRACT_ID"].str[7:11]
    r["iv"]=pd.to_numeric(r["IMPLC_VOLATLTY"],errors="coerce"); r["delta"]=pd.to_numeric(r["DELTA_VALUE"],errors="coerce")
    r=r[(r.iv>0.02)&(r.iv<3.0)]
    out=[]
    for code,g in r.groupby("code"):
        em=ds[2:6]; cur=g[g.exp>=em]
        if cur.empty: cur=g
        p=cur[(cur.cp=="P")&(cur.delta.between(-0.40,-0.15))].iv
        c=cur[(cur.cp=="C")&(cur.delta.between(0.15,0.40))].iv
        a=cur[cur.delta.abs().between(0.40,0.60)].iv
        if len(p) and len(c):
            out.append(dict(date=dt,code=code,iv_put25=p.median(),iv_call25=c.median(),
                            iv_atm=a.median() if len(a) else np.nan,np25=len(p),nc25=len(c)))
    if out:
        d=pd.DataFrame(out); d["skew"]=d.iv_put25-d.iv_call25; rows.append(d)
    if (i+1)%50==0: print(i+1,ds,flush=True)
if rows:
    new=pd.concat([old]+rows).drop_duplicates(["date","code"]).sort_values(["date","code"])
    new.to_csv(P,index=False); print("写入",len(new))
open(os.path.join(BASE,"skew_pre_done.flag"),"w").write("ok")
print("DONE")
