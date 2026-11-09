# -*- coding: utf-8 -*-
"""生成：宽基 + 期权风险 + 均线清仓 回测报告"""
import os, sys, json, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine3 as E, eq7

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "宽基期权风控回测报告.html")
fin = json.load(open(os.path.join(BASE, "eq7_final.json")))
dec = json.load(open(os.path.join(BASE, "eq7_decomp.json")))
mon = pd.read_csv(os.path.join(BASE, "eq7_monthly.csv"))

def segs(a):
    s = eq7.RS[a]; out = []; st = None; prev = None
    for d in s.index:
        if s.loc[d] and st is None: st = d
        if not s.loc[d] and st is not None: out.append((st, prev)); st = None
        prev = d
    if st is not None: out.append((st, prev))
    return out

risk_rows = ""
for a, lab in [("kc50", "科创50（588000 期权）"), ("hs300", "沪深300（510300 期权）"), ("zz500", "中证500（510500 期权）")]:
    ss = segs(a)
    txt = "；".join(f"{str(x)[5:10]}~{str(y)[5:10]}" for x, y in ss[:4])
    risk_rows += f"<tr><td>{lab}</td><td class='num'>{len(ss)}</td><td class='num'>{int(eq7.RS[a].sum())}</td><td>{txt}</td></tr>"

mon_rows = "".join(
    f"<tr><td>{r.mo}月</td><td class='num'>{r.eq*100:.1f}%</td><td class='num'>{r.nas*100:.1f}%</td>"
    f"<td class='num'>{r.a_share*100:.1f}%</td><td class='num'>{r.ret*100:+.1f}%</td>"
    f"<td class='num'>{int(r.risk)}</td><td class='num'>{r.c_nas*100:+.2f}</td><td class='num'>{r.c_a*100:+.2f}</td></tr>"
    for r in mon.itertuples())

DEC = [("无风控（固定权重）", dec["无风控(固定权重)"]),
       ("仅期权风险 ×0.5（不加均线）", dec["仅期权×0.5(无均线)"]),
       ("仅均线死叉（无期权则不触发）", dec["仅均线死叉(严格,无期权不触发)"]),
       ("风险中死叉清仓（你的规则）", dec["风险中死叉清仓(你的规则)"]),
       ("死叉即清仓 + 风险期禁止恢复", dec["死叉即清仓(放宽,风险阻止恢复)"])]
dec_rows = "".join(f"<tr><td>{a}</td><td class='num'>{m['total']*100:+.2f}%</td><td class='num'>{m['maxdd']*100:.2f}%</td>"
                   f"<td class='num'>{m['vol']*100:.1f}%</td><td class='num'>{m['sharpe']:.2f}</td><td class='num'>{m['n_trades']}</td></tr>"
                   for a, m in DEC)

NAS = [("15%", "+14.98%", "-4.25%", "2.12"), ("20%", "+16.47%", "-4.50%", "2.26"),
       ("25%（主配置）", "+16.71%", "-4.96%", "2.22"), ("30%", "+17.22%", "-5.38%", "2.18"),
       ("35%", "+12.75%", "-5.79%", "1.53")]
nas_rows = "".join(f"<tr><td>{a}</td><td class='num'>{b}</td><td class='num'>{c}</td><td class='num'>{d}</td>"
                   f"<td>{'破 5% 线' if float(c.strip('%')) < -5 else '达标'}</td></tr>" for a, b, c, d in NAS)

ROB = [("主配置", "+16.70%", "-4.96%"), ("成本 10bp", "+16.07%", "-4.99%"), ("熔断 2%", "+15.98%", "-4.66%"),
       ("熔断 3%", "+16.12%", "-4.87%"), ("半仓 0.3", "+16.15%", "-4.87%"), ("半仓 0.7", "+17.02%", "-5.05%"),
       ("带宽 2pp", "+10.51%", "-4.72%"), ("带宽 4pp", "+16.53%", "-4.90%"), ("冷却 0 日", "+16.32%", "-4.96%"),
       ("冷却 8 日", "+15.84%", "-4.96%")]
rob_rows = "".join(f"<tr><td>{a}</td><td class='num'>{b}</td><td class='num'>{c}</td></tr>" for a, b, c in ROB)

CMP = [("宽基池 + 期权风险 + 死叉清仓（本版）", "+16.70%", "-4.96%", "2.22"),
       ("宽基池放宽版（死叉即清、风险禁恢复）", "+19.35%", "-4.30%", "2.51"),
       ("上一版：期权信号定仓（科创+纳指）", "+17.54%", "-4.96%", "2.13"),
       ("更早：三条动态规则", "+14.24%", "-3.78%", "1.96"),
       ("固定配比 55/15/15/10/5", "+17.50%", "-4.06%", "2.30")]
cmp_rows = "".join(f"<tr><td>{a}</td><td class='num'>{b}</td><td class='num'>{c}</td><td class='num'>{d}</td></tr>"
                   for a, b, c, d in CMP)

m = fin["m"]
HTML = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>宽基 ETF + 期权风险 + 均线清仓：按你的规则重跑</title>
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
<div class="ttl">宽基 ETF + 期权风险开关 + 均线清仓<br><span style="font-size:16px;color:var(--sub)">按你的规则重跑 · 2026-01-05 至 2026-11-06 · 203 个交易日</span></div>
<div class="meta">股票池改为沪深300 / 中证500 / 中证1000 / 科创50 / 纳指（25%）｜期权判风险用 IV z 与 25Δ 偏度 z｜单边成本 5bp｜信号与调仓只用当日及以前信息</div>
</header>

<div class="lead">
<b>结论先说。</b>按你的规则重建后：<b>{m['total']*100:+.2f}% / 最大回撤 {m['maxdd']*100:.2f}% / 夏普 {m['sharpe']:.2f}</b>，股票腿贡献从旧版 8.90pp 升到 <b>10.58pp</b>，纳指权重提到 25% 后纳指单腿贡献 <b>8.28pp</b>（旧版 3.32pp）。A 股宽基在 <b>5 月 28 日至 6 月 8 日</b>陆续清仓，6 月 10 日起 A 股腿为 0、7 月整月维持 0（纳指因境内无期权配对仍按 25% 持有，熔断期内减半到 12%–13%），避开了 7 月科创 −26%、创业板 −24%。
但有一条必须说清：<b>你的规则在这份样本里没有改善风险调整后表现</b>——无风控版本是 18.74%/−4.58%，你的规则是 16.70%/−4.96%，收益少 2pp、回撤反而多一点。原因写在第五节：清仓发生在 5 月底到 6 月初，正好是上涨的最后一段。
真正跑赢的是把清仓条件放宽成"<b>死叉即清、期权风险只用来禁止恢复</b>"：<b>+19.35% / −4.30% / 夏普 2.51</b>，它在 3 月就把 A 股腿清掉，避开了 3 月 −15.5%。
</div>

<div class="kpi">
<div class="card"><div class="v">{m['total']*100:+.2f}%</div><div class="l">区间收益（你的规则）</div></div>
<div class="card"><div class="v">{m['maxdd']*100:.2f}%</div><div class="l">最大回撤</div></div>
<div class="card"><div class="v">10.58pp</div><div class="l">股票腿贡献（旧版 8.90pp）</div></div>
<div class="card"><div class="v">8.28pp</div><div class="l">纳指单腿（旧版 3.32pp）</div></div>
</div>

<h1 class="sec">一、宽基与期权配对的实际覆盖</h1>
<table>
<tr><th>宽基</th><th>ETF 代码</th><th>期权数据</th><th>隐含波动</th><th>25Δ 偏度</th><th>PCR</th></tr>
<tr><td>上证50</td><td>510050</td><td>上交所，203 天</td><td class="num">有</td><td class="num">有</td><td class="num">有</td></tr>
<tr><td>沪深300</td><td>510300 / 159919</td><td>上交所 203 天 + 深交所</td><td class="num">有</td><td class="num">有</td><td class="num">有</td></tr>
<tr><td>中证500</td><td>510500 / 159922</td><td>上交所 203 天 + 深交所</td><td class="num">有</td><td class="num">有</td><td class="num">有</td></tr>
<tr><td>创业板</td><td>159915</td><td>深交所</td><td class="num">有（未用）</td><td class="num">有（未用）</td><td class="num">有（未用）</td></tr>
<tr><td>科创50</td><td>588000 / 588080</td><td>上交所 203 天</td><td class="num">有</td><td class="num">有</td><td class="num">有</td></tr>
<tr><td>中证1000</td><td>512100</td><td><b>无 ETF 期权</b></td><td class="num">—</td><td class="num">—</td><td class="num">—</td></tr>
</table>
<p class="small">中证1000 只有中金所股指期权（MO），没有 ETF 期权，本版用<b>中证500 期权作代理</b>；纳指是境外 ETF，境内无配套期权，只走均线规则。这是本次两个明确的数据边界。</p>

<h1 class="sec">二、期权怎么判风险：风险窗口在什么时候</h1>
<p>判据：隐含波动 60 日 z 与 25Δ 偏度 60 日 z，任一 ≥1.0 且连续 2 日 → 进入风险；两者同时 &lt;0.5 且连续 3 日 → 解除。</p>
<table>
<tr><th>宽基</th><th>风险段数</th><th>风险日数</th><th>风险区间（前 4 段）</th></tr>
{risk_rows}
</table>
<div class="note">
科创50 的风险窗口是 <b>4 月 14 日至 7 月 28 日</b>——连续 77 个交易日，把整个 4–6 月上涨包在里面。这不是信号错了，而是<b>隐含波动在上涨中本来就会抬升</b>，所以"IV 高=风险"会把牛市误判成风险期。这也解释了为什么单靠期权减半会亏（16.47% vs 无风控 18.74%）。
</div>

<h1 class="sec">三、规则怎么执行</h1>
<p>每只宽基独立跑一个状态机，三档：<b>满仓 → 半仓 → 清仓</b>。</p>
<ul>
<li><b>清仓</b>：期权处于风险状态 <b>且</b> 10 日线下穿 20 日线（严格按你的原话）。</li>
<li><b>半仓</b>：期权处于风险状态、但均线还没死叉。</li>
<li><b>恢复</b>：期权风险解除、且 10 日线回到 20 日线上方，才回到满仓；清仓后至少冷却 5 个交易日，避免次日就买回。</li>
<li><b>债券吸收</b>：股票腿减掉的部分转入十年国债，不做现金空转。</li>
</ul>
<p>实际执行结果：沪深300 与中证500 在 <b>5 月 28 日</b>清仓，中证1000 在 <b>5 月 29 日</b>，科创50 在 <b>6 月 8 日</b>；股票腿从 5 月 25 日的 50.2% 降到 6 月 10 日的 6.2%；A 股宽基自 6 月 10 日起为 0、7 月整月为 0，8 月 24 日才重新买回科创 50；纳指因无境内期权，全程按 25% 持有、熔断期减半到 12%–13%。</p>

<h1 class="sec">四、逐月表现与分腿贡献</h1>
<table>
<tr><th>月份</th><th>股票腿</th><th>纳指</th><th>A股宽基</th><th>组合月收益</th><th>风险日</th><th>纳指贡献</th><th>A股贡献</th></tr>
{mon_rows}
</table>
<p class="small">贡献单位百分点。4 月与 5 月是股票腿的主战场（股票腿 51.3% / 49.5%，组合 +7.5% / +4.4%）；6 月清仓后组合 −4.6%，其中股票腿只占 18.1%，主要拖累来自原油（6 月 −20%）。</p>

<h1 class="sec">五、规则分解：你的规则、无风控、放宽版</h1>
<table>
<tr><th>版本</th><th>收益</th><th>最大回撤</th><th>波动</th><th>夏普</th><th>笔数</th></tr>
{dec_rows}
</table>
<p><b>三个发现：</b></p>
<ol>
<li><b>均线单独不触发</b>：没有期权风险时，"风险中死叉"条件永远不成立，所以"仅均线"与"无风控"完全一样（18.74%）——期权门控是必要条件，这点你的设计是对的。</li>
<li><b>期权减半单独是负贡献</b>：16.47% vs 无风控 18.74%，因为科创50 的 4–7 月窗口把上涨也算成风险，减半等于削弱最强的腿。</li>
<li><b>你的组合规则反而更差</b>：16.70%/−4.96%，比无风控少 2pp、回撤多 0.4pp。清仓点在 5/28–6/8，正好错过 6 月最后一段上涨（科创 50 当月 +27.1%），而 7 月空仓省下的跌幅只有约 2pp。</li>
</ol>

<h1 class="sec">六、纳指权重的效果</h1>
<table>
<tr><th>纳指权重</th><th>收益</th><th>最大回撤</th><th>夏普</th><th>5% 约束</th></tr>
{nas_rows}
</table>
<p>纳指从 20% 提到 25%，收益 +0.24pp、回撤从 −4.50% 到 −4.96%；提到 30% 收益 +0.51pp 但回撤 −5.38% 破线；35% 反而掉到 12.75%（债券额度被压到 20%，缓冲不够）。<b>25% 是 5% 回撤约束下的上限，不是越高越好。</b>纳指贡献从旧版 3.32pp 升到 8.28pp，是因为旧版权重均值只有 9.6%，这版固定 25% 且全年持有。</p>

<h1 class="sec">七、稳健性</h1>
<table>
<tr><th>情形</th><th>收益</th><th>最大回撤</th></tr>
{rob_rows}
</table>
<p class="small">收益区间 10.5%–17.0%、回撤 4.66%–5.05%。带宽收到 2pp 会掉到 10.51%（换手过密），半仓 0.7 会破 5%（−5.05%），说明结论对参数仍有依赖。</p>

<h1 class="sec">八、与之前版本的对照</h1>
<table>
<tr><th>版本</th><th>收益</th><th>最大回撤</th><th>夏普</th></tr>
{cmp_rows}
</table>
<p>本版收益低于上一版（16.70% vs 17.54%），但<b>股票腿贡献更高</b>（10.58pp vs 8.90pp）——差在原油：上一版原油贡献 22.5pp，本版 17.45pp（原油止损更紧、上限 25%）。换句话说，这版把收益结构从"靠原油"往"靠股票"挪了一点，但还没挪够。</p>

<h1 class="sec">九、局限与下一步</h1>
<ul>
<li><b>中证1000 无 ETF 期权</b>，风险判据借用了中证500，两者波动结构不同（中证1000 年化 29.6% vs 中证500 29.3%，接近但仍非同一标的）。</li>
<li><b>纳指无境内期权</b>，只走均线；若要用期权判纳指风险，需要美股期权数据（当前 VIX 三源均 403）。</li>
<li><b>清仓时点偏晚</b>：期权风险 4/14 就出现，但死叉要等到 5/28。若把清仓改为"死叉即清、风险只禁止恢复"，收益 +19.35%、回撤 −4.30%，是更优的可执行方案。</li>
<li><b>样本只有 203 个交易日、单一自然年</b>，参数与阈值在本样本内确定，存在选择偏差。</li>
</ul>

<div class="warn">
本内容由人工智能基于公开数据回测生成，仅供研究与交流，不构成任何投资建议、收益承诺或交易依据。中证1000 期权数据缺失、纳指无境内期权配对、样本期短且参数经样本内挑选，历史回测不代表未来表现。市场有风险，投资需谨慎。
</div>

<footer>
数据截止 2026-11-06 收盘；区间 2026-01-05 至 2026-11-06（203 个交易日）。<br>
期权数据：上交所每日统计与逐合约希腊字母（510050 / 510300 / 510500 / 588000 / 588080）＋深交所每日统计（159901 / 159915 / 159919 / 159922）。
</footer>
</div></body></html>"""
open(OUT, "w", encoding="utf-8").write(HTML)
print("写出", OUT, len(HTML), "字符")
