import json,os,numpy as np,pandas as pd,dyn5 as M5,dyn3 as D3
CFG=dict(dd=0.025,oil_hi=0.25,band=0.04,oil_stop=0.08,kc_hi=0.20)
m,tr,tl=M5.run(record=True,**CFG)
tl["date"]=pd.to_datetime(tl.date); tl=tl.set_index("date")
px=M5.panel.kc50; tl["kret"]=px.pct_change().reindex(tl.index).fillna(0)
out={"cfg":CFG,"metrics":m,"n_trades":len(tr),
     "kc_contrib":float((tl.kc50*tl.kret).sum()),
     "trades":tr}
# 分月
rows=[]
for mo,g in tl.groupby(tl.index.to_period("M")):
    rows.append(dict(month=str(mo),kc_w=float(g.kc50.mean()),kc_wmax=float(g.kc50.max()),
                     oil_w=float(g.oil.mean()),tot=float(g.tot.mean()),
                     kc_ret=float((1+g.kret).prod()-1),kc_contrib=float((g.kc50*g.kret).sum()),
                     dd=float(g.dd.min()),hot_days=int((g.overheat>0).sum())))
out["monthly"]=rows
# 上下半年
def seg(a,b):
    r=tl.nav.pct_change().fillna(0.0)
    r=r.loc[a:b]; nav=(1+r).cumprod()
    return float(nav.iloc[-1]-1), float((nav/nav.cummax()-1).min())
out["H1"]=seg("2026-01-05","2026-06-30"); out["H2"]=seg("2026-07-01","2026-11-06")
# 成本 10bp
m10,_,_=M5.run(cost=10.0,record=False,**CFG); out["cost10"]=m10
# 邻域
nb={}
for k,v in {"dd0.02":dict(dd=0.02),"dd0.03":dict(dd=0.03),"oil_hi0.20":dict(oil_hi=0.20),
            "oil_hi0.30":dict(oil_hi=0.30),"band0.03":dict(band=0.03),"band0.05":dict(band=0.05),
            "oil_stop0.06":dict(oil_stop=0.06),"oil_stop0.12":dict(oil_stop=0.12),
            "kc_hi0.15":dict(kc_hi=0.15),"kc_hi0.25":dict(kc_hi=0.25)}.items():
    cfg=dict(CFG); cfg.update(v); mm,_,_=M5.run(record=False,**cfg); nb[k]=mm
out["neighbors"]=nb
tl.reset_index().to_csv("dyn5_trail.csv",index=False)
json.dump(out,open("dyn5_final.json","w"),ensure_ascii=False,indent=2,default=float)
print("最终：收益 %.2f%% 回撤 %.2f%% 波动 %.1f%% 夏普 %.2f 笔数 %d 科创腿 %.2f%%"%(
    m["total"]*100,m["maxdd"]*100,m["vol"]*100,m["sharpe"],len(tr),out["kc_contrib"]*100))
print("H1 %.2f%% / %.2f%%  H2 %.2f%% / %.2f%%"%(out["H1"][0]*100,out["H1"][1]*100,out["H2"][0]*100,out["H2"][1]*100))
print("10bp：%.2f%% / %.2f%%"%(m10["total"]*100,m10["maxdd"]*100))
print("\n邻域：")
for k,v in nb.items(): print("  %-12s %+.1f%% / %.2f%%"%(k,v["total"]*100,v["maxdd"]*100))
print("\n月度：")
for r in rows: print(f"  {r['month']} kc均{r['kc_w']:.1%} kc贡献{r['kc_contrib']*100:+.2f}pp 油{r['oil_w']:.0%} 总{r['tot']:.0%} 科创{r['kc_ret']:+.1%} 月内回撤{r['dd']:.2%} 过热{r['hot_days']}日")
