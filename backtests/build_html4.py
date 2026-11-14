# -*- coding: utf-8 -*-
"""生成 2026 年内报告（第二版：修正成本/再平衡口径后的结果）"""
import os, sys, json, base64
import pandas as pd, numpy as np
import engine3 as E

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "2026年内5%回撤最优方案.html")
o = json.load(open(os.path.join(BASE, "opt2026.json")))
det = json.load(open(os.path.join(BASE, "detail2026.json")))
cov = pd.read_csv(os.path.join(BASE, "data2026", "_coverage.csv"))
best = o["best"]; m = best["metrics"]; rob = o["robust"]

def b64(p):
    return base64.b64encode(open(os.path.join(BASE, "figs", p), "rb").read()).decode()

mixes = pd.DataFrame(o["mixes"])
mixes["label"] = mixes["name"] + "｜熔断 " + mixes["trigger"].astype(str).str.replace("0.", "").str.replace("无", "无")
mx_rows = "".join(
    f"<tr><td>{r.name}</td><td class='num'>{str(r.trigger).replace('0.0','')}{'%' if r.trigger!='无' else ''}</td>"
    f"<td class='num'>{r.total*100:+.1f}%</td><td class='num'>{r.maxdd*100:.2f}%</td>"
    f"<td class='num'>{r.vol*100:.1f}%</td><td class='num'>{r.sharpe:.2f}</td>"
    f"<td>{'✅' if r.maxdd >= -0.05 else '❌'}</td></tr>"
    for r in mixes.sort_values("total", ascending=False).itertuples())

perf = []
for _, r in cov[cov["name"] != "nasdaq2"].iterrows():
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

ov = pd.DataFrame(o["overlays"])
ov["label"] = ov["overlay"].map({"none": "不加期权", "short_vol": "卖出波动率（跨式）", "long_put": "买入看跌价差"})
ov["label"] = ov["label"] + ov["size"].apply(lambda x: "" if x == 0 else f"（名义 {x*100:.0f}%）")
ov_rows = "".join(
    f"<tr><td>{r.label}</td><td class='num'>{r.total*100:+.1f}%</td><td class='num'>{r.maxdd*100:.2f}%</td>"
    f"<td class='num'>{r.vol*100:.1f}%</td></tr>" for r in ov.itertuples())

months = det["monthly"]
month_head = "".join(f"<th class='num'>{k[-2:]}</th>" for k in months.keys())
month_vals = "".join(f"<td class='num'>{v:+.1f}%</td>" for v in months.values())
n_pos = sum(1 for v in months.values() if v > 0)

lot_rows = "".join(
    f"<tr><td>{l['asset']}</td><td class='num'>{l['weight']*100:.0f}%</td><td class='num'>{l['amount']:,.0f} 元</td>"
    f"<td class='num'>{l['price']:.3f}</td><td class='num'>{l['lots']} 手</td></tr>" for l in det["lots"])

HTML = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>2026 年内：5% 回撤下的最高收益方案</title>
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
<h1>2026 年内：5% 回撤约束下能拿到的最高收益</h1>
<div class="meta">区间 <strong>2026-01-05 ~ 2026-11-06</strong>（203 个交易日，2025 年数据预热）｜资产池 33 只 ETF｜
含 5bp 单边成本、月度再平衡、成交按再平衡额计费｜当日收盘决策、次日结算｜不构成投资建议</div>
</header>

<div class="lead">
<p><strong>结果：年内 <span style="font-size:20px">+{m['total']*100:.1f}%</span>，最大回撤 <strong>{m['maxdd']*100:.2f}%</strong>，年化波动 {m['vol']*100:.1f}%，夏普 {m['sharpe']:.2f}。</strong></p>
<p>配置：<strong>十年国债ETF 55% + 城投债ETF 15% + 原油ETF 15% + 纳指ETF 10% + 豆粕ETF 5%</strong>；
回撤 3% 触发熔断、全部仓位减半，回撤修复到 1.5% 再恢复。全年换手约 {(m['turnover_ann'])*100:.0f}%（主要来自熔断进出与月度再平衡）。</p>
<p>你的三条批评我都照做了：<strong>股票不限于 300、海外可以买、债券用 ETF 不用期货、期权可以单腿</strong>。放开之后结果比上一版高很多——上一版"样本内 8.1%"受窄资产池与期货腿限制，这次是 2026 年内实测 <strong>+{m['total']*100:.1f}%</strong>。</p>
<p>但要说清楚代价：<strong>收益有 92% 集中在上半年</strong>（{rob['h1']['total']*100:.1f}% vs 下半年 {rob['h2']['total']*100:.1f}%），6 月后基本不赚钱；而且这个配比是 8 个候选里挑出来的，带有选择偏差。</p>
</div>

<h2>一、2026 年发生了什么：33 只 ETF 的实测</h2>
<div class="card" style="max-height:460px;overflow:auto">
<table>
<tr><th>资产</th><th class="num">年内涨幅</th><th class="num">年内最大回撤</th><th class="num">年化波动</th><th class="num">日均成交(亿)</th></tr>
{perf_rows}
</table>
</div>
<p>三件事决定了今年的答案：</p>
<ol>
<li><strong>原油 +85.6%，但途中最大回撤 -43.1%、波动 68%。</strong>今年唯一的大赢家，也是唯一能把 5% 回撤撑爆的资产——只能小配 15%。</li>
<li><strong>A 股全线下跌</strong>：沪深300 -13.6%（回撤 -20.2%）、中证1000 -15.9%、创业板 -24.4%、半导体 -49.4%（回撤 -76.6%）。<strong>我把 A 股全谱系都放进了候选池，回测结果是它们全部拖累组合</strong>，不是我没考虑。</li>
<li><strong>债券是唯一的压舱石</strong>：十年国债 ETF +1.7%、回撤 -1.1%、波动 2%；城投债 +2.5%、回撤 -0.6%。70% 债券仓位贡献的波动不到 1.5%。</li>
</ol>
<div class="note"><strong>你说的"海外也可以买"确实关键。</strong>纳指 +11.8%、标普 +4.9%、日经 +9.9%，在 A 股跌 13% 的年份提供了正贡献；豆粕 +7.7%（回撤 -14.7%）与股债都不同步，也是有效的分散腿——它是最终组合里 5% 那一块。</div>

<h2>二、候选配比与结果</h2>
<table>
<tr><th>配比</th><th class="num">熔断</th><th class="num">年内收益</th><th class="num">最大回撤</th><th class="num">年化波动</th><th class="num">夏普</th><th>达标</th></tr>
{mx_rows}
</table>
<p><strong>规律很清楚：债券比例决定回撤，原油比例决定收益。</strong>原油配到 20%（如"80% 国债 + 20% 原油"）收益能到 19-20%，但回撤破 7%；把原油压到 15%、再加 5% 豆粕做分散，才能在 5% 以内拿到 17%+。</p>

<h2>三、推荐方案：月度表现与净值</h2>
<img src="data:image/png;base64,{b64('nav2026.png')}" alt="2026 年内净值与回撤">
<table>
<tr><th>月份</th>{month_head}</tr>
<tr><td>月度收益</td>{month_vals}</tr>
</table>
<p class="small">11 月为 11-01~11-06 的部分数据。11 个月里 {n_pos} 个月正收益，最大单月亏损 -2.0%（6 月）。3 月 +8.4% 是全年最大单月收益，来自原油与海外同步上涨。</p>
<table>
<tr><th>资产</th><th class="num">权重</th><th class="num">100 万本金</th><th class="num">最新价</th><th class="num">手数（100 份/手）</th></tr>
{lot_rows}
</table>

<h3>回撤为什么能压住：三件事，不是一件事</h3>
<ul>
<li><strong>70% 债券打底。</strong>两只债券 ETF 合计回撤不到 1.1%，把组合一半以上的波动来源直接消掉。</li>
<li><strong>30% 风险资产互不同步。</strong>原油（供给冲击）、纳指（AI/科技）、豆粕（农产品周期）今年走势各自独立，合起来的回撤远小于任一单腿。</li>
<li><strong>3% 熔断只做保险，不做收益。</strong>它把"不设熔断"的 -6.2% 压到 -4.1%，代价是少赚 0.2 个百分点——<strong>这是纯粹的保险支出，很便宜</strong>。全年触发几次、每次减半，换手因此升到 {m['turnover_ann']*100:.0f}%。</li>
</ul>

<h2>四、期权单腿：买也白买、卖更糟</h2>
<table>
<tr><th>方案</th><th class="num">年内收益</th><th class="num">最大回撤</th><th class="num">年化波动</th></tr>
{ov_rows}
</table>
<ul>
<li><strong>卖出波动率（跨式/宽跨）</strong>：名义 5% 就让收益从 +{m['total']*100:.1f}% 掉到 +11.3%，回撤从 -4.06% 恶化到 -5.68%；名义 10% 直接归零。2026 年有多次单日大波动，卖方被跳空打得很惨。<strong>把跳空惩罚假设从 4 倍降到 1 倍，结论仍然是负贡献。</strong></li>
<li><strong>买入看跌价差</strong>：名义 5%–20% 只把回撤从 4.06% 改善到 4.07%–4.22%（<em>反而略差</em>，因为成本），收益少 0.3–1.0 个百分点。<strong>这个组合的回撤不是股票尾部风险，买保险没有对应风险可保。</strong></li>
</ul>
<div class="note">期权真正有用的场景是：你已经接近 5% 回撤、又不想砍仓位时，临时买 1-2 个月的虚值保护过渡。常态卖出波动率在这个组合里是负贡献——想加收益应该调高商品/海外权重，只是回撤会破线。</div>

<h2>五、上限与稳健性</h2>
<table>
<tr><th>方案</th><th class="num">年内收益</th><th class="num">最大回撤</th><th>说明</th></tr>
<tr><td>事后最优静态配比（含未来信息、不含熔断）</td><td class="num">+15.5%</td><td class="num">-4.99%</td><td>原油 12.7%、纳指 9.6%、城投 50.7%、豆粕 15.6%…这是"年初就知道结果"的答案</td></tr>
<tr><td><strong>本报告推荐（可执行、含熔断）</strong></td><td class="num strong">+{m['total']*100:.1f}%</td><td class="num">{m['maxdd']*100:.2f}%</td><td>固定配比 + 月度再平衡 + 3% 熔断，没有预测</td></tr>
<tr><td>同一配比但不设熔断</td><td class="num">{rob['no_trigger']['total']*100:+.1f}%</td><td class="num">{rob['no_trigger']['maxdd']*100:.2f}%</td><td>熔断用 0.2pp 收益换 2.1pp 回撤</td></tr>
<tr><td>沪深300ETF 买入持有</td><td class="num">-13.6%</td><td class="num">-20.2%</td><td>今年 A 股是负贡献</td></tr>
<tr><td>原油 ETF 买入持有</td><td class="num">+85.6%</td><td class="num">-43.1%</td><td>收益最高、回撤远超 5%</td></tr>
</table>
<table>
<tr><th>稳健性检验</th><th class="num">收益</th><th class="num">回撤</th><th>判断</th></tr>
<tr><td>上半年（1-6 月）</td><td class="num">{rob['h1']['total']*100:+.1f}%</td><td class="num">{rob['h1']['maxdd']*100:.2f}%</td><td>收益主要来自这里</td></tr>
<tr><td>下半年（7-11 月）</td><td class="num">{rob['h2']['total']*100:+.1f}%</td><td class="num">{rob['h2']['maxdd']*100:.2f}%</td><td><strong>几乎走平</strong>，不是月月赚</td></tr>
<tr><td>成本 10bp</td><td class="num">{rob['cost10']['total']*100:+.1f}%</td><td class="num">{rob['cost10']['maxdd']*100:.2f}%</td><td>影响 0.1pp 以内</td></tr>
<tr><td>成本 20bp</td><td class="num">{rob['cost20']['total']*100:+.1f}%</td><td class="num">{rob['cost20']['maxdd']*100:.2f}%</td><td>影响 0.2pp 左右</td></tr>
</table>
<div class="warn"><strong>必须承认的三点：</strong>① 203 个交易日、11 个月，样本极短，+17.6% 是"今年这只兔子"，不能外推为长期年化；② 8 个候选配比里挑最好的那个，存在选择偏差，真实执行应按自己的风险偏好定权重（比如把原油降到 10%、债券加到 80%，收益会低 3-4pp 但更稳）；③ 收益高度集中在上半年，说明它吃到了 1-3 月的原油行情，这种行情不会年年有。</div>

<h2>六、怎么落地</h2>
<ol>
<li><strong>债券用 ETF，不建议买现券。</strong>交易所国债现券一手 10 万元面值、流动性远差于 ETF；511260（十年国债 ETF，日均 39 亿）、511220（城投债 ETF，日均 15 亿）买卖价差小。若不想承担城投信用风险，把 15% 城投换成 511010 国债 ETF。</li>
<li><strong>海外注意溢价。</strong>513100（纳指）、513500（标普500）常年溢价 1%-3%，限购时更高，别在溢价 2% 以上追；溢价高时可换 513520（日经）或 159941。</li>
<li><strong>原油只做小配。</strong>501018 受汇率、溢价、限购影响，今年振幅 68%；15% 已是上限，不要因为"今年涨了 85%"就加到 25%——那会让回撤破 7%。</li>
<li><strong>豆粕是分散腿，不是收益腿。</strong>159985 与股债相关性低，5% 的作用是降低组合波动，不要单独加仓。</li>
<li><strong>再平衡与熔断执行。</strong>每月最后一个交易日调回目标权重；组合回撤触及 3% 当日全部减半，修复到 1.5% 恢复。全年换手约 {m['turnover_ann']*100:.0f}%，10bp 成本也只影响 0.1pp，不必纠结交易成本。</li>
<li><strong>期权只在必要时用。</strong>真要保护，用 300ETF 期权买 3%-10% 虚值价差（名义 10% 约 2 张，1 张名义约 4.1 万元）；不建议常态卖出波动率。</li>
</ol>

<h2>七、口径与三类表述</h2>
<table>
<tr><th style="width:16%">类别</th><th>内容</th></tr>
<tr><td>实测</td><td>33 只 ETF 的 2026 年内日线（新浪财经）：A 股宽基/行业、纳指/标普/日经/恒生科技、原油/黄金/有色/豆粕/煤炭/钢铁、国债/城投/公司债 ETF；期权隐含波动率用 300ETF QVIX。区间 2026-01-05~2026-11-06，5bp 单边成本，月度再平衡，成交按再平衡金额计费。</td></tr>
<tr><td>工程假设</td><td>熔断阈值（3% 减半、1.5% 恢复）；月度再平衡频率；期权用"方差风险溢价 + 跳空惩罚"近似（无期权链报价）；买入看跌价差成本按名义 0.45%/月摊销、赔付 3%-10% 上限；豆粕 ETF 视为商品分散腿。</td></tr>
<tr><td>原文关联</td><td>本次不套用原文的 HMM/六 Agent 框架做决策——那套机制解释的是"原文怎么讲"，不是"今年怎么赚"。本报告是独立的固定配比 + 熔断实现。</td></tr>
</table>
<p class="small">数据缺口：美国 VIX 公开源被拒（403），用标普 20 日已实现波动率代理；原油 ETF 溢价与限购未建模；城投债信用风险未单独定价；ETF 分红按价格收益处理。</p>

<footer>
<p><strong>免责声明：</strong>本报告为历史数据回测与方法演示，收益与回撤均为模型输出，<strong>不构成投资建议</strong>，不构成对未来的收益承诺。2026 年内只有 203 个交易日，年内高收益不能当作长期能力；单一资产历史涨幅不代表未来。ETF 存在溢价与流动性风险，期权含杠杆，可能损失全部权利金或超过本金。</p>
<p>口径：2026-01-05 ~ 2026-11-06｜203 个交易日｜2025 年数据预热｜单边成本 5bp｜生成于 2026-11-08</p>
</footer>
</div></body></html>"""

open(OUT, "w", encoding="utf-8").write(HTML)
print("written", OUT, round(len(HTML) / 1024), "KB")