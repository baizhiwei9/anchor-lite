"""2026 单年：行业 ETF 池 + 风格动量 + 均线状态（可选期权/拓扑风控）"""
import os, numpy as np, pandas as pd
BASE=os.path.dirname(os.path.abspath(__file__)); D=os.path.join(BASE,"data2026")
STYLE={"成长":["semiconductor","newenergy","solar","military"],"金融":["broker","bank"],"周期":["coal","steel"],"消费":["liquor","consumer","pharma"]}
BUCKET={a:k for k,v in STYLE.items() for a in v}
IND=["semiconductor","military","pharma","broker","bank","coal","steel","liquor","consumer","newenergy","solar"]
def load(n,full=True):
    f=os.path.join(D,f"{n}{'_full' if full else ''}.csv")
    d=pd.read_csv(f,parse_dates=["date"]).drop_duplicates("date").set_index("date")["close"].astype(float)
    return d
PX={n:load(n) for n in IND+["hs300","cyb","kc50","nasdaq","oil","treasury10_etf","dividend"]}
IDX=PX["hs300"].index
RET=pd.DataFrame({k:v.pct_change() for k,v in PX.items()}).reindex(IDX).fillna(0.0)

def run(top_n=2, cap=0.6, mom_win=60, ma_fast=20, ma_slow=60, dd=0.016, dd2=0.020, style_cap=False,
        band=0.03, cost=5.0, use_ma=True, gate=True, risk=None):
    cols=list(RET.columns); n=len(cols)
    w=np.zeros(n); w[cols.index("treasury10_etf")]=1.0
    nav=peak=1.0; br=1.0; wk=None; tgt=np.zeros(n)
    rr=[]; trades=[]; trail=[]; sel_log={}
    MA={a:(PX[a].rolling(ma_fast).mean(), PX[a].rolling(ma_slow).mean()) for a in IND}
    for dt in IDX[:-1]:
        k=tuple(dt.isocalendar()[:2])
        if k!=wk:
            wk=k
            mom={}
            for a in IND:
                s=PX[a].loc[:dt]
                mom[a]=float(s.iloc[-1]/s.iloc[-1-mom_win]-1) if len(s)>mom_win else -9
            ok=[a for a in IND if mom[a]>0]
            if use_ma:
                ok=[a for a in ok if (not np.isnan(MA[a][0].get(dt,np.nan))) and float(MA[a][0].loc[dt])>float(MA[a][1].loc[dt])]
            if style_cap:
                top=[]; used=set()
                for a in sorted(ok,key=lambda x:-mom[x]):
                    b=BUCKET.get(a)
                    if b in used: continue
                    top.append(a); used.add(b)
                    if len(top)>=top_n: break
            else:
                top=sorted(ok,key=lambda x:-mom[x])[:top_n]
            sel_log[str(dt)[:10]]=(top,{k2:round(v,3) for k2,v in mom.items()})
            if risk is not None and bool(risk.get(dt,False)):
                top=[]
            tgt=np.zeros(n)
            for a in top: tgt[cols.index(a)]=cap/len(top) if top else 0.0
            tgt[cols.index("treasury10_etf")]=max(1.0-tgt.sum(),0.0)
        tgt2=tgt.copy()
        if gate: tgt2[cols.index("treasury10_etf")]=1.0-(tgt2.sum()-tgt2[cols.index("treasury10_etf")])*br
        for j in [cols.index(a) for a in IND]: tgt2[j]*= br if gate else 1.0
        tgt2[cols.index("treasury10_etf")]=max(1.0-tgt2[[cols.index(a) for a in IND]].sum(),0.0)
        trade=np.zeros(n)
        for j in range(n):
            if abs(w[j]-tgt2[j])>band: trade[j]=tgt2[j]-w[j]
        c=0.0
        if np.abs(trade).sum()>1e-6:
            w=w+trade; c=float(np.abs(trade).sum())*cost/1e4
            trades.append((str(dt)[:10],[(cols[j],round(trade[j],3)) for j in range(n) if abs(trade[j])>0.005]))
        pnl=float((w*RET.loc[dt].values).sum()); ret=pnl-c
        nav*=1+ret; peak=max(peak,nav); dcur=nav/peak-1
        if gate:
            if dcur<=-dd2: br=0.0
            elif dcur<=-dd: br=min(br,0.4)
            elif dcur>=-dd/2: br=1.0
        if pnl>-1: w=w*(1+RET.loc[dt].values)/(1+pnl)
        if w.sum()>1: w=w/w.sum()
        rr.append(ret)
        trail.append(dict(date=str(dt)[:10],ret=ret,eq=float(w[[cols.index(a) for a in IND]].sum()),
                          bond=float(w[cols.index("treasury10_etf")]),br=br))
    r=np.array(rr); nv=np.cumprod(1+r); ddv=nv/np.maximum.accumulate(nv)-1
    m=dict(total=float(nv[-1]-1),maxdd=float(ddv.min()),vol=float(r.std()*np.sqrt(252)),
           sharpe=float((nv[-1]**(252/len(r))-1)/(r.std()*np.sqrt(252))),n_trades=len(trades))
    return m,pd.DataFrame(trail),sel_log,trades
