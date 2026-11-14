# -*- coding: utf-8 -*-
import os, pandas as pd, numpy as np, long2022 as L
BASE=os.path.dirname(os.path.abspath(__file__)); OUT=os.path.join(BASE,"三者交集开闸回测报告.html")
CFG=dict(stock_base=0.42,dd=0.016,dd2=0.020,nas_indep=True)
cmp=pd.read_csv(os.path.join(BASE,"all3_compare.csv"))
row="".join(f"<tr><td>{r['方案']}</td><td class='num'>{r['total']:+.2f}%</td><td class='num'>{r['dd']:.2f}%</td>"
            f"<td class='num'>{r['y22']:+.2f}%</td><td class='num'>{r['y25']:+.2f}%</td><td class='num'>{r['y26']:+.2f}%</td>"
            f"<td class='num'>{r['br0']}</td><td class='num'>{r['eq']:.1f}%</td></tr>" for _,r in cmp.iterrows())
rel=L.REL
cov=[("期权安全（偏度 z<0.5 且 IV 20 日回落）",int(rel["opt"].sum())),
     ("均线安全（≥3 只站上 MA20 且 MA20 上行）",int(rel["ma"].sum())),
     ("波动率安全（20 日已实现波动<60 日中位且回落）",int(rel["vol"].sum())),
     ("三者同时安全（本版采用）",int(rel["all3"].sum()))]
cov_rows="".join(f"<tr><td>{a}</td><td class='num'>{b}</td></tr>" for a,b in cov)
# 两种配置细节
def detail(**kw):
    m,tl,_,_=L.run(**{**CFG,**kw}); tl["date"]=pd.to_datetime(tl.date); tl=tl.set_index("date")
    R=L.RET.reindex(tl.index).fillna(0); d={"total":m["total"]*100,"dd":m["maxdd"]*100,"sharpe":float(m["sharpe"])}
    for y in [2025,2026]:
        g=tl.loc[str(y)]
        d[y]=dict(ret=((1+g["ret"]).prod()-1)*100,br0=int((g["br"]==0).sum()),eq=float(g["eq"].mean())*100,
                  kc=float(g["w_kc50"].mean())*100,cy=float(g["w_cyb"].mean())*100,
                  kcc=float((g["w_kc50"]*R.loc[g.index,"kc50"]).sum())*100,
                  cyc=float((g["w_cyb"]*R.loc[g.index,"cyb"]).sum())*100)
    z=(tl["br"]==0).astype(int); grp=(z.diff()!=0).cumsum(); lg=z.groupby(grp).agg(["sum","size"])
    segs=[]
    for k in lg[lg["sum"]>0].sort_values("size",ascending=False).head(3).index:
        s=tl.loc[grp==k]; segs.append((str(s.index[0].date()),str(s.index[-1].date()),len(s),float(s["eq"].mean())*100))
    d["segs"]=segs
    sig=L.REL["all3"].reindex(tl.index).fillna(False)
    reopen=tl[(tl["br"]>0)&(tl["br"].shift(1).fillna(1)==0)]
    d["reopen"]=len(reopen); d["reopen_ok"]=all(bool(sig.get(x,False)) for x in reopen.index)
    return d
A=detail(); B=detail(reset_mode="all3",reset_confirm=1)
cmp_rows="".join(
 f"<tr><td>{lab}</td><td class='num'>{D[2025]['ret']:+.2f}% / {D[2026]['ret']:+.2f}%</td>"
 f"<td class='num'>{D[2025]['br0']} / {D[2026]['br0']}</td>"
 f"<td class='num'>{D[2025]['eq']:.1f}% / {D[2026]['eq']:.1f}%</td>"
 f"<td class='num'>{D[2025]['kc']:.2f}% / {D[2026]['kc']:.2f}%</td>"
 f"<td class='num'>{D[2025]['cy']:.2f}% / {D[2026]['cy']:.2f}%</td></tr>" for lab,D in [("现行（净值复位）",A),("三者同时安全（确认 1 日）",B)])
seg_rows="".join(f"<tr><td>{a} ~ {b}</td><td class='num'>{c}</td><td class='num'>{d:.1f}%</td></tr>" for a,b,c,d in B["segs"])
HTML=f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>三者同时安全才开闸：期权+均线+波动率交集</title>
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
.kpi{{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}}
.card{{flex:1 1 150px;background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px 14px}}
.card .v{{font-size:20px;font-weight:700}}
.card .l{{font-size:12px;color:var(--sub)}}
footer{{margin-top:38px;border-top:1px solid var(--line);padding-top:16px;color:var(--sub);font-size:12.5px}}
ul,ol{{margin:8px 0 8px 20px}} li{{margin:4px 0}}
</style></head><body><div class="wrap">
<header><div class="ttl">三者同时安全才开闸<br><span style="font-size:16px;color:var(--sub)">期权偏度 + 均线 + 波动率取交集 · 2022-01-04 至 2026-11-06 · 1172 个交易日 · 单边 5bp</span></div>
<div class="meta">引擎 anchor_lite/long2022.py｜闸门只负责关，开闸必须三者同时安全｜年度重置不再绕过</div></header>

<div class="lead"><b>结论。</b>按你的要求做成“三者同时安全才开闸”后：全区间 <b>{B['total']:+.2f}%</b>（现行 {A['total']:+.2f}%）、最大回撤 <b>{B['dd']:.2f}%</b>（现行 {A['dd']:.2f}%）、夏普 {B['sharpe']:.2f}。回撤更小，但<b>关闸天数反而更多</b>：2025 年 {A[2025]['br0']}→{B[2025]['br0']} 天、2026 年 {A[2026]['br0']}→{B[2026]['br0']} 天，2026 年股票均仓从 {A[2026]['eq']:.1f}% 降到 {B[2026]['eq']:.1f}%。三者同时安全全区间只有 {int(rel['all3'].sum())} 天（5.2%），2026 年只有 2 天——交集太严，闸门几乎打不开。</div>

<div class="kpi">
<div class="card"><div class="v">{B['total']:+.2f}%</div><div class="l">全区间收益</div></div>
<div class="card"><div class="v">{B['dd']:.2f}%</div><div class="l">最大回撤</div></div>
<div class="card"><div class="v">2025 {B[2025]['ret']:+.2f}%</div><div class="l">2025 年（现行 {A[2025]['ret']:+.2f}%）</div></div>
<div class="card"><div class="v">2026 {B[2026]['ret']:+.2f}%</div><div class="l">2026 年（现行 {A[2026]['ret']:+.2f}%）</div></div>
</div>

<h1 class="sec">一、三类“安全”判据与交集</h1>
<table><tr><th>判据</th><th>触发天数（1172 日）</th></tr>{cov_rows}</table>
<p>定义：期权＝沪深300 期权偏度 z&lt;0.5 且隐含波动 20 日回落；均线＝池内≥3 只站上 MA20 且 MA20 五日内上行；波动率＝20 日已实现波动低于自身 60 日中位且 10 日内回落。三者交集 61 天、14 段、最长连续 22 天。</p>

<h1 class="sec">二、开闸规则</h1>
<ul>
<li>闸门只负责关：回撤 −1.6% 减到四成、−1.8% 清零 A 股腿与原油。</li>
<li><b>开闸必须三者同时安全</b>：触发当日回到四成，连续确认日数满则回满仓。</li>
<li>年度重置<strong>不再</strong>无条件开闸（已验证：{B['reopen']} 次开闸全部落在信号日，判定={B['reopen_ok']}）。</li>
</ul>

<h1 class="sec">三、回测对照</h1>
<table><tr><th>方案</th><th>全区间</th><th>最大回撤</th><th>2022</th><th>2025</th><th>2026</th><th>关闸天数</th><th>股票均仓</th></tr>{row}</table>
<p>交集版（确认 1 日）−4.05% 的回撤是这批里最低的，但收益 {B['total']:+.2f}% 低于现行 {A['total']:+.2f}%。单判据版本全部破 5%：仅期权 −7.70%、仅均线 −9.25%、仅波动率 −7.08%。</p>

<h1 class="sec">四、仓位回补核对</h1>
<table><tr><th>方案</th><th>2025 / 2026 收益</th><th>关闸天数（2025/2026）</th><th>股票均仓（2025/2026）</th><th>科创50 均仓</th><th>创业板 均仓</th></tr>{cmp_rows}</table>
<p>2025 年创业板均仓从 {A[2025]['cy']:.2f}% 升到 {B[2025]['cy']:.2f}%、收益从 {A[2025]['ret']:+.2f}% 升到 {B[2025]['ret']:+.2f}%；但科创50 从 {A[2025]['kc']:.2f}% 掉到 {B[2025]['kc']:.2f}%，2026 年整体仓位也更低。交集并没有把仓位“加回来”，只是换了一种少亏的方式。</p>

<h1 class="sec">五、交集过严导致的长期不开闸</h1>
<table><tr><th>区段</th><th>交易日</th><th>段内股票均仓</th></tr>{seg_rows}</table>
<p>最长一段 201 个交易日（2024-08 至 2025-06）闸门全关，段内股票均仓只有 12.6%；2026-06-10 至 11-05 又连续关了 100 天。这说明“三者同时安全”在 A 股很难满足——偏度安全与均线安全往往不同时出现。</p>

<h1 class="sec">六、结论与可选调整</h1>
<ol>
<li>你的规则已经如实实现：只有三者同时安全才开闸，回撤从 −4.44% 降到 −4.05%，代价是收益少 2.0pp、2026 年仓位更低。</li>
<li>想要“既安全又能加回仓位”，只有两条路：把三者从“同时”放宽为“两两同时”（期权+均线或期权+波动率），或把安全阈值放宽（偏度 z&lt;0.8、波动率低于 70% 分位）。</li>
<li>若坚持严格同时，就必须接受闸门常年关闭——这是 A 股风险结构的客观结果，不是参数没调好。</li>
</ol>

<div class="warn">本内容由人工智能基于公开数据回测生成，仅供研究与交流，不构成任何投资建议或收益承诺。样本 2022–2026 单一区间、参数经样本内筛选；创业板偏度为科创50 期权代理，2022 年仅有沪深300/上证50 期权；历史回测不代表未来表现。市场有风险，投资需谨慎。</div>
<footer>数据：ETF 日线（新浪）、上交所/深交所 ETF 期权日统计与希腊字母、QVIX 序列；区间 2022-01-04 至 2026-11-06。</footer>
</div></body></html>"""
open(OUT,"w",encoding="utf-8").write(HTML)
print("写出",OUT,len(HTML))
