# -*- coding: utf-8 -*-
import json,os
BASE=os.path.dirname(os.path.abspath(__file__)); R=json.load(open(os.path.join(BASE,"res_factor.json")))
def pct(x): return f"{x:+.2f}%"
rows_mf40=[("前 1 名","1 日",-1.57,-20.33,0.02,29.9,108),("前 1 名","5 日",-6.17,-22.03,-0.25,29.9,98),
           ("前 2 名","1 日",-0.99,-12.25,-0.04,30.0,149),("前 2 名","5 日",-2.67,-11.26,-0.21,30.0,112),
           ("前 3 名","1 日",-2.91,-10.92,-0.34,30.0,188),("前 3 名","5 日",0.61,-8.13,0.13,30.0,111)]
rows_base=[("满仓单一风格（状态先验，现版基准）",6.69,-7.03,0.97,29.9,41),
           ("概率分摊 5 风格（现版）",-0.45,-5.99,-0.08,29.7,188),
           ("概率前二 70/30（现版）",4.14,-4.47,0.84,29.9,135),
           ("单押红利（对照上限参考）",3.17,-4.48,0.71,29.8,8)]
rows_rel=[("前 1 名","1 日",-10.40,-23.80,-0.52,29.9,128),("前 1 名","5 日",-3.28,-20.75,-0.07,29.9,110),
          ("前 3 名","1 日",0.72,-9.95,0.14,30.1,161),("前 3 名","5 日",-2.60,-11.40,-0.29,30.0,127)]
rows_xgb=[("40 日窗","前 1 名",-19.88,-26.71,-1.24,30.0,438),("40 日窗","前 3 名",-12.55,-17.65,-1.74,30.0,799),
          ("60 日窗","前 1 名",-14.22,-20.31,-0.79,30.0,430),("60 日窗","前 3 名",-3.27,-8.98,-0.41,30.0,757)]
rows_win=[("多因子 前 1 名·平滑 5 日",-6.17,-22.03,-8.31,-21.82,+2.14),
          ("多因子 前 3 名·平滑 5 日",0.61,-8.13,-0.43,-8.51,+1.04),
          ("XGBoost 前 1 名",-19.88,-26.71,-14.22,-20.31,-5.66),
          ("XGBoost 前 3 名",-12.55,-17.65,-3.27,-8.98,-9.28)]
IND=[("煤炭",13.1),("银行",7.0),("医药",0.3),("消费",-11.7),("酒",-17.9),("券商",-18.1),
     ("光伏",-23.5),("钢铁",-26.3),("有色",-26.9),("军工",-27.4),("新能源",-28.3),("半导体",-49.3)]
T=lambda rows: "".join(f"<tr><td>{r[0]}</td><td class='num'>{pct(r[1])}</td><td class='num'>{pct(r[2])}</td>"
                       f"<td class='num'>{r[3]:.2f}</td><td class='num'>{r[4]:.1f}%</td><td class='num'>{r[5]}</td></tr>" for r in rows)
H=f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>行业强弱打分与 XGBoost 对照</title><style>
:root{{--ink:#12161C;--sub:#5A6472;--line:#E3E7EE;--bg:#F7F8FA;--accent:#1F3A5F}}
body{{margin:0;background:var(--bg);color:var(--ink);font-family:"Noto Sans SC","PingFang SC",system-ui,sans-serif;font-size:14.5px;line-height:1.72}}
.wrap{{max-width:1060px;margin:0 auto;padding:32px 24px 56px}}
header{{border-bottom:3px solid var(--accent);padding-bottom:14px;margin-bottom:22px}}
h1.ttl{{font-size:23px;margin:0 0 8px}} .meta{{color:var(--sub);font-size:12.5px}}
h2{{font-size:17px;margin:30px 0 10px;padding-left:10px;border-left:4px solid var(--accent)}}
table{{width:100%;border-collapse:collapse;font-size:13px;background:#fff;margin:8px 0}}
th,td{{border-bottom:1px solid var(--line);padding:7px 9px;text-align:left}} th{{background:#F2F5F9;font-size:12.5px}}
td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
.lead{{background:#fff;border-left:4px solid var(--accent);padding:13px 17px;margin:14px 0}}
.note{{background:#FFFBEB;border:1px solid #FDE68A;padding:11px 14px;font-size:13px;color:#7C4A03;margin:12px 0}}
.warn{{background:#FEF2F2;border:1px solid #FECACA;padding:11px 14px;font-size:13px;color:#7F1D1D;margin:12px 0}}
.small{{font-size:12.5px;color:var(--sub)}} ul,ol{{margin:8px 0 8px 20px}} li{{margin:4px 0}}
footer{{margin-top:34px;border-top:1px solid var(--line);padding-top:14px;color:var(--sub);font-size:12.5px}}
</style></head><body><div class="wrap">
<header><h1 class="ttl">行业强弱打分与 XGBoost 对照</h1>
<div class="meta">2026 单年 · A 股股票总仓位 ≤30% · 单边成本 5bp · 全部因子与训练样本仅用 t-1 及以前信息 · 数据截至 2026-11-06</div></header>

<div class="lead"><b>结论。</b>这条路在 2026 年不成立。行业强弱多因子打分最好的组合是「前 3 名 + 5 日平滑」<b>+0.61%/−8.13%</b>，XGBoost 全部为负（最好 −3.27%/−8.98%），而现版状态机满仓单一风格是 <b>+6.69%/−7.03%</b>。把窗口压到 40 日：多因子小幅改善 1~2pp，XGBoost 反而恶化 6~9pp。<b>失败原因是信号本身，不是窗口长度。</b></div>

<h2>一、对照基线（现版状态机方案）</h2>
<table><tr><th>方案</th><th>收益</th><th>最大回撤</th><th>夏普</th><th>平均股仓</th><th>调仓次数</th></tr>{T(rows_base)}</table>

<h2>二、行业相对强弱打分（单因子，40 日窗）</h2>
<p>最简版：只用一个因子——行业相对沪深300 的 20 日相对强度，每日取最强行业配置 30%。这是“红利在变强就给它加仓、在变弱就减仓”的直接实现。</p>
<table><tr><th>选样</th><th>收益</th><th>最大回撤</th><th>夏普</th><th>平均股仓</th><th>调仓次数</th></tr>
{''.join(f"<tr><td>{r[0]}（平滑{r[1]}）</td><td class='num'>{pct(r[2])}</td><td class='num'>{pct(r[3])}</td><td class='num'>{r[4]:.2f}</td><td class='num'>{r[5]:.1f}%</td><td class='num'>{r[6]}</td></tr>" for r in rows_rel)}</table>
<p class="small">单因子最强版是前 3 名 +0.72%/−9.95%；<b>满仓单押最强行业反而亏 3~10 个百分点且回撤 −20% 以上</b>，说明“最强行业”这个信号在 2026 年不可靠。</p>

<h2>三、行业强弱多因子暴露打分（40 日窗）</h2>
<p>12 个行业（半导体/军工/医药/券商/银行/煤炭/钢铁/酒/消费/新能源/光伏/有色）每日截面打分，取前 N 名按等权配置 30% 股票仓位，其余给十年国债 ETF。因子：动量 20 日、动量 40 日、相对沪深300 强度、波动倒数、成交额占比分位（拥挤度）、均线趋势加分，权重 0.30/0.22/0.20/0.13/0.15，趋势加分 0.25。</p>
<table><tr><th>选样</th><th>收益</th><th>最大回撤</th><th>夏普</th><th>平均股仓</th><th>调仓次数</th></tr>
{''.join(f"<tr><td>{r[0]}（平滑{r[1]}）</td><td class='num'>{pct(r[2])}</td><td class='num'>{pct(r[3])}</td><td class='num'>{r[4]:.2f}</td><td class='num'>{r[5]:.1f}%</td><td class='num'>{r[6]}</td></tr>" for r in rows_mf40)}</table>
<p class="small">平滑 5 日＝对得分取 5 日均值后再排序，用于降低换手；表中已含 5bp 双边摩擦。</p>

<h2>四、XGBoost walk-forward 打分</h2>
<p>同一因子表作为特征，标签为「次日行业收益」，滚动训练：仅使用结果已实现（≤t-2）的样本，最小训练窗口 60 日 × 12 行业，每 5 个交易日重估，预测当日行业排序。XGBoost 3.4.1，深度 3、学习率 0.05、120 轮。</p>
<table><tr><th>窗口</th><th>选样</th><th>收益</th><th>最大回撤</th><th>夏普</th><th>平均股仓</th><th>调仓次数</th></tr>
{''.join(f"<tr><td>{r[0]}</td><td>{r[1]}</td><td class='num'>{pct(r[2])}</td><td class='num'>{pct(r[3])}</td><td class='num'>{r[4]:.2f}</td><td class='num'>{r[5]:.1f}%</td><td class='num'>{r[6]}</td></tr>" for r in rows_xgb)}</table>
<div class="note"><b>过程中发现并修正一处未来函数</b>：首版 XGBoost 用了当日因子（含当日收盘）预测当日收益，跑出 +55.91%/−4.50% 的假结果；改为特征同样 shift(1) 后跌到 −19.88%/−26.71%。这条记录保留在此，用于说明为什么后续所有版本都要先做前视自检。</div>
<p>特征重要性（40 日窗，gain 归一）：拥挤度 0.0087、波动 0.0078、相对强度 0.0044、动量 40 日 0.0039、动量 20 日 0.0033、趋势 0.0028——六个因子的重要性几乎均等且都很低，说明模型没有学到稳定的行业轮动结构。</p>

<h2>五、40 日窗与 60 日窗的差别</h2>
<table><tr><th>方案</th><th>40 日收益</th><th>40 日回撤</th><th>60 日收益</th><th>60 日回撤</th><th>收益差</th></tr>
{''.join(f"<tr><td>{r[0]}</td><td class='num'>{pct(r[1])}</td><td class='num'>{pct(r[2])}</td><td class='num'>{pct(r[3])}</td><td class='num'>{pct(r[4])}</td><td class='num'>{r[5]:+.2f}pp</td></tr>" for r in rows_win)}</table>
<p>40 日对多因子是小幅改善（+1.0~2.1pp），对 XGBoost 是明显恶化（−5.7~−9.3pp）——训练样本从 60 日缩到 40 日后过拟合更重、调仓次数升到 400~800 次。缩短窗口不是解药。</p>

<h2>六、为什么打分无效：信号本身没有区分力</h2>
<ul>
<li>因子得分与未来收益的截面 IC：次日 <b>+0.056</b>、5 日 <b>+0.070</b>、20 日 <b>+0.030</b>，IC&gt;0 占比 54%~58%——有微弱正向关系，但远低于可用阈值。</li>
<li>得分第一名次日相对行业截面平均<b>跑输 0.14%</b>，胜率 48%：排序头部不可靠，典型“动量追高在反转年失效”。</li>
<li>2026 行业收益极度分化且反转快：煤炭 +13.1%、银行 +7.0%、医药 +0.3%，而半导体 −49.3%、新能源 −28.3%、军工 −27.4%。30% 仓位押错一次就是 −8pp 量级。</li>
</ul>

<h2>七、数据边界</h2>
<ul>
<li>行业 ETF 日线自 2025-01 起（部分 2025 年中），因子表可用样本 367 个交易日，2026 年只有约 200 个交易日，单年结果不足以判断稳定性。</li>
<li>拥挤度用成交额占比分位，受 ETF 申赎与规模变化影响，不等价于真实行业资金流。</li>
<li>全部为 ETF 现货、无杠杆、无期权对冲；不含停牌、涨跌停无法成交、冲击成本与容量约束。</li>
<li>截面只有 12 个行业，XGBoost 的训练样本量（60 日 × 12）偏小，属于弱数据条件下的对照实验，不代表方法上限。</li>
<li>基准方案（+6.69%/−7.03%）来自本工程既有状态机引擎，口径一致但参数为样本内筛选。</li>
</ul>

<div class="warn">本内容由人工智能基于历史数据回测生成，仅供研究与交流，不构成任何投资建议、收益承诺或交易依据。全部结果为样本内回测，参数经过事后比较，存在过拟合风险；历史表现不代表未来。市场有风险，投资需谨慎。</div>
<footer>数据：新浪 ETF 日线（close/amount），区间 2025-05-07 至 2026-11-06；计算口径见各节说明。</footer>
</div></body></html>"""
open(os.path.join(BASE,"行业强弱打分与XGBoost对照报告.html"),"w",encoding="utf-8").write(H)
print("写出",len(H))
