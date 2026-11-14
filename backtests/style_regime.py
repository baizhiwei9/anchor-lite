# -*- coding: utf-8 -*-
"""Anchor-Lite 风格概率引擎（自建）：高斯 HMM 状态概率 → 风格先验 → 概率加权配置
用法：
  python3 style_regime.py --backtest 2026          # 2026 单年回测 + 对照
  python3 style_regime.py --asof latest --out style_out   # 周度任务：输出概率与建议权重
口径：滤波概率（仅用 t-1 及以前信息），股票合计上限 30%，单边成本 5bp。
"""
import os, sys, json, argparse, numpy as np, pandas as pd
BASE=os.path.dirname(os.path.abspath(__file__)); D=os.path.join(BASE,"data2026")
STYLES={"红利":["dividend"],"成长":["cyb","kc50"],"周期":["coal","steel"],
        "消费":["consumer","liquor","pharma"],"科技":["semiconductor","solar","newenergy","military"]}
# 状态 → 风格先验（低波趋势 / 中性震荡 / 高波危机，按状态波动升序）
PRIOR_DEFAULT=np.array([[0.10,0.35,0.10,0.15,0.30],   # 低波趋势：成长/科技偏高
                [0.25,0.15,0.20,0.25,0.15],   # 中性震荡：均衡偏向红利/消费
                [0.45,0.15,0.10,0.20,0.10]])  # 高波危机：红利为主
PRIOR_SHARP=np.array([[0.10,0.40,0.10,0.15,0.25],[0.30,0.10,0.20,0.25,0.15],[0.70,0.05,0.10,0.10,0.05]])
PRIOR=PRIOR_DEFAULT.copy()
def load(n):
    f=os.path.join(D,f"{n}_full.csv")
    return pd.read_csv(f,parse_dates=["date"]).drop_duplicates("date").set_index("date")["close"].astype(float)
need=sorted({a for v in STYLES.values() for a in v})+["hs300","treasury10_etf"]
PX={n:load(n) for n in need}; IDX=PX["hs300"].index
RET=pd.DataFrame({k:v.pct_change() for k,v in PX.items()}).reindex(IDX).fillna(0.0)
SRET=pd.DataFrame({k:RET[v].mean(axis=1) for k,v in STYLES.items()}).fillna(0.0)   # 风格等权收益

def gauss_hmm(X,K=3,iters=40,seed=0,var_floor=0.0):
    rng=np.random.default_rng(seed); T,d=X.shape
    q=np.quantile(X[:,1],[0.33,0.66]); mu=np.tile(X.mean(axis=0),(K,1)).astype(float)
    for k in range(K): mu[k,1]=q[min(k,K-2)]
    var=np.tile(X.var(axis=0)+1e-6,(K,1)); A=np.full((K,K),0.1/(K-1)); np.fill_diagonal(A,0.9); pi=np.ones(K)/K
    def pdf(X,mu,var):
        e=np.exp(-0.5*((X[:,None,:]-mu[None])**2)/var[None])/np.sqrt(2*np.pi*var[None])
        return e.prod(axis=2)
    ll_old=-1e18
    for _ in range(iters):
        B=pdf(X,mu,var); al=np.zeros((T,K)); c=np.zeros(T)
        al[0]=pi*B[0]; c[0]=al[0].sum(); al[0]/=c[0]
        for t in range(1,T):
            al[t]=(al[t-1]@A)*B[t]; c[t]=al[t].sum(); al[t]/=max(c[t],1e-300)
        be=np.ones((T,K))
        for t in range(T-2,-1,-1):
            be[t]=A@(B[t+1]*be[t+1]); be[t]/=max(c[t+1],1e-300)
        g=al*be; g/=g.sum(axis=1,keepdims=True)+1e-300
        xi=np.zeros((T-1,K,K))
        for t in range(T-1):
            m=al[t][:,None]*A*(B[t+1]*be[t+1])[None,:]; xi[t]=m/max(m.sum(),1e-300)
        pi=g[0]; A=(xi.sum(0)+1e-6); A/=A.sum(1,keepdims=True)
        mu=np.array([(g[:,k,None]*X).sum(0)/max(g[:,k].sum(),1e-9) for k in range(K)])
        var=np.array([(g[:,k,None]*(X-mu[k])**2).sum(0)/max(g[:,k].sum(),1e-9) for k in range(K)])
        var=var+np.maximum(1e-6,var_floor*X.var(axis=0)[None,:])
        ll=np.log(c+1e-300).sum()
        if abs(ll-ll_old)<1e-4: break
        ll_old=ll
    return dict(pi=pi,A=A,mu=mu,var=var)
def filter_last(m,X):
    B=(np.exp(-0.5*((X[:,None,:]-m["mu"][None])**2)/m["var"][None])/np.sqrt(2*np.pi*m["var"][None])).prod(axis=2)
    al=m["pi"]*B[0]; al/=max(al.sum(),1e-300)
    for t in range(1,len(X)):
        al=(al@m["A"])*B[t]; al/=max(al.sum(),1e-300)
    return al
mk=RET["hs300"].rolling(20).std()*np.sqrt(252)
m60=PX["hs300"].pct_change(60)
disp=SRET.std(axis=1).rolling(5).mean()*np.sqrt(252)
FEA=pd.DataFrame({"r":RET["hs300"],"v":mk,"m":m60,"d":disp}).dropna()
def state_probs():
    """逐日：用 t-1 及以前 252 窗口训练+滤波，输出当日状态概率（不看 t）"""
    out={}; months=set()
    for i,(dt,row) in enumerate(FEA.iterrows()):
        if i<150: continue
        if (dt.year,dt.month) not in months and i>=150:
            months.add((dt.year,dt.month))
            win=FEA.iloc[max(0,i-252):i]                 # 严格截止 t-1
            X=((win-win.mean())/ (win.std()+1e-9)).values
            model=gauss_hmm(X,var_floor=0.02)
            order=np.argsort(model["mu"][:,1])           # 按波动升序重排：0=低波 2=高波
            model={k:(v[order] if k in ("pi","mu","var") else v[np.ix_(order,order)]) for k,v in model.items()}
            months_cache[0]=model
        model=months_cache.get(0)
        if model is None: continue
        win=FEA.iloc[max(0,i-126):i]                     # 滤波用近 126 日
        X=((win-win.mean())/(win.std()+1e-9)).values
        out[dt]=filter_last(model,X)
    return pd.DataFrame(out,index=[0,1,2]).T
months_cache={}
SP=state_probs()
TEMP=0.5; MIX=0.15; KAPPA=0.5          # 温度>1 软化极端概率；MIX 向均匀先验回拉
def style_probs(dt):
    p=SP.loc[dt].values if dt in SP.index else np.ones(3)/3
    p=np.power(np.clip(p,1e-6,None),TEMP); p=p/p.sum()
    p=(1-MIX)*p+MIX*np.ones(3)/3
    prior=p@PRIOR
    mom=(PX_style.loc[:dt].iloc[-1]/PX_style.loc[:dt].iloc[-21]-1).values
    z=(mom-mom.mean())/(mom.std()+1e-9)
    w=prior*np.exp(KAPPA*np.clip(z,-2,2)); return w/w.sum()
PX_style=pd.DataFrame({k:(1+SRET[k]).cumprod() for k in STYLES}).reindex(IDX)
MEM=STYLES  # 风格 -> 成员 ETF
def inner_w(style,dt,kind="eq"):
    """用 t-1 及以前动量决定风格内成员权重；单成员则 1.0"""
    ms=[m for m in MEM[style] if m in RET.columns]
    if len(ms)==1: return {ms[0]:1.0}
    if kind=="eq": return {m:1.0/len(ms) for m in ms}
    mom={}
    for m in ms:
        s_=PX[m].loc[:dt]
        mom[m]=float(s_.iloc[-1]/s_.iloc[-21]-1) if len(s_)>21 else 0.0
    order=sorted(ms,key=lambda x:-mom[x])
    if len(order)==2: return {order[0]:0.8,order[1]:0.2}
    w={order[0]:0.6,order[1]:0.3}; w[order[2]]=0.1
    return w
def backtest(year=2026,cap=0.30,mode="prob",cost=5.0,band=0.01,rebal="D",inner="eq",tau=0.3,pthr=0.0,confirm=1):
    days=IDX[IDX.year==year]; cols=list(STYLES); n=len(cols)
    w=np.zeros(n); wb=1.0; trades=0; rec=[]; prev=None
    brk=SRET.loc[days].values
    PR=np.array([style_probs(days[max(0,i-1)]) for i in range(len(days))])
    held=None
    for i,dt in enumerate(days):
        wk=(i==0) or (dt.isocalendar()[1]!=days[i-1].isocalendar()[1])
        if i>0 and (rebal=="D" or wk):
            p=PR[i]                  # 只用昨日及以前
            if mode=="prob": tw=cap*p/p.sum()
            elif mode=="soft":
                q=np.power(np.clip(p,1e-9,None),1.0/tau); tw=cap*q/q.sum()
            elif mode=="conc1":
                pick=int(np.argmax(p))
                if confirm>1:
                    ok=all(int(np.argmax(PR[k]))==pick for k in range(max(1,i-confirm+1),i+1))
                    if not ok and held is not None: pick=held
                    elif not ok: pick=None
                if pick is None: tw=np.zeros(n)
                else: tw=np.zeros(n); tw[pick]=cap; held=pick
            elif mode=="conc2":
                idx=np.argsort(-p)[:2]; tw=np.zeros(n); tw[idx]=cap*p[idx]/p[idx].sum()
            elif mode=="top1":
                tw=np.zeros(n); tw[int(np.argmax(p))]=cap
            elif mode=="equal": tw=np.full(n,cap/n)
            elif mode=="binary":
                j=[cols.index("红利"),cols.index("成长")]
                s=p[j].sum(); tw=np.zeros(n); tw[j]=cap*p[j]/max(s,1e-9)
            elif mode=="div": tw=np.zeros(n); tw[cols.index("红利")]=cap
            elif mode=="growth": tw=np.zeros(n); tw[cols.index("成长")]=cap
            elif mode=="broad":
                tw=np.zeros(n); m=PX["cyb"].loc[:dt].iloc[-1]/PX["cyb"].loc[:dt].iloc[-61]-1
                m2=PX["kc50"].loc[:dt].iloc[-1]/PX["kc50"].loc[:dt].iloc[-61]-1
                tw[cols.index("成长")]=cap if m>=m2 else 0.0
            else: tw=w.copy()
            if pthr>0 and p.max()<pthr: tw=np.zeros(n)
            tgt=np.append(tw,1-tw.sum())
            cur=np.append(w,wb)
            d=tgt-cur; d[np.abs(d)<band]=0
            if np.abs(d).sum()>1e-9:
                trades+=int((np.abs(d)>1e-9).sum()/2+0.5); cur=cur+d
                w,wb=cur[:-1],cur[-1]
                cur=cur-np.abs(d)*cost/1e4
        sret=np.zeros(n)
        for j,st_ in enumerate(cols):
            iw=inner_w(st_, days[i-1] if i>0 else dt, inner)
            sret[j]=sum(iw[m]*RET[m].loc[dt] for m in iw if dt in RET.index)
        r=(w*sret).sum()+wb*(RET["treasury10_etf"].loc[dt] if dt in RET.index else 0.0)
        nav_=rec[-1]["nav"]*(1+r) if rec else 1+r
        rec.append(dict(date=dt,ret=r,nav=nav_,eq=w.sum()))
        w=w*(1+sret); wb=wb*(1+(RET["treasury10_etf"].loc[dt] if dt in RET.index else 0.0))
        tot=w.sum()+wb; w=w/tot; wb=wb/tot
    df=pd.DataFrame(rec).set_index("date")
    nv=df["nav"]; dd=(nv/nv.cummax()-1).min()
    sh=df["ret"].mean()/ (df["ret"].std()+1e-12)*np.sqrt(252)
    return dict(ret=(nv.iloc[-1]-1)*100,dd=dd*100,sharpe=sh,mdd=df["eq"].mean()*100,trades=trades),df
if __name__=="__main__":
    ap=argparse.ArgumentParser(); ap.add_argument("--backtest",type=int); ap.add_argument("--asof",default=None)
    ap.add_argument("--out",default="style_out"); a=ap.parse_args()
    if a.asof:
        dt=IDX[-1]; p=state_probs()
        sp=p.loc[dt].values if dt in p.index else np.ones(3)/3
        tp=sp**TEMP; tp=(1-MIX)*tp/tp.sum()+MIX*np.ones(3)/3
        prior=tp@PRIOR
        mom=(PX_style.loc[:dt].iloc[-1]/PX_style.loc[:dt].iloc[-21]-1).values
        z=(mom-mom.mean())/(mom.std()+1e-9)
        w=prior*np.exp(0.5*np.clip(z,-2,2)); w=w/w.sum()
        tgt={k:round(float(v)*0.30,4) for k,v in zip(STYLES,w)}
        m,df=backtest(dt.year,"prob" if False else 2026) if False else backtest(dt.year)
        last=df.index[-5:]
        os.makedirs(a.out,exist_ok=True)
        rec=dict(asof=str(dt.date()),state_prob={str(i):round(float(x),4) for i,x in enumerate(tp)},
                 style_prob={k:round(float(v),4) for k,v in zip(STYLES,w)},
                 target_weight=tgt,stock_total=round(sum(tgt.values()),4),
                 last_week=dict(ret=round(float((1+df.loc[last,'ret']).prod()-1)*100,2),
                                eq=round(float(df.loc[last,'eq'].mean()*100),1)),
                 year_to_date=dict(ret=round(m["ret"],2),dd=round(m["dd"],2),sharpe=round(m["sharpe"],2)))
        json.dump(rec,open(os.path.join(a.out,f"{dt.date()}.json"),"w"),ensure_ascii=False,indent=2)
        md=[f"# Anchor-Lite 风格概率 {dt.date()}","",f"状态概率: {rec['state_prob']}","",
            "| 风格 | 概率 | 目标权重(股票合计≤30%) |","|---|---|---|"]
        md+=[f"| {k} | {rec['style_prob'][k]*100:.1f}% | {rec['target_weight'][k]*100:.1f}% |" for k in STYLES]
        md+=["",f"上周组合: {rec['last_week']['ret']:+.2f}%，股票均仓 {rec['last_week']['eq']:.1f}%",
             f"年内: {rec['year_to_date']['ret']:+.2f}%，回撤 {rec['year_to_date']['dd']:.2f}%，夏普 {rec['year_to_date']['sharpe']:.2f}"]
        open(os.path.join(a.out,f"{dt.date()}.md"),"w").write("\n".join(md))
        print("\n".join(md)); sys.exit(0)
    if a.backtest:
        print("== 2026 单年（股票上限 30%，单边 5bp）")
        for mode,lab in [("prob","概率加权"),("top1","硬切换Top1"),("equal","等权5风格"),
                         ("binary","红利/成长二元"),("div","单押红利"),("growth","单押成长"),("broad","宽基60日动量")]:
            m,_=backtest(a.backtest,mode=mode)
            print(f"{lab:12s} 收益 {m['ret']:+6.2f}%  回撤 {m['dd']:6.2f}%  夏普 {m['sharpe']:.2f}  股仓 {m['mdd']:.1f}%  调仓 {m['trades']}")
        dt=IDX[IDX.year==a.backtest][-1]; p=style_probs(dt)
        print("末日风格概率", {k:round(v*100,1) for k,v in zip(STYLES,p)})
        print("末日状态概率", [round(x,3) for x in (SP.loc[dt].values if dt in SP.index else [1/3]*3)])
