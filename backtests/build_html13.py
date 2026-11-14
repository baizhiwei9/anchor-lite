# -*- coding: utf-8 -*-
"""生成：股票腿失血归因报告"""
import os, sys, json, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine3 as E, dyn3 as D3

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "股票腿归因与多指数重建报告.html")
mon = pd.read_csv(os.path.join(BASE, "eq_monthly.csv"))
pool = pd.read_csv(os.path.join(BASE, "pool_coverage.csv"))
eq6 = json.load(open(os.path.join(BASE, "eq6_final.json")))

mon_rows = "".join(
    f"<tr><td>{r.mo[5:]}月</td><td class='num'>{r.eq*100:.1f}%</td><td class='num'>{r.kc*100:.1f}%</td>"
    f"<td class='num'>{r.nas*100:.1f}%</td><td class='num'>{r.div*100:.1f}%</td><td class='num'>{r.low*100:.1f}%</td>"
    f"<td class='num'>{r.cb_kc50:+.2f}</td><td class='num'>{r.cb_nas:+.2f}</td>"
    f"<td class='num'>{r.cb_div+r.cb_lowvol:+.2f}</td><td class='num'>{r.br_days}</td></tr>"
    for r in mon.itertuples())
tot_kc = mon.cb_kc50.sum(); tot_nas = mon.cb_nas.sum(); tot_def = (mon.cb_div + mon.cb_lowvol).sum()
pre = mon[mon.mo < "2026-07"]
pre_kc = pre.cb_kc50.sum(); pre_nas = pre.cb_nas.sum(); pre_def = (pre.cb_div + pre.cb_lowvol).sum()

pool_rows = "".join(
    f"<tr><td>{r.name}</td><td>{'在池' if r.in_pool=='是' else '未用'}</td>"
    f"<td class='num'>{r.r_jun:+.1f}%</td><td class='num'>{r.r_full:+.1f}%</td><td class='num'>{r.vol:.1f}%</td></tr>"
    for r in pool.itertuples())

BOUND = [(0.20, "+7.5%", "-3.62%", "+8.1%"), (0.25, "+9.0%", "-4.59%", "+10.0%"),
         (0.30, "+10.5%", "-5.55%", "+11.9%"), (0.35, "+12.0%", "-6.51%", "+13.8%"),
         (0.45, "+15.0%", "-8.41%", "+17.7%")]
bound_rows = "".join(f"<tr><td class='num'>{s:.0%}</td><td class='num'>{a}</td><td class='num'>{d}</td>"
                     f"<td class='num'>{p}</td><td>{'破 5% 线' if d[1:4] and float(d.strip('%')) < -5 else '达标'}</td></tr>"
                     for s, a, d, p in BOUND)

VAR = [("多指数 12 只 + 期权闸门 + 广度闸门（25% 股票腿）", "-0.6%", "-5.43%", "43"),
       ("多指数 12 只 + 仅期权闸门（25%）", "-0.1%", "-5.12%", "29"),
       ("多指数 12 只 + 闸门（45%）", "-0.0%", "-6.23%", "53"),
       ("多指数 12 只 + 动量选 3 + 闸门（45%）", "-3.0%", "-7.75%", "48"),
       ("科创板 + 纳指 固定 25%（趋势过滤）", "+9.0%", "-4.59%", "—"),
       ("科创板 + 纳指 固定 30%（趋势过滤）", "+10.5%", "-5.55%", "—"),
       ("当前组合（期权信号定仓 + 原油/债券）", "+17.54%", "-4.96%", "63")]
var_rows = "".join(f"<tr><td>{a}</td><td class='num'>{b}</td><td class='num'>{c}</td><td class='num'>{d}</td></tr>"
                   for a, b, c, d in VAR)

HTML = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>股票腿为什么没赚到：归因与多指数重建</title>
<style>
:root{{--ink:#12161C;--sub:#5A6472;--line:#E3E7EE;--bg:#F7F8FA;--accent:#1F3A5F}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font-family:"Noto Sans SC","PingFang SC",system-ui,sans-serif;font-size:14.5px;line-height:1.72}}
.wrap{{max-width:1080px;margin:0 auto;padding:34px 26px 60px}}
header{{border-bottom:3px solid var(--accent);padding-bottom:16px;margin-bottom:24px}}
.ttl{{font-size:24px;line-height:1.36;margin:0 0 10px;font-weight:700}}
.meta{{color:var(--sub);font-size:12.5px}}
h1.sec{{font-size:18px;margin:34px 0 12px;padding-left:11px;border-left:4px solid var(--accent)}}
h2.sub{{font-size:15px;margin:20px 0 8px;color:#1F3A5F}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin:10px 0;background:#fff}}
th,td{{border-bottom:1px solid var(--line);padding:7px 9px;text-align:left;vertical-align:top}}
th{{background:#F2F5F9;font-weight:600;font-size:12.5px;color:#2B3442}}
td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
.lead{{background:#fff;border-left:4px solid var(--accent);padding:14px 18px;margin:16px 0;border-radius:0 8px 8px 0}}
.note{{background:#FFFBEB;border:1px solid #FDE68A;border-radius:8px;padding:12px 15px;font-size:13px;color:#7C4A03;margin:12px 0}}
.warn{{background:#FEF2F2;border:1px solid #FECACA;border-radius:8px;padding:12px 15px;font-size:13px;color:#7F1D1D;margin:12px 0}}
ul,ol{{margin:8px 0 8px 20px;padding:0}} li{{margin:5px 0}}
.small{{font-size:12.5px;color:var(--sub)}}
.kpi{{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}}
.card{{flex:1 1 150px;background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px 14px}}
.card .v{{font-size:20px;font-weight:700;font-variant-numeric:tabular-nums}}
.card .l{{font-size:12px;color:var(--sub);margin-top:2px}}
footer{{margin-top:38px;border-top:1px solid var(--line);padding-top:16px;color:var(--sub);font-size:12.5px}}
</style></head><body><div class="wrap">
<header>
<div class="ttl">股票腿为什么没赚到<br><span style="font-size:16px;color:var(--sub)">归因拆解 · 资产池核对 · 多指数重建 · 2026-01-05 至 2026-11-06</span></div>
<div class="meta">203 个交易日｜32 只 ETF｜单边成本 5bp｜信号与调仓只用当日及以前信息</div>
</header>

<div class="lead">
<b>先纠正一个前提：股票不是没赚，是只赚到该赚的一半。</b>当前组合的股票腿（科创 50 + 纳指 + 红利 + 低波）全年贡献 <b>+{tot_kc+tot_nas+tot_def:+.2f}pp</b>，7 月前 <b>+{pre_kc+pre_nas+pre_def:+.2f}pp</b>。同样两条进攻腿若固定 45% 仓位（月度 MA40 趋势过滤），到 6 月底能赚 <b>+17.7%</b>——差距来自三处：只有两条进攻腿、防御腿在 7 月前是负贡献、仓位被压到 39.8%。
但真相比「没赚到」更难接受：<b>7 月之后 A 股全线下挫</b>（科创 50 −26%、创业板 −24%、半导体 −49%），把股票腿扩到 12 只反而更差（−0.1% / −5.12%）。5% 回撤约束下，股票腿 30% 就破线，25% 是硬上限，对应 7 月前只能赚 +10.0%——这才是「十几个点」与「5% 回撤」的真实边界。
</div>

<div class="kpi">
<div class="card"><div class="v">{tot_kc+tot_nas+tot_def:+.2f}pp</div><div class="l">股票腿全年贡献</div></div>
<div class="card"><div class="v">+{pre_kc+pre_nas+pre_def:.2f}pp</div><div class="l">其中 7 月前</div></div>
<div class="card"><div class="v">39.8%</div><div class="l">股票腿平均仓位</div></div>
<div class="card"><div class="v">51%</div><div class="l">熔断半仓天数占比</div></div>
</div>

<h1 class="sec">一、股票腿逐月拆解</h1>
<table>
<tr><th>月份</th><th>股票腿权重</th><th>科创50</th><th>纳指</th><th>红利</th><th>低波</th>
<th>科创贡献</th><th>纳指贡献</th><th>防守腿贡献</th><th>熔断天数</th></tr>
{mon_rows}
</table>
<p class="small">贡献单位为百分点（权重×当月涨跌）。防守腿 = 红利 + 低波。</p>
<p>全年拆完：科创 50 <b>{tot_kc:+.2f}pp</b>、纳指 <b>{tot_nas:+.2f}pp</b>、防守腿 <b>{tot_def:+.2f}pp</b>、原油 +22.50pp、债券 +0.55pp。7 月前防守腿就已经是 <b>{pre_def:+.2f}pp</b>（红利 7 月前 −8.3%、低波 −9.0%），等于一边做多科技一边做空风格。</p>

<h1 class="sec">二、资产池核对：不是只用沪深300，是比那更窄</h1>
<p>当前配置只有 6 类：科创 50、纳指、红利、低波、原油、十年国债。<b>沪深 300、中证 500/1000、创业板、半导体、标普、日经、恒生科技全部在数据面板里，但从未进入配置。</b></p>
<table>
<tr><th>标的</th><th>是否在池</th><th>7 月前涨幅</th><th>全年涨幅</th><th>年化波动</th></tr>
{pool_rows}
</table>
<div class="note">
<b>三个被漏掉的收益源：</b>半导体 ETF 7 月前 <b>+96.4%</b>（全年 −49.3%）、日经 <b>+37.4%</b>（全年 +9.9%）、创业板 <b>+33.0%</b>（全年 −24.4%）。日经是唯一「7 月前大涨、全年仍正」的漏项；半导体和创业板属于「7 月前暴涨、全年腰斩」，纳入池子后会在下半年反噬——这正是下面重建测试变差的原因。
</div>

<h1 class="sec">三、上限测算：股票仓位与 5% 回撤的数学边界</h1>
<p>把科创 50 与纳指等权、月度按 MA40 趋势过滤，只改股票腿仓位：</p>
<table>
<tr><th>股票腿仓位</th><th>全年收益</th><th>最大回撤</th><th>7 月前收益</th><th>5% 约束</th></tr>
{bound_rows}
</table>
<p><b>结论很清楚：</b>股票腿 20% 只赚 8.1%，25% 赚到 10.0%（回撤 −4.59%，刚好守住），30% 赚 11.9% 但回撤 −5.55% 破线，45% 能赚 17.7% 却要承受 −8.41%。你要的「十几个点」落在 30%–35% 区间，而那个区间的回撤已经越过 5%。真正的瓶颈是 3 月：科创 50 单月 −15.5%，30% 仓位就是 −4.7pp，一次就把全年预算用光。</p>

<h1 class="sec">四、重建测试：多指数 + 期权闸门</h1>
<p>把池子扩到 12 只（含创业板、中证 500/1000、沪深 300、纳指、日经、标普、恒生科技、半导体、新能源、军工），月度按「价格在 MA40 上方且 60 日动量为正」选样，日频用期权信号（科创 50 的隐含波动 z 与偏度 z）和广度（池内站上 MA20 的比例）做闸门：</p>
<table>
<tr><th>方案</th><th>全年收益</th><th>最大回撤</th><th>调仓笔数</th></tr>
{var_rows}
</table>
<p>扩池没有救回收益，反而把回撤推高。原因是选样规则在 2026 下半年持续把「刚暴跌、但仍在 MA40 上方或动量刚转正」的标的放进来：半导体 7 月前 +96.4%、全年 −49.3%，军工 −27.4%，恒生科技 −25.1%，纳指与日经的正收益被这些拖累抵消。期权闸门能把回撤从 −9.4% 压到 −5.1%，代价是 6 月就减仓——而 6 月科创 50 还在涨 27.1%。</p>

<h1 class="sec">五、纳指为什么也没贡献多少</h1>
<ul>
<li><b>权重偏小且不稳：</b>纳指腿权重 2 月被压到 3.5%，4–5 月涨 17.0% / 16.0% 时只有 10.2% / 15.9%，6 月又降到 9.0%。</li>
<li><b>纳指其实贡献不错：</b>全年 +{tot_nas:.2f}pp、7 月前 +{pre_nas:.2f}pp，权重均值只有 7.7%（7 月前 9.6%），单位仓位效率反而是全组合最高的。</li>
<li><b>纳指全年 +11.8%</b>，是池内除原油外唯一正收益的风险资产；这一点被「股票腿整体亏损」的印象掩盖了。</li>
</ul>

<h1 class="sec">六、诊断结论</h1>
<ol>
<li><b>股票腿赚了，但只赚到一半：</b>全年 +{tot_kc+tot_nas+tot_def:.2f}pp、7 月前 +{pre_kc+pre_nas+pre_def:.2f}pp；固定 45% 的科创+纳指同期 +17.7%，差距 9.0pp。</li>
<li><b>三个失血点：</b>①池子窄（只有 2 条进攻腿）；②防守腿反向（红利、低波 7 月前 −8~−9%，贡献 {pre_def:+.2f}pp）；③仓位被摊薄（股票腿均值 39.8%，熔断半仓占 51% 的天数）。</li>
<li><b>扩池不是解：</b>12 只多指数 + 闸门最好也只有 −0.1% / −5.12%，因为 2026 下半年 A 股全线下跌，池子越大越容易买到灾难。</li>
<li><b>约束是硬边界：</b>25% 股票腿（回撤 −4.59%）对应 7 月前 +10.0%；30% 就破 5%。想在 5% 内赚 15pp 以上，只能靠更准的 7 月退出时点，而现有期权信号在 6 月就报警，方向对、时点早了一个月。</li>
<li><b>下一步唯一有意义的动作：</b>把「7 月退出」的判据换成价格结构（跌破 MA20 且 5 日跌幅超 8%）而不是隐含波动水平，并把股票腿固定在 25%——这样既不牺牲 7 月前收益，也不会在 6 月误杀。</li>
</ol>

<h1 class="sec">七、数据与局限</h1>
<table>
<tr><th>层级</th><th>内容</th></tr>
<tr><td>真实数据</td><td>32 只 ETF 日线（2026-01-05 至 11-06，203 天）；科创 50 期权日统计与逐合约希腊字母；SC/IF 等期货主力持仓量</td></tr>
<tr><td>代理</td><td>期权闸门用科创 50 单一标的的隐含波动 z 与 25Δ 偏度，未覆盖创业板/沪深 300 的独立期权信号；广度用池内 MA20 比例近似</td></tr>
<tr><td>工程假设</td><td>月度选样「MA40 + 60 日动量」、25% 股票腿上限、熔断 3%、5bp 成本、等权分配</td></tr>
<tr><td>不可得</td><td>ETF 成分与财务数据；期权完整历史链（只有交易所日统计与风险指标）；分钟级资金流；个股层面持仓</td></tr>
</table>
<div class="warn">
三点提醒：①样本只有 203 个交易日、单一自然年，2026 下半年 A 股的单边下跌是结论的主要来源，换年份结果会变；②「固定 45% 赚 17.7%」是事后测算，不构成可执行策略；③参数与池子在同一样本内比较，存在选择偏差。本内容不构成投资建议。
</div>

<footer>
数据截止 2026-11-06 收盘；区间 2026-01-05 至 2026-11-06。<br>
本内容由人工智能基于公开数据回测生成，仅供研究与交流，不构成任何投资建议、收益承诺或交易依据。市场有风险，投资需谨慎。
</footer>
</div></body></html>"""
open(OUT, "w", encoding="utf-8").write(HTML)
print("写出", OUT, len(HTML), "字符")
