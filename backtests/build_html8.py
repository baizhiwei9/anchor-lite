# -*- coding: utf-8 -*-
"""生成四项改动报告"""
import os, sys, json
import pandas as pd, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "四项改动实盘化回测报告.html")
fc = json.load(open(os.path.join(BASE, "four_changes.json")))
rob = json.load(open(os.path.join(BASE, "four_changes_robust.json")))
liq = pd.read_csv(os.path.join(BASE, "liquidity_capacity.csv")).set_index("key")
NAME = {"treasury10_etf": "十年国债ETF 511260", "citybond": "城投债ETF 511220", "corp_bond": "公司债ETF 511030",
        "treasury_active": "活跃国债ETF 511020", "sp500": "标普500ETF 513500", "coal": "煤炭ETF 515220",
        "soybean": "豆粕ETF 159985", "oil": "原油ETF 501018", "nasdaq": "纳指ETF 513100"}

rows = "".join(
    f"<tr><td>{k}</td><td class='num'>{v['metrics']['total']*100:+.1f}%</td>"
    f"<td class='num'>{v['metrics']['maxdd']*100:.2f}%</td><td class='num'>{v['metrics']['vol']*100:.1f}%</td>"
    f"<td class='num'>{v['metrics']['sharpe']:.2f}</td><td class='num'>{v['metrics']['avg_comm']*100:.0f}%</td></tr>"
    for k, v in fc.items())

mon = fc["四项全改"]["monthly"]
mon_rows = "".join(
    f"<tr><td>{r['month']}</td><td>{r['state']}</td><td class='num'>{r['target_vol']*100:.0f}%</td>"
    f"<td class='num'>{r['scale']:.2f}</td><td class='num'>{r['comm']*100:.0f}%</td>"
    f"<td>{'、'.join(f'{k} {v*100:.0f}%' for k, v in r['top'].items())}</td></tr>" for r in mon)

cur = {"treasury_active": 0.118, "citybond": 0.118, "corp_bond": 0.118, "sp500": 0.118,
       "coal": 0.105, "soybean": 0.105, "oil": 0.105}
px = {k: float(liq.loc[k, "amt20_yi"]) for k in cur}  # placeholder, replaced below
price = {"treasury_active": 119.613, "citybond": 10.45, "corp_bond": 108.79, "sp500": 2.588,
         "coal": 1.169, "soybean": 2.107, "oil": 2.062}
lot_rows = "".join(
    f"<tr><td>{NAME[k]}</td><td class='num'>{w*100:.1f}%</td><td class='num'>{w*1e6:,.0f} 元</td>"
    f"<td class='num'>{price[k]:.3f}</td><td class='num'>{int(w*1e6/price[k]/100)} 手</td></tr>"
    for k, w in sorted(cur.items(), key=lambda x: -x[1]))

HTML = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>四项改动：类别预算 + 组合层波动 + 商品下限 + 极端档 6%</title>
<style>
:root{{--ink:#12161C;--sub:#5A6472;--line:#E3E7EE;--bg:#F7F8FA;--accent:#1F3A5F}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font-family:"Noto Sans SC","PingFang SC",system-ui,sans-serif;font-size:14.5px;line-height:1.72}}
.wrap{{max-width:1080px;margin:0 auto;padding:34px 26px 60px}}
header{{border-bottom:3px solid var(--accent);padding-bottom:16px;margin-bottom:24px}}
h1{{font-size:24px;line-height:1.36;margin:0 0 10px}}
.meta{{color:var(--sub);font-size:12.5px}}
h2{{font-size:18px;margin:34px 0 12px;padding-left:11px;border-left:4px solid var(--accent)}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin:10px 0;background:#fff}}
th,td{{border-bottom:1px solid var(--line);padding:7px 9px;text-align:left;vertical-align:top}}
th{{background:#F2F5F9;font-weight:600;font-size:12.5px;color:#2B3442}}
td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
.lead{{background:#fff;border-left:4px solid var(--accent);padding:14px 18px;margin:16px 0;border-radius:0 8px 8px 0}}
.note{{background:#FFFBEB;border:1px solid #FDE68A;border-radius:8px;padding:12px 15px;font-size:13px;color:#7C4A03;margin:12px 0}}
.warn{{background:#FEF2F2;border:1px solid #FECACA;border-radius:8px;padding:12px 15px;font-size:13px;color:#7F1D1D;margin:12px 0}}
ul,ol{{margin:8px 0 8px 20px;padding:0}} li{{margin:5px 0}}
.small{{font-size:12.5px;color:var(--sub)}}
footer{{margin-top:38px;border-top:1px solid var(--line);padding-top:16px;color:var(--sub);font-size:12.5px}}
</style></head><body><div class="wrap">

<header>
<h1>四项改动都改完之后：机制生效了，但这套配置在 2026 年还是亏的</h1>
<div class="meta">区间 2026-01-05 ~ 2026-11-06（203 个交易日）｜32 只 ETF｜月度重设 + 3% 熔断（回撤 3% 减半、修复到 1.5% 恢复）｜5bp 单边成本｜
八态宏观 HMM + 波动四档（低 12%/中 9%/高 6%/极端 6%）｜不构成投资建议</div>
</header>

<div class="lead">
<p><strong>四项全改的结果：年内 −3.5%，最大回撤 −7.82%（破 5%），年化波动 6.1%。</strong></p>
<p>改动本身是生效的：商品平均权重从 4% 提到 19%，组合波动从 2.5% 升到 6.1%，极端档不再把仓位砍到 3%。
<strong>但 2026 年的资产选择是错的——池子里选出的 A 股、创业板、有色、半导体大多下跌，放大暴露等于放大亏损。</strong></p>
<p>单改任何一项也都没能翻正：仅① −3.9%、仅② −5.8%、仅③ −1.0%、仅④ −1.9%（仅③比旧规则少亏 0.6pp，是唯一有正贡献的改动）。</p>
</div>

<h2>一、四项改动的具体实现</h2>
<table>
<tr><th style="width:16%">改动</th><th>实现</th><th style="width:24%">参数与理由</th></tr>
<tr><td>① 类别预算</td><td>宏观状态先给类别预算（商品/海外/债券/A 股），类内再按 60 日动量排序、按 15% 预算取 1–3 只</td>
<td>例如商品强通胀：商品 40%、海外 15%、债券 45%。理由：让状态真正决定“暴露方向”，而不是让单资产波动决定权重</td></tr>
<tr><td>② 组合层波动预算</td><td>类内等权，得到组合后按 σ_target/σ(组合) 整体缩放，缩放上限 1.0</td>
<td>波动预算只作用一次、作用在组合层；不再用单资产逆波动加权（那会让债券吃掉 80%+）</td></tr>
<tr><td>③ 商品腿下限</td><td>温和通胀/商品强通胀态，商品预算不低于 25%，缺口从债券挪</td>
<td>25% 取值理由：低于 25% 时商品对组合收益几乎没有贡献（旧规则实测 4%）</td></tr>
<tr><td>④ 极端档 6%</td><td>波动分位 &gt;85% 时目标波动由 3% 改为 6%</td>
<td>理由：3% 等于近乎空仓，回撤靠它压住但收益也没了；改 6% 后靠熔断与类别预算控回撤</td></tr>
</table>
<p class="small">改动依据（旧规则诊断）：债券占月度目标权重均值 81%、最高 90%；11 个月中 5 个月落在极端档、目标波动 3%；商品腿只有 2%–5%。</p>

<h2>二、六组对照（旧规则 + 四项单改 + 全改）</h2>
<table>
<tr><th>方案</th><th class="num">年内收益</th><th class="num">最大回撤</th><th class="num">年化波动</th><th class="num">夏普</th><th class="num">平均商品权重</th></tr>
{rows}
</table>
<table>
<tr><th>既有基线</th><th class="num">年内收益</th><th class="num">最大回撤</th></tr>
<tr><td>固定配比 55/15/15/10/5 + 3% 熔断</td><td class="num">+17.5%</td><td class="num">-4.06%</td></tr>
<tr><td>状态软混合（单资产≤35%）</td><td class="num">-10.4%</td><td class="num">-20.66%</td></tr>
<tr><td>状态软混合（无上限）</td><td class="num">-13.7%</td><td class="num">-29.54%</td></tr>
<tr><td>动态池×波动预算（旧规则，即本表第一行）</td><td class="num">-1.6%</td><td class="num">-3.69%</td></tr>
</table>

<h2>三、四项全改的逐月配置</h2>
<table>
<tr><th>月份</th><th>宏观状态</th><th class="num">目标波动</th><th class="num">缩放</th><th class="num">商品权重</th><th>前三大持仓</th></tr>
{mon_rows}
</table>
<p class="small">缩放系数 0.46–1.0：8 月波动分位最高时只用到 46% 仓位；11 月回到 1.0（目标波动 9%）。商品权重从 18% 升到 40%，明显高于旧规则的 4%。</p>

<h2>四、当前状态下的持仓（2026-11-06）</h2>
<p>宏观状态：商品强通胀（86.8%）；波动分位 75%（高波档）→ 目标波动 6%；缩放系数 0.786，总仓位 78.6%，其余现金。</p>
<table>
<tr><th>标的</th><th class="num">权重</th><th class="num">100 万本金</th><th class="num">最新价</th><th class="num">手数</th></tr>
{lot_rows}
</table>
<p class="small">类别分布：债券 35.4%、海外 11.8%、商品 31.4%、A 股 0%，剩余 21.4% 为现金（波动预算缩放所致）。</p>

<h2>五、为什么改完更差</h2>
<ul>
<li><strong>放大的暴露本身是错的。</strong>2026 年 A 股全谱系下跌（创业板 −24%、科创 50 回撤 −48%、半导体 −49%），池子按 60 日动量选出来的恰恰是“上月涨、下月跌”的资产；把商品与权益暴露从 4% 提到 19%，等于加速亏损。</li>
<li><strong>回撤破线是必然。</strong>组合波动从 2.5% 升到 6.1%，而熔断只在回撤 3% 时减半、按日收盘执行，遇到跳空（7 月、8 月）来不及反应，最大回撤 −7.82%。</li>
<li><strong>唯一有正贡献的是③商品下限。</strong>−1.0% 比旧规则 −1.6% 少亏 0.6pp，因为它把原油权重提了一点、恰好赶上原油 11 月的反弹。</li>
<li><strong>②最差（−5.8%）。</strong>类内等权把资金平均撒给池内资产，等于放弃了“挑最强的那个”，在反转年伤害最大。</li>
</ul>
<div class="warn"><strong>结论：四项改动在机制上都按预期生效（商品暴露、波动预算、极端档、类别预算都动了），但收益没有改善，回撤还破了线。</strong>
问题不在规则层，而在<strong>“用 60 日动量选资产”这个信号在 2026 年是反向的</strong>——所有动量因子的 IC 都是负的（MOM_5D −0.15、MOM_60D −0.07）。规则再精细，选错资产就救不回来。</div>

<h2>六、稳健性</h2>
<ul>
<li>成本 10bp：{rob['成本10bp']['total']*100:+.1f}% / {rob['成本10bp']['maxdd']*100:.2f}%</li>
<li>不设熔断（正常版本为回撤 3% 减半、1.5% 恢复）：{rob['不设熔断']['total']*100:+.1f}% / {rob['不设熔断']['maxdd']*100:.2f}%（熔断几乎没起作用，因为回撤是跳空式）</li>
<li>上半年 +1.3% / −4.23%；下半年 −6.0% / −5.95%（亏损集中在 7 月之后）</li>
</ul>

<h2>七、口径与局限</h2>
<ul class="small">
<li>区间 2026-01-05~2026-11-06，203 个交易日；32 只 ETF，不含个股；5bp 单边成本；月度重设 + 3% 熔断。</li>
<li>类别预算表（商品强通胀：商品 40/海外 15/债券 45）是工程设定，不是历史估计；商品下限 25%、极端档 6% 同理，都属于参数假设，换一组参数结果会变。</li>
<li>八态状态样本 16–97 天，2025 与 2026 状态几乎不重叠；动量选资产在反转年失效是本次失败的主因，这条结论只对 2026 年成立。</li>
<li>未建模：ETF 溢价与限购、期权、期货保证金；流动性在 1000 万规模下无约束、1 亿规模下仅新能源与钢铁受限。</li>
</ul>

<footer>
<p><strong>免责声明：</strong>本报告为历史数据回测与方法演示，收益与回撤均为模型输出，<strong>不构成投资建议</strong>，不构成对未来收益的承诺。样本期短、状态样本稀疏，任何“年内最优”都不能外推。ETF 存在溢价与流动性风险，投资有风险。</p>
<p>口径：2026-01-05 ~ 2026-11-06（203 个交易日）｜32 只 ETF｜月度重设 + 3% 熔断（回撤 3% 减半、修复到 1.5% 恢复）｜5bp 单边成本｜生成于 2026-11-08</p>
</footer>
</div></body></html>"""

open(OUT, "w", encoding="utf-8").write(HTML)
print("written", OUT, round(len(HTML) / 1024), "KB")