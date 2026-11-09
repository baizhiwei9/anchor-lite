# -*- coding: utf-8 -*-
"""行业强弱因子表：全部因子用 t-1 及以前数据，shift(1) 后供 t 日决策使用"""
import os, numpy as np, pandas as pd
BASE=os.path.dirname(os.path.abspath(__file__)); D=os.path.join(BASE,"data2026")
IND=["semiconductor","military","pharma","broker","bank","coal","steel","liquor","consumer","newenergy","solar","nonferrous"]
def load_df(n):
    d=pd.read_csv(os.path.join(D,f"{n}_full.csv"),parse_dates=["date"]).drop_duplicates("date").set_index("date")
    return d[["close","amount"]].astype(float)
RAW={n:load_df(n) for n in IND+["hs300"]}
IDX=RAW["hs300"].index
CLOSE=pd.DataFrame({n:RAW[n]["close"] for n in IND}).reindex(IDX).ffill()
AMT  =pd.DataFrame({n:RAW[n]["amount"] for n in IND}).reindex(IDX).ffill()
RET=CLOSE.pct_change().fillna(0.0)
HS=RAW["hs300"]["close"].reindex(IDX).ffill()
def zs(df): return (df.sub(df.mean(axis=1),axis=0)).div(df.std(axis=1)+1e-12,axis=0)
def factors(W=20,W2=40,MINP=60,CROWD=40,VOL=20):
    """W/W2 为动量窗口（用户要求 ≤40 日）"""
    F={}
    F["mom"]  =(CLOSE/CLOSE.shift(W)-1).pipe(zs)
    F["mom2"] =(CLOSE/CLOSE.shift(W2)-1).pipe(zs)
    F["rs"]   =((CLOSE/CLOSE.shift(W)-1).sub((HS/HS.shift(W)-1),axis=0)).pipe(zs)
    F["vol"]  =(-(RET.rolling(VOL).std()*np.sqrt(252))).pipe(zs)
    share=AMT.div(AMT.sum(axis=1),axis=0).rolling(W).mean()
    crowd=share.rolling(CROWD,min_periods=max(20,CROWD)).rank(pct=True)
    F["crowd"]=(-crowd).pipe(zs)
    ma1=CLOSE.rolling(W).mean(); ma2=CLOSE.rolling(W2).mean()
    F["trend"]=((CLOSE>ma1)&(ma1>ma2)).astype(float)
    return F
def score(F,w=(0.30,0.22,0.20,0.13,0.15),tw=0.25):
    """趋势作为加分项而非开/关闸门，避免弱势市全部归零"""
    return (F["mom"]*w[0]+F["mom2"]*w[1]+F["rs"]*w[2]+F["vol"]*w[3]+F["crowd"]*w[4]
            + tw*(F["trend"]-0.5))
def build(W=20,W2=40,CROWD=40,VOL=20):
    F=factors(W,W2,CROWD=CROWD,VOL=VOL); S=score(F)
    return F,S.shift(1)      # 关键：shift(1) 只用 t-1 及以前
if __name__=="__main__":
    F,S=build()
    S=S.dropna(how="all")
    print("因子表区间",S.index[0].date(),S.index[-1].date(),"形状",S.shape)
    # 前视自检：用截止 d-1 的数据重算 d 日得分，应与表内一致
    d=pd.Timestamp("2026-06-15"); cut=IDX[IDX<d]
    C2=CLOSE.loc[cut]; A2=AMT.loc[cut]; R2=C2.pct_change().fillna(0); H2=HS.loc[cut]
    f2={"mom":(C2/C2.shift(20)-1).pipe(zs),"mom2":(C2/C2.shift(40)-1).pipe(zs),
        "rs":((C2/C2.shift(20)-1).sub((H2/H2.shift(20)-1),axis=0)).pipe(zs),
        "vol":(-(R2.rolling(20).std()*np.sqrt(252))).pipe(zs)}
    sh=A2.div(A2.sum(axis=1),axis=0).rolling(20).mean(); f2["crowd"]=(-sh.rolling(250,min_periods=60).rank(pct=True)).pipe(zs)
    ma1=C2.rolling(20).mean(); ma2=C2.rolling(40).mean(); f2["trend"]=((C2>ma1)&(ma1>ma2)).astype(float)
    s2=score(f2).iloc[-1]; s1=S.loc[d]
    diff=(s2-s1).abs().max()
    print(f"前视自检：截止 {cut[-1].date()} 重算 vs 表内 {d.date()} 最大差异 {diff:.2e}（应≈0）")
    print("\n2026-06-15 强弱排序:"); print(s1.sort_values(ascending=False).round(2).to_string())
