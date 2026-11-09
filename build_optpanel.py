"""期权信号面板：PCR(成交量/持仓)、偏度(25delta风险逆转)、ATM隐含波动、持仓量变化。"""
import os, sys, numpy as np, pandas as pd
BASE=os.path.dirname(os.path.abspath(__file__)); D=os.path.join(BASE,"data_signals")
S=pd.read_csv(os.path.join(D,"opt_stats_raw.csv"),parse_dates=["交易日"])
R=pd.read_csv(os.path.join(D,"opt_risk_raw.csv"),parse_dates=["TRADE_DATE"])
S=S.rename(columns={"交易日":"date","合约标的代码":"code","认购成交量":"cvol","认沽成交量":"pvol",
                    "未平仓认购合约数":"coi","未平仓认沽合约数":"poi","总成交额":"amt"})
S["code"]=S["code"].astype(str)
g=S.groupby(["date","code"])[["cvol","pvol","coi","poi","amt"]].sum().reset_index()
g["pcr_vol"]=g.pvol/g.cvol.replace(0,np.nan); g["pcr_oi"]=g.poi/g.coi.replace(0,np.nan)
g=g.sort_values(["code","date"])
g["doi"]=g.groupby("code")[["coi","poi"]].diff().sum(axis=1)
g["doi_call"]=g.groupby("code")["coi"].diff(); g["doi_put"]=g.groupby("code")["poi"].diff()
g["oi_net"]=(g.doi_call-g.doi_put)/(g.coi+g.poi)   # 认购增仓相对认沽，>0 偏多
R["code"]=R.CONTRACT_ID.str[:6]; R["cp"]=R.CONTRACT_ID.str[6]
R["iv"]=R.IMPLC_VOLATLTY; R["delta"]=R.DELTA_VALUE
R=R[(R.iv>0.02)&(R.iv<2.0)]
rows=[]
for (dt,code),x in R.groupby(["TRADE_DATE","code"]):
    p=x[(x.cp=="P")&(x.delta.between(-0.40,-0.15))].iv
    c=x[(x.cp=="C")&(x.delta.between(0.15,0.40))].iv
    a=x[x.delta.abs().between(0.40,0.60)].iv
    rows.append(dict(date=dt,code=code,iv_put25=p.median(),iv_call25=c.median(),
                     iv_atm=a.median(),np25=len(p),nc25=len(c)))
sk=pd.DataFrame(rows); sk["skew"]=sk.iv_put25-sk.iv_call25
P=g.merge(sk,on=["date","code"],how="left").sort_values(["code","date"])
P.to_csv(os.path.join(D,"opt_panel.csv"),index=False)
print(P.shape, "skew缺失", P["skew"].isna().sum())
print(P[P.code=="588000"][["pcr_vol","pcr_oi","skew","iv_atm","oi_net"]].describe().round(3).to_string())
