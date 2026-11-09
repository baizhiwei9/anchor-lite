import os,numpy as np,pandas as pd,dyn3 as D
BASE=os.path.dirname(os.path.abspath(__file__))
P=pd.read_csv(os.path.join(BASE,"data_signals/opt_panel.csv"),parse_dates=["date"])
panel=D.panel
def sigs(code):
    x=P[P.code.astype(str)==str(code)].set_index("date").sort_index()
    s=pd.DataFrame(index=x.index)
    s["pcr_vol"]=x.pcr_vol; s["pcr_oi"]=x.pcr_oi; s["skew"]=x["skew"]; s["iv_atm"]=x.iv_atm; s["oi_net"]=x.oi_net
    s["d_pcr5"]=x.pcr_vol.diff(5); s["d_skew5"]=x["skew"].diff(5); s["d_iv5"]=x.iv_atm.diff(5)
    s["z_pcr"]=(x.pcr_vol-x.pcr_vol.rolling(60).mean())/x.pcr_vol.rolling(60).std()
    s["z_skew"]=(x["skew"]-x["skew"].rolling(60).mean())/x["skew"].rolling(60).std()
    s["z_iv"]=(x.iv_atm-x.iv_atm.rolling(60).mean())/x.iv_atm.rolling(60).std()
    s["rv20"]=panel[code2name(code)].pct_change().rolling(20).std()*np.sqrt(252)
    s["vrp"]=x.iv_atm-s.rv20
    return s
M={"588000":"kc50","510300":"hs300","510500":"zz500"}
def code2name(c): return M.get(c,c)
for code,nm in M.items():
    s=sigs(code); px=panel[nm]
    fwd5=px.shift(-5)/px-1; fwd20=px.shift(-20)/px-1
    s=s.loc["2026-01-05":]; fwd5=fwd5.loc[s.index]; fwd20=fwd20.loc[s.index]
    print("\n===",nm,code,"n=",len(s))
    for c in ["pcr_vol","pcr_oi","skew","iv_atm","oi_net","d_pcr5","d_skew5","d_iv5","z_pcr","z_skew","z_iv","vrp"]:
        v=s[c]
        if v.notna().sum()<30: continue
        ic5=v.corr(fwd5,method="spearman"); ic20=v.corr(fwd20,method="spearman")
        print(f"  {c:9s} IC5 {ic5:+.3f}  IC20 {ic20:+.3f}")
    if nm=="kc50":
        mm=s.copy(); mm["kc_r"]=px.pct_change().loc[s.index]
        for mo,g in mm.groupby(mm.index.to_period("M")):
            print(f"   {mo} pcr {g.pcr_vol.mean():.2f} skew {g['skew'].mean():+.3f} iv {g.iv_atm.mean():.3f} vrp {g.vrp.mean():+.3f} kc月收益 {float((1+g.kc_r).prod()-1):+.1%}")
