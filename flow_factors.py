# -*- coding: utf-8 -*-
"""场内资金流因子（t-1 截断，窗口≤40）：CMF、净流入比率、成交额占比变化、放量分位"""
import os, numpy as np, pandas as pd, factor_table as FT
BASE=os.path.dirname(os.path.abspath(__file__)); D=os.path.join(BASE,"data2026")
IND=FT.IND
def load_raw(n):
    d=pd.read_csv(os.path.join(D,f"{n}_full.csv"),parse_dates=["date"]).drop_duplicates("date").set_index("date")
    for c in ("high","low","close","volume","amount"):
        if c not in d.columns: d[c]=np.nan
    return d[["high","low","close","volume","amount"]].astype(float)
R={n:load_raw(n) for n in IND}
IDX=FT.IDX
H=pd.DataFrame({n:R[n]["high"] for n in IND}).reindex(IDX).ffill()
L=pd.DataFrame({n:R[n]["low"] for n in IND}).reindex(IDX).ffill()
C=pd.DataFrame({n:R[n]["close"] for n in IND}).reindex(IDX).ffill()
V=pd.DataFrame({n:R[n]["volume"] for n in IND}).reindex(IDX).ffill()
A=pd.DataFrame({n:R[n]["amount"] for n in IND}).reindex(IDX).ffill()
def zs(df): return (df.sub(df.mean(axis=1),axis=0)).div(df.std(axis=1)+1e-12,axis=0)
def build(W=20,W2=40,shift=True):
    rng=(H-L).replace(0,np.nan)
    mfm=(((C-L)-(H-C))/rng).fillna(0.0)
    cmf=(mfm*A).rolling(W).sum()/A.rolling(W).sum().replace(0,np.nan)      # Chaikin 资金流
    sgn=np.sign(C.diff().fillna(0.0))
    net=(sgn*A).rolling(W).sum()/A.rolling(W).sum().replace(0,np.nan)      # 净流入比率
    share=A.div(A.sum(axis=1),axis=0)
    chg=(share/share.shift(W)-1).replace([np.inf,-np.inf],np.nan)          # 资金关注度变化
    vpct=V.rolling(W).mean().rolling(W2).rank(pct=True)                    # 放量分位
    F={"cmf":zs(cmf),"net":zs(net),"fchg":zs(chg),"vpct":zs(vpct)}
    return {k:(v.shift(1) if shift else v) for k,v in F.items()}   # 默认只保留 t-1 及以前
def combined(W=20,W2=40,wprice=(0.20,0.15,0.15,0.10,0.10),wflow=(0.12,0.10,0.08,0.0)):
    """价格因子（FT）+ 资金流因子（本模块）合并成统一打分"""
    Pf,Ps=FT.build(W=W,W2=W2,CROWD=W2,VOL=W)
    Fl=build(W,W2,shift=False)
    keys=["mom","mom2","rs","vol","crowd"]
    s=sum(Pf[k]*w for k,w in zip(keys,wprice))+0.25*(Pf["trend"]-0.5)
    s=s+sum(Fl[k]*w for k,w in zip(["cmf","net","fchg","vpct"],wflow))
    return Pf,Fl,s.shift(1)
if __name__=="__main__":
    Pf,Fl,s=combined()
    print("资金流因子区间", s.dropna(how="all").index[0].date(), s.dropna(how="all").index[-1].date())
    RET=pd.DataFrame({n:FT.CLOSE[n].pct_change() for n in IND}).reindex(IDX).fillna(0.0)
    days=[d for d in IDX if d.year==2026 and d in s.index]
    def ic(series):
        out=[]
        for d in days:
            if d not in series.index: continue
            f=RET.loc[d] if False else None
        return out
    fwd1=RET.shift(-1)
    print("\n== 单因子 IC（次日收益，2026）")
    for k,v in list(Fl.items()):
        icv=[]
        for d in days:
            if d not in fwd1.index or fwd1.loc[d].isna().any() or v.loc[d].isna().any(): continue
            a=v.loc[d].values; b=fwd1.loc[d].values
            if a.std()<1e-9 or b.std()<1e-9: continue
            icv.append(np.corrcoef(a,b)[0,1])
        print(f"  {k:6s} IC {np.mean(icv):+.3f}  IC>0 {(np.array(icv)>0).mean()*100:.0f}%  n={len(icv)}")
    # 前视自检：用截止 T 的数据重算未平移打分，应等于表内 T+1 日的平移值
    d=pd.Timestamp("2026-06-15"); cut=IDX[IDX<d]; T=cut[-1]
    f2=FT.factors(W=20,W2=40,CROWD=40,VOL=20)
    C2,C3=C.loc[:T],C.loc[:T]
    A2=A.loc[:T]; H2=H.loc[:T]; L2=L.loc[:T]; V2=V.loc[:T]
    rng=(H2-L2).replace(0,np.nan)
    cmf=(((C2-L2)-(H2-C2))/rng).fillna(0.0).mul(A2).rolling(20).sum()/A2.rolling(20).sum()
    net=(np.sign(C2.diff().fillna(0.0))*A2).rolling(20).sum()/A2.rolling(20).sum()
    sh=A2.div(A2.sum(axis=1),axis=0)
    fchg=(sh/sh.shift(20)-1).replace([np.inf,-np.inf],np.nan)
    vpct=V2.rolling(20).mean().rolling(40).rank(pct=True)
    g2={"cmf":zs(cmf),"net":zs(net),"fchg":zs(fchg),"vpct":zs(vpct)}
    sc=(sum(f2[k]*w for k,w in zip(["mom","mom2","rs","vol","crowd"],(0.20,0.15,0.15,0.10,0.10)))
        +0.25*(f2["trend"]-0.5)+sum(g2[k]*w for k,w in zip(["cmf","net","fchg","vpct"],(0.12,0.10,0.08,0.0))))
    print(f"\n前视自检：截止 {T.date()} 重算 vs 表内 {d.date()} 差异 {float((sc.loc[T]-s.loc[d]).abs().max()):.2e}（应≈0）")
