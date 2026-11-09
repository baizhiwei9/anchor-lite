# -*- coding: utf-8 -*-
"""生成第二轮 HTML：5% 回撤约束下的最优配置"""
import os, sys, json, base64
import pandas as pd, numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "5%回撤约束下的最优配置.html")

rob = pd.read_csv(os.path.join(BASE, "robust.csv"))
fr = pd.read_csv(os.path.join(BASE, "grid_frontier.csv"))
full = json.load(open(os.path.join(BASE, "full_window.json")))
tb = json.load(open(os.path.join(BASE, "trend_bench.json")))
rb = json.load(open(os.path.join(BASE, "robust.json")))
best_is = pd.read_csv(os.path.join(BASE, "best_is.csv"), index_col=0, parse_dates=True)

def b64(p):
    return base64.b64encode(open(os.path.join(BASE, "figs", p), "rb").read()).decode()

def pct(x, d=1):
    return f"{x*100:+.{d}f}%"

# 三档
g5 = full["5%档(vol7%+熔断+均线闸门)"]; g10 = full["上一版(vol10%)"]; g12 = full["放开(vol12%)"]
s5 = full["保守档(vol5%)"]
row = lambda name, m, extra="": (f"<tr><td>{name}</td><td class='num'>{m['cagr']*100:+.1f}%</td>"
                                f"<td class='num'>{m['maxdd']*100:.2f}%</td><td class='num'>{m['vol']*100:.1f}%</td>"
                                f"<td class='num'>{m['sharpe']:.2f}</td><td class='num'>{extra}</td></tr>")

# 前沿表：每个波动预算取 IS 收益最高的一组
fr_best = (fr.assign(_p=fr["pass5"].astype(int))
             .sort_values(["_p", "is_cagr"], ascending=[False, False])
             .groupby("vol_target").head(1).sort_values("vol_target"))
fr_rows = "".join(
    f"<tr><td class='num'>{r.vol_target*100:.0f}%</td><td class='num'>{r.is_cagr*100:.1f}%</td>"
    f"<td class='num'>{r.is_dd*100:.2f}%</td><td class='num'>{r.oos_cagr*100:.1f}%</td>"
    f"<td class='num'>{r.oos_dd*100:.2f}%</td><td class='num strong'>{r.worst_dd*100:.2f}%</td>"
    f"<td class='num'>{r.s2020*100:.1f}%</td><td class='num'>{r.s2022*100:.1f}%</td>"
    f"<td>{'✅ 全窗口 ≤5%' if r.pass5 else '❌ 破 5%'}</td></tr>"
    for r in fr_best.itertuples())

rob_rows = "".join(
    f"<tr><td>{r.label}</td><td class='num'>{r.is_cagr*100:+.1f}%</td><td class='num'>{r.is_dd*100:.2f}%</td>"
    f"<td class='num'>{r.oos_cagr*100:+.1f}%</td><td class='num'>{r.oos_dd*100:.2f}%</td>"
    f"<td class='num'>{r.is_sharpe:.2f}</td></tr>"
    for r in rob.itertuples())

bench_rows = "".join(
    f"<tr><td>{k}</td><td class='num'>{v['cagr']*100:+.1f}%</td><td class='num'>{v['maxdd']*100:.1f}%</td>"
    f"<td class='num'>{v['vol']*100:.1f}%</td>"
    f"<td class='num'>{rb['bench_oos'][k]['cagr']*100:+.1f}%</td><td class='num'>{rb['bench_oos'][k]['maxdd']*100:.1f}%</td></tr>"
    for k, v in rb["bench_is"].items())

tb_rows = "".join(
    f"<tr><td>简单趋势基准（{float(k)*100:.0f}% 波动预算）</td><td class='num'>{tb[f'FULL_{k}']['cagr']*100:+.1f}%</td>"
    f"<td class='num'>{tb[f'FULL_{k}']['maxdd']*100:.2f}%</td>"
    f"<td class='num'>{tb[f'IS_{k}']['cagr']*100:+.1f}%</td><td class='num'>{tb[f'OOS_{k}']['cagr']*100:+.1f}%</td></tr>"
    for k in ("0.04", "0.05", "0.07", "0.1"))

last = best_is.iloc[-1]
w = {f"w_{c}": float(last[f"w_{c}"]) for c in ["kc50", "hs300", "oil", "nf", "t10", "sv"]}
px = {"w_kc50": 1.479, "w_hs300": 4.712, "w_oil": 2.266, "w_nf": 2.136}
def lots(cap, k):
    amt = cap * w[k]
    return amt, int(amt / px[k] / 100)
cap1 = 1_000_000
lot_rows = "".join(
    f"<tr><td>{ {'w_kc50':'科创50ETF 588000','w_hs300':'沪深300ETF 510300','w_oil':'原油ETF 501018','w_nf':'有色ETF 512400'}[k] }</td>"
    f"<td class='num'>{w[k]*100:.2f}%</td><td class='num'>{lots(cap1,k)[0]:,.0f} 元</td>"
    f"<td class='num'>{lots(cap1,k)[1]} 手</td></tr>"
    for k in ["w_kc50", "w_hs300", "w_oil", "w_nf"])
lot_rows += (f"<tr><td>10 年国债期货 T（空）</td><td class='num'>{w['w_t10']*100:.2f}%</td>"
             f"<td class='num'>{cap1*w['w_t10']:,.0f} 元名义</td><td class='num'>0 手（1 手 ≈108.5 万，不足 1 手）</td></tr>")
lot_rows += (f"<tr><td>做空波动率价差（配对）</td><td class='num'>{w['w_sv']*100:.2f}%</td>"
             f"<td class='num'>0 元</td><td class='num'>0 组（低波概率仅 3.8%，闸门关闭）</td></tr>")

HTML = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>5% 回撤约束下，这套系统能给你的最好状态</title>
<style>
:root{{--ink:#12161C;--sub:#5A6472;--line:#E3E7EE;--bg:#F7F8FA;--accent:#1F3A5F;--warn:#B45309;--red:#C0392B}}
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
<h1>5% 回撤约束下，这套系统能给你的最好状态</h1>
<div class="meta">数据截止 <strong>2026-04-14</strong>｜全区间连续回测 2018-06-01 ~ 2026-04-14（1829 个交易日）｜含 5bp 单边成本｜
所有决策只用当日及以前信息，权重作用于次日收益｜本文件中的数字全部由本地脚本实算</div>
</header>

<div class="lead">
<p><strong>直接回答你的三个问题。</strong></p>
<p><strong>① 能不能达到原文那个水准？</strong>原文正文没有披露收益率，只写了“整体表现平稳”，-4.39% 是图表口径。所以无法对标“他的收益”，只能对标“同等回撤下我能做到多少”。</p>
<p><strong>② 5% 回撤下我能做到多少？</strong>真正把全窗口压在 5% 以内的是保守档（5% 波动预算）：<strong>全区间年化 {s5['cagr']*100:.1f}%、回撤 {s5['maxdd']*100:.1f}%</strong>；推荐的 7% 档全区间是<strong>年化 {g5['cagr']*100:.1f}%、回撤 {g5['maxdd']*100:.1f}%</strong>（略微超线）；只看 2024-01 之后则是<strong>年化 8.1%、回撤 3.7%</strong>。想守住 5%，代价就是收益掉到个位数低段。</p>
<p><strong>③ 他那种 15%+ 收益、5% 回撤的组合存在吗？</strong>只在 2024-2026 这段样本里存在（年化 15.5%、回撤 5.5%）。同一套规则放到 2020 年 1-5 月会打出 <strong>-10.4%</strong>——因为那时它正拿着 20% 原油、19% 有色、21% 的国债空头，而 HMM 到 3 月才切到危机状态。<strong>回撤的“丝滑”是样本期的运气 + 低敞口，不是能力。</strong></p>
</div>

<h2>一、先把“5% 回撤”定义清楚，否则数字没有意义</h2>
<p>同一套规则，换个回撤口径，收益能差 5 倍。我按三种口径分别算了，先摆出来：</p>
<table>
<tr><th style="width:30%">口径</th><th class="num">年化</th><th class="num">最大回撤</th><th class="num">年化波动</th><th class="num">夏普</th><th>说明</th></tr>
{row("① 只看 2024-01 ~ 2026-04（样本内）", {"cagr":0.1554,"maxdd":-0.0550,"vol":0.0761,"sharpe":2.04}, "10% 波动预算")}
{row("② 全区间连续 2018-06 ~ 2026-04（真实持仓不重置）", g10, "10% 波动预算")}
{row("③ 全区间 + 熔断（5% 档）", g5, "7% 波动预算 + 2% 熔断")}
<tr><td>④ 参考：沪深300ETF 买入持有（全区间）</td><td class="num">+3.1%</td><td class="num">-45.1%</td><td class="num">20.2%</td><td class="num">0.15</td><td>同期最大回撤是策略的 8 倍</td></tr>
</table>
<p class="small">口径②③ 是“一笔钱从 2018 年一直拿着”，不在每年重置仓位；口径① 是上一份报告用的窗口。压力测试（2020-03 等）也从空仓起步，会系统性低估真实伤害——这一点在第五节单独说。</p>

<h2>二、波动预算 → 收益/回撤前沿（这是你真正要的那张表）</h2>
<img src="data:image/png;base64,{b64('frontier.png')}" alt="波动预算前沿">
<table>
<tr><th class="num">波动预算</th><th class="num">样本内年化</th><th class="num">样本内回撤</th><th class="num">样本外年化</th><th class="num">样本外回撤</th><th class="num">全窗口最差回撤</th><th class="num">2020 短窗</th><th class="num">2022 短窗</th><th>是否守住 5%</th></tr>
{fr_rows}
</table>
<p>读法：<strong>波动预算每加 1 个百分点，样本内年化大约加 2 个百分点，但全窗口最差回撤也同步放大</strong>。4%–7% 预算（配合 2% 熔断）基本能守住全窗口 5%，样本内年化 5.9%–8.1%；8% 起没有任何一组参数能守住，最差回撤直接跳到 -8.5% 以上。</p>
<p class="small">表里 6% 档被标成不通过，是因为它的最差回撤是 <strong>-5.03%</strong>，比 5% 差了 0.03 个百分点——这种“贴线判负”恰好说明：<strong>5% 这个目标在这套策略上没有安全边际</strong>，换个窗口、多 1bp 成本就可能翻盘。</p>
<div class="note"><strong>杠杆不是瓶颈。</strong>我做了 gross 上限 1.1 与 1.6 的对照，结果完全一致——因为实际缩放系数只有 0.26–0.40，总敞口最高 77%，从来没顶到上限。<strong>制约你的不是能不能加杠杆，而是这套规则的风险效率（夏普 0.6–0.8）不够高。</strong></div>

<h2>三、推荐配置：5% 档的三个参数</h2>
<table>
<tr><th style="width:24%">参数</th><th class="num">取值</th><th>为什么是这个值</th></tr>
<tr><td>波动预算</td><td class="num">7%（贴线）<br>5.5%（有缓冲）</td><td>7% 的样本内年化最高（8.1%），但全窗口最差回撤 -4.99%，<strong>等于没有缓冲</strong>；5.5% 的分段最差回撤 -4.9%、样本内 7.2%，少赚 0.9pp 换 0.1pp 缓冲。实战建议 5.5%，但要清楚：<strong>连续口径下它的回撤仍是 -5.7%</strong></td></tr>
<tr><td>回撤熔断</td><td class="num">回撤 2% → 仓位砍半</td><td>触发太松（3%）会在 2020 年被打穿；太紧（1%）会被日常噪声反复触发、成本吃掉收益</td></tr>
<tr><td>商品集中度上限</td><td class="num">25%</td><td>原油+有色是主要风险源（风险贡献 85%+），限到 25% 能把 2020 式商品崩塌的伤害减半</td></tr>
<tr><td>60 日均线闸门</td><td class="num">开（商品与债券空头）</td><td>价格跌破 60 日线时把商品目标砍到 25%；债券在涨势里不做空。这一条在 10% 波动档上单独测得样本内 +1.3pp、样本外 +1.0pp</td></tr>
<tr><td>期权尾部对冲</td><td class="num">关</td><td>实测是负贡献，见第六节</td></tr>
</table>
<table>
<tr><th>版本</th><th class="num">样本内年化</th><th class="num">样本内回撤</th><th class="num">样本外年化</th><th class="num">样本外回撤</th><th class="num">夏普</th></tr>
{rob_rows}
</table>
<img src="data:image/png;base64,{b64('full_nav.png')}" alt="全区间净值与回撤">

<h2>四、为什么 5% 守不住：2020 年 1-5 月的解剖</h2>
<p>10% 波动档在 2020-01-07 → 2020-05-21 的 85 个交易日里回撤 <strong>-10.4%</strong>。当时它拿着什么：</p>
<table>
<tr><th>腿</th><th class="num">1 月平均仓位</th><th>当时发生了什么</th></tr>
<tr><td>原油</td><td class="num">约 20%</td><td>COVID 需求崩塌 + 价格战，油价从 65 美元跌到 20 美元以下</td></tr>
<tr><td>有色</td><td class="num">约 19%</td><td>同步下跌</td></tr>
<tr><td>国债期货（空）</td><td class="num">约 -21%</td><td>避险资金涌入债券，收益率暴跌，空头亏钱</td></tr>
<tr><td>股票</td><td class="num">约 15%</td><td>2-3 月补跌</td></tr>
</table>
<p><strong>四条腿同时逆风，而 HMM 的危机概率当时还只有 3.8%</strong>（到 3 月中才升到 73%）。这不是参数没调好，是“通胀体制里做多商品、做空债券”这套逻辑在需求崩塌型危机里天然会亏——它防的是通胀，不是防衰退。</p>
<div class="warn"><strong>两个必须说清的方法论问题：</strong><br>
① 压力测试（2020-03 短窗 -1.7%）从空仓起步，会低估伤害；真实连续持仓是 -10.4%。看结论请用连续回测，不要用短窗。<br>
② 5% 档在全区间连续回测里实际打到了 <strong>{g5['maxdd']*100:.1f}%</strong>，严格说也没守住 5%。真正守住 5% 的是保守档（5% 预算，{s5['maxdd']*100:.1f}%），代价是年化 {s5['cagr']*100:.1f}%。</div>

<h2>五、期权能不能加分：卖也亏、买也亏</h2>
<table>
<tr><th>方案</th><th class="num">样本内年化</th><th class="num">样本内回撤</th><th class="num">样本外年化</th><th class="num">样本外回撤</th><th>结论</th></tr>
<tr><td>不加期权（推荐）</td><td class="num">+8.1%</td><td class="num">-3.72%</td><td class="num">+0.5%</td><td class="num">-4.99%</td><td>基准</td></tr>
<tr><td>加 20% 尾部对冲（买 3%–10% 价差）</td><td class="num">+4.9%</td><td class="num">-3.75%</td><td class="num">-0.6%</td><td class="num">-6.49%</td><td><strong>回撤没降、收益少 3.2pp</strong>：月度成本约 0.45% 名义，一年 5%+，赔付很少触发</td></tr>
<tr><td>卖波动率价差（低波体制才开）</td><td class="num">贡献 −0.06pp</td><td class="num">−5.77%（vs −5.90%）</td><td class="num">—</td><td class="num">—</td><td>全区间只有 298 个交易日（16%）允许开仓；carry 基本被跳空惩罚吃掉，只换来 0.13pp 的回撤改善</td></tr>
</table>
<p><strong>结论：想降回撤，降波动预算比买保险有效得多。</strong>期权在你的组合里更像是“成本项”而不是“风控项”——除非你能拿到极便宜的虚值保护（比如券商收益互换或结构化产品），否则不建议为了 5% 目标去买 put。</p>

<h2>六、Regime 层到底贡献了什么：和“简单趋势”对照</h2>
<table>
<tr><th>方案（全区间连续）</th><th class="num">年化</th><th class="num">最大回撤</th><th class="num">样本内年化</th><th class="num">样本外年化</th></tr>
<tr><td>Anchor-Lite 5% 档（HMM + 概率加权 + 熔断）</td><td class="num">{g5['cagr']*100:+.1f}%</td><td class="num">{g5['maxdd']*100:.1f}%</td><td class="num">+8.1%</td><td class="num">+0.5%</td></tr>
{tb_rows}
</table>
<p>简单趋势（涨就拿、跌就空仓 + 同样波动预算）的收益略高，但回撤也更大。所以 <strong>HMM 层的主要价值是压回撤、不是提收益</strong>：它把 -6.4% 的回撤压到 -5.8%，代价是少赚一点。如果你只想少赔钱、不在乎收益差 1pp，其实更简单的趋势规则就够了。</p>

<h2>七、散户能落地吗：手数与合约的现实约束</h2>
<p>按推荐配置 2026-04-13 的最新仓位（当前处于熔断半仓状态，缩放系数 0.40）：</p>
<table>
<tr><th>标的</th><th class="num">权重</th><th class="num">100 万本金对应金额</th><th class="num">手数</th></tr>
{lot_rows}
</table>
<div class="warn"><strong>最关键的落地问题：国债期货根本没法按 5% 的精度用。</strong>10 年期国债期货 T 合约 1 手面值 100 万元、按 108.5 报价名义约 108.5 万元；你要的空头只有 1.45% ≈ 1.45 万元。<strong>1 手就等于把 108% 的净值压上去</strong>，最小交易单位比目标仓位大约 75 倍。100 万以下账户的现实选择是：<br>
① 放弃债券空头，只用“商品 + 股票”两腿（我测过：样本内 7.0%、样本外 1.0%，几乎无差）；<br>
② 用国债 ETF 做多头替代（通胀体制里少配债券）；<br>
③ 账户做到 500 万以上，1 手 T 约 21.7% 名义，才谈得上“可调”，但仍很粗。</div>
<p class="small">另外三条现实成本：ETF 是 T+1，日内信号只能次日执行；501018 原油 ETF 长期有溢价（限购时会到 3%+），实际买入要按溢价折价；日均换手约 1.7%（年化约 430%），5bp 成本约拖累 1.4pp/年，10bp 时拖累 2.8pp——这是这套策略最脆弱的地方之一。</p>

<h2>八、稳健性与成本敏感性</h2>
<table>
<tr><th>情形</th><th class="num">样本内年化</th><th class="num">样本内回撤</th><th class="num">样本外回撤</th><th>判断</th></tr>
<tr><td>基准（5bp 成本）</td><td class="num">+8.1%</td><td class="num">-3.72%</td><td class="num">-4.99%</td><td>刚好压线</td></tr>
<tr><td>成本抬到 10bp</td><td class="num">+6.7%</td><td class="num">-3.73%</td><td class="num">-5.17%</td><td><strong>破线</strong>：成本翻倍就守不住，说明缓冲很薄</td></tr>
<tr><td>波动预算 −1pp（6%）</td><td class="num">+8.0%</td><td class="num">-3.19%</td><td class="num">-5.02%</td><td>回撤更好、收益几乎不变——说明 6% 比 7% 更划算</td></tr>
<tr><td>波动预算 +1pp（8%）</td><td class="num">+7.8%</td><td class="num">-4.25%</td><td class="num">-5.93%</td><td>收益反而降、回撤升，不划算</td></tr>
<tr><td>商品上限放到 30%</td><td class="num">+7.6%</td><td class="num">-3.77%</td><td class="num">-5.52%</td><td>也不划算</td></tr>
</table>
<p>邻域很平：波动预算在 6%–8% 之间，样本内年化都在 7.8%–8.1%，回撤 3.2%–4.3%。<strong>这说明结果不是挑参数挑出来的</strong>，但也说明这套东西的收益天花板就在 8% 附近（样本内）。</p>

<h2>九、参照系：不买这套，你会拿到什么</h2>
<table>
<tr><th>买入持有（样本内 / 样本外）</th><th class="num">样本内年化</th><th class="num">样本内回撤</th><th class="num">样本内波动</th><th class="num">样本外年化</th><th class="num">样本外回撤</th></tr>
{bench_rows}
</table>
<p>样本外（2018-06 ~ 2023-12）沪深300ETF 年化 -4.1%、回撤 -46.6%；策略同期 +0.5%、回撤 -5.0%。<strong>绝对收益很难看，但相对价值很大</strong>——这套东西的意义不是“赚得多”，而是“在别人亏 46% 的时候你只回撤 5%”。如果你的目标是绝对收益，它可能不适合你；如果你要的是“睡得着”，它是可用的。</p>

<h2>十、所以“你能拿到的最好状态”到底是</h2>
<div class="card">
<p><strong>结论一句话：</strong>在“全区间 5% 回撤”这个硬约束下，我能给你的最好状态是<strong>年化 2.5%–8%</strong>（实战建议用 5.5% 波动预算：样本内 7.2%、全区间连续 2.7%、回撤 -5.7%），取决于你把哪段历史算进“回撤”；平均下来（2018-2026 七年九个月）是<strong>年化 {g5['cagr']*100:.1f}%、最大回撤 {g5['maxdd']*100:.1f}%、年化波动 {g5['vol']*100:.1f}%</strong>。这不是“他那种丝滑 + 高收益”，而是“用极低敞口换来的低波动”。</p>
<p>如果你能接受把回撤放宽到 8%–11%：年化 {g10['cagr']*100:.1f}%、回撤 {g10['maxdd']*100:.1f}%（全区间）；只对最近两年承诺 5%：年化 15.5%、回撤 5.5%。</p>
</div>

<h2>十一、局限与声明</h2>
<ul class="small">
<li><strong>样本短：</strong>全区间只有 7.9 年、1830 个交易日，其中 2024-2026 是商品与风险资产普涨的窗口；2018-2023 商品震荡、2022 债券熊市，策略在这段几乎没有收益。任何“年化”都要打折看。</li>
<li><strong>回撤口径敏感：</strong>同一规则在“窗口重置”和“连续持仓”下差 4pp 以上；压力测试从空仓起步会低估伤害，本报告以连续回测为准。</li>
<li><strong>期权是模型近似：</strong>没有期权链数据，做空波动率用方差风险溢价近似、尾部对冲用“月度成本 + 3%–10% 赔付”结构近似，不是真实成交价。</li>
<li><strong>数据缺口：</strong>美国 VIX 三个公开源均被拒（403），用标普 20 日已实现波动率代理；信用利差接口无返回，未入模。</li>
<li><strong>落地摩擦未完全建模：</strong>原油 ETF 溢价、ETF 申赎限购、期货保证金随波动调整、期权保证金占用，都只做了定性提示。</li>
</ul>

<footer>
<p><strong>免责声明：</strong>本报告为历史数据回测与方法研究，全部收益、回撤与仓位均为模型输出，<strong>不构成投资建议</strong>，也不构成对未来收益的承诺。期货与期权含杠杆，可能损失超过本金；历史回测不代表未来表现。</p>
<p>口径：数据截止 2026-04-14｜连续回测 2018-06-01~2026-04-14｜单边成本 5bp｜当日决策、次日结算｜生成于 2026-11-08</p>
</footer>
</div></body></html>"""

open(OUT, "w", encoding="utf-8").write(HTML)
print("written", OUT, round(len(HTML) / 1024), "KB")