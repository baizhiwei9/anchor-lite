# -*- coding: utf-8 -*-
"""XGBoost walk-forward 行业打分：t-1 特征 → 预测 t 日收益，仅用 s+1<=t-1 的样本训练"""
import os, numpy as np, pandas as pd, xgboost as xgb, factor_table as FT, mf_backtest as MF
IND=FT.IND; RET=MF.RET
def panel(win=None):
    F,S=FT.build(**(win or {}))
    F={k:v.shift(1) for k,v in F.items()}   # 关键：特征同样只用 t-1 及以前
    feats=list(F.keys())
    rows=[]; ys=[]
    for d in S.index:
        if d not in RET.index: continue
        nxt=RET.loc[d,IND].values            # t 日收益（t-1 特征预测 t）
        for j,n in enumerate(IND):
            v=[F[k].loc[d,n] for k in feats]
            if any(pd.isna(x) for x in v): continue
            rows.append(dict(date=d,ind=n,**{k:float(F[k].loc[d,n]) for k in feats})); ys.append(float(nxt[j]))
    X=pd.DataFrame(rows); X["y"]=ys
    return feats,X
def run(year=2026,top=1,cap=0.30,cost=5.0,refit=5,min_train=60,nest=120,depth=3,feats=None,win=None):
    feats,X=panel(win)
    feats=feats or [c for c in X.columns if c not in ("date","ind","y")]
    days=[d for d in MF.IDX if d.year==year]
    w=np.zeros(len(IND)+1); w[-1]=1.0; trades=0; rec=[]; model=None; last_fit=None; imp=None
    for dt in days:
        if dt not in set(X["date"]): continue
        tr=X[X["date"]<=dt-pd.Timedelta(days=2)]   # 只用结果已实现的样本
        if len(tr)<min_train*len(IND): continue
        if model is None or last_fit is None or (dt-last_fit).days>=refit:
            dtr=xgb.DMatrix(tr[feats].values,label=tr["y"].values,feature_names=feats)
            model=xgb.train({"objective":"reg:squarederror","max_depth":depth,"eta":0.05,
                             "subsample":0.8,"colsample_bytree":0.9,"lambda":1.0,"nthread":4,"seed":0},
                            dtr,num_boost_round=nest); last_fit=dt
            sc=model.get_score(importance_type="gain"); imp=pd.Series({f:sc.get(f,0.0) for f in feats})
        cur=X[X["date"]==dt]; pred=model.predict(xgb.DMatrix(cur[feats].values,feature_names=feats))
        s=pd.Series(pred,index=cur["ind"].values).sort_values(ascending=False)
        pick=list(s.index[:top]); tw=np.array([cap/len(pick) if p in pick else 0.0 for p in IND])
        tgt=np.append(tw,1-tw.sum()); cc=np.append(w[:-1],w[-1]); d=tgt-cc
        if np.abs(d).sum()>1e-9:
            trades+=int((np.abs(d)>1e-12).sum()/2+0.5); cc=cc+d; cc=cc-np.abs(d)*cost/1e4
        w=cc
        r=float((w[:-1]*RET.loc[dt,IND].values).sum()+w[-1]*RET.loc[dt,"treasury10_etf"])
        rec.append(dict(date=dt,ret=r,eq=float(w[:-1].sum()),top=pick[0]))
        w=w*(1+RET.loc[dt].values); w=w/w.sum()
    df=pd.DataFrame(rec).set_index("date"); nv=(1+df["ret"]).cumprod()
    return (dict(ret=(nv.iloc[-1]-1)*100,dd=(nv/nv.cummax()-1).min()*100,
                 sharpe=df["ret"].mean()/(df["ret"].std()+1e-12)*np.sqrt(252),
                 eq=df["eq"].mean()*100,trades=trades),df,imp)
if __name__=="__main__":
    print("== XGBoost walk-forward 打分（2026，A股≤30%，单边5bp）")
    for top in (1,3):
        m,df,imp=run(2026,top=top)
        print(f"  前{top}名 收益 {m['ret']:+6.2f}% 回撤 {m['dd']:6.2f}% 夏普 {m['sharpe']:5.2f} 股仓 {m['eq']:.1f}% 调仓 {m['trades']}")
    print("\n特征重要性:"); print(imp.round(3).to_string())
    print("\n各季度选中行业:", df.groupby(df.index.to_period("Q"))["top"].agg(lambda x:x.value_counts().idxmax()).to_dict())
