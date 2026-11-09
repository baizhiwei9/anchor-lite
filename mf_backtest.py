# -*- coding: utf-8 -*-
"""多因子强弱打分 → 行业集中配置（A股总仓≤30%，单边 5bp，只用 t-1 信息）"""
import os, numpy as np, pandas as pd, factor_table as FT
BASE=os.path.dirname(os.path.abspath(__file__)); D=os.path.join(BASE,"data2026")
IND=FT.IND
PX={n:pd.read_csv(os.path.join(D,f"{n}_full.csv"),parse_dates=["date"]).drop_duplicates("date").set_index("date")["close"].astype(float) for n in IND+["treasury10_etf"]}
IDX=FT.IDX
RET=pd.DataFrame({n:PX[n].pct_change() for n in IND+["treasury10_etf"]}).reindex(IDX).fillna(0.0)
def run(year=2026,top=1,cap=0.30,smooth=1,cost=5.0,mode="eq",band=0.005,win=None):
    F,S=FT.build(**(win or {})); S=S.dropna(how="all")
    if smooth>1: S=S.rolling(smooth,min_periods=1).mean()
    days=[d for d in IDX if d.year==year and d in S.index]
    w=np.zeros(len(IND)+1); w[-1]=1.0; trades=0; rec=[]; prev_pick=None
    for i,dt in enumerate(days):
        s=S.loc[dt].dropna()
        pick=list(s.sort_values(ascending=False).index[:top])
        sub=s[pick]
        if mode=="eq" or len(pick)==1: tw=np.array([cap/len(pick) if p in pick else 0.0 for p in IND])
        else:
            p=(sub-sub.min()+1e-6); p=p/p.sum(); tw=np.array([cap*p[x] if x in pick else 0.0 for x in IND])
        tgt=np.append(tw,1-tw.sum()); cur=np.append(w[:-1],w[-1])
        d=tgt-cur; d[np.abs(d)<band]=0
        if np.abs(d).sum()>1e-9:
            trades+=int((np.abs(d)>1e-9).sum()/2+0.5); cur=cur+d; cur=cur-np.abs(d)*cost/1e4
        w=cur
        r=float((w[:-1]*RET.loc[dt,IND].values).sum()+w[-1]*RET.loc[dt,"treasury10_etf"])
        rec.append(dict(date=dt,ret=r,eq=float(w[:-1].sum())))
        grow=1+RET.loc[dt].values; w=w*grow; w=w/w.sum()
    df=pd.DataFrame(rec).set_index("date"); nv=(1+df["ret"]).cumprod()
    return dict(ret=(nv.iloc[-1]-1)*100,dd=(nv/nv.cummax()-1).min()*100,
                sharpe=df["ret"].mean()/(df["ret"].std()+1e-12)*np.sqrt(252),
                eq=df["eq"].mean()*100,trades=trades),df
if __name__=="__main__":
    print("== 多因子强弱打分（2026，A股≤30%，单边5bp）")
    for top in (1,2,3):
        for sm in (1,5):
            m,_=run(2026,top=top,smooth=sm)
            print(f"  前{top} 名 平滑{sm}日 收益 {m['ret']:+6.2f}% 回撤 {m['dd']:6.2f}% 夏普 {m['sharpe']:5.2f} 股仓 {m['eq']:.1f}% 调仓 {m['trades']}")
    m,_=run(2026,top=3,smooth=5,mode="score")
    print(f"  前3 名 平滑5日 得分加权 收益 {m['ret']:+6.2f}% 回撤 {m['dd']:6.2f}% 夏普 {m['sharpe']:5.2f} 调仓 {m['trades']}")
