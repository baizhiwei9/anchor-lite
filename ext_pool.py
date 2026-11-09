# -*- coding: utf-8 -*-
"""扩展池（12 行业 + 红利 510880 + 科创50 588000）：验证「减成长→加红利」能否实现"""
import os, pandas as pd, numpy as np, factor_table as FT, gradual as G
BASE=os.path.dirname(os.path.abspath(__file__)); D=os.path.join(BASE,"data2026")
EXT=FT.IND+["dividend","kc50"]
def load(n,col="close"):
    return pd.read_csv(os.path.join(D,f"{n}_full.csv"),parse_dates=["date"]).drop_duplicates("date").set_index("date")[col].astype(float)
IDX=FT.IDX
CLOSE=pd.DataFrame({n:load(n) for n in EXT}).reindex(IDX).ffill()
AMT=pd.DataFrame({n:load(n,"amount") for n in EXT}).reindex(IDX).ffill()
def apply_ext():
    FT.IND=EXT; FT.CLOSE=CLOSE; FT.AMT=AMT; FT.RET=CLOSE.pct_change().fillna(0.0)
    G.IND=EXT; G.RET=CLOSE.pct_change().fillna(0.0)
    return EXT
if __name__=="__main__":
    apply_ext()
    m,df=G.run(2026,topk=3,band=0.01,step_up=0.01,step_dn=0.01)
    print(f"扩展池 渐进Top3: 收益 {m['ret']:+.2f}% 回撤 {m['dd']:.2f}% 夏普 {m['sharpe']:.2f} 股仓 {m['eq']:.1f}% 换手 {m['turnover']:.0f}%")
    seg=df.loc["2026-06-24":"2026-07-15"]
    print("\n日期       半导体实际  红利实际  科创实际  总股仓  收益")
    for d,r in seg.iterrows():
        w=r["w"]
        print(f"{d.date()}  {w.get('semiconductor',0)*100:5.1f}%  {w.get('dividend',0)*100:5.1f}%  {w.get('kc50',0)*100:5.1f}%  {r['eq']*100:5.1f}%  {r['ret']*100:+.2f}%")
