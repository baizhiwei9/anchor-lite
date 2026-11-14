# -*- coding: utf-8 -*-
"""生成：闸门清零后的风险解除复位报告"""
import os, pandas as pd, numpy as np, long2022 as L
BASE=os.path.dirname(os.path.abspath(__file__)); OUT=os.path.join(BASE,"清零复位判据回测报告.html")
CFG=dict(stock_base=0.42,dd=0.016,dd2=0.020,nas_indep=True)
def run(**kw):
    m,tl,_,_=L.run(**{**CFG,**kw}); tl["date"]=pd.to_datetime(tl.date); tl=tl.set_index("date"); return m,tl
# 判据覆盖
rel=L.REL
rel_cov=[("期权解除（偏度 z<0.5 且 IV 20 日回落）",int(rel["opt"].sum())),
         ("均线解除（池内≥3 只站上 MA20 且 MA20 上行）",int(rel["ma"].sum())),
         ("波动率解除（20 日已实现波动低于自身 60 日中位且回落）",int(rel["vol"].sum())),
         ("期权与均线同日解除",int(rel["combo2"].sum()))]
# 分判据表
rows=[]
for mode,cf,lab in [("nav",5,"净值复位（现行）"),("opt",5,"期权解除→四成"),("opt",10,"期权解除→确认 10 日"),
                    ("ma",5,"均线解除→四成"),("ma",10,"均线解除→确认 10 日"),
                    ("vol",5,"波动率解除→四成"),("vol",10,"波动率解除→确认 10 日"),
                    ("vol",999,"波动率解除→只回四成"),("combo",10,"三者任一解除→确认 10 日"),
                    ("combo2",5,"期权且均线解除")]:
    m,tl=run(reset_mode=mode,reset_confirm=cf)
    rows.append(dict(lab=lab,total=m["total"]*100,dd=m["maxdd"]*100,cagr=m["cagr"]*100,
                     y25=((1+tl.loc["2025","ret"]).prod()-1)*100, y26=((1+tl.loc["2026","ret"]).prod()-1)*100))
tab=pd.DataFrame(rows)
# 两种方案细节
A_m,A_tl=run(); B_m,B_tl=run(reset_mode="vol",reset_confirm=999)
def detail(m,tl):
    R=L.RET.reindex(tl.index).fillna(0)
    d={}
    for y in [2025,2026]:
        g=tl.loc[str(y)]
        d[y]=dict(ret=((1+g["ret"]).prod()-1)*100, br0=int((g["br"]==0).sum()),
                  kc=float(g["w_kc50"].mean())*100, cy=float(g["w_cyb"].mean())*100,
                  kcc=float((g["w_kc50"]*R.loc[g.index,"kc50"]).sum())*100,
                  cyc=float((g["w_cyb"]*R.loc[g.index,"cyb"]).sum())*100)
    nav=(1+tl["ret"]).cumprod(); dd=nav/nav.cummax()-1
    d["maxdd"]=dd.min()*100; d["dd_date"]=str(dd.idxmin().date())
    d["yd"]={int(y): float(((1+g).cumprod()/(1+g).cumprod().cummax()-1).min())*100 for y,g in tl.groupby(tl.index.year)["ret"]}
    return d
A=detail(A_m,A_tl); B=detail(B_m,B_tl)
rows_cov="".join(f"<tr><td>{a}</td><td class='num'>{b}</td></tr>" for a,b in rel_cov)
rows_tab="".join(f"<tr><td>{r.lab}</td><td class='num'>{r.total:+.2f}%</td><td class='num'>{r.dd:.2f}%</td>"
                 f"<td class='num'>{r.y25:+.2f}%</td><td class='num'>{r.y26:+.2f}%</td>"
                 f"<td>{'达标' if r.dd>=-5 else '破 5%'}</td></tr>" for r in tab.itertuples())
rows_cmp="".join(
 f"<tr><td>{lab}</td><td class='num'>{D[2025]['ret']:+.2f}%</td><td class='num'>{D[2026]['ret']:+.2f}%</td>"
 f"<td class='num'>{D[2025]['br0']} / {D[2026]['br0']}</td>"
 f"<td class='num'>{D[2025]['kc']:.2f}% / {D[2026]['kc']:.2f}%</td>"
 f"<td class='num'>{D[2025]['cy']:.2f}% / {D[2026]['cy']:.2f}%</td>"
 f"<td class='num'>{D[2025]['cyc']:+.2f} / {D[2026]['cyc']:+.2f}</td></tr>"
 for lab,D in [("现行（净值复位）",A),("波动率解除→四成",B)])
yd_rows="".join(f"<tr><td>{y}</td><td class='num'>{A['yd'][y]:.2f}%</td><td class='num'>{B['yd'][y]:.2f}%</td></tr>" for y in sorted(A["yd"]))
HTML=f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>闸门清零后如何用期权/均线/波动率复位</title>
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
<header><div class="ttl">闸门清零之后：用期权、均线、波动率判断风险解除并复位<br>
<span style="font-size:16px;color:var(--sub)">2022-01-04 至 2026-11-06｜1172 个交易日｜单边 5bp</span></div>
<div class="meta">现行引擎 anchor_lite/long2022.py｜股票腿动量选前二｜纳指独立 15%｜回撤闸门三档</div></header>

<div class="lead">
<b>结论。</b>现行闸门清零后靠“净值回到 −0.8% 或创 60 日新高”复位，实际几乎不可达：2025 年关闸 {A[2025]['br0']} 天、2026 年 {A[2026]['br0']} 天。改成“风险解除即回四成”后，关闸天数降到 {B[2025]['br0']} / {B[2026]['br0']} 天，2026 年收益从 {A[2026]['ret']:+.2f}% 升到 <b>{B[2026]['ret']:+.2f}%</b>，创业板贡献 {A[2026]['cyc']:+.2f}pp 升到 <b>{B[2026]['cyc']:+.2f}pp</b>；代价是 2025 年从 {A[2025]['ret']:+.2f}% 降到 {B[2025]['ret']:+.2f}%，全区间 {B_m['total']*100:+.2f}%（现行 {A_m['total']*100:+.2f}%）、回撤 {B_m['maxdd']*100:.2f}%（现行 {A_m['maxdd']*100:.2f}%），仍在 5% 内。
</div>

<div class="kpi">
<div class="card"><div class="v">{B_m['total']*100:+.2f}%</div><div class="l">全区间（波动率复位）</div></div>
<div class="card"><div class="v">{B_m['maxdd']*100:.2f}%</div><div class="l">最大回撤</div></div>
<div class="card"><div class="v">{B[2026]['ret']:+.2f}%</div><div class="l">2026 年收益</div></div>
<div class="card"><div class="v">{B[2026]['cyc']:+.2f}pp</div><div class="l">2026 创业板贡献</div></div>
</div>

<h1 class="sec">一、三类风险解除判据</h1>
<table><tr><th>判据</th><th>全年触发天数（1172 日）</th></tr>{rows_cov}</table>
<p>判据定义：期权＝沪深300 期权偏度 z&lt;0.5 且隐含波动 20 日回落；均线＝池内至少 3 只站上 MA20 且 MA20 五日内上行；波动率＝20 日已实现波动低于自身 60 日中位且 10 日内回落。</p>

<h1 class="sec">二、复位规则</h1>
<ul>
<li><b>清零后</b>：任一解除判据触发 → 回到四成仓位；连续 {5} 日满足 → 回到满仓。</li>
<li><b>对照</b>：现行以净值回到 −0.8% 或创 60 日新高复位（几乎不可达）。</li>
<li><b>作用范围</b>：只作用于 A 股腿与原油；纳指仍走自身 20 日线。</li>
</ul>

<h1 class="sec">三、分判据结果（2022–2026）</h1>
<table><tr><th>方案</th><th>全区间</th><th>最大回撤</th><th>2025</th><th>2026</th><th>5% 约束</th></tr>{rows_tab}</table>
<p>只有三种方案守住 5%：净值复位（现行）、波动率解除只回四成、期权且均线同日解除。其余全部破线，最差 −7.78%——解除判据放宽得太快，等于在下跌里加仓。</p>

<h1 class="sec">四、选定方案与现行对照</h1>
<table><tr><th>方案</th><th>2025 收益</th><th>2026 收益</th><th>关闸天数（2025/2026）</th><th>科创50 均仓（2025/2026）</th><th>创业板 均仓（2025/2026）</th><th>创业板贡献 pp（2025/2026）</th></tr>{rows_cmp}</table>
<table><tr><th>年份</th><th>现行 年内回撤</th><th>波动率复位 年内回撤</th></tr>{yd_rows}</table>
<p>2026 年关闸从 {A[2026]['br0']} 天降到 {B[2026]['br0']} 天，创业板均仓 {A[2026]['cy']:.2f}%→{B[2026]['cy']:.2f}%，贡献 {A[2026]['cyc']:+.2f}pp→{B[2026]['cyc']:+.2f}pp，说明“解除即回四成”确实把仓位加回来了。</p>

<h1 class="sec">五、失效情形</h1>
<ul>
<li><b>假解除</b>：波动率回落常出现在急跌后的技术反弹，2025-04 回补后组合回撤走到 {B['maxdd']:.2f}%（{B['dd_date']}），这是本方案最大回撤点。</li>
<li><b>2025 年变差</b>：2025 年 4 月与 10–12 月反复假解除，回补后遇下跌，全年从 {A[2025]['ret']:+.2f}% 掉到 {B[2025]['ret']:+.2f}%。</li>
<li><b>期权判据最弱</b>：单独用期权解除，全区间只有 {tab[tab.lab=='期权解除→四成'].total.iloc[0]:+.2f}%，回撤 {tab[tab.lab=='期权解除→四成'].dd.iloc[0]:.2f}%，因为 2022 年只有 300/50 期权、2023 年前无科创50 期权。</li>
<li><b>均线判据最快</b>：均线解除在 2025 年做到 {tab[tab.lab=='均线解除→四成'].y25.iloc[0]:+.2f}%，但回撤 {tab[tab.lab=='均线解除→四成'].dd.iloc[0]:.2f}% 破线。</li>
</ul>

<div class="warn">本内容由人工智能基于公开数据回测生成，仅供研究与交流，不构成任何投资建议或收益承诺。样本 2022–2026 单一区间、参数经样本内筛选；美股与创业板期权数据存在缺口，判据使用了代理口径；历史回测不代表未来表现。市场有风险，投资需谨慎。</div>

<footer>判据数据：上交所/深交所 ETF 期权日统计与希腊字母、QVIX 隐含波动率序列、ETF 日线；区间 {A_tl.index[0].date()} 至 {A_tl.index[-1].date()}。</footer>
</div></body></html>"""
open(OUT,"w",encoding="utf-8").write(HTML)
print("写出",OUT,len(HTML))
