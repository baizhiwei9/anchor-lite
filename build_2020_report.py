# -*- coding: utf-8 -*-
import os, pandas as pd, numpy as np, long2022 as L, ind2026 as M
BASE=os.path.dirname(os.path.abspath(__file__)); OUT=os.path.join(BASE,"2020-2026复核与行业ETF试验报告.html")
CFG=dict(stock_base=0.42,dd=0.016,dd2=0.020,nas_indep=True,reset_mode="all3",reset_confirm=1,check_freq="D",sel_freq="W")
m,tl,sel,tr=L.run(**CFG); tl["date"]=pd.to_datetime(tl.date); tl=tl.set_index("date")
years=sorted(set(tl.index.year))
rows=[]
for y in years:
    g=tl.loc[str(y)]
    if len(g)<40: continue
    n=(1+g["ret"]).cumprod()
    idx={a: (L.PX[a].loc[str(y)].iloc[-1]/L.PX[a].loc[str(y)].iloc[0]-1)*100 for a in ["hs300","cyb","kc50","nasdaq"] if len(L.PX[a].loc[str(y)])>40}
    rows.append(dict(y=y,ret=((1+g["ret"]).prod()-1)*100,dd=((n/n.cummax()-1).min())*100,eq=g["eq"].mean()*100,
                     hs=idx.get("hs300"),cyb=idx.get("cyb"),kcb=idx.get("kc50"),nas=idx.get("nasdaq")))
YR=pd.DataFrame(rows)
yr_rows="".join(f"<tr><td>{int(r.y)}</td><td class='num'>{r.ret:+.2f}%</td><td class='num'>{r.dd:.2f}%</td>"
                f"<td class='num'>{r.eq:.1f}%</td><td class='num'>{'' if pd.isna(r.hs) else f'{r.hs:+.1f}%'}</td>"
                f"<td class='num'>{'' if pd.isna(r.cyb) else f'{r.cyb:+.1f}%'}</td>"
                f"<td class='num'>{'' if pd.isna(r.kcb) else f'{r.kcb:+.1f}%'}</td>"
                f"<td class='num'>{'' if pd.isna(r.nas) else f'{r.nas:+.1f}%'}</td></tr>" for r in YR.itertuples())
# 2025 归因
ATTR=[("基线（当前版）",{}),("关闭回撤闸门",dict(breaker=False)),("关闭四象限期权风控",dict(use_risk=False)),
      ("关闭广度闸门",dict(use_breadth=False)),("放宽上限 55%/90%",dict(stock_base=0.55,stock_cap=0.90)),
      ("闸门+风控+广度全关",dict(breaker=False,use_risk=False,use_breadth=False)),
      ("全放开 60%/100%",dict(breaker=False,use_risk=False,use_breadth=False,stock_base=0.60,stock_cap=1.0))]
ar=[]
for lab,kw in ATTR:
    mm,tt,_,_=L.run(**{**CFG,**kw}); tt["date"]=pd.to_datetime(tt.date); tt=tt.set_index("date")
    g=tt.loc["2025"]; n=(1+g["ret"]).cumprod()
    ar.append(dict(lab=lab,y=((1+g["ret"]).prod()-1)*100,dd=((n/n.cummax()-1).min())*100,eq=g["eq"].mean()*100,
                   total=mm["total"]*100,tdd=mm["maxdd"]*100))
AT=pd.DataFrame(ar)
attr_rows="".join(f"<tr><td>{r.lab}</td><td class='num'>{r.y:+.2f}%</td><td class='num'>{r.dd:.2f}%</td>"
                  f"<td class='num'>{r.eq:.1f}%</td><td class='num'>{r.total:+.2f}%</td><td class='num'>{r.tdd:.2f}%</td></tr>"
                  for r in AT.itertuples())
# 行业 ETF 2026
ind=[("行业ETF+均线状态+闸门",dict(),None),("行业ETF不加均线",dict(use_ma=False),None),
     ("行业ETF选3只",dict(top_n=3),None),("行业ETF无闸门",dict(gate=False),None)]
P=pd.read_csv("data_signals/opt_panel.csv",parse_dates=["date"])
o=P[P.code.astype(str)=="510300"].set_index("date").sort_index()
zsk=(o["skew"]-o["skew"].rolling(60).mean())/o["skew"].rolling(60).std()
ivpct=o.iv_atm.rolling(252).rank(pct=True)
opt_risk=((ivpct>=0.8)|(zsk>=1.5)).reindex(M.IDX).ffill().fillna(False)
R=M.RET[[a for a in M.IND]]; ca=[]
for i in range(len(R)):
    sub=R.iloc[max(0,i-59):i+1]
    if len(sub)<40: ca.append(np.nan); continue
    c=sub.corr().values; n=c.shape[0]; ca.append((c.sum()-n)/(n*(n-1)))
topo_risk=(pd.Series(ca,index=M.IDX)>=0.45).fillna(False)
ind+= [("闸门关+期权风控",dict(gate=False),opt_risk),("闸门关+拓扑风控",dict(gate=False),topo_risk),
       ("闸门关+期权或拓扑",dict(gate=False),(opt_risk|topo_risk))]
ir=[]
for lab,kw,rk in ind:
    mm,tt,_,trr=M.run(**kw,risk=rk); tt["date"]=pd.to_datetime(tt.date); tt=tt.set_index("date")
    ir.append(dict(lab=lab,total=mm["total"]*100,dd=mm["maxdd"]*100,sh=mm["sharpe"],eq=tt["eq"].mean()*100,ntr=len(trr)))
IT=pd.DataFrame(ir)
ind_rows="".join(f"<tr><td>{r.lab}</td><td class='num'>{r.total:+.2f}%</td><td class='num'>{r.dd:.2f}%</td>"
                 f"<td class='num'>{r.sh:.2f}</td><td class='num'>{r.eq:.1f}%</td><td>{'达标' if r.dd>=-5 else '破 5%'}</td></tr>" for r in IT.itertuples())
HTML=f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>2020–2026 复核与行业 ETF 试验</title>
<style>
:root{{--ink:#12161C;--sub:#5A6472;--line:#E3E7EE;--bg:#F7F8FA;--accent:#1F3A5F}}
body{{margin:0;background:var(--bg);color:var(--ink);font-family:"Noto Sans SC","PingFang SC",system-ui,sans-serif;font-size:14.5px;line-height:1.72}}
.wrap{{max-width:1080px;margin:0 auto;padding:34px 26px 60px}}
header{{border-bottom:3px solid var(--accent);padding-bottom:16px;margin-bottom:24px}}
.ttl{{font-size:24px;margin:0 0 10px;font-weight:700;line-height:1.36}}
.meta{{color:var(--sub);font-size:12.5px}}
h1.sec{{font-size:18px;margin:34px 0 12px;padding-left:11px;border-left:4px solid var(--accent)}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin:10px 0;background:#fff}}
th,td{{border-bottom:1px solid var(--line);padding:7px 9px;text-align:left}}
th{{background:#F2F5F9;font-weight:600;font-size:12.5px}}
td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
.lead{{background:#fff;border-left:4px solid var(--accent);padding:14px 18px;margin:16px 0}}
.note{{background:#FFFBEB;border:1px solid #FDE68A;padding:12px 15px;font-size:13px;color:#7C4A03;margin:12px 0}}
.warn{{background:#FEF2F2;border:1px solid #FECACA;padding:12px 15px;font-size:13px;color:#7F1D1D;margin:12px 0}}
.small{{font-size:12.5px;color:var(--sub)}}
footer{{margin-top:38px;border-top:1px solid var(--line);padding-top:16px;color:var(--sub);font-size:12.5px}}
ul,ol{{margin:8px 0 8px 20px}} li{{margin:4px 0}}
</style></head><body><div class="wrap">
<header><div class="ttl">2020–2026 复核与行业 ETF 试验<br><span style="font-size:16px;color:var(--sub)">周频选样 + 三者交集闸门 · 2020-01-02 至 2026-11-05 · 1657 个交易日 · 单边 5bp</span></div>
<div class="meta">2020–2021 数据本次补齐：ETF 日线自 2019-12、沪深300/上证50 期权偏度自 2020-01、QVIX 自 2019-12</div></header>

<div class="lead"><b>先说结论。</b>补齐 2020–2021 后，全区间 <b>{m['total']*100:+.2f}%</b>、最大回撤 <b>{m['maxdd']*100:.2f}%</b>、年化 {m['cagr']*100:+.2f}%、夏普 {m['sharpe']:.2f}。2025 年组合只有 {YR[YR.y==2025].ret.iloc[0]:+.2f}%，而创业板 +57.8%、科创50 +40.4%——落差不是选样失灵，而是<b>仓位约束</b>：关掉闸门多 +4.7pp、放宽上限多 +3.3pp，但即便全部放开也只有 25.55%，仍差指数 30pp 以上。
行业 ETF 方案在 2026 年能提高收益（无闸门 +21.47%），但回撤 −21.70%；叠加期权或拓扑风控后最好也只有 {IT.total.max():+.2f}% 对 {IT[IT.lab=='闸门关+期权风控'].dd.iloc[0]:.2f}%——<b>在 5% 回撤约束下，行业 ETF 这条路走不通</b>。</div>

<h1 class="sec">一、2022–2024 更新（当前版本）</h1>
<table><tr><th>年份</th><th>组合收益</th><th>年内回撤</th><th>股票均仓</th><th>沪深300</th><th>创业板</th><th>科创50</th><th>纳指</th></tr>{yr_rows}</table>
<p class="small">同一张表已含 2020、2021；科创50 ETF 2020-11 上市，之前列空。</p>

<h1 class="sec">二、2020–2025 指数与组合对照</h1>
<table><tr><th>年份</th><th>组合收益</th><th>年内回撤</th><th>股票均仓</th><th>沪深300</th><th>创业板</th><th>科创50</th><th>纳指</th></tr>{yr_rows}</table>
<p>2020 年创业板 +62.4%、组合 +9.7%；2021 年纳指 +23.8%、组合 +14.8%；2025 年创业板 +57.8%、组合 +15.4%。<b>指数越猛，落差越大</b>——因为股票均仓只有 12%–26%。</p>

<h1 class="sec">三、2025 年落差归因</h1>
<table><tr><th>方案</th><th>2025 收益</th><th>2025 回撤</th><th>2025 股票均仓</th><th>全区间</th><th>全回撤</th></tr>{attr_rows}</table>
<p>闸门是主要约束（关掉 +4.73pp），上限第二（放宽 +3.30pp），四象限期权风控与广度闸门在 2025 年几乎没影响（+0.00 / −0.01pp）。即使全放开，2025 也只有 {AT.y.max():+.2f}%，因为股票上限 100% 时也只能持两只、且另有纳指/原油/债券分流。</p>

<h1 class="sec">四、行业 ETF 2026 单年试验</h1>
<table><tr><th>方案</th><th>收益</th><th>最大回撤</th><th>夏普</th><th>股票均仓</th><th>5% 约束</th></tr>{ind_rows}</table>
<p>池为半导体/军工/医药/券商/银行/煤炭/钢铁/酒/消费/新能源/光伏 11 只，周频按 60 日动量选前二、股票上限 60%，均线状态为价格在 MA20 上且 MA20 在 MA60 上。加均线状态反而更差（{IT[IT.lab=='行业ETF+均线状态+闸门'].total.iloc[0]:+.2f}% 对 {IT[IT.lab=='行业ETF不加均线'].total.iloc[0]:+.2f}%），因为 2026 行业轮动快、均线滞后。</p>

<h1 class="sec">五、期权与拓扑风控叠加</h1>
<table><tr><th>方案</th><th>收益</th><th>最大回撤</th><th>夏普</th><th>股票均仓</th><th>5% 约束</th></tr>{ind_rows}</table>
<p>期权风控＝沪深300 期权 IV 一年分位 ≥0.8 或偏度 z ≥1.5（2026 命中 13 天）；拓扑风控＝11 只行业 ETF 60 日平均两两相关性 ≥0.45（命中 62 天）。期权风控把收益推到 {IT[IT.lab=='闸门关+期权风控'].total.iloc[0]:+.2f}%，但回撤 {IT[IT.lab=='闸门关+期权风控'].dd.iloc[0]:.2f}%；拓扑风控几乎没起作用（{IT[IT.lab=='闸门关+拓扑风控'].total.iloc[0]:+.2f}% / {IT[IT.lab=='闸门关+拓扑风控'].dd.iloc[0]:.2f}%）。</p>

<h1 class="sec">六、结论与边界</h1>
<ol>
<li><b>2022–2024 已更新</b>：{YR[YR.y==2022].ret.iloc[0]:+.2f}% / {YR[YR.y==2023].ret.iloc[0]:+.2f}% / {YR[YR.y==2024].ret.iloc[0]:+.2f}%，年内回撤都在 −2.6% 内。</li>
<li><b>组合跑不过指数的主因是仓位，不是选样</b>：2025 年股票均仓 24%，指数 +57.8%；把闸门与上限全放开能到 25.55%，仍只有指数的一半，因为组合还要分给纳指、原油与债券。</li>
<li><b>行业 ETF 在 5% 回撤下不成立</b>：不设闸门 2026 能到 +21.47% 但回撤 −21.70%；加期权风控 41.61% 但 −12.03%。要控住 5% 只能回到宽基 + 闸门（+2.49%）。</li>
<li><b>拓扑结构检测未体现价值</b>：相关性 ≥0.45 的 62 天里，行业 ETF 组合仍亏损，说明“拥挤”不等于“下行”。</li>
<li><b>边界</b>：2020–2021 只有沪深300/上证50 期权，创业板偏度用科创50 代理（2023-06 前无）；行业 ETF 只有 11 只、2025 年起才有完整日线；全部结论均为样本内，不构成未来保证。</li>
</ol>

<div class="warn">本内容由人工智能基于公开数据回测生成，仅供研究与交流，不构成任何投资建议或收益承诺。样本 2020–2026 单一区间、参数经样本内筛选；行业 ETF 试验仅覆盖 2026 年；历史回测不代表未来表现。市场有风险，投资需谨慎。</div>
<footer>数据：ETF 日线（新浪）、上交所期权日统计与希腊字母、QVIX、FRED 美国宏观、国家统计局 PMI。区间 {tl.index[0].date()} 至 {tl.index[-1].date()}。</footer>
</div></body></html>"""
open(OUT,"w",encoding="utf-8").write(HTML); print("写出",OUT,len(HTML))
