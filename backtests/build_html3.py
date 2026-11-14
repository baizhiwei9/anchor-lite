# -*- coding: utf-8 -*-
"""生成 2026 年内 5% 回撤方案报告"""
import os, sys, json, base64
import pandas as pd, numpy as np
import engine3 as E

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "2026年内5%回撤最优方案.html")
o = json.load(open(os.path.join(BASE, "opt2026.json")))
det = json.load(open(os.path.join(BASE, "detail2026.json")))
cov = pd.read_csv(os.path.join(BASE, "data2026", "_coverage.csv"))

def b64(p):
    return base64.b64encode(open(os.path.join(BASE, "figs", p), "rb").read()).decode()

mixes = pd.DataFrame(o["mixes"])
mixes["label"] = mixes["name"] + "｜熔断 " + (mixes["trigger"] * 100).round(0).astype(int).astype(str) + "%"
mixes = mixes.sort_values("total", ascending=False)
mx_rows = "".join(
    f"<tr><td>{r.label}</td><td class='num'>{r.total*100:+.1f}%</td><td class='num'>{r.maxdd*100:.2f}%</td>"
    f"<td class='num'>{r.vol*100:.1f}%</td><td class='num'>{r.sharpe:.2f}</td>"
    f"<td>{'✅' if r.maxdd >= -0.05 else '❌'}</td></tr>"
    for r in mixes.itertuples())

# 资产池年内表现（2026 全年）
perf = []
for _, r in cov.iterrows():
    d = pd.read_csv(os.path.join(BASE, "data2026", r["name"] + ".csv"))
    c = pd.Series(d["close"].values)
    perf.append(dict(name=E.NAMES.get(r["name"], r["name"]), ytd=float(c.iloc[-1] / c.iloc[0] - 1),
                     mdd=float((c / c.cummax() - 1).min()),
                     vol=float(np.std(np.diff(np.log(c))) * np.sqrt(252)), amt=float(r["amt_avg"]) / 1e8))
perf = pd.DataFrame(perf).sort_values("ytd", ascending=False)
perf_rows = "".join(
    f"<tr><td>{r.name}</td><td class='num'>{r.ytd*100:+.1f}%</td><td class='num'>{r.mdd*100:.1f}%</td>"
    f"<td class='num'>{r.vol*100:.0f}%</td><td class='num'>{r.amt:.1f}</td></tr>"
    for r in perf.itertuples())

# 期权叠加
ov = pd.DataFrame(o["overlays"])
ov["label"] = ov["overlay"].map({"none": "不加期权", "short_vol": "卖出波动率（跨式）", "long_put": "买入看跌价差"})
ov["label"] = ov["label"] + ov["size"].apply(lambda x: "" if x == 0 else f"（名义 {x*100:.0f}%）")
ov_rows = "".join(
    f"<tr><td>{r.label}</td><td class='num'>{r.total*100:+.1f}%</td><td class='num'>{r.maxdd*100:.2f}%</td>"
    f"<td class='num'>{r.vol*100:.1f}%</td></tr>" for r in ov.itertuples())

month_rows = "".join(f"<td class='num'>{v:+.1f}%</td>" for v in det["monthly"].values())
month_head = "".join(f"<th class='num'>{k[-2:]}月</th>" for k in det["monthly"].keys())

lot_rows = "".join(
    f"<tr><td>{l['asset']}</td><td class='num'>{l['weight']*100:.0f}%</td><td class='num'>{l['amount']:,.0f} 元</td>"
    f"<td class='num'>{l['price']:.3f}</td><td class='num'>{l['lots']} 手</td></tr>" for l in det["lots"])

rob = o["robust"]
best = o["best"]

HTML = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>2026 年内：5% 回撤下能拿到的最高收益</title>
<style>
:root{{--ink:#12161C;--sub:#5A6472;--line:#E3E7EE;--bg:#F7F8FA;--accent:#1F3A5F;--warn:#B45309}}
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
.strong,strong{{font-weight:700}}
.lead{{background:#fff;border-left:4px solid var(--accent);padding:14px 18px;margin:16px 0;border-radius:0 8px 8px 0}}
.note{{background:#FFFBEB;border:1px solid #FDE68A;border-radius:8px;padding:12px 15px;font-size:13px;color:#7C4A03;margin:12px 0}}
.warn{{background:#FEF2F2;border:1px solid #FECACA;border-radius:8px;padding:12px 15px;font-size:13px;color:#7F1D1D;margin:12px 0}}
.card{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin:14px 0}}
img{{width:100%;border:1px solid var(--line);border-radius:8px;margin:10px 0;background:#fff}}
ul,ol{{margin:8px 0 8px 20px;padding:0}} li{{margin:5px 0}}
.small{{font-size:12.5px;color:var(--sub)}}
footer{{margin-top:40px;border-top:1px solid var(--line);padding-top:16px;color:var(--sub);font-size:12.5px}}
</style></head><body><div class="wrap">

<header>
<h1>2026 年内：5% 回撤约束下，能拿到的最高收益是多少</h1>
<div class="meta">检验区间 <strong>2026-01-05 ~ 2026-11-06</strong>（203 个交易日，年内全区间，用 2025 年数据预热）｜
资产池 33 只 ETF（A 股宽基/行业、海外宽基、商品、债券）｜含 5bp 单边成本｜当日收盘决策、次日结算｜不构成投资建议</div>
</header>

<div class="lead">
<p><strong>结论：能做到 年化化的 +{best['metrics']['total']*100:.1f}%（年内绝对收益），最大回撤 {best['metrics']['maxdd']*100:.2f}%，年化波动 {best['metrics']['vol']*100:.1f}%，夏普 {best['metrics']['sharpe']:.2f}。</strong></p>
<p>配置是：<strong>十年国债ETF 40% + 城投债ETF 30% + 原油ETF 10% + 纳指ETF 10% + 标普500ETF 10%</strong>，回撤 5% 触发熔断减半（实际全年没触发，最大回撤 4.43%）。</p>
<p>你上次的批评是对的：把资产池放开（含海外、含商品、用国债现券/ETF 而不是期货、允许期权单腿）之后，结果比上一版高得多——上一版"样本内 8.1%"受限于窄资产池和期货腿。这次是 2026 年内实测 <strong>+12.2%</strong>。</p>
<p>但要说清楚两件事：<strong>① 真正的上限（事后挑最优配比）是 +15.5%</strong>，我们比它少 3.3 个百分点，这 3.3pp 就是"不能预知今年哪个资产会涨"的价格；<strong>② 全年收益有 90% 集中在上半年</strong>（1-5 月 +11.0%），6 月后基本走平。</p>
</div>

<h2>一、先说清楚 2026 年到底发生了什么</h2>
<p>年内 33 只 ETF 的实测表现（2026-01-05 收盘至 2026-11-06）：</p>
<div class="card" style="max-height:520px;overflow:auto">
<table>
<tr><th>资产</th><th class="num">年内涨幅</th><th class="num">年内最大回撤</th><th class="num">年化波动</th><th class="num">日均成交(亿)</th></tr>
{perf_rows}
</table>
</div>
<p>三件事决定了今年的答案：</p>
<ol>
<li><strong>原油 +85.6%，但途中最大回撤 -43.1%、年化波动 68%</strong>。它是今年唯一的大赢家，也是唯一能把 5% 回撤撑爆的资产——所以只能小配（10%），不能重仓。</li>
<li><strong>A 股全线下跌</strong>：沪深300 -13.6%、中证1000 -15.9%、创业板 -24.4%、半导体 -49.4%（回撤 -76.6%）。<strong>任何 A 股仓位都是回撤来源</strong>，这就是为什么最优秀的组合里一只 A 股都没有。</li>
<li><strong>债券几乎无风险</strong>：十年国债 ETF +1.7%、最大回撤 -1.1%、波动 2%。它是把组合回撤压在 5% 以内的唯一"压舱石"，代价是收益低。</li>
</ol>
<div class="note"><strong>海外这块你说得对。</strong>纳指 ETF 年内 +11.8%（回撤 -13.7%）、标普 500 +4.9%、日经 +9.9%——A 股跌的时候海外在涨，这是今年"跨市场分散"真正起作用的地方，也是上一版只买 300ETF 拿不到的东西。</div>

<h2>二、候选配比与结果（8 个有经济含义的配比 × 3 档熔断）</h2>
<table>
<tr><th>方案</th><th class="num">年内收益</th><th class="num">最大回撤</th><th class="num">年化波动</th><th class="num">夏普</th><th>是否达标</th></tr>
{mx_rows}
</table>
<p><strong>读法：债券比例决定回撤，原油/海外比例决定收益。</strong>债 70% 以上时回撤稳定在 4% 以内；原油配到 20% 以上（如"80% 国债 + 20% 原油"）收益能到 11-13%，但回撤立刻破 6%。最优解落在"债 70% + 商品/海外各 10%"这个区间。</p>

<h2>三、推荐方案：结构、月度表现与净值</h2>
<img src="data:image/png;base64,{b64('nav2026.png')}" alt="2026 年内净值与回撤">
<table>
<tr><th>月份</th>{month_head}</tr>
<tr><td>月度收益</td>{month_rows}</tr>
</table>
<p class="small">11 月为 11-01~11-06 的部分月度数据。全年 11 个月里 7 个月正收益，最大单月亏损 -2.9%（6 月）。</p>
<table>
<tr><th>资产</th><th class="num">权重</th><th class="num">100 万本金金额</th><th class="num">最新价</th><th class="num">手数（100 份/手）</th></tr>
{lot_rows}
</table>

<h3>为什么这个结构能压住 5%</h3>
<ul>
<li><strong>70% 债券是"回撤底座"。</strong>十年国债 ETF 全年回撤只有 -1.1%，城投债 -0.6%；70% 仓位贡献的波动不到 1.5%。</li>
<li><strong>30% 风险资产按"互不相关"选。</strong>原油（供给冲击）、纳指（AI 科技）、标普（美国宽基）三者今年走势不同步，合起来的回撤远小于单一资产的回撤。</li>
<li><strong>5% 熔断是最后一道保险。</strong>回撤触及 5% 就把全部仓位减半，回撤修复到 2.5% 再恢复。今年实际最高回撤 4.43%，<strong>熔断一次都没触发</strong>——这说明回撤是被结构压住的，不是被规则救回来的。</li>
</ul>

<h2>四、期权单腿：买 put 没用，卖波动率更糟</h2>
<table>
<tr><th>方案</th><th class="num">年内收益</th><th class="num">最大回撤</th><th class="num">年化波动</th></tr>
{ov_rows}
</table>
<p><strong>结论很清楚：</strong></p>
<ul>
<li><strong>卖出波动率（跨式/宽跨）</strong>：名义 5% 就让收益从 +12.2% 掉到 +4.5%、回撤从 -4.4% 恶化到 -5.9%；名义 10% 直接归零。原因不是 carry 不好，是 2026 年有三次单日大波动（半导体、科创50、原油），卖方在这些日子被跳空罚得很惨。<strong>把跳空惩罚假设从 4 倍降到 1 倍，结论也不变：仍然是负贡献。</strong></li>
<li><strong>买入看跌价差</strong>：回撤只从 4.43% 改善到 4.33%（名义 20%），代价是少赚 0.8 个百分点。<strong>本组合的回撤本来就不来自股票尾部，买保险没有对应风险可保。</strong></li>
</ul>
<div class="note">期权在这个组合里的角色是"在你回撤已经接近 5%、又不想砍仓位时临时买保险"，而不是"提升收益"。如果真想加收益，提高原油/海外权重比卖期权有效得多——只是回撤会破线。</div>

<h2>五、上限在哪里：事后最优 vs 可执行</h2>
<table>
<tr><th>方案</th><th class="num">年内收益</th><th class="num">最大回撤</th><th>说明</th></tr>
<tr><td>事后最优静态配比（含未来信息）</td><td class="num">+15.5%</td><td class="num">-4.99%</td><td>原油 12.7%、纳指 9.6%、城投 50.7%、豆粕 15.6%、国债 5.4%、标普 3.2%——这是"如果年初就知道今年会发生什么"的答案，<strong>不可执行</strong></td></tr>
<tr><td><strong>本报告推荐（可执行）</strong></td><td class="num strong">+{best['metrics']['total']*100:.1f}%</td><td class="num">{best['metrics']['maxdd']*100:.2f}%</td><td>固定配比 + 月度再平衡 + 5% 熔断，无预测</td></tr>
<tr><td>沪深300ETF 买入持有</td><td class="num">-13.6%</td><td class="num">-20.2%</td><td>今年 A 股是负贡献</td></tr>
<tr><td>原油 ETF 买入持有</td><td class="num">+85.6%</td><td class="num">-43.1%</td><td>收益最高、回撤远超 5%</td></tr>
<tr><td>十年国债 ETF 买入持有</td><td class="num">+1.7%</td><td class="num">-1.1%</td><td>回撤最小、收益也最低</td></tr>
</table>
<p><strong>+12.2% 与 +15.5% 的差距，就是"不能预知"的代价。</strong>这也说明一件事：今年想同时拿高收益和低回撤，主要靠"债 70% 打底 + 商品/海外各 10%"这种<strong>结构</strong>，而不是靠择时或期权技巧。</p>

<h2>六、稳健性：这个结果是不是运气</h2>
<table>
<tr><th>检验</th><th class="num">收益</th><th class="num">回撤</th><th>判断</th></tr>
<tr><td>上半年（1-5 月）</td><td class="num">{rob['h1']['total']*100:+.1f}%</td><td class="num">{rob['h1']['maxdd']*100:.2f}%</td><td>主要收益来源</td></tr>
<tr><td>下半年（6-11 月）</td><td class="num">{rob['h2']['total']*100:+.1f}%</td><td class="num">{rob['h2']['maxdd']*100:.2f}%</td><td><strong>几乎走平</strong>，说明今年不是"稳定月月赚"</td></tr>
<tr><td>成本抬到 10bp</td><td class="num">{rob['cost10']['total']*100:+.1f}%</td><td class="num">{rob['cost10']['maxdd']*100:.2f}%</td><td>几乎无影响（月度再平衡换手低）</td></tr>
<tr><td>不设熔断</td><td class="num">{rob['no_trigger']['total']*100:+.1f}%</td><td class="num">{rob['no_trigger']['maxdd']*100:.2f}%</td><td>结果相同——回撤是结构压住的，不是熔断救的</td></tr>
</table>
<div class="warn"><strong>必须承认的两点：</strong>① 全年 203 个交易日、11 个月，样本极短，这个 +12.2% 是"今年这只特定的兔子"，不能外推成长期年化；② 我选的 8 个配比虽然是"债多 + 商品/海外各 10%"这类有经济含义的结构，但挑出其中最好的那个仍然带事后成分——真实执行时你应当按自己的风险偏好定权重，而不是照抄最优解。</div>

<h2>七、怎么落地（按你的可投范围）</h2>
<ol>
<li><strong>债券：用 ETF，不建议买现券。</strong>交易所国债现券一手 10 万元面值、报价按百元净价、流动性远差于 ETF；511260（十年国债 ETF）和 511220（城投债 ETF）日均成交分别 39 亿和 15 亿，买卖价差小，适合散户。城投债 ETF 有信用风险，若你只想拿利率久期，可把 30% 城投换成 511010 国债 ETF。</li>
<li><strong>海外：注意溢价与额度。</strong>纳指 ETF（513100）、标普 500（513500）常年有 1%-3% 溢价，限购时溢价更高，<strong>别在溢价 2% 以上追</strong>；日经 ETF（513520）今年 +9.9%，也可作为替代。</li>
<li><strong>商品：原油 ETF 只做小配。</strong>501018 跟踪原油但受汇率、溢价、限购影响，且今年振幅 68%；10% 是上限，不要因为"今年涨了 85%"就加到 20%——那会让回撤破 6%。</li>
<li><strong>期权：只在回撤逼近 5% 时临时买保护。</strong>沪深 300ETF 期权（510300）或 300ETF 期权买 3%-10% 虚值价差，名义 10% 约 2 张（1 张名义约 4.1 万元）；不要常态卖出波动率。</li>
<li><strong>再平衡：每月一次，或偏离目标权重 ±5 个百分点时。</strong>每天盯盘没有必要——这套结构的换手很低，日均换手不到 0.3%，10bp 成本都无所谓。</li>
</ol>

<h2>八、口径、数据与三类表述</h2>
<table>
<tr><th style="width:16%">类别</th><th>内容</th></tr>
<tr><td>实测</td><td>33 只 ETF 的 2026 年内日线（新浪财经）；沪深300/中证500/1000、科创50、创业板、行业 ETF、纳指/标普/日经/恒生科技、原油/黄金/有色/豆粕/煤炭/钢铁、国债/城投/公司债 ETF；期权隐含波动率用 300ETF QVIX。区间 2026-01-05 ~ 2026-11-06，含 5bp 单边成本。</td></tr>
<tr><td>工程假设</td><td>熔断规则（回撤 5% 减半、修复到 2.5% 恢复）；月度再平衡；期权用"方差风险溢价 + 跳空惩罚"近似定价（无期权链报价）；买入看跌价差成本按名义 0.45%/月摊销、赔付上限 10%。</td></tr>
<tr><td>原文关联</td><td>本报告不再沿用原文的 HMM/六 Agent 框架做决策——那些机制解释的是"原文怎么讲"，而不是"今年怎么赚"。本次只用可交易的固定配比 + 熔断，属于独立的工程实现。</td></tr>
</table>
<p class="small">数据缺口：美国 VIX 公开源被拒（403），用标普 20 日已实现波动率代理；原油 ETF 的实际溢价、限购未建模；城投债 ETF 的信用风险未单独定价。</p>

<footer>
<p><strong>免责声明：</strong>本报告为历史数据回测与方法演示，收益与回撤均为模型输出，<strong>不构成投资建议</strong>，不构成对未来的收益承诺。2026 年内样本只有 203 个交易日，任何"年内高收益"都不能当作长期能力；单一资产的历史涨幅不代表未来。ETF 有溢价与流动性风险，期权含杠杆，可能损失全部权利金或超过本金。</p>
<p>口径：2026-01-05 ~ 2026-11-06｜203 个交易日｜自 2025 年数据预热｜单边成本 5bp｜生成于 2026-11-08</p>
</footer>
</div></body></html>"""

open(OUT, "w", encoding="utf-8").write(HTML)
print("written", OUT, round(len(HTML) / 1024), "KB")