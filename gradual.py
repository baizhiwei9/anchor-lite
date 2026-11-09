# -*- coding: utf-8 -*-
"""概率分布 + 渐进调仓引擎：softmax 概率 → 目标仓 → 每日限步靠拢，允许不满仓、减仓后滞回"""
import os, numpy as np, pandas as pd, factor_table as FT, flow_factors as FF
IND=FT.IND; IDX=FT.IDX
RET=pd.DataFrame({n:FT.CLOSE[n].pct_change() for n in IND}).reindex(IDX).fillna(0.0)
TB=pd.read_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)),"data2026","treasury10_etf_full.csv"),
               parse_dates=["date"]).drop_duplicates("date").set_index("date")["close"].astype(float).reindex(IDX).ffill()
RB=TB.pct_change().fillna(0.0)
def probs(score,temp=0.6):
    x=score.values/temp; x=x-x.max(); e=np.exp(x); return e/e.sum()
def run(year=2026,cap=0.30,temp=0.6,step_up=0.010,step_dn=0.015,freeze=5,p_hi=0.35,use_flow=False,one_shot=False,cost=5.0,topk=12,band=0.003,smooth=5,vol_adapt=False,vol_win=20,vol_mode="both"):
    Pf,Fl,sc=FF.combined() if use_flow else (None,None,None)
    if sc is None:
        F,_=FT.build(); Pf={k:v for k,v in F.items()}
        sc=(sum(Pf[k]*w for k,w in zip(["mom","mom2","rs","vol","crowd"],(0.30,0.22,0.20,0.13,0.15)))
            +0.25*(Pf["trend"]-0.5)).shift(1)
    sc=sc.rolling(smooth,min_periods=1).mean()      # 打分平滑，降低日频重排
    VOL=RET.rolling(vol_win).std()*np.sqrt(252)
    Vmed=VOL.median(axis=1)
    days=[d for d in IDX if d.year==year and d in sc.index]
    n=len(IND); w=np.zeros(n); wb=1.0; frz={}; rec=[]; turnover=0.0; days_traded=0
    for dt in days:
        s=sc.loc[dt].dropna()
        if len(s)<n//2 or s.isna().all():
            r=wb*RB.loc[dt]; rec.append(dict(date=dt,ret=r,eq=0.0,p={})); wb*=(1+RB.loc[dt]); continue
        s=s.reindex(IND)
        p=probs(s,temp)
        conf=(p.max()-1/n)/max(1-1/n,1e-9)
        cap_eff=cap*(0.5+0.5*conf)
        sel=np.argsort(-p)[:topk]
        tgt=np.zeros(n); tgt[sel]=p[sel]/p[sel].sum()*cap_eff
        for j,a in enumerate(IND):
            cur=w[j]; t=tgt[j]
            if one_shot: nxt=t
            else:
                d=t-cur
                if d>0 and frz.get(a,0)>0 and p[j]<p_hi: nxt=cur        # 减仓后滞回：不立即回补
                else:
                    if vol_adapt and (vol_mode=="both" or d<0):
                        vv=float(VOL.loc[dt].get(a,np.nan)); vm=float(Vmed.loc[dt])
                        k=np.clip(vv/vm,0.6,2.5) if (vm and not np.isnan(vv)) else 1.0
                    else:
                        k=1.0
                    lim=(step_up if d>0 else step_dn)*k
                    nxt=cur+np.clip(d,-lim,lim)
                if nxt<cur-1e-12: frz[a]=freeze                        # 本次减仓 → 进入冻结期
            if abs(nxt-cur)<band: nxt=cur
            if frz.get(a,0)>0 and nxt>=cur-1e-12: frz[a]=max(frz.get(a,1)-1,0)
            w[j]=max(nxt,0.0)
        if w.sum()>cap: w*=cap/w.sum()
        tv=np.abs(w.sum()- (0 if one_shot else w.sum()))
        wb=1.0-w.sum()
        chg=np.abs(np.diff(np.append(w,0))).sum()
        if chg>1e-9: days_traded+=1; turnover+=chg
        r=float((w*RET.loc[dt].values).sum())+wb*RB.loc[dt]
        rec.append(dict(date=dt,ret=r,eq=float(w.sum()),p={a:round(float(p[j]),3) for j,a in enumerate(IND)},
                        w={a:round(float(w[j]),4) for j,a in enumerate(IND)},
                        tgt={a:round(float(tgt[j]),4) for j,a in enumerate(IND)}))
        w=w*(1+RET.loc[dt].values); wb=wb*(1+RB.loc[dt]); tot=w.sum()+wb; w=w/tot; wb=wb/tot
    df=pd.DataFrame(rec).set_index("date"); nv=(1+df["ret"]).cumprod()
    return dict(ret=(nv.iloc[-1]-1)*100,dd=(nv/nv.cummax()-1).min()*100,
                sharpe=df["ret"].mean()/(df["ret"].std()+1e-12)*np.sqrt(252),
                eq=df["eq"].mean()*100,days_traded=days_traded,turnover=turnover*100),df
if __name__=="__main__":
    print("== 渐进调仓（2026，A股≤30%，5bp）")
    for lab,kw in [("价格因子·一次性",dict(one_shot=True)),
                   ("价格因子·渐进1pp/1.5pp",dict()),
                   ("价格+资金流·一次性",dict(one_shot=True,use_flow=True)),
                   ("价格+资金流·渐进",dict(use_flow=True))]:
        m,_=run(2026,**kw)
        print(f"  {lab:22s} 收益 {m['ret']:+6.2f}% 回撤 {m['dd']:6.2f}% 夏普 {m['sharpe']:5.2f} 股仓 {m['eq']:.1f}% 交易天数 {m['days_traded']} 换手 {m['turnover']:.0f}%")
