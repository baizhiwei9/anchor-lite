# -*- coding: utf-8 -*-
"""生成单文件 HTML 报告"""
import os, sys, json, base64
import pandas as pd, numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "Anchor-Lite_回撤复盘与Regime模拟.html")

eng = json.load(open(os.path.join(BASE, "engine_out.json")))
hmm = json.load(open(os.path.join(BASE, "hmm_out.json")))
diag = json.load(open(os.path.join(BASE, "diag_out.json")))
fx = json.load(open(os.path.join(BASE, "forensics.json")))
probs = pd.read_csv(os.path.join(BASE, "probs_filtered.csv"), index_col=0, parse_dates=True)
rets = pd.read_csv(os.path.join(BASE, "returns.csv"), index_col=0, parse_dates=True)
wti_px = pd.read_csv(os.path.join(BASE, "data/fut_wti.csv"), parse_dates=["date"]).set_index("date")["close"].dropna()

def b64(p):
    with open(os.path.join(BASE, "figs", p), "rb") as f:
        return base64.b64encode(f.read()).decode()

# ---------- 快照核验数值 ----------
D = "2026-04-14"
def last(s): 
    s = s.dropna(); return float(s[s.index <= D].iloc[-1])
kc = rets["_kc50_px"]; hs = rets["_hs300_px"]; qv = rets["_qvix"]
kospi = pd.read_csv(os.path.join(BASE, "data/idx_kospi.csv"), parse_dates=["date"]).set_index("date")["close"]
y = pd.read_csv(os.path.join(BASE, "data/yields.csv"), parse_dates=["日期"]).set_index("日期")
y = y[y.index <= D]
us10, us2 = float(y["美国国债收益率10年"].iloc[-1]), float(y["美国国债收益率2年"].iloc[-1])
slope_now = float(y["美国国债收益率10年-2年"].iloc[-1]); slope_20 = float(y["美国国债收益率10年-2年"].iloc[-21])
w_now = float(wti_px[wti_px.index <= D].iloc[-1])
w20 = float(wti_px[wti_px.index <= D].iloc[-21]); w60 = float(wti_px[wti_px.index <= D].iloc[-61])
kc_now, kc_start = last(kc), float(kc[kc.index >= "2026-01-01"].iloc[0])
ko_now, ko_start = last(kospi), float(kospi[kospi.index >= "2026-01-01"].iloc[0])
qv_now = last(qv)
p = eng["snapshot"]
att = eng["attribution_main"]; att26 = eng["attribution_2026"]

def pct(x, d=2):
    return f"{x*100:+.{d}f}%"

def chip(kind):
    m = {"原文": ("#E8F0FE", "#1A56DB"), "推演": ("#FFF4E5", "#B45309"), "假设": ("#F3E8FF", "#6B21A8")}
    bg, fg = m[kind]
    return f'<span class="chip" style="background:{bg};color:{fg}">{kind}</span>'

# 原油路径（取关键节点）
path = {r["date"][:10]: r for r in eng["oil_path"]}
milestones = ["2025-10-26", "2025-11-30", "2025-12-14", "2026-01-04", "2026-01-18", "2026-02-08",
              "2026-03-08", "2026-04-12"]
oil_rows = []
for m in milestones:
    # 取不晚于 m 的最近一条
    ks = [k for k in path if k <= m]
    if not ks: continue
    r = path[max(ks)]
    oil_rows.append((m, r["w_oil"], r["w_kc50"], r["w_t10"], r["w_sv"], r["p_inf"], r["qvix"]))

# 当前因子值
feat = pd.read_csv(os.path.join(BASE, "features.csv"), index_col=0, parse_dates=True).loc[D]
def ret20(col):
    s_ = rets[col].dropna(); s_ = s_[s_.index <= D]
    return float(s_.iloc[-1] / s_.iloc[-21] - 1)
factor_rows = [
    ("沪深300ETF 20 日动量", f"{ret20('_hs300_px')*100:+.1f}%", f"{feat['eq_mom']:+.2f}", "趋势方向：走平偏弱"),
    ("科创50ETF 20 日动量", f"{ret20('_kc50_px')*100:+.1f}%", "—（未入模）", "与沪深300 高度相关，入模会重复计权；3 月暴跌后 4 月修复中"),
    ("韩国综指 20 日动量", f"{(ko_now/float(kospi[kospi.index<=D].iloc[-21])-1)*100:+.1f}%", f"{feat['em_mom']:+.2f}", "新兴市场仍是全球最强腿"),
    ("WTI 原油 20 日动量", f"{(w_now/w20-1)*100:+.1f}%", f"{feat['oil_mom']:+.2f}", "突破后的动量衰减"),
    ("标普 20 日已实现波动（年化）", f"{feat['vol_spx']*100:.1f}%", f"{feat['vol_spx']:.2f}", "高于 252 日均值 15.7%"),
    ("美债 10Y-2Y", f"{feat['slope_us']:.2f}", f"{feat['slope_us']:.2f}", "20 日前 0.52，微幅走平"),
    ("股债 60 日相关性", f"{feat['corr_eq_bond']:+.3f}", f"{feat['corr_eq_bond']:+.2f}", "由负转正＝通胀体制签名"),
    ("50ETF QVIX", f"{qv_now:.2f}", "—（闸门用，不入模）", "低于 20 日均值，波动率在回落；只用于做空波动率闸门"),
]
factor_table = "".join(f"<tr><td>{a}</td><td class='num'>{b}</td><td class='num'>{c}</td><td>{d}</td></tr>"
                       for a, b, c, d in factor_rows)

# Regime 概率表
prob_rows = "".join(
    f"<tr><td>{k}</td><td class='num'>{v*100:.2f}%</td><td class='num'>{(0.25 if k not in ['通胀交易'] else 0.25)}</td></tr>"
    for k, v in p["probs"].items())

# 状态特征均值
means = pd.DataFrame(hmm["means_raw"])
feat_cn = {"eq_mom": "沪深300 20日动量(z)", "em_mom": "韩国综指 20日动量(z)",
           "oil_mom": "原油 20日动量(z)", "vol_spx": "标普20日已实现波动(年化)",
           "slope_us": "美债10Y-2Y 利差(%)", "corr_eq_bond": "股债60日相关性"}
mean_rows = "".join(
    "<tr><td>{}</td>{}</tr>".format(k, "".join(f"<td class='num'>{means.loc[k, c]:.3f}</td>" for c in means.columns))
    for k in means.index)

# 信号权重表
sig = [
    ("科创50ETF（588000）", "kc50", 0.35, 0.08, -0.10, 0.08),
    ("沪深300ETF（510300）", "hs300", 0.30, 0.10, -0.10, 0.10),
    ("原油ETF（501018）", "oil", 0.10, 0.30, 0.05, 0.08),
    ("有色ETF（512400）", "nonferrous", 0.10, 0.28, 0.05, 0.05),
    ("10年国债期货 T（可空）", "t10", 0.00, -0.35, 0.55, 0.15),
    ("做空波动率（必须配对）", "sv", -0.15, -0.05, 0.00, 0.00),
]
sig_rows = "".join(
    f"<tr><td>{n}</td><td class='num'>{a:+.0%}</td><td class='num'>{b:+.0%}</td>"
    f"<td class='num'>{c:+.0%}</td><td class='num'>{d:+.0%}</td>"
    f"<td class='num strong'>{p['target_blend'][k]*100:+.1f}%</td></tr>"
    for n, k, a, b, c, d in sig)

# 仓位表
carrier = {"kc50": "ETF 现货（多头）", "hs300": "ETF 现货（多头）", "oil": "ETF 现货（多头）",
           "nonferrous": "ETF 现货（多头）", "t10": "国债期货（可多可空）", "sv": "期权配对（卖近买远）"}
pos_rows = ""
for k in ["kc50", "hs300", "oil", "nonferrous", "t10", "sv"]:
    w = p["weights"][k]; rc = p["risk_contrib"].get(k, 0.0)
    direction = "多头" if w > 0 else ("空头" if w < 0 else "空仓")
    pos_rows += (f"<tr><td>{dict(zip(['kc50','hs300','oil','nonferrous','t10','sv'],['科创50ETF','沪深300ETF','原油ETF','有色ETF','10年国债期货T','做空波动率价差']))[k]}</td>"
                 f"<td>{carrier[k]}</td><td>{direction}</td><td class='num'>{w*100:+.1f}%</td>"
                 f"<td class='num'>{rc*100:+.1f}%</td></tr>")

# 净值对照
main = eng["main"]; bh = eng["buyhold_hs300"]; bhk = eng["buyhold_kc50"]
comp_rows = f"""
<tr><td>Anchor-Lite（概率加权 + 风险预算）</td><td class="num">{main['anchor']['total']*100:+.1f}%</td><td class="num">{main['anchor']['vol']*100:.1f}%</td><td class="num strong">{main['anchor']['maxdd']*100:.2f}%</td><td class="num">{main['anchor']['sharpe']:.2f}</td></tr>
<tr><td>硬切换（argmax 一把到位，同风险预算）</td><td class="num">{main['switch']['total']*100:+.1f}%</td><td class="num">{main['switch']['vol']*100:.1f}%</td><td class="num">{main['switch']['maxdd']*100:.2f}%</td><td class="num">{main['switch']['sharpe']:.2f}</td></tr>
<tr><td>概率加权但不做风险预算</td><td class="num">{main['norisk']['total']*100:+.1f}%</td><td class="num">{main['norisk']['vol']*100:.1f}%</td><td class="num">{main['norisk']['maxdd']*100:.2f}%</td><td class="num">{main['norisk']['sharpe']:.2f}</td></tr>
<tr><td>沪深300ETF 买入持有</td><td class="num">{bh['total']*100:+.1f}%</td><td class="num">{bh['vol']*100:.1f}%</td><td class="num">{bh['maxdd']*100:.2f}%</td><td class="num">—</td></tr>
<tr><td>科创50ETF 买入持有</td><td class="num">{bhk['total']*100:+.1f}%</td><td class="num">{bhk['vol']*100:.1f}%</td><td class="num">{bhk['maxdd']*100:.2f}%</td><td class="num">—</td></tr>
"""

stress = eng["stress_with_mult"]
s18 = fx["stress_2018_riskonly"]
stress_rows = f"""
<tr><td>2020-03 流动性危机</td><td class="num">{stress['2020-03']['total']*100:+.2f}%</td><td class="num">{stress['2020-03']['maxdd']*100:.2f}%</td><td class="num">{fx['bh_hs300_2020']['maxdd']*100:.2f}%</td><td>识别为高波危机（0.73→0.89），切长债，股票转空</td></tr>
<tr><td>2022 全年加息/缩表</td><td class="num">{stress['2022']['total']*100:+.2f}%</td><td class="num">{stress['2022']['maxdd']*100:.2f}%</td><td class="num">—</td><td>危机与震荡交替，长债平均 31.6%</td></tr>
<tr><td>2025-04 关税冲击</td><td class="num">{stress['2025-04']['total']*100:+.2f}%</td><td class="num">{stress['2025-04']['maxdd']*100:.2f}%</td><td class="num">—</td><td>波动率跳升，风险预算自动降杠杆</td></tr>
<tr><td>2018-02 波动率爆炸（仅风险层）</td><td class="num">{s18['total']*100:+.2f}%</td><td class="num strong">{s18['maxdd']*100:.2f}%</td><td class="num">{fx['bh_hs300_2018']['maxdd']*100:.2f}%</td><td>HMM 样本不足，强制“震荡未知”，只测风险层</td></tr>
"""

att_rows = "".join(
    f"<tr><td>{ {'kc50':'科创50ETF','hs300':'沪深300ETF','oil':'原油ETF','nonferrous':'有色ETF','t10':'国债期货','sv':'做空波动率'}[k] }</td>"
    f"<td class='num'>{att[k]*100:+.2f}%</td><td class='num'>{att26[k]*100:+.2f}%</td></tr>"
    for k in ["nonferrous", "oil", "kc50", "hs300", "t10", "sv"])

e = eng["engine_stats"]; exm = fx["exposure"]
dd_ep = fx["dd_episode"]

HTML = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Anchor-Lite：Regime 自适应系统复现与回撤机制复盘</title>
<style>
:root{{--ink:#12161C;--sub:#5A6472;--line:#E3E7EE;--bg:#F7F8FA;--accent:#1F3A5F;--warn:#B45309;--red:#C0392B}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
font-family:"Noto Sans SC","PingFang SC","Microsoft YaHei",system-ui,sans-serif;
font-size:14.5px;line-height:1.72;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1080px;margin:0 auto;padding:36px 26px 60px}}
header{{border-bottom:3px solid var(--accent);padding-bottom:18px;margin-bottom:26px}}
h1{{font-size:25px;line-height:1.35;margin:0 0 10px;letter-spacing:-.2px}}
.meta{{color:var(--sub);font-size:12.5px}}
h2{{font-size:18px;margin:38px 0 12px;padding-left:11px;border-left:4px solid var(--accent)}}
h3{{font-size:15px;margin:22px 0 8px}}
p{{margin:9px 0}}
.card{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:18px 20px;margin:14px 0}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin:10px 0;background:#fff}}
th,td{{border-bottom:1px solid var(--line);padding:7px 9px;text-align:left;vertical-align:top}}
th{{background:#F2F5F9;font-weight:600;color:#2B3442;font-size:12.5px}}
td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
td.strong,strong{{font-weight:700}}
tr:last-child td{{border-bottom:none}}
.chip{{display:inline-block;font-size:11px;padding:1px 7px;border-radius:20px;margin-right:5px;font-weight:600}}
.lead{{background:#fff;border-left:4px solid var(--accent);padding:14px 18px;border-radius:0 8px 8px 0;margin:16px 0}}
.note{{background:#FFFBEB;border:1px solid #FDE68A;border-radius:8px;padding:12px 15px;font-size:13px;color:#7C4A03}}
.warn{{background:#FEF2F2;border:1px solid #FECACA;border-radius:8px;padding:12px 15px;font-size:13px;color:#7F1D1D}}
ul,ol{{margin:8px 0 8px 20px;padding:0}}
li{{margin:5px 0}}
img{{width:100%;border:1px solid var(--line);border-radius:8px;margin:10px 0;background:#fff}}
.small{{font-size:12.5px;color:var(--sub)}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
@media(max-width:760px){{.grid2{{grid-template-columns:1fr}}}}
footer{{margin-top:44px;border-top:1px solid var(--line);padding-top:16px;color:var(--sub);font-size:12.5px}}
</style></head><body><div class="wrap">

<header>
<h1>当 Regime 剧烈切换时，一套「Anchor-Lite」系统到底靠什么把回撤压成一条平滑曲线</h1>
<div class="meta">原文：《当市场Regime反复剧烈变化，AI Agents策略系统如何自动适应》（粒子量化）｜复现与推演：DuMate ｜
数据截止 <strong>2026-04-14</strong>（模拟日 2026-04-15 的前一交易日，全文不使用 4-14 之后的信息）｜
图表与表格均为本次实算，非引用原文</div>
</header>

<div class="lead">
<p><strong>先给结论。</strong>原文那 -4.39% 的最大回撤之所以看起来丝滑，<strong>不是因为它预测得准，而是四件算术上很“钝”的事叠加</strong>：
① 名义敞口被风险预算压到净值的三成左右（我复现时平均净敞口 29.6%、总敞口 48%），收益和回撤一起被缩小；
② 概率加权 + 渐进建仓把换手从每天 4.8% 降到 3.0%，少了来回割肉；
③ 危机状态切长债（2020-03 平均 34% 长债、2022 平均 31.6%），把股票腿的亏损对冲掉；
④ 做空波动率在低波期持续收 carry、波动率抬头自动收窄，抹平了净值曲线的毛刺。</p>
<p>把同一套规则压到 <strong>15% 波动预算</strong>，回撤立刻从 -5.47% 变 -8.31%；换成“硬切换”（argmax 一把到位）是 -12.0%。<strong>回撤深度基本等于波动预算的算术结果</strong>，剩下的才是择时贡献。</p>
<p>同时必须说清楚：原文正文只写了“表现整体平稳”，<strong>没有给出 -4.39% 这个数字</strong>（该数字来自你转述的图表）；196 个交易日样本里没有真正的流动性危机，它不足以证明实盘有效。</p>
</div>

<h2>一、原文到底设计了什么：5 条机制与它们的证据等级</h2>
<p>先把“原文说了什么”和“我补了什么”切开，避免把工程假设当成原作者的结论。</p>
<table>
<tr><th style="width:22%">机制</th><th>原文的实际表述与关键数据</th><th style="width:14%">证据等级</th></tr>
<tr><td><strong>Regime Agent 输出概率分布</strong></td>
<td>“基于经济学理论、隐马尔可夫模型与宏观因子聚类，<strong>实时输出当前市场Regime的概率分布</strong>”。原文只说了“概率分布”，没有说明用的是滤波概率还是平滑概率，也没有给状态数量、窗口长度、重估频率。</td><td>{chip('原文')}表述<br>{chip('假设')}实现细节</td></tr>
<tr><td><strong>Research Agent 的对抗性辩论</strong></td>
<td>“为了消除AI常见的‘顺从偏见’或‘幻觉’，引入‘对抗性辩论’机制，让特长各异的‘AI分析师们’通过辩论形成建议共识”。原文没有披露辩论轮数、裁决规则、如何防止辩论变成“和稀泥”。</td><td>{chip('原文')}机制<br>{chip('推演')}防幻觉逻辑</td></tr>
<tr><td><strong>仓位渐进式加仓</strong></td>
<td>原油“从年初的不足2%，两个月内加到13.3%”；“2月初趋势强度分值刚转正，系统分配约3%仓位；到3月中旬，多个动量形态确认向上……逐步加到13%”。这是原文最扎实的一段过程描述。</td><td>{chip('原文')}数据<br>{chip('推演')}步长规则</td></tr>
<tr><td><strong>做空波动率随波动率抬头收窄</strong></td>
<td>“做空波动率的仓位从-14.7%收窄到-8.7%——原油上涨带动整体波动率上升，做空波动率的风险在变大”；9 月加息落地后“调降至3%以下”。注意：这是<strong>连续收窄</strong>，不是一刀切平。</td><td>{chip('原文')}数据<br>{chip('推演')}触发阈值</td></tr>
<tr><td><strong>Risk 与 Portfolio 共同确认敞口</strong></td>
<td>“Risk Management Agent 对组合进行风险指标的测算，运行尾部风险压力测试，<strong>与构建组合的Agent协作，共同确认最终的组合敞口</strong>”。另有 Human-in/on-the-loop 作为黑天鹅熔断备案。</td><td>{chip('原文')}机制<br>{chip('假设')}冲突裁决</td></tr>
</table>
<p class="small">原文另一条不在你列的 5 点里、但对“回撤丝滑”很关键：<strong>Human-in/on-the-loop 只在黑天鹅熔断时介入</strong>——意味着日常波动完全交给规则，这既降低了人为干预的噪声，也意味着系统在真正极端行情里依赖人工兜底。</p>

<h2>二、把叙事和行情对齐：2026-04-15 快照的真实读数</h2>
<p>你给的快照带着明确的叙事（原油突破、CPI 反弹、韩国 +102%、科创50 +64%、10Y 4.5% 且陡峭化）。我把每一条都拿去和 4-14 收盘核对了一遍，<strong>其中四条与当日数据不一致，而且不一致的方向很关键</strong>。</p>
<table>
<tr><th style="width:20%">快照输入</th><th style="width:16%">4-14 实测</th><th>差异与处理</th></tr>
<tr><td>原油突破上行</td><td class="num">WTI 92.02 美元<br>60日 {pct(w_now/w60-1,1)}／20日 {pct(w_now/w20-1,1)}</td><td>突破发生在 2–3 月（60 日 +55%），<strong>到 4-14 的 20 日动量已经转平甚至小负</strong>。这条叙事描述的是“已经发生过的突破”，不是“当天正在突破”。</td></tr>
<tr><td>CPI 预期反弹 / 中东冲突升级</td><td>无当日可核验高频数据</td><td>保留为宏观先验（定性），不进入定量因子。原文的“通胀交易”Regime 在数据上确实成立，见第四节。</td></tr>
<tr><td>韩国综指年初至今 +102%</td><td class="num">{pct(ko_now/ko_start-1,1)}（{ko_start:.0f}→{ko_now:.0f}）</td><td>+102.42% 是<strong>到 6 月底</strong>的数字（原文自己写的也是“上半年”）。4-14 只有 +38.5%。用 +102% 做 4 月决策 = 偷看未来。</td></tr>
<tr><td>科创50 年初至今 +64%</td><td class="num">{pct(kc_now/kc_start-1,2)}（{kc_start:.3f}→{kc_now:.3f}，恰好回到年初水平，非数据缺失）</td><td>同理。4-14 科创50 ETF 年内几乎零涨幅，3 月还从 1.588 跌到 1.324。<strong>科技股狂飙是 4 月下旬到 6 月的事</strong>。</td></tr>
<tr><td>VIX 18.5</td><td class="num">美国 VIX 数据源不可得<br>50ETF QVIX {qv_now:.2f}</td><td>Yahoo／stooq／investing 三个源都被拒（403/反爬），美国 VIX 无法取到。HMM 用标普 20 日已实现波动率代理，波动率闸门用 50ETF QVIX；18.5 仅作外部参考水平，不冒充数据。</td></tr>
<tr><td>10Y 美债 4.5%，曲线陡峭化</td><td class="num">10Y {us10:.2f}%／2Y {us2:.2f}%<br>10Y-2Y {slope_now:.2f}（20日前 {slope_20:.2f}）</td><td>收益率比叙事低 24bp，<strong>曲线是轻微走平而不是陡峭化</strong>。这条差异直接影响“通胀交易”的证据强度，我在辩论环节单独处理。</td></tr>
<tr><td>美联储按兵不动、偏鹰</td><td>无当日会议记录可核验</td><td>保留为定性先验。原文后续提到 9-17 加息 25bp，与本条并不矛盾（时点不同）。</td></tr>
</table>
<div class="note"><strong>为什么要把这件事放在第二节：</strong>你问的是“他的回撤是怎么做到的、为什么这么丝滑”。答案的一半在机制，另一半在<strong>叙述方式</strong>——原文用“上半年 +102%”“4 月科技股狂飙”这类事后成立的数字去描述当时的决策环境，会让读者误以为系统在 4 月就已经知道科技股要涨。真实的 4-14 时点，科技股是零涨幅、原油动量已经转平。<strong>把叙事当成实盘输入，本身就是一种未来函数。</strong></div>

<h2>三、Anchor-Lite 的六 Agent 流程（本次真实实现）</h2>
<table>
<tr><th style="width:19%">Agent</th><th style="width:31%">输入 → 输出</th><th>本次实现与字段</th></tr>
<tr><td><strong>1 Data Ingestion</strong></td><td>价格／宏观／相关性／动量 → 标准化因子表</td><td>8 类序列：A股 ETF（588000、510300、512400、501018）、韩国综指、标普、WTI、铜、国债期货 T/TF/TS、中美国债收益率、50ETF 与科创50 QVIX。统一到 A 股交易日历，海外序列只用前向填充（不引入未来值）。</td></tr>
<tr><td><strong>2 Macro Factor</strong></td><td>宏观因子库 → 分位标注</td><td>6 个入模因子：沪深300 20 日动量、韩国综指 20 日动量、原油 20 日动量、标普 20 日已实现波动、美债 10Y-2Y、股债 60 日相关性。全部按 252 日窗口内的均值／标准差做 z 标准化。</td></tr>
<tr><td><strong>3 Regime（核心）</strong></td><td>滤波概率 → 状态分布＋置信度＋滞后</td><td>4 状态高斯 HMM（对角协方差、协方差下限 1.5 防止后验硬化），252 日滚动窗口，<strong>每月首个交易日用 EM 重估</strong>，日频只跑前向滤波；概率与均匀先验按 85:15 收缩，避免出现 0／1 的假精度。</td></tr>
<tr><td><strong>4 Research</strong></td><td>概率 × 经验信号库 → 目标敞口＋辩论</td><td>每个 Regime 一套预设目标敞口（见第五节表），<strong>加权而非开关</strong>；趋势确认度控制加仓步长；多空分析师各出证据，输出共识与分歧点。</td></tr>
<tr><td><strong>5 Portfolio + Risk</strong></td><td>风险预算 → 仓位＋压力测试</td><td>9% 年化波动目标、净敞口上限 55%、总敞口上限 110%、单资产目标上限 35%；空波动率需“低波概率 ≥ 45% 且 QVIX ≤ 20”双条件；压力测试窗口 2020-03／2022／2025-04／2018-02。</td></tr>
<tr><td><strong>6 Execution / Attribution</strong></td><td>成交 → 收益拆解 → 反馈</td><td>按次日收益结算（当日的权重只用当日及以前信息），单边成本 5bp，按资产腿拆解贡献，回撤区间再拆到“哪条腿亏的”。</td></tr>
</table>

<h3>3.1 Data Ingestion 的关键因子值（2026-04-14 收盘）</h3>
<table>
<tr><th style="width:26%">因子</th><th class="num">原始读数</th><th class="num">入模 z 值</th><th>当前含义</th></tr>
{factor_table}
</table>

<h3>3.2 宏观因子库（复用此前工程的月度序列，按发布日截断）</h3>
<table>
<tr><th style="width:22%">指标</th><th class="num">4-14 可得的最新读数</th><th class="num">历史分位</th><th>解读</th></tr>
<tr><td>CPI 同比（2026-03）</td><td class="num">+1.0%</td><td class="num">29.5%（n=220）</td><td>比 2 月的 1.3% 回落，<strong>中国端看不到“CPI 反弹”</strong></td></tr>
<tr><td>PPI 同比（2026-03）</td><td class="num">口径冲突：+0.5% / -0.6%</td><td class="num">不采用</td><td>两份历史工程对同一月份读数不一致，本期不把 PPI 具体数值放进模型，只保留“价格端由负转正”的方向</td></tr>
<tr><td>M2 同比（2026-03）</td><td class="num">+8.5%</td><td class="num">约 23%</td><td>货币端不宽，内需补库动能偏弱</td></tr>
<tr><td>制造业 PMI（2026-03）</td><td class="num">50.4</td><td class="num">约 46%</td><td>刚过荣枯线，扩张力度温和</td></tr>
</table>
<p class="small">口径说明：月度序列取自此前宏观月度展望工程的本地数据文件，按“发布日不晚于 2026-04-14”截断（3 月 CPI 于 4 月上旬发布、3 月 PMI 于 3 月 31 日发布；4 月数据 5 月才发布，<strong>不进入本期</strong>）。中国宏观端没有出现通胀加速，因此 4-14 的“通胀交易”判断主要由<strong>原油、股债相关性、美债曲线</strong>三条市场因子支撑——这也是它只有 88.75% 而不是 99% 的原因。</p>

<h2>四、Regime Agent：4-14 的概率分布，以及“平滑概率为什么不能用”</h2>
<img src="data:image/png;base64,{b64('regime.png')}" alt="Regime 滤波概率">
<p>模型在 4-14 的读数是<strong>通胀交易 88.75%</strong>，其余三态各 3.75%。这个结果不是拍出来的：它的驱动因子是股债相关性转正（+0.105）、美债 10Y-2Y 抬到 0.50、原油 60 日 +55%——正是“通胀交易”的教科书签名。</p>

<h3>4.1 当前概率与状态特征（可用于判断“这个状态长什么样”）</h3>
<div>
<table>
<tr><th>Regime</th><th class="num">滤波概率</th><th class="num">均匀先验</th></tr>
{prob_rows}
</table>
<p class="small">置信度（最大值）88.75%，归一化熵 0.343。概率高度集中，说明当期证据强；但也意味着<strong>“概率加权”在实践中会退化成近似开关</strong>——这是原文没有说的一个副作用，第七节会用它解释回撤形状。</p>
<h3 style="margin-top:22px">各状态的特征均值（原始单位，用来判断“这个状态长什么样”）</h3>
<table>
<tr><th>状态</th><th class="num">沪深300动量</th><th class="num">韩股动量</th><th class="num">原油动量</th><th class="num">标普波幅</th><th class="num">10Y-2Y</th><th class="num">股债相关</th></tr>
{mean_rows}
</table>
<p class="small">最近一次重估 {hmm['refit_last']}，训练样本 252 日。</p>
</div>
</div>

<h3>4.2 滤波 vs 平滑：为什么“看起来更准”恰恰是不能用的证据</h3>
<p>同一套参数、同一个 252 日窗口，只用<strong>截至当日</strong>的信息做前向滤波，和让整段窗口的信息回流做平滑，得到的是两条不同的概率曲线。</p>
<table>
<tr><th style="width:30%">指标（近 252 个交易日）</th><th class="num">滤波概率（实盘可用）</th><th class="num">平滑概率（含未来信息）</th><th>含义</th></tr>
<tr><td>Regime 切换次数</td><td class="num">{diag['switches_filtered']}</td><td class="num">{diag['switches_smoothed']}</td><td>平滑版把 6 次切换“整理”成 4 次，看起来更干净</td></tr>
<tr><td>通胀概率 与 未来 20 日原油收益 的相关系数</td><td class="num">{diag['ic']['filtered_inf_vs_fwd_oil']:.2f}</td><td class="num">{diag['ic']['smoothed_inf_vs_fwd_oil']:.2f}</td><td>平滑版“预测力”更高，因为它偷看了结果</td></tr>
<tr><td>低波概率 与 未来 20 日股票收益 的相关系数</td><td class="num">{diag['ic']['filtered_low_vs_fwd_eq']:.2f}</td><td class="num">{diag['ic']['smoothed_low_vs_fwd_eq']:.2f}</td><td>滤波版甚至是负的——真实世界里的低波状态不保证股票接下来涨</td></tr>
<tr><td>“通胀 → 未来油涨”方向命中率</td><td class="num">{diag['hit_inf_oil'][0]*100:.1f}%（n={diag['hit_inf_oil'][1]}）</td><td class="num strong">{diag['hit_inf_oil_sm'][0]*100:.1f}%（n={diag['hit_inf_oil_sm'][1]}）</td><td><strong>100% 命中率是未来函数的标准体征</strong></td></tr>
<tr><td>最大概率 &gt; 0.85 的交易日占比</td><td class="num">{diag['p_max_share']*100:.1f}%</td><td class="num">{diag['sm_max_share']*100:.1f}%</td><td>平滑版更容易“事后确信”</td></tr>
<tr><td>最近一次状态切换的确认日</td><td class="num">{diag['last_switch_filtered']}</td><td class="num">{diag['last_switch_smoothed']}</td><td>这次滤波反而早 8 个交易日，说明“平滑一定更早”也不成立——它只是更“对”</td></tr>
</table>
<div class="warn"><strong>如果拿平滑概率回测，你会得到一个漂亮的、无法复制的策略。</strong>平滑概率在 t 时刻已经用到了 t 之后的信息，所以它永远在拐点前“恰好”转向。实盘只有滤波版，命中率 {diag['hit_inf_oil'][0]*100:.1f}%，而且是 20 日方向口径。这条差异本身就是回测虚高的主要来源之一。</div>

<h2>五、Research Agent：概率加权后的信号、原油路径与多空辩论</h2>
<h3>5.1 目标敞口 = Σ（Regime 概率 × 该 Regime 下的经验信号）</h3>
<table>
<tr><th style="width:26%">资产／载体</th><th class="num">低波趋势</th><th class="num">通胀交易</th><th class="num">高波危机</th><th class="num">震荡未知</th><th class="num">概率加权结果</th></tr>
{sig_rows}
</table>
<p class="small">加权结果几乎是“通胀交易”那一列的复制品（88.75% 权重），这再次说明：<strong>概率一旦高度集中，加权和开关没有区别</strong>。这正是原文“不是开关”的说法在真实数据上会打折扣的地方。</p>

<h3>5.2 原油为什么是从 3% 慢慢加到 13%，而不是一次性切</h3>
<p>我的复现里，原油权重走的是同一条“台阶”形状，只是时点比原文早一个季度：</p>
<table>
<tr><th>日期</th><th class="num">原油权重</th><th class="num">科创50</th><th class="num">国债期货</th><th class="num">做空波动率</th><th class="num">通胀概率</th><th class="num">QVIX</th></tr>
{''.join(f"<tr><td>{d}</td><td class='num'>{o*100:.1f}%</td><td class='num'>{k*100:+.1f}%</td><td class='num'>{t*100:+.1f}%</td><td class='num'>{s*100:+.1f}%</td><td class='num'>{pi*100:.0f}%</td><td class='num'>{q:.1f}</td></tr>" for d, o, k, t, s, pi, q in oil_rows)}
</table>
<p>从 2025-10-26 的 <strong>2.9%</strong> 到 2025-12-14 的 <strong>12.4%</strong>，中间经过 6 个周度台阶，没有一步超过 4 个百分点；原文是 2 月初 3% 到 3 月中旬 13.3%，<strong>形状一致、时点不同（我的模型早了一个季度识别到通胀体制）</strong>。</p>
<p>三个机制共同造出这条曲线：</p>
<ol>
<li><strong>概率先动，仓位后动。</strong>Regime 概率在 2025-12-02 先切到“通胀交易”，但仓位要靠趋势确认一格格加上去。</li>
<li><strong>加仓按趋势确认度分档。</strong>三周期（20／60／120 日）同向时步长 0.35，只有一周期同向时步长 0.15，方向不一致时 0.10。所以“刚转正给 3%、多周期确认后给到 13%”是这套步长的自然结果，而不是人工设定。</li>
<li><strong>减仓比加仓快。</strong>反向或缩减时步长直接给 0.45，这就是为什么 2 月原油动量转平后，权重从 15.5% 很快回落到 10%——<strong>原文只强调了“渐进加”，没讲“渐进减”其实更快</strong>，这一点对回撤控制贡献更大。</li>
</ol>
<img src="data:image/png;base64,{b64('nav.png')}" alt="净值与回撤对照">

<h3>5.3 多空分析师的对抗性辩论（基于 4-14 数据）</h3>
<div class="grid2">
<div class="card">
<strong>看多分析师（通胀交易延续）</strong>
<ul>
<li>原油 60 日 +54.9%，中东冲突仍在，供给溢价没有消失；</li>
<li>股债 60 日相关性转正（+0.105），是通胀体制的典型特征，不是普通风险偏好上升；</li>
<li>美债 10Y-2Y 从 0.52 到 0.50 只是微幅走平，绝对水平 0.50 仍是正的、没有倒挂，对“通胀体制”不构成否定；</li>
<li>有色与原油同步走强，说明是“实物通胀”而非金融属性驱动。</li>
</ul>
</div>
<div class="card">
<strong>看空分析师（通胀交易已经过期）</strong>
<ul>
<li>原油 20 日动量 {pct(w_now/w20-1,2)}，<strong>突破已经是两个月前的事</strong>，现在买的是“昨天的新闻”；</li>
<li>50ETF QVIX {qv_now:.2f}，低于 20 日均值，波动率在回落而不是抬头，风险溢价不支持继续加风险；</li>
<li>沪深300 与科创50 年内几乎零涨幅，股票腿没有确认，商品单腿支撑的组合很脆；</li>
<li>10Y 只有 {us10:.2f}%，比“4.5% 陡峭化”的叙事低 24bp，<strong>利率端并不支持“通胀失控”</strong>。</li>
</ul>
</div>
</div>
<p><strong>共识：</strong>维持“通胀交易”状态判断，但<strong>不再加仓</strong>，并把商品腿的增量预算砍半。<strong>分歧点：</strong>看多派要加有色，看空派要降原油——裁决结果是“原油停在 10% 附近、有色小幅跟随”，因为原油 20 日动量为负，趋势确认度不给加仓步长。<strong>这就是辩论的实际作用：不是找方向，而是让“该不该加”这件事有反对票。</strong></p>

<h2>六、Portfolio + Risk：4-14 的最终仓位表</h2>
<table>
<tr><th>资产</th><th>载体（按你的执行约束）</th><th>方向</th><th class="num">权重</th><th class="num">风险贡献</th></tr>
{pos_rows}
</table>
<p class="small">组合年化波动（120 日协方差口径）{p['port_vol']*100:.1f}%，低于 9% 的目标——缩放系数按 60 日协方差计算，叠加敞口上限后进一步压缩；风险贡献按边际风险拆分，合计约 100%。</p>
<div class="note"><strong>这张表最重要的一行不是原油，而是风险贡献。</strong>原油 9.9% 的权重吃掉了 <strong>{p['risk_contrib']['oil']*100:.0f}%</strong> 的组合风险，有色再占 {p['risk_contrib']['nonferrous']*100:.0f}%——股票和债券加起来不到 1%。也就是说，这个“多资产组合”实际上是一个<strong>商品单腿策略</strong>，只是外面套了概率加权的壳。</div>

<h3>6.1 三条硬约束怎么落地</h3>
<ul>
<li><strong>净敞口上限 55%、总敞口上限 110%。</strong>实测平均净敞口 {exm['avg_net']*100:.1f}%、最高 {exm['max_net']*100:.1f}%；平均总敞口 {exm['avg_gross']*100:.1f}%、最高 {exm['max_gross']*100:.1f}%——上限从未被顶到，真正压住仓位的是 9% 的波动预算。</li>
<li><strong>做空波动率必须配对。</strong>按你的约束只做“卖近月、买远月宽跨”的价差，vega 名义敞口上限 -15%，且要求“低波概率 ≥ 45% 且 QVIX ≤ 20”。4-14 低波概率只有 3.75%，<strong>闸门直接关闭，仓位 0</strong>——这与原文“9 月加息后才降到 3% 以下”不同，我这版更保守：只要不是低波体制，一分钱不收。</li>
<li><strong>尾部压力测试反馈降仓。</strong>先用基准规则跑 2020-03／2022／2025-04，最差回撤 {min(v['maxdd'] for v in eng['stress_base'].values())*100:.2f}%，未触发 8% 的降仓阈值，因此降仓系数 = {eng['risk_mult']}。</li>
</ul>

<h3>6.2 压力测试：把规则扔进四次真实冲击</h3>
<table>
<tr><th>窗口</th><th class="num">区间收益</th><th class="num">最大回撤</th><th class="num">同期沪深300回撤</th><th>系统状态与动作</th></tr>
{stress_rows}
</table>
<p class="small">压力窗口的波动被压到 5% 左右，所以“回撤绝对值小”里有一半是“下注小”换来的，不能直接和满仓组合比。2018-02 因 HMM 需要 252 日样本（数据自 2017-04 起）而无法识别状态，只测了风险层，结论要打折扣。</p>

<h2>七、归因：这笔组合赚的是什么钱，最脆弱的地方在哪</h2>
<table>
<tr><th>收益来源</th><th class="num">2024-01～2026-04 累计贡献</th><th class="num">2026 年内贡献</th></tr>
{att_rows}
</table>
<p>把主区间（526 个交易日）拆开看：<strong>有色 +{att['nonferrous']*100:.1f}%、原油 +{att['oil']*100:.1f}%</strong>，两条商品腿贡献了绝大部分收益；股票腿合计只有 {(att['kc50']+att['hs300'])*100:.1f}%，国债期货 {(att['t10']*100):+.1f}%，做空波动率只有 {att['sv']*100:+.2f}%。</p>
<p><strong>赚的是什么钱：</strong>本质是“在通胀体制里做多商品、同时用国债空头对冲利率风险”的<strong>体制 beta</strong>，不是 alpha。Regime 层的作用是决定“什么时候敢把商品腿放大”，而不是选出哪个商品会涨。</p>
<div class="warn"><strong>最大脆弱点（按严重程度排序）：</strong>
<ol>
<li><strong>商品单腿集中度。</strong>风险贡献 85% 在原油、15% 在有色。原油一旦反向，股票和债券腿根本救不回来——它们合计风险贡献不到 1%。</li>
<li><strong>空波动率的尾部。</strong>本版闸门很严（只在 63 个交易日开启，累计贡献仅 {att['sv']*100:+.2f}%），所以尾部风险被压掉了，但代价是放弃了原文那条重要的收益来源；如果像原文那样长期维持 -14.7% 的 vega 敞口，一次 2020-03 式的波动率爆炸就能吃掉全年 carry。</li>
<li><strong>Regime 判断的路径依赖。</strong>4-14 的“通胀交易 88.75%”建立在“原油 60 日 +55%”这个<strong>存量</strong>事实上；一旦 20 日动量继续为负，概率会在几周内翻转，而仓位因为“减仓更快”会快速掉头——<strong>这既是保护，也是来回打脸的成本</strong>。</li>
<li><strong>数据缺口。</strong>美国 VIX 与信用利差拿不到，波动率体制的判断少了一只眼睛；用标普已实现波动率代理会系统性低估“隐含波动率跳升”的预警速度。</li>
</ol></div>

<h2>八、回到你的问题：-4.39% 的回撤，是怎么做到这么丝滑的</h2>
<p>先说清楚证据边界：<strong>原文正文没有出现 -4.39% 这个数字</strong>，只有“整体表现平稳”。这个数来自你转述的图表，我按“图表口径”理解，并用同口径复现做对照。</p>
<p>我复现的组合在同区间是 <strong>{main['anchor']['maxdd']*100:.2f}%</strong> 的最大回撤（年化波动 {main['anchor']['vol']*100:.1f}%），量级和 -4.39% 很接近。把它的来源拆开，答案是四层叠加，而不是某种神秘的对冲技巧：</p>
<table>
<tr><th style="width:24%">机制</th><th>量化证据</th><th style="width:22%">对回撤的贡献</th></tr>
<tr><td><strong>① 敞口被预算压小</strong></td><td>平均净敞口 {exm['avg_net']*100:.0f}%、总敞口 {exm['avg_gross']*100:.0f}%；9% 波动目标下，目标风险敞口 71.4% 被压缩到 19.3%（缩放系数约 0.27）</td><td>回撤绝对值天然只有满仓的约 1/3。<strong>同规则用 15% 波动预算，回撤 -8.31%</strong>，几乎线性放大</td></tr>
<tr><td><strong>② 概率加权 + 渐进建仓</strong></td><td>日均换手 {exm['turnover']*100:.2f}%，硬切换版本是 4.76%——<strong>低 37%</strong>；回撤区间只有 6 个交易日</td><td>少了在拐点被反复扫损的成本；但也意味着<strong>拐点确认慢</strong></td></tr>
<tr><td><strong>③ 危机状态切长债</strong></td><td>2020-03 平均 34% 长债、股票转空 → 区间 {stress['2020-03']['total']*100:+.1f}%、回撤 {stress['2020-03']['maxdd']*100:.1f}%；2022 平均 31.6% 长债 → {stress['2022']['total']*100:+.1f}%</td><td>这是<strong>唯一真正“对冲”</strong>的一层；没有它，2020-03 不会只有 -3.5%</td></tr>
<tr><td><strong>④ 空波动率收窄</strong></td><td>原文 -14.7% → -8.7% → &lt;3%；本版闸门更严，仅 63 天开启、贡献 {att['sv']*100:+.2f}%</td><td>抹平净值毛刺、压低波动；<strong>代价是尾部</strong></td></tr>
</table>

<h3>8.1 但这个“丝滑”有一个不能忽略的裂缝</h3>
<p>把最大回撤那一段放大：它发生在 <strong>{dd_ep['peak']} → {dd_ep['trough']}，只有 {dd_ep['days']} 个交易日，-{abs(dd_ep['depth'])*100:.2f}%</strong>，其中原油贡献 {dd_ep['contrib']['oil']*100:+.2f}%、有色 {dd_ep['contrib']['nonferrous']*100:+.2f}%、科创50 {dd_ep['contrib']['kc50']*100:+.2f}%。单日最大亏损 {fx['daily']['max_loss']*100:.2f}%（{fx['daily']['max_loss_date']}）。</p>
<p><strong>也就是说：它不是“慢慢磨”出来的回撤，而是一次 6 天内的商品集中度事件。</strong>日频净值图上看起来平滑，是因为① 敞口小 ② 事件短 ③ 后面很快修复。如果那一周原油跌的不是约 11%（权重 12.9% × 跌幅贡献 -1.37%）而是 25%，同样的结构会在一周内打出 -12%，而 196 天样本里根本没有这样的样本。</p>
<div class="note"><strong>可复现性评估（我的判断）：</strong>
<ul>
<li><strong>能复现的部分：</strong>“低波动预算 + 多资产 + 危机切债 + 空波动率收窄”确实能把 8% 波动口径的回撤压到 -5% 附近，我在 2024-01～2026-04 上复现出 {main['anchor']['maxdd']*100:.2f}%。</li>
<li><strong>不能复现的部分：</strong>① 原文 196 天里没有真正的流动性危机（2020-03 式的美元荒），我的 2018-02 测试显示<strong>没有 Regime 识别时，5 天就能打出 -5.58%</strong>；② 原文的阶段划分（3 月加原油、4 月加股票、6 月转空）与 4-14 的真实行情对不上，说明这段复盘是<strong>事后叙述</strong>，不能当作实时能力的证据；③ 图表口径未披露成本、杠杆和空波动率的名义定义，-4.39% 无法逐笔核对。</li>
<li><strong>结论：</strong>这套系统的“丝滑”是<strong>风险预算的算术 + 危机切债 + 短样本友好</strong>三者叠加，不是“预测准”。把它当实盘能力证明，会高估它在真正流动性危机里的表现。</li>
</ul></div>

<h2>九、如果把 4-15 真的当成决策日，我会怎么下这一手</h2>
<p>基于第六节仓位表，4-14 收盘后的动作是：<strong>原油停加（10%）、有色小幅跟随（5.8%）、股票腿只留 3.6%、国债期货空头 5.5%、做空波动率 0</strong>。理由不是“看空”，而是三条证据同时指向“通胀状态成立但动量已衰减”：</p>
<ol>
<li>原油 20 日动量已转平，趋势确认度不给加仓步长——这是渐进建仓规则的自然结果，不是主观判断；</li>
<li>QVIX {qv_now:.2f} 处于低位，做空波动率的赔率最差的时候恰恰是“大家都觉得没事”的时候，闸门关闭；</li>
<li>净敞口 19%，看起来“太保守”，但在 9% 波动预算下这已经是商品腿能给到的上限——<strong>如果想把敞口做大，正确做法是提高波动预算，而不是偷偷加杠杆。</strong></li>
</ol>
<p class="small">对应到原文：它在 4 月下旬把股票加到峰值、6 月底又整体转空。这种大幅切换在真实数据里需要 3-4 周的确认，做不到原文描述的那么“及时”。</p>

<h2>十、这份报告里，哪些是原文的、哪些是我加的</h2>
<table>
<tr><th style="width:16%">类别</th><th>内容</th></tr>
<tr><td>{chip('原文')}</td><td>六 Agent 角色划分；Regime Agent 用 HMM + 宏观因子聚类输出概率分布；Research Agent 的对抗性辩论；原油 2%→3%→13.3% 的渐进加仓；做空波动率 -14.7%→-8.7%→&lt;3%；Risk 与 Portfolio 共同确认敞口；Human-in/on-the-loop；196 个交易日复盘。</td></tr>
<tr><td>{chip('推演')}</td><td>“概率加权而非开关”的具体权重结构；趋势确认度分档（0.10／0.15／0.35）与减仓更快（0.45）；“滤波概率才能在实盘用”的判断；危机状态切长债的具体比例；空波动率闸门用 QVIX 而非 VIX。</td></tr>
<tr><td>{chip('假设')}</td><td>4 状态的具体定义与命名；252 日窗口、月度 EM 重估；协方差下限 1.5 与 85:15 均匀先验收缩（防后验硬化）；9% 波动目标、净敞口 55%／总敞口 110%、单资产 35% 上限；5bp 单边成本；空波动率用“方差风险溢价 + 跳空惩罚”近似定价；科创50 ETF 上市前用沪深300 代理。</td></tr>
</table>

<h2>十一、数据来源与口径</h2>
<ul class="small">
<li><strong>行情</strong>：新浪财经 ETF 日线（588000、510300、512400、501018）；东方财富全球指数（韩国综指）；新浪美股指数（标普500）；外盘期货（WTI、铜）；新浪国债期货主连（T／TF／TS，2017 起）。全部截至 2026-04-14。</li>
<li><strong>宏观</strong>：中美国债收益率与 10Y-2Y 利差（2015 起）；50ETF 与科创50 QVIX 期权隐含波动率。</li>
<li><strong>月度宏观序列</strong>：复用此前的宏观月度展望工程（<code>macro_monthly_report/data/latest.json</code>、<code>macro_monthly.csv</code> 与 2026 年国内月度序列文件），按发布日截断后取 CPI／PPI／M2／PMI 的最新可得值与历史分位。</li>
<li><strong>缺口</strong>：美国 VIX（三个公开源均被拒）、信用利差（中债收益率曲线接口无返回）——前者用标普 20 日已实现波动率代理，后者未入模。</li>
<li><strong>口径</strong>：收益率均为价格收益，不含分红与期货基差；国债期货用价格变动近似（不含 CTD 切换与 carry）；做空波动率为模型近似，非真实期权报价；所有回测按“当日收盘决策、次日收益结算”，不含未来信息。</li>
</ul>

<div class="warn"><strong>重要提醒（必须说在前面）：</strong>原文的 196 个交易日样本偏短，最大回撤 -4.39%（你转述的图表口径）<strong>没有经历过真正的流动性危机</strong>——2020-03 那种美元荒、ETF 折价、相关性全部打满的行情，和 2024-2026 的趋势市完全不是一回事。本报告的复现结果同样受此限制：2024-01～2026-04 是商品与风险资产普涨的窗口，任何“低回撤”结论都要打折看。</div>

<footer>
<p><strong>免责声明：</strong>本文为方法论研究与系统模拟，全部仓位、路径与归因均为模型输出，<strong>不构成任何投资建议</strong>，也不代表对原文策略真实业绩的认定。历史数据不代表未来表现，期货与期权含杠杆，可能损失超过本金。</p>
<p>复现口径：数据截止 2026-04-14 ｜ 526 个交易日主区间 ｜ 单边成本 5bp ｜ 生成于 2026-11-08</p>
</footer>
</div></body></html>"""

with open(OUT, "w", encoding="utf-8") as f:
    f.write(HTML)
print("written", OUT, len(HTML))