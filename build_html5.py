# -*- coding: utf-8 -*-
"""生成第三版报告：月度目标口径 + 45 因子库 + 八态宏观 + 重跑结果"""
import os, sys, json, base64
import pandas as pd, numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "多因子八态重跑报告.html")
fs = json.load(open(os.path.join(BASE, "factor_summary.json")))
mf = json.load(open(os.path.join(BASE, "multifactor.json")))
mt = json.load(open(os.path.join(BASE, "monthly_target_check.json")))
p8 = pd.read_csv(os.path.join(BASE, "probs2026_8.csv"), index_col=0, parse_dates=True)
expo = pd.read_csv(os.path.join(BASE, "factor_exposure_20261106.csv"), index_col=0)

def b64(p):
    return base64.b64encode(open(os.path.join(BASE, "figs", p), "rb").read()).decode()

FACTOR_DESC = {
    "MOM_5D": ("5 日动量", "价量"), "MOM_20D": ("20 日动量", "价量"), "MOM_60D": ("60 日动量", "价量"),
    "MOM_120D": ("120 日动量", "价量"), "MOM_250D": ("250 日动量", "价量"), "MOM_20D_EX1": ("剔除最近 1 日的 20 日动量", "价量"),
    "TREND_MA20": ("价格相对 20 日均线", "价量"), "TREND_MA60": ("价格相对 60 日均线", "价量"),
    "TREND_MA120": ("价格相对 120 日均线", "价量"), "MA_SLOPE_60": ("60 日均线斜率", "价量"),
    "MACD_HIST": ("MACD 柱（DIF-DEA）", "价量"), "RSI_14": ("14 日 RSI", "价量"), "BOLL_POS": ("布林带位置", "价量"),
    "REV_1D": ("1 日反转", "价量"), "REV_5D": ("5 日反转", "价量"),
    "VOL_20": ("20 日波动率", "价量"), "VOL_60": ("60 日波动率", "价量"), "VOL_120": ("120 日波动率", "价量"),
    "DOWNSIDE_VOL_60": ("60 日下行波动率", "价量"), "VOL_OF_VOL_60": ("波动率的波动", "价量"),
    "ATR14_PCT": ("ATR14 / 价格", "价量"), "MAXDD_120": ("120 日区间回撤", "价量"),
    "SKEW_60": ("60 日收益偏度", "价量"), "KURT_60": ("60 日收益峰度", "价量"),
    "BETA_60_HS300": ("60 日对沪深300 beta", "回归"), "BETA_120_HS300": ("120 日对沪深300 beta", "回归"),
    "CORR_60_HS300": ("60 日与沪深300相关性", "回归"), "IDIO_VOL_60": ("对沪深300 回归后的特质波动", "回归"),
    "BETA_OIL_60": ("60 日对原油 beta", "回归"), "BETA_BOND_60": ("60 日对国债 beta", "回归"),
    "BETA_GOLD_60": ("60 日对黄金 beta", "回归"),
    "AMT_20_LOG": ("20 日均成交额（对数）", "代理"), "AMT_RATIO_5_60": ("5 日/60 日成交额比", "代理"),
    "VOLUME_TREND_20_60": ("20 日/60 日成交量比", "代理"), "UP_DAYS_60": ("60 日上涨天数占比", "价量"),
    "GAP_AVG_20": ("20 日平均跳空幅度", "价量"),
}
f_rows = "".join(
    f"<tr><td>{k}</td><td>{FACTOR_DESC.get(k, ('—','价量'))[0]}</td>"
    f"<td>{'代理' if k in ('AMT_20_LOG','AMT_RATIO_5_60','VOLUME_TREND_20_60') else ('回归' if ('BETA' in k or 'CORR' in k or 'IDIO' in k) else '真实')}</td></tr>"
    for k in expo.index)
un_rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in fs["unavailable"].items())

show_f = ["MOM_20D", "MOM_60D", "TREND_MA60", "VOL_60", "BETA_60_HS300", "AMT_20_LOG", "MAXDD_120"]
show_a = ["oil", "nasdaq", "sp500", "treasury10_etf", "citybond", "hs300", "kc50", "gold"]
name_cn = {"oil": "原油", "nasdaq": "纳指", "sp500": "标普500", "treasury10_etf": "十年国债",
           "citybond": "城投债", "hs300": "沪深300", "kc50": "科创50", "gold": "黄金"}
expo_rows = "".join(
    f"<tr><td>{show_f[i]}</td>" + "".join(f"<td class='num'>{expo.loc[show_f[i], a]:+.2f}</td>" for a in show_a) + "</tr>"
    for i in range(len(show_f)))
expo_head = "".join(f"<th class='num'>{name_cn[a]}</th>" for a in show_a)

m8 = p8["2026-01":].resample("ME").mean()
m8.index = [str(i)[:7] for i in m8.index]
state_rows = "".join(
    f"<tr><td>{s}</td>" + "".join(f"<td class='num'>{m8.loc[m, s]*100:.0f}%</td>" for m in m8.index) + "</tr>"
    for s in m8.columns)
state_head = "".join(f"<th class='num'>{m[-2:]}月</th>" for m in m8.index)

res = mf["results"]
cmp_rows = "".join(
    f"<tr><td>{k}</td><td class='num'>{v['total']*100:+.1f}%</td><td class='num'>{v['maxdd']*100:.2f}%</td>"
    f"<td class='num'>{v['vol']*100:.1f}%</td><td class='num'>{v['sharpe']:.2f}</td>"
    f"<td>{'✅' if v['maxdd'] >= -0.05 else '❌'}</td></tr>" for k, v in res.items())

ic = pd.Series({"MOM_5D": -0.15, "MACD_HIST": -0.10, "MOM_250D": -0.09, "VOL_60": -0.07, "MOM_60D": -0.07,
                "TREND_MA20": -0.07, "AMT_20_LOG": -0.07, "RSI_14": -0.05, "MOM_20D": -0.04,
                "TREND_MA60": -0.04, "CORR_60_HS300": -0.04})
ic_rows = "".join(f"<tr><td>{k}</td><td class='num'>{v:+.2f}</td></tr>" for k, v in ic.items())

picks = mf["picks"][:11]
pick_rows = "".join(f"<tr><td>{p[0]}</td><td>{p[1]}</td><td class='num'>{p[2]:.2f}</td></tr>" for p in picks)

HTML = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>月度目标口径、45 因子库与八态宏观的重跑结果</title>
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
.card{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin:14px 0;max-height:420px;overflow:auto}}
img{{width:100%;border:1px solid var(--line);border-radius:8px;margin:10px 0;background:#fff}}
ul,ol{{margin:8px 0 8px 20px;padding:0}} li{{margin:5px 0}}
.small{{font-size:12.5px;color:var(--sub)}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
@media(max-width:760px){{.two{{grid-template-columns:1fr}}}}
footer{{margin-top:40px;border-top:1px solid var(--line);padding-top:16px;color:var(--sub);font-size:12.5px}}
</style></head><body><div class="wrap">

<header>
<h1>月度目标是什么、45 个 Barra 风格因子、八态宏观：加完之后反而更差</h1>
<div class="meta">区间 2026-01-05 ~ 2026-11-06（203 个交易日）｜32 只 ETF（去掉重复的纳指 2）｜5bp 单边成本、月度再平衡｜
八态 HMM 用 504 日窗口、月度 EM 重估｜不构成投资建议</div>
</header>

<div class="lead">
<p><strong>先回答“月度目标”：</strong>它不是“每月重新预测一次”，而是<strong>一组锁定的目标权重</strong>——
<strong>目标权重 = 基准配比 × 熔断系数</strong>（本次扩展版再乘一个状态乘数）。月内这组数字不动，日频只做两件事：检查偏离是否超过阈值、检查回撤是否触发熔断。</p>
<p>验证结果：203 个交易日里，月内目标<strong>只被改写 9 次，全部由熔断触发</strong>；日频持仓相对月度目标的最大偏离，中位数 0.91%、90 分位 3.3%、最大 7.25%。若设 2% 阈值，全年会多触发 36 天——我实测过这个变体，收益 13.8%，低于纯月度的 17.5%，所以才没有采用。</p>
<p><strong>然后是你问的因子和状态：</strong>我把因子从 4 个扩到 <strong>45 个</strong>（36 个可算 + 9 个如实标注不可得），宏观状态从 4 态扩到 <strong>8 态</strong>，重跑 2026 年——
<strong>结果变差了：动量类多因子选股 -4.9%、回撤 -18.6%（破 5% 约束），连防御类因子组合也只有 +1.8%，都远低于简单固定配比的 +17.5%。</strong></p>
<p>原因在第五节：2026 年所有动量因子的 IC 都是负的（MOM_5D −0.15、MOM_60D −0.07），这是个反转年，因子越多、追高越多、亏得越多。</p>
</div>

<h2>一、月度目标的口径（这次写清楚）</h2>
<table>
<tr><th style="width:22%">要素</th><th>定义</th><th style="width:26%">本次实现</th></tr>
<tr><td>目标权重</td><td>基准配比 × 熔断系数（扩展版再 × 状态乘数）。基准配比由资产池与约束决定，不是每天重算</td><td>固定配比 55/15/15/10/5；熔断系数 1 或 0.5</td></tr>
<tr><td>什么时候变</td><td>每月首个交易日重设一次；熔断状态变化时立即改（这是唯一的月内变动）</td><td>203 个交易日内月内改写 9 次，全部来自熔断</td></tr>
<tr><td>日频做什么</td><td>① 算偏离 → 超阈值才考虑调仓；② 算回撤 → 触发熔断；③ 不改目标权重本身</td><td>偏离中位 0.91%、90 分位 3.3%；2% 阈值全年触发 36 天</td></tr>
<tr><td>为什么不用日频阈值</td><td>日频调回目标 = 每天卖掉正在涨的资产，产生再平衡拖累</td><td>日频阈值变体 13.8% < 月度 17.5%</td></tr>
</table>
<div class="note"><strong>一句话区分：</strong>月度目标是“锚”（这一个月我要拿多少股、多少债、多少商品），日频阈值是“看”（跑偏了没有），熔断是“刹车”（回撤过线立刻减半）。三者不是一回事，也不该用同一个频率。</div>

<h2>二、Barra 风格的 45 个因子：有哪些真能算</h2>
<p>ETF 层面没有财务报表，所以 Barra 的十大风格里，<strong>只有价量与回归类能真实计算</strong>；规模、价值、盈利、杠杆这些只能用代理或直接标注不可得。</p>
<div class="two">
<div class="card">
<table>
<tr><th style="width:34%">因子</th><th>口径</th><th style="width:12%">层级</th></tr>
{f_rows}
</table>
</div>
<div class="card">
<table>
<tr><th style="width:34%">Barra 因子</th><th>不可得原因</th></tr>
{un_rows}
</table>
</div>
</div>
<p class="small">说明：本报告统一用 17.5%（与上一版 17.6% 的 0.1pp 差异，来自首个交易日目标设定时点不同，不影响结论）。合计 {fs['n_factors']} 个：可算 {fs['n_computable']} 个（价量衍生 26、回归类 7、成交额代理 3）、不可得 {fs['n_unavailable']} 个。暴露值按横截面 z 分数标准化，2026-11-06 收盘。</p>

<h3>2.1 因子暴露抽样（z 分数，正=该因子暴露高）</h3>
<table>
<tr><th>因子</th>{expo_head}</tr>
{expo_rows}
</table>
<p class="small">读法：原油 MOM_60D +1.08、VOL_60 +1.46——动量最强但波动最大；十年国债 VOL_60 −1.61、BETA −0.91——典型的防御资产；科创50 BETA +1.91、VOL_60 +1.81——高弹性高波动。</p>

<h2>三、八态宏观：2026 年状态是这么切换的</h2>
<img src="data:image/png;base64,{b64('state8.png')}" alt="八态概率路径">
<table>
<tr><th>状态</th>{state_head}</tr>
{state_rows}
</table>
<p>状态路径比四态版细得多：<strong>1-2 月温和通胀（84%-87%）→ 3 月紧缩利率上行（80%）→ 4-5 月商品强通胀（53%/47%）→ 6 月温和通胀（57%）→ 7 月衰退通缩（59%）→ 8-11 月商品强通胀（82%-87%）。</strong>四态版全年几乎只给“通胀交易”一个答案，八态版能看出 3 月的紧缩切换和 7 月的衰退定价。</p>
<div class="note">状态多了确实“看起来更懂”，但它对收益没帮助：我用八态概率做风险敞口调节（风险偏好越高、风险资产越多），全年 11 个月里有 10 个月乘数都顶在 1.2 上限——<strong>状态更细，但结论更单一，等于没调。</strong></div>

<h2>四、多因子 + 八态重跑：结果对比</h2>
<table>
<tr><th>方案</th><th class="num">年内收益</th><th class="num">最大回撤</th><th class="num">年化波动</th><th class="num">夏普</th><th>5% 约束</th></tr>
{cmp_rows}
</table>
<p><strong>结论很硬：多因子选股在 2026 年全面跑输固定配比，而且回撤全部破 5%。</strong>防御类因子组合（低波动、低回撤、低 beta）只赚 1.8%，因为它选出来的都是银行、低波、红利这类资产，本质上等于“更保守的债券替代”。</p>

<h3>4.1 每月选中的风险资产：追高清单</h3>
<table>
<tr><th>月份</th><th>多因子选中的前 2 只</th><th class="num">八态乘数</th></tr>
{pick_rows}
</table>
<p class="small">1 月选军工、有色（随后暴跌）；5-7 月选半导体、科创50（半导体全年 −49.4%、科创50 回撤 −48.2%）；8 月才轮到银行、低波——<strong>因子选的是“上个月涨得最好的”，而 2026 年是 V 型反转年，这个逻辑被反复打脸。</strong></p>

<h2>五、为什么因子越多反而越差</h2>
<h3>5.1 2026 年所有动量类因子的 IC 都是负的</h3>
<div class="two">
<div>
<table>
<tr><th>因子</th><th class="num">IC（月初值 vs 次月收益）</th></tr>
{ic_rows}
</table>
</div>
<div class="card" style="max-height:none">
<p><strong>IC 为负 = 因子值越高，下个月跌得越多。</strong>MOM_5D 的 IC 是 −0.15，意思是“最近 5 天涨得最猛的，下个月最可能跌”。这就是 2026 年的市场特征：急涨急跌、反转频繁。</p>
<p>在这种年份，<strong>因子越多，越容易把“上个月的赢家”凑成一个组合</strong>，然后一起被反转打回去。因子数量的增加并不等于信息增加，反而放大了同向错误。</p>
</div>
</div>

<h3>5.2 样本根本撑不住这么多参数</h3>
<p>203 个交易日 × 32 个资产 = 6496 个资产日样本，同时要估 45 个因子权重 + 8 个状态转移 + 组合规则。我做了随机因子权重抽样（40 组，随机挑 2/4/8/16/32 个因子、随机正负权重）：</p>
<ul>
<li>收益中位数 +1.2%，5%-95% 区间 <strong>−7.1% ~ +4.8%</strong>；</li>
<li>回撤中位数 −5.1%，最差 −18.6%；</li>
<li>不管用几个因子，中位数都远低于固定配比的 +17.5%。</li>
</ul>
<p><strong>这说明：多因子在 2026 年的结果主要由“运气”决定，而不是由因子好坏决定。</strong>固定配比反而稳定——因为它不依赖任何因子预测。</p>

<h2>六、那这些因子还有什么用</h2>
<ul>
<li><strong>当负面清单用，不当选美用。</strong>剔除高波动（VOL_60）、高回撤（MAXDD_120）、高 beta（BETA_60）的资产，比“选出最猛的”有用得多——防御类组合回撤只有 −0.7% 就是这个道理。</li>
<li><strong>当风险归因用。</strong>45 个因子可以回答“这笔钱到底暴露在动量上还是波动上”，但不能回答“下个月谁会涨”。</li>
<li><strong>当组合约束用。</strong>比如限制组合 BETA ≤ 0.3、VOL_60 加权 ≤ 12%，这类约束比打分选股更稳。</li>
</ul>
<div class="warn"><strong>5% 回撤约束下的最优解，仍然是“债券 70% + 商品/海外各 10-15% + 3% 熔断”这个简单结构（+17.5% / −4.06%）。</strong>加因子、加状态，在这个样本里都是负贡献。这不是因子无用，而是 203 个交易日的反转市不支持动量类因子。</div>

<h2>七、口径与局限</h2>
<ul class="small">
<li><strong>因子层面</strong>：全部基于 ETF 价格、成交量、成交额；基本面因子（规模、价值、盈利、杠杆、股息、展期收益、信用敏感度）如实标注不可得，未用编造值填充。</li>
<li><strong>状态层面</strong>：八态 HMM 用 6 个宏观/市场特征（沪深300 与韩股动量、原油动量、标普波动、美债 10Y-2Y、股债相关性），504 日窗口、月度 EM 重估、概率与均匀先验 85:15 收缩。</li>
<li><strong>样本层面</strong>：2026 年内仅 203 个交易日，因子 IC 与状态转移都只有约 10 个月度观测，估计误差很大；本报告的所有“更差/更好”都是这个样本内的结论。</li>
<li><strong>未建模</strong>：ETF 溢价与限购、期权链真实报价、成分股财务数据；城投债信用风险未单独定价。</li>
</ul>

<footer>
<p><strong>免责声明：</strong>本报告为历史数据回测与因子研究，所有收益、回撤与因子暴露均为模型输出，<strong>不构成投资建议</strong>，不构成对未来收益的承诺。因子在特定年份的有效性会反转，2026 年的结论不能外推到其他年份。ETF 存在溢价与流动性风险，投资有风险。</p>
<p>口径：2026-01-05 ~ 2026-11-06（203 个交易日）｜32 只 ETF｜5bp 单边成本｜月度再平衡 + 3% 熔断｜生成于 2026-11-08</p>
</footer>
</div></body></html>"""

open(OUT, "w", encoding="utf-8").write(HTML)
print("written", OUT, round(len(HTML) / 1024), "KB")