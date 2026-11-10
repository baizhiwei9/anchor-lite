#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成扩展池对比报告 v2（自包含、内联 SVG）。"""
import json
import os
import pandas as pd
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
HTML = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e424df3c0ffepkEhgMkuPETfQj/扩展池与5%回撤约束对比报告.html"

M = json.load(open(os.path.join(OUT, "metrics_opt.json"), encoding="utf-8"))
A = json.load(open(os.path.join(OUT, "results_ext.json"), encoding="utf-8"))
NAV = pd.read_csv(os.path.join(OUT, "nav_opt.csv"), index_col=0)
NAV2 = pd.read_csv(os.path.join(OUT, "nav_ext2.csv"), index_col=0)
for _df in (NAV, NAV2):
    _df.index = pd.to_datetime(_df.index, errors="coerce")
NAV = NAV[NAV.index.notna()].ffill()
NAV2 = NAV2[NAV2.index.notna()].ffill()

names = {"core": "核心 13 池（MLP/CNN）", "core_braked": "核心 + 波动目标10% + 熔断",
         "combined": "70%核心 + 30%卫星（原油/黄金/国债/低波）",
         "A_nasdaq": "扩展A：80%核心 + 20%卫星（+纳指）",
         "B_2025": "扩展B：2025 起加豆粕/行业ETF"}
colors = {"core": "#1F6F8B", "core_braked": "#7A8B99", "combined": "#B03A2E",
          "A_nasdaq": "#C08A2E", "B_2025": "#3E7C4A"}


def svg_lines(df, w=980, h=340, pad=(56, 16, 26, 46), zero=False):
    L, T, R, B = pad
    iw, ih = w - L - R, h - T - B
    vals = df.values.astype(float)
    ymin, ymax = float(np.nanmin(vals)), float(np.nanmax(vals))
    if zero:
        ymin, ymax = min(ymin, 0.0), max(ymax, 0.0)
    span = (ymax - ymin) or 1.0
    ymin -= span * 0.10; ymax += span * 0.10; span = ymax - ymin
    n = len(df)
    parts = [f'<svg viewBox="0 0 {w} {h}" width="100%" xmlns="http://www.w3.org/2000/svg">',
             f'<rect x="0" y="0" width="{w}" height="{h}" fill="#fff"/>']
    for k in range(5):
        v = ymin + span * k / 4
        yy = T + ih * (1 - (v - ymin) / span)
        parts.append(f'<line x1="{L}" y1="{yy:.1f}" x2="{L+iw}" y2="{yy:.1f}" stroke="#e6e9ee"/>')
        lab = f"{v*100:.0f}%" if zero else f"{v:.2f}"
        parts.append(f'<text x="{L-8}" y="{yy+4:.1f}" font-size="11" fill="#6b7280" text-anchor="end">{lab}</text>')
    for i, col in enumerate(df.columns):
        pts = " ".join(f"{L+iw*j/max(n-1,1):.1f},{T+ih*(1-(v-ymin)/span):.1f}"
                       for j, v in enumerate(df[col].values) if np.isfinite(v))
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{colors.get(col,"#888")}" stroke-width="1.6"/>')
        lx = L + 8 + i * 250
        parts.append(f'<rect x="{lx}" y="{h-14}" width="9" height="9" fill="{colors.get(col,"#888")}"/>')
        parts.append(f'<text x="{lx+14}" y="{h-6}" font-size="11" fill="#374151">{names.get(col,col)}</text>')
    if hasattr(df.index, "year"):
        for frac in (0.0, 0.33, 0.66, 1.0):
            xi = int((n - 1) * frac)
            lab = f"{df.index[xi]:%Y-%m}"
            parts.append(f'<text x="{L+iw*frac:.1f}" y="{T+ih+16:.1f}" font-size="10.5" fill="#6b7280" '
                         f'text-anchor="{"start" if frac==0 else ("end" if frac==1 else "middle")}">{lab}</text>')
    return "".join(parts) + "</svg>"


def svg_bars(years, series, w=980, h=320, pad=(56, 20, 26, 46)):
    L, T, R, B = pad
    iw, ih = w - L - R, h - T - B
    allv = [v for s in series.values() for v in s]
    ymin, ymax = min(min(allv), 0.0), max(max(allv), 0.0)
    span = (ymax - ymin) or 1.0
    y0 = T + ih * (1 - (0 - ymin) / span)
    n, k = len(years), len(series)
    gw, bw = iw / n, iw / n * 0.3
    parts = [f'<svg viewBox="0 0 {w} {h}" width="100%" xmlns="http://www.w3.org/2000/svg">',
             f'<rect x="0" y="0" width="{w}" height="{h}" fill="#fff"/>',
             f'<line x1="{L}" y1="{y0:.1f}" x2="{L+iw}" y2="{y0:.1f}" stroke="#9ca3af"/>']
    for k in range(4):
        vv = ymin + span * k / 3
        yy = T + ih * (1 - (vv - ymin) / span)
        parts.append(f'<line x1="{L}" y1="{yy:.1f}" x2="{L+iw}" y2="{yy:.1f}" stroke="#eceff3"/>')
        parts.append(f'<text x="{L-8}" y="{yy+4:.1f}" font-size="10.5" fill="#6b7280" text-anchor="end">{vv:.0f}%</text>')
    for i, y in enumerate(years):
        cx = L + gw * (i + 0.5)
        parts.append(f'<text x="{cx:.1f}" y="{h-24}" font-size="12" fill="#374151" text-anchor="middle">{y}</text>')
        for j, (nm, arr) in enumerate(series.items()):
            v = arr[i] / span * ih
            x0 = cx - k * bw / 2 + j * bw
            if arr[i] >= 0:
                parts.append(f'<rect x="{x0:.1f}" y="{y0-v:.1f}" width="{bw-3:.1f}" height="{max(v,0):.1f}" fill="{colors[nm]}"/>')
            else:
                parts.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{bw-3:.1f}" height="{max(-v,0):.1f}" fill="{colors[nm]}"/>')
    for j, nm in enumerate(series):
        lx = L + 8 + j * 250
        parts.append(f'<rect x="{lx}" y="{h-14}" width="9" height="9" fill="{colors[nm]}"/>')
        parts.append(f'<text x="{lx+14}" y="{h-6}" font-size="11" fill="#374151">{names.get(nm,nm)}</text>')
    return "".join(parts) + "</svg>"


def svg_hbars(items, w=980, h=None):
    h = h or (70 + 30 * len(items))
    LB, RB = 250, 30          # 左侧标签栏、右侧余量
    plot_l, plot_w = LB, w - LB - RB
    zero = plot_l + plot_w * 0.15
    maxbw = plot_w * 0.78
    maxv = max(abs(v) for _, v in items) or 1.0
    parts = [f'<svg viewBox="0 0 {w} {h}" width="100%" xmlns="http://www.w3.org/2000/svg">',
             f'<rect x="0" y="0" width="{w}" height="{h}" fill="#fff"/>',
             f'<line x1="{zero:.1f}" y1="18" x2="{zero:.1f}" y2="{h-18}" stroke="#9ca3af"/>']
    for i, (k, v) in enumerate(items):
        y = 40 + i * 30
        bw = abs(v) / maxv * maxbw
        if v >= 0:
            parts.append(f'<rect x="{zero:.1f}" y="{y-10}" width="{bw:.1f}" height="18" fill="#B03A2E"/>')
            tx, anchor = zero + bw + 6, "start"
        else:
            parts.append(f'<rect x="{zero-bw:.1f}" y="{y-10}" width="{bw:.1f}" height="18" fill="#2F6B4F"/>')
            tx, anchor = zero - bw - 6, "end"
        parts.append(f'<text x="{LB-10}" y="{y+5}" font-size="12" fill="#374151" text-anchor="end">{k}</text>')
        parts.append(f'<text x="{tx:.1f}" y="{y+5}" font-size="11" fill="#4b5563" text-anchor="{anchor}">{v*100:+.1f}%</text>')
    return "".join(parts) + "</svg>"


nav = NAV.ffill().bfill()
nav = nav / nav.iloc[0]
navA = NAV2["A_nasdaq"].ffill().bfill()
navA = navA / navA.iloc[0]
navCombine = pd.concat([nav[["core", "combined"]], navA.rename("A_nasdaq")], axis=1, join="inner").dropna()
dd = navCombine / navCombine.cummax() - 1
yrA = sorted(A["A"]["metrics"]["yearly"].keys())
bars = svg_bars(yrA, {"A_nasdaq": [A["A"]["metrics"]["yearly"][y] * 100 for y in yrA],
                      "core": [M["core"]["yearly"][y] * 100 for y in yrA]})
contribA = sorted(A["A"]["contrib"].items(), key=lambda x: -abs(x[1]))
hbars = svg_hbars([(k, v) for k, v in contribA])


def pc(x): return f"{x*100:+.2f}%"


def prow(label, m):
    return (f"<tr><td>{label}</td><td class='num'>{pc(m['total'])}</td><td class='num'>{pc(m['ann'])}</td>"
            f"<td class='num'>{m['dd']*100:.2f}%</td><td class='num'>{m['sharpe']:.2f}</td>"
            f"<td class='num'>{m['win']*100:.1f}%</td></tr>")


html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>扩展池与 5% 回撤约束对比报告</title>
<style>
body{{margin:0;background:#f6f7f9;color:#1f2937;font-family:-apple-system,"Noto Sans SC","PingFang SC","Microsoft YaHei",sans-serif;line-height:1.65}}
.wrap{{max-width:1080px;margin:0 auto;padding:28px 24px 56px;background:#fff}}
h1{{font-size:25px;margin:0 0 6px}} h2{{font-size:18px;margin:30px 0 10px;padding-left:10px;border-left:4px solid #B03A2E}}
h3{{font-size:15px;margin:18px 0 8px;color:#374151}}
.meta{{color:#6b7280;font-size:13px;margin-bottom:16px}}
table{{width:100%;border-collapse:collapse;margin:10px 0 16px;font-size:13px}}
th,td{{border:1px solid #e5e7eb;padding:7px 9px;text-align:left}} th{{background:#f3f4f6;font-weight:600}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}}
.chart{{border:1px solid #e5e7eb;padding:10px;margin:12px 0 4px;background:#fff}}
.note{{font-size:12.5px;color:#6b7280;margin:4px 0 16px}}
.box{{background:#fbfaf7;border:1px solid #eee7dd;padding:14px 16px;margin:14px 0;font-size:13.5px}}
ul{{padding-left:20px}} li{{margin:4px 0}} code{{background:#f3f4f6;padding:1px 5px;border-radius:3px;font-size:12.5px}}
</style></head><body><div class="wrap">
<h1>扩展池与 5% 回撤约束对比报告</h1>
<div class="meta">主窗口 {A['A']['window'][0]} ~ {A['A']['window'][1]}（1145 个交易日）｜扩展B 窗口 {A['B']['window'][0]} ~ {A['B']['window'][1]}｜
日频、T 日信号 T+1 生效、单边 5bp、1x 无杠杆、只多+空仓、regime 等权净值 MA200 闸门｜生成于 {pd.Timestamp.now():%Y-%m-%d %H:%M}</div>

<div class="box"><b>结论：</b>在账户级最大回撤 ≤5% 的硬约束下，把「核心 13 池 MLP/CNN 信号」与「卫星腿（原油、黄金、10 年国债、低波、<b>纳指</b>）」
按 80/20 混合、波动目标 10%，全区间做到 <b>累计 {pc(A['A']['metrics']['total'])}</b>、<b>年化 {pc(A['A']['metrics']['ann'])}</b>、
最大回撤 <b>{A['A']['metrics']['dd']*100:.2f}%</b>、夏普 <b>{A['A']['metrics']['sharpe']:.2f}</b>。
再加<b>豆粕与行业 ETF</b>（半导体/新能源/军工/医药，数据自 2025 年起）的短窗口版本为 {pc(A['B']['metrics']['total'])} / 年化 {pc(A['B']['metrics']['ann'])} / 回撤 {A['B']['metrics']['dd']*100:.2f}%。</div>

<h2>一、五套组合并列</h2>
<table><thead><tr><th>组合</th><th class="num">累计收益</th><th class="num">年化</th><th class="num">最大回撤</th><th class="num">夏普</th><th class="num">日胜率</th></tr></thead><tbody>
{prow(names['core'], M['core'])}
{prow(names['core_braked'], M['core_braked'])}
{prow(names['combined'], M['combined'])}
{prow(names['A_nasdaq'], A['A']['metrics'])}
{prow(names['B_2025'], A['B']['metrics'])}
</tbody></table>
<div class="note">核心=13 只 ETF 的 0.7×MLP+0.3×CNN 信号，含 5bp 成本；卫星腿=价格 &gt; MA60 时按 60 日波动率倒数加权持有，日频再平衡，扣 5bp。
扩展B 仅覆盖 2025-01-02 起，不能与 2022 起的四套直接比较。</div>

<h2>二、资金曲线</h2>
<div class="chart">{svg_lines(navCombine)}</div>
<div class="note">归一化起点=1；扩展A 与核心、70/30 组合同窗口可比。</div>

<h2>三、回撤曲线</h2>
<div class="chart">{svg_lines(dd, zero=True)}</div>
<div class="note">核心全区间最深 {M['core']['dd']*100:.2f}%（略超 5%），扩展A {A['A']['metrics']['dd']*100:.2f}%，70/30 组合 {M['combined']['dd']*100:.2f}%。</div>

<h2>四、逐年收益（扩展A vs 核心）</h2>
<div class="chart">{bars}</div>
<div class="note">扩展A 逐年：{ "、".join(f"{y} {A['A']['metrics']['yearly'][y]*100:+.1f}%" for y in yrA) }，五年全部为正。</div>

<h2>五、扩展A 卫星腿贡献（累计，未缩放前）</h2>
<div class="chart">{hbars}</div>
<div class="note">黄金是最大贡献来源（+{A['A']['contrib']['gold']*100:.1f}%），纳指 +{A['A']['contrib']['nasdaq']*100:.1f}%、原油 +{A['A']['contrib']['oil']*100:.1f}%、
10 年国债 +{A['A']['contrib']['bond10']*100:.1f}%、低波 {A['A']['contrib']['lowvol']*100:+.1f}%（负贡献）。扩展B：半导体 +{A['B']['contrib']['semiconductor']*100:.1f}%、
新能源 +{A['B']['contrib']['newenergy']*100:.1f}%、豆粕 {A['B']['contrib']['soybean']*100:+.1f}%、军工 +{A['B']['contrib']['military']*100:.1f}%、医药 +{A['B']['contrib']['pharma']*100:.1f}%。</div>

<h2>六、结论与口径</h2>
<h3>6.1 关键结论</h3>
<ul>
<li>加纳指后组合最优从 {pc(M['combined']['total'])}/{M['combined']['dd']*100:.2f}% 提升到 <b>{pc(A['A']['metrics']['total'])}/{A['A']['metrics']['dd']*100:.2f}%</b>，夏普 {M['combined']['sharpe']:.2f} → {A['A']['metrics']['sharpe']:.2f}，收益提升 {(A['A']['metrics']['total']-M['combined']['total'])*100:.1f}pp。</li>
<li>纯核心不加约束时回撤 {M['core']['dd']*100:.2f}% 已略超 5%；要压进 5% 必须降低目标波动（15%→10%）并掺入低波资产。</li>
<li>豆粕与行业细分 ETF 只在 2025 年后有数据，短窗口版（{pc(A['B']['metrics']['total'])}，回撤 {A['B']['metrics']['dd']*100:.2f}%）可作前瞻参考，不能当作 2022 年以来的长期结论。</li>
</ul>
<h3>6.2 波动目标与 MA200 闸门</h3>
<ul>
<li>沿用原口径的 regime 闸门：等权净值 &gt; 其 MA200（参数在 2013–2021 训练段选定）才开仓，取 t-1 信息；未使用该闸门时回撤显著放大。</li>
<li>原口径的波动率目标是 15%，但在本扩展池上 15% 档回撤 {M['core']['dd']*100:.2f}% 超标，因此最优档降到 10%（扩展B 为 8%）；15% 档仍保留在网格中，作为约束边界的对照。</li>
</ul>
<h3>6.3 宏观因子与美债</h3>
<ul>
<li>宏观闸门（PMI&lt;50 降仓 20%、CFNAI&lt;0 降仓 15%、美国衰退标记 15%、QVIX&gt;90 分位 25%）实测为负贡献，未纳入；月度因子滞后，降仓多发生在反弹前。</li>
<li>美债：<code>bond_alloc.csv</code> 的 us_bond/cn_bond 利率序列可作久期轮动因子；本轮以 10 年国债 ETF 511260 作卫星持有，未单独做久期择时。</li>
</ul>
<h3>6.4 区间与信号来源</h3>
<ul>
<li>主窗口止于 2026-09-22，原因是核心信号矩阵 <code>sig_MLP/sig_CNN.npz</code>（生成于 2026-09-23）末日期为 2026-09-22；行情数据本身到 2026-11-06，若延长需重跑特征与信号，本轮未重算，故未延伸。</li>
<li>信号沿用既有矩阵，未训练新模型；卫星腿的信号为本轮新算（MA60 趋势 + 逆波动），已标注。</li>
<li>扩展标的清单：核心 13 只；卫星 原油 501018、黄金 518880、10 年国债 511260、低波 512890、纳指 513100；扩展B 另有豆粕 159985、半导体、新能源、军工、医药行业 ETF。</li>
</ul>
<h3>6.5 限制</h3>
<ul>
<li>豆粕与行业细分 ETF 数据只从 2025-01 起（<code>soybean_full.csv</code> 等），行业池 <code>pool_rules71</code> 缺失率 47%，无法用于 2022 起的长窗口。</li>
<li>熔断阈值 3%（切至 30% 仓位）在本区间未触发，回撤控制实际由波动目标与低波卫星完成。</li>
<li>参数（核心权重、目标波动）是在 2022–2026 上网格选出的，属样本内挑选，存在过拟合风险；未做 walk-forward。</li>
<li>成本只计单边 5bp，未建模冲击成本、ETF 折溢价与申赎限制。</li>
</ul>

<h2>七、复跑</h2>
<div class="note"><code>cd ext_pool &amp;&amp; python make_report.py</code>｜结果快照：<code>ext_pool/out/metrics_opt.json</code>、<code>results_ext.json</code>、<code>nav_opt.csv</code>、<code>nav_ext2.csv</code>。</div>
<div class="note">本内容由人工智能基于公开信息及相关数据生成，仅供研究与交流，不构成任何投资建议、收益承诺或交易依据。市场有风险，投资需谨慎。</div>
</div></body></html>"""

open(HTML, "w", encoding="utf-8").write(html)
print("写出", HTML, len(html), "字节; svg:", html.count("<svg"), "; 外部资源:", html.count("https://"))