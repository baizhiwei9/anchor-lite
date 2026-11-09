# -*- coding: utf-8 -*-
"""生成：动态池 × 波动预算 报告"""
import os, sys, json
import pandas as pd, numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "动态池与波动预算回测报告.html")
pool = json.load(open(os.path.join(BASE, "pool_history.json")))["monthly"]
vol = json.load(open(os.path.join(BASE, "vol_history.json")))
bt = json.load(open(os.path.join(BASE, "dynamic_backtest.json")))
conc = json.load(open(os.path.join(BASE, "concentration.json")))
spec = json.load(open(os.path.join(BASE, "dynamic_rule_spec.json")))
r = bt["result"]; rob = bt["robust"]

pool_rows = "".join(
    f"<tr><td>{p['month']}</td><td>{p['state']}（{p['prob']*100:.0f}%）</td><td>{'、'.join(p['pool'])}</td>"
    f"<td>{'、'.join(p['enter']) or '—'}</td><td>{'、'.join(p['exit']) or '—'}</td></tr>" for p in pool)

vol_rows = "".join(
    f"<tr><td>{v['month']}</td><td class='num'>{v['v20']*100:.1f}%</td><td class='num'>{v['v60']*100:.1f}%</td>"
    f"<td class='num'>{v['q20']*100:.0f}%</td><td>{v['bucket']}</td><td class='num'>{v['target_vol']*100:.0f}%</td></tr>"
    for v in vol)

group_rows = "".join(
    f"<tr><td>{s}</td><td class='num'>{c['a_share']}</td><td class='num'>{c['overseas']}</td>"
    f"<td class='num'>{c['commodity']}</td><td class='num'>{c['bond']}</td></tr>"
    for s, c in spec["macro_to_pool"].items())

HTML = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>动态资产池 × 波动率预算：2026 年内回测与诊断</title>
<style>
:root{{--ink:#12161C;--sub:#5A6472;--line:#E3E7EE;--bg:#F7F8FA;--accent:#1F3A5F}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font-family:"Noto Sans SC","PingFang SC","Microsoft YaHei",system-ui,sans-serif;font-size:14.5px;line-height:1.72}}
.wrap{{max-width:1080px;margin:0 auto;padding:34px 26px 60px}}
header{{border-bottom:3px solid var(--accent);padding-bottom:16px;margin-bottom:24px}}
h1{{font-size:24px;line-height:1.36;margin:0 0 10px}}
.meta{{color:var(--sub);font-size:12.5px}}
h2{{font-size:18px;margin:36px 0 12px;padding-left:11px;border-left:4px solid var(--accent)}}
h3{{font-size:15px;margin:20px 0 8px}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin:10px 0;background:#fff}}
th,td{{border-bottom:1px solid var(--line);padding:7px 9px;text-align:left;vertical-align:top}}
th{{background:#F2F5F9;font-weight:600;color:#2B3442;font-size:12.5px}}
td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
.lead{{background:#fff;border-left:4px solid var(--accent);padding:14px 18px;margin:16px 0;border-radius:0 8px 8px 0}}
.note{{background:#FFFBEB;border:1px solid #FDE68A;border-radius:8px;padding:12px 15px;font-size:13px;color:#7C4A03;margin:12px 0}}
.warn{{background:#FEF2F2;border:1px solid #FECACA;border-radius:8px;padding:12px 15px;font-size:13px;color:#7F1D1D;margin:12px 0}}
ul,ol{{margin:8px 0 8px 20px;padding:0}} li{{margin:5px 0}}
.small{{font-size:12.5px;color:var(--sub)}}
footer{{margin-top:40px;border-top:1px solid var(--line);padding-top:16px;color:var(--sub);font-size:12.5px}}
</style></head><body><div class="wrap">

<header>
<h1>宏观状态选资产、波动率状态定仓位：2026 年内实测与诊断</h1>
<div class="meta">区间 2026-01-05 ~ 2026-11-06（203 个交易日）｜32 只 ETF｜月度重设 + 3% 熔断｜5bp 单边成本｜
宏观状态：八态 HMM 每日滤波；波动档位：全资产 20 日波动率中位数 vs 过去 252 日分位｜不构成投资建议</div>
</header>

<div class="lead">
<p><strong>结果：年内 {r['total']*100:+.1f}%，最大回撤 {r['maxdd']*100:.2f}%，年化波动 {r['vol']*100:.1f}%，夏普 {r['sharpe']:.2f}。</strong>
5% 回撤守住了，但<strong>没赚到钱</strong>——同期固定配比是 +17.5% / −4.06%。</p>
<p>规则本身按你的思路实现了：<strong>宏观状态决定每月的候选池（选什么），波动率档位决定目标波动预算（买多少）</strong>，
每月 5 只入池、月均换出 2.2 只，波动档位在“极端/高波/中波”之间切换，目标波动 3%–9%。</p>
<p>为什么跑输？诊断在第五节：<strong>池内用逆波动加权，导致债券拿到 81% 的权重</strong>（最高 90%），
再叠加 3%–6% 的低波动预算，等于把商品腿压到个位数——状态选对了资产，仓位也没让它们发挥作用。</p>
</div>

<h2>一、规则规格</h2>
<table>
<tr><th style="width:22%">环节</th><th>规则</th></tr>
<tr><td>宏观状态 → 池子</td><td>八态取最大概率状态；每个状态预设“组别配额”（见下表），组内按 60 日动量排序取前 k 个，要求 20 日均成交额 ≥ 1 亿元</td></tr>
<tr><td>池内权重</td><td>逆波动加权 w0（低波动资产权重高）</td></tr>
<tr><td>波动档位 → 预算</td><td>全资产 20 日波动率中位数相对过去 252 日分位：&lt;25% 低波→12%；25–60% 中波→9%；60–85% 高波→6%；&gt;85% 极端→3%</td></tr>
<tr><td>仓位缩放</td><td>σ_target / σ(w0)，上限 1.0（不加杠杆）</td></tr>
<tr><td>执行</td><td>宏观与波动档位月度重设；回撤 3% 熔断减半、1.5% 恢复（日频检查）</td></tr>
</table>
<h3>1.1 各状态的组别配额（每月按此数量选资产）</h3>
<table>
<tr><th>状态</th><th class="num">A 股</th><th class="num">海外</th><th class="num">商品</th><th class="num">债券</th></tr>
{group_rows}
</table>

<h2>二、2026 年逐月池子（选什么）</h2>
<table>
<tr><th>月份</th><th>宏观状态</th><th>入池资产（5 只）</th><th>新增</th><th>剔除</th></tr>
{pool_rows}
</table>
<p class="small">月均换出 2.2 只，11 个月里只出现 3 个状态（低波趋势、温和通胀、紧缩利率上行、商品强通胀），说明八态模型在 2026 年实际只用了少数几种。</p>

<h2>三、2026 年逐月波动档位（买多少）</h2>
<table>
<tr><th>月份</th><th class="num">20 日波动</th><th class="num">60 日波动</th><th class="num">20 日分位</th><th>档位</th><th class="num">目标波动</th></tr>
{vol_rows}
</table>
<p class="small">2026 年波动整体偏高：11 个月里 5 个月落在“极端”（分位 90% 以上），目标波动只有 3%；这直接压低了全年收益。</p>

<h2>四、回测结果与对照</h2>
<table>
<tr><th>方案</th><th class="num">年内收益</th><th class="num">最大回撤</th><th class="num">年化波动</th><th class="num">夏普</th><th>5% 约束</th></tr>
<tr><td><strong>动态池 × 波动预算（本报告）</strong></td><td class="num strong">{r['total']*100:+.1f}%</td><td class="num">{r['maxdd']*100:.2f}%</td><td class="num">{r['vol']*100:.1f}%</td><td class="num">{r['sharpe']:.2f}</td><td>✅</td></tr>
<tr><td>固定配比 55/15/15/10/5 + 3% 熔断</td><td class="num">+17.5%</td><td class="num">-4.06%</td><td class="num">9.7%</td><td class="num">2.30</td><td>✅</td></tr>
<tr><td>状态软混合（单资产≤35%）</td><td class="num">-10.4%</td><td class="num">-20.66%</td><td class="num">15.6%</td><td class="num">—</td><td>❌</td></tr>
<tr><td>状态软混合（无上限）</td><td class="num">-13.7%</td><td class="num">-29.54%</td><td class="num">—</td><td class="num">—</td><td>❌</td></tr>
</table>
<h3>4.1 稳健性</h3>
<ul>
<li>成本 10bp：{rob['cost10']['total']*100:+.1f}% / {rob['cost10']['maxdd']*100:.2f}%（换手 {r['turnover']*100:.0f}% 下成本影响有限）</li>
<li>不设熔断：{rob['no_trig']['total']*100:+.1f}% / {rob['no_trig']['maxdd']*100:.2f}%</li>
<li>上半年 {rob['h1'][0]*100:+.1f}% / {rob['h1'][1]*100:.2f}%；下半年 {rob['h2'][0]*100:+.1f}% / {rob['h2'][1]*100:.2f}%</li>
</ul>

<h2>五、诊断：为什么守住了回撤却没赚到钱</h2>
<table>
<tr><th style="width:26%">问题</th><th>证据</th></tr>
<tr><td><strong>① 池内权重被债券吃掉</strong></td><td>逐月目标里最大单资产权重平均 {conc['avg']*100:.0f}%、最高 90%，几乎每月都是城投债/活跃国债——逆波动加权天然把低波动资产推到 80% 以上，商品腿只剩个位数</td></tr>
<tr><td><strong>② 波动档位长期压仓</strong></td><td>11 个月里 5 次“极端档”，目标波动 3%；即便 σ_target 没触发缩放（缩放系数多为 1.0），组合本身已因债券化而低波动，等于“双重保守”</td></tr>
<tr><td><strong>③ 状态选对、权重没跟上</strong></td><td>商品强通胀状态下池子里有原油、豆粕、煤炭，但逆波动加权后各占 2%–5%，对收益没有实质贡献</td></tr>
</table>
<div class="note"><strong>根因是权重规则与“暴露随状态放大”这个目标冲突。</strong>你要的是“状态对了就多买”，而逆波动加权做的是“谁波动小谁多买”——两者方向相反。宏观定池子解决了“选什么”，但“买多少”被波动率规则接管后，商品永远拿不到大仓位。</div>

<h2>六、怎么改才能同时满足“状态放大”和“5% 回撤”</h2>
<ol>
<li><strong>先定资产类别预算，再在类内选资产。</strong>例如商品强通胀态：商品 30–40%、海外 15–20%、债券 40–50%；类别预算由状态决定，类内再逆波动或等权。这样状态真的能放大暴露。</li>
<li><strong>波动预算作用在组合层，不作用在单资产权重上。</strong>先按状态配好类别比例，再用 σ_target/σ(组合) 整体缩放；不要用单资产逆波动加权，否则债券会吃掉一切。</li>
<li><strong>给商品腿设下限。</strong>通胀类状态下商品最低 20–25%，避免“选了原油却只买 2%”的形式主义。</li>
<li><strong>波动档位不要一刀切到 3%。</strong>极端档可改为 6%，靠熔断与类别预算控制回撤，而不是把仓位砍到几乎空仓。</li>
</ol>

<h2>七、局限与口径</h2>
<ul class="small">
<li>样本仅 203 个交易日，状态样本 16–97 天，池子选择与波动分位都高度依赖这段样本；结论只对 2026 年成立。</li>
<li>池子筛选用 60 日动量排序，2026 年是反转年，动量选出的资产常常随后下跌（半导体、创业板、有色均如此）。</li>
<li>波动档位用全资产中位数分位，属于市场层面口径，不是组合自身波动；两者在债券化后会背离。</li>
<li>未建模：ETF 溢价与限购、期权、期货保证金；成交按月度再平衡金额计 5bp。</li>
</ul>

<footer>
<p><strong>免责声明：</strong>本报告为历史数据回测与方法演示，收益与回撤均为模型输出，<strong>不构成投资建议</strong>，不构成对未来收益的承诺。样本期短、状态样本稀疏，任何“年内最优”都不能外推。ETF 存在溢价与流动性风险，投资有风险。</p>
<p>口径：2026-01-05 ~ 2026-11-06（203 个交易日）｜32 只 ETF｜月度重设 + 3% 熔断｜5bp 单边成本｜生成于 2026-11-08</p>
</footer>
</div></body></html>"""

open(OUT, "w", encoding="utf-8").write(HTML)
print("written", OUT, round(len(HTML) / 1024), "KB")