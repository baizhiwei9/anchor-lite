# -*- coding: utf-8 -*-
"""生成：期权/期货信号源回测报告（Anchor-Lite 第十一期）"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import engine3 as E, dyn3 as D3

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "期权期货信号源回测报告.html")
fin = json.load(open(os.path.join(BASE, "dyn5_final.json")))
panel, rets = E.load_panel(full=True)
d = panel.loc["2026-01-05":"2026-11-06"]

# ---- 原版（dyn3）科创50轨迹 ----
t3 = pd.read_csv(os.path.join(BASE, "kc50_trail.csv"), parse_dates=["date"]).set_index("date")
t3["kret"] = d["kc50"].pct_change().reindex(t3.index).fillna(0)
# ---- 新版（dyn5）轨迹 ----
t5 = pd.DataFrame(fin["monthly"])
tl5 = pd.read_csv(os.path.join(BASE, "dyn5_trail.csv"), parse_dates=["date"]).set_index("date")
tl5["kret"] = d["kc50"].pct_change().reindex(tl5.index).fillna(0)

m3 = {"total": 0.1424, "maxdd": -0.0378, "vol": 0.0922, "sharpe": 1.96, "n": 24}
m5 = fin["metrics"]; cfg = fin["cfg"]
c10 = fin["cost10"]; H1 = fin["H1"]; H2 = fin["H2"]

# 月度对照
rows = []
for mo in ["2026-01","2026-02","2026-03","2026-04","2026-05","2026-06","2026-07","2026-08","2026-09","2026-10","2026-11"]:
    g3 = t3.loc[mo]; g5 = tl5.loc[mo]
    kr = float((1 + d["kc50"].pct_change().loc[mo]).prod() - 1) if mo != "2026-01" else float(d["kc50"].loc[:"2026-01-31"].iloc[-1] / d["kc50"].iloc[0] - 1)
    rows.append(dict(mo=mo[5:], w3=g3.kc50_w.mean(), c3=(g3.kc50_w*g3.kret).sum(),
                     w5=g5.kc50.mean(), c5=(g5.kc50*g5.kret).sum(), kr=kr))
mo_rows = "".join(f"<tr><td>{r['mo']}月</td><td class='num'>{r['kr']*100:+.1f}%</td>"
                  f"<td class='num'>{r['w3']*100:.1f}%</td><td class='num'>{r['c3']*100:+.2f}</td>"
                  f"<td class='num'>{r['w5']*100:.1f}%</td><td class='num'>{r['c5']*100:+.2f}</td></tr>" for r in rows)

# ---- 信号 IC（重算，仅用 2026 年内样本）----
P = pd.read_csv(os.path.join(BASE, "data_signals/opt_panel.csv"), parse_dates=["date"])
KK = P[P.code.astype(str) == "588000"].set_index("date").sort_index()
def zs(s, w=60): return (s - s.rolling(w).mean()) / s.rolling(w).std()
KK["z_pcr"] = zs(KK.pcr_vol); KK["z_skew"] = zs(KK["skew"]); KK["z_iv"] = zs(KK.iv_atm)
KK["z_poi"] = zs(KK.pcr_oi); KK["z_oi"] = zs(KK.oi_net)
KK["rv20"] = d["kc50"].pct_change().rolling(20).std() * np.sqrt(252)
KK["vrp"] = KK.iv_atm - KK.rv20
fwd5 = d["kc50"].shift(-5) / d["kc50"] - 1; fwd20 = d["kc50"].shift(-20) / d["kc50"] - 1
ic = {}
for k, lab in [("z_pcr", "PCR 成交量 z"), ("z_poi", "PCR 持仓量 z"), ("z_skew", "偏度 z"), ("z_iv", "ATM 隐含波动 z"),
               ("z_oi", "期权持仓净增 z"), ("vrp", "隐含-已实现溢价")]:
    s = KK[k].reindex(d.index)
    ic[k] = (s.corr(fwd5, method="spearman"), s.corr(fwd20, method="spearman"), lab)
ic_rows = "".join(f"<tr><td>{v[2]}</td><td class='num'>{v[0]:+.2f}</td><td class='num'>{v[1]:+.2f}</td>"
                  f"<td>{'逆向（高=恐慌→后续偏涨）' if v[1] > 0.15 else ('顺向偏弱' if v[1] > 0 else '与后续收益反向')}</td></tr>"
                  for k, v in ic.items())

# ---- 调仓要点 ----
key = []
for t in fin["trades"]:
    if "科创" in t["action"]:
        key.append(t)
def fz(v):
    return "—" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:+.2f}"
kc_tr = "".join(f"<tr><td>{t['date']}</td><td>{t['action']}</td><td class='num'>{t['r20']*100:+.1f}%</td>"
                f"<td class='num'>{fz(t['ziv'])}</td><td class='num'>{fz(t['zsk'])}</td>"
                f"<td>{'过热' if t['overheat'] else '正常'}</td></tr>" for t in key)

# ---- 邻域 ----
nb_rows = "".join(f"<tr><td>{k}</td><td class='num'>{v['total']*100:+.1f}%</td>"
                  f"<td class='num'>{v['maxdd']*100:.2f}%</td><td class='num'>{v['vol']*100:.1f}%</td></tr>"
                  for k, v in fin["neighbors"].items())

# ---- 科创50权重走势（内联 SVG）----
idx = t3.index.intersection(tl5.index)
def path(series, lo, hi, w=920, h=150):
    v = series.reindex(idx).ffill().values
    n = len(v); x = np.linspace(8, w - 8, n)
    y = h - 14 - (v - lo) / (hi - lo) * (h - 28)
    return "M" + " L".join(f"{a:.1f},{b:.1f}" for a, b in zip(x, y)), x, y
lo, hi = 0, 0.26
p3, xs, y3 = path(t3.kc50_w, lo, hi)
p5, _, y5 = path(tl5.kc50, lo, hi)
xt = [idx[0], idx[len(idx)//3], idx[2*len(idx)//3], idx[-1]]

HTML = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>换信号源：期权 PCR / 偏度 / 持仓量 驱动科创50与原油仓位</title>
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
<div class="ttl">换信号源：期权 PCR、偏度、持仓量驱动仓位<br><span style="font-size:16px;color:var(--sub)">科创板建仓与止盈核查 · 2026-01-05 至 2026-11-06</span></div>
<div class="meta">数据截止 2026-11-06 收盘｜样本 203 个交易日｜32 只 ETF + 上交所 5 个期权标的 + 7 个期货主力｜单边成本 5bp｜成本与信号只用当日及以前信息</div>
</header>

<div class="lead">
<b>先说结论。</b>原版策略<b>一直持有科创50</b>，202 个交易日里没有一天空仓，但 8% 组合波动目标把它压到 4–5%，1–6 月科创腿只贡献 +3.87 个百分点，而同期资产本身涨了 <b>58.5%</b>——买到了，但只赚到零头。6 月波动率确实高（20 日已实现 46%→55%），可 6 月是全年最强月份（+27.1%），「波动高就减」会砍掉最赚钱的一个月；真正的顶在 6 月 30 日至 7 月 1 日。
把信号换成期权期货后（PCR、25Δ 偏度、ATM 隐含波动、期权与期货持仓量），科创腿贡献从 +1.79pp 升到 <b>+4.31pp</b>，7 月少亏 1.4pp；全组合 <b>{m5['total']*100:+.2f}%</b>、最大回撤 <b>{m5['maxdd']*100:.2f}%</b>、夏普 {m5['sharpe']:.2f}，<b>收益与固定配比（+17.5%）基本持平、回撤更大</b>。改善发生在单腿，没有变成组合层的超额。
</div>

<div class="kpi">
<div class="card"><div class="v">{m5['total']*100:+.2f}%</div><div class="l">区间收益（203 日）</div></div>
<div class="card"><div class="v">{m5['maxdd']*100:.2f}%</div><div class="l">最大回撤（限 5%）</div></div>
<div class="card"><div class="v">{fin['kc_contrib']*100:+.2f}pp</div><div class="l">科创50 腿贡献（原版 +1.79pp）</div></div>
<div class="card"><div class="v">{len(fin['trades'])}</div><div class="l">调仓笔数</div></div>
</div>

<h1 class="sec">一、核查：科创板到底买了没有、止盈没有</h1>
<p>把原版（三条动态规则）的科创 50 逐日持仓还原，答案是：<b>买了，而且一直没卖光；但仓位被组合级波动预算压着，赚到的只是零头。</b></p>
<table>
<tr><th>月份</th><th>科创50 月涨跌</th><th>原版权重均值</th><th>原版贡献 pp</th><th>新版权重均值</th><th>新版贡献 pp</th></tr>
{mo_rows}
</table>
<p class="small">贡献 = Σ(当日权重 × 当日科创50 涨跌)，单位百分点。原版 1–6 月合计 +3.87pp，新版 +7.86pp。</p>

<h2 class="sub">1.1 建仓与止盈的具体时点</h2>
<ul>
<li><b>建仓</b>：不是某天才买，而是从 1 月 5 日起就带 10% 底仓（1 月均值 10.4%）。2 月起组合波动目标把总仓压到 30–40%，科创 50 随之降到 4.5–5%。</li>
<li><b>止盈</b>：原版全年只在 <b>6 月 3 日</b>卖过一次 6.0pp（10.9%→5.1%），触发的是「权重偏离目标 6pp」的再平衡，<b>不是状态切换止盈</b>——原版引擎里 Regime 八态已在上一版被移除，不再驱动仓位。</li>
<li><b>6 月的「减仓」</b>：确实减了，但机制是波动率定仓——20 日波动从 46% 升到 55%，目标权重反向缩到 4.8%，6 月 16 日回撤熔断又把持仓砍半到 2.9%。<b>减仓方向对，时点错</b>：6 月科创 50 还涨 27.1%。</li>
</ul>

<h2 class="sub">1.2 科创板权重：原版 vs 新版</h2>
<svg viewBox="0 0 920 150" width="100%" height="150" role="img" aria-label="科创50权重走势对比">
<line x1="8" y1="136" x2="912" y2="136" stroke="#E3E7EE"/>
<line x1="8" y1="8" x2="912" y2="8" stroke="#F1F3F7"/>
<path d="{p3}" fill="none" stroke="#9AA6B6" stroke-width="1.6"/>
<path d="{p5}" fill="none" stroke="#1F3A5F" stroke-width="2.2"/>
<text x="12" y="20" font-size="11" fill="#5A6472">26%</text>
<text x="12" y="132" font-size="11" fill="#5A6472">0%</text>
<text x="12" y="148" font-size="11" fill="#9AA6B6">灰线=原版（三条动态规则）　深蓝=新版（期权期货信号）</text>
</svg>

<h1 class="sec">二、6 月该不该减：数据说了两件事</h1>
<table>
<tr><th>时点</th><th>20 日涨幅</th><th>20 日波动</th><th>ATM 隐含波动</th><th>隐含波动 z</th><th>后续 20 日</th></tr>
<tr><td>2026-06-03</td><td class="num">+4.5%</td><td class="num">50.6%</td><td class="num">0.505</td><td class="num">+1.69</td><td class="num">+16.2%</td></tr>
<tr><td>2026-06-30</td><td class="num">+33.6%</td><td class="num">46.8%</td><td class="num">0.540</td><td class="num">+1.03</td><td class="num">-23.8%</td></tr>
<tr><td>2026-07-01</td><td class="num">+28.6%</td><td class="num">48.7%</td><td class="num">0.706</td><td class="num">+2.58</td><td class="num">-22.4%</td></tr>
</table>
<p>6 月 3 日波动率已经很高（50.6%），但当时 20 日涨幅只有 4.5%，属于「高波动、无过热」，事后 20 日仍涨 16.2%；<b>7 月 1 日才是三类条件同时成立的点</b>——20 日涨 28.6%、创新高、隐含波动一天跳到 0.706（z=2.58）。所以止盈条件不能只看波动率，必须是「涨幅过热 + 期权定价极端 + 创新高」三项叠加，这也是新版引擎里过热状态机的判据。</p>

<h1 class="sec">三、换信号源：四类期权期货信号怎么算</h1>
<table>
<tr><th>信号</th><th>原始数据</th><th>构造</th><th>用于</th></tr>
<tr><td>PCR（成交量）</td><td>上交所每日期权统计：认购/认沽成交量</td><td>认沽成交量 ÷ 认购成交量，取 60 日 z 值</td><td>对冲需求高低 → 科创50 加/减</td></tr>
<tr><td>PCR（持仓量）</td><td>同表：未平仓认购/认沽合约数</td><td>认沽 OI ÷ 认购 OI，取 z 值</td><td>存量情绪，交叉验证</td></tr>
<tr><td>偏度</td><td>期权风险指标：逐合约 DELTA 与隐含波动</td><td>中位 IV(认沽 Δ∈[-0.40,-0.15]) − 中位 IV(认购 Δ∈[0.15,0.40])，即 25Δ 风险逆转</td><td>恐慌/贪婪 → 逆向加仓、过热减仓</td></tr>
<tr><td>持仓量（期货）</td><td>SC 原油、IF 股指主力连续：成交量、持仓量</td><td>5 日持仓变化 × 5 日价格方向（增仓上行=多头进攻）</td><td>原油加/减仓</td></tr>
</table>
<p class="small">偏度全年均值 +5.4pp（认沽比认购贵 5.4 个波动点），2026-06 均值升到 +11.9pp 为全年最高，与 7 月下跌方向一致；但 2026 全年「偏度高位」整体偏逆向（见下表 IC 为正），说明它更适合做「恐慌后加仓」，不适合当单边做空信号。</p>

<h2 class="sub">3.1 预测力检验（科创50，n=203，Spearman IC）</h2>
<table>
<tr><th>信号</th><th>未来 5 日 IC</th><th>未来 20 日 IC</th><th>性质</th></tr>
{ic_rows}
</table>
<p class="small">偏度 z 的 20 日 IC 达 +0.47，是四个信号里最强的；PCR 持仓量口径与后续收益反向（-0.18），所以新版只把它当交叉验证，不用它定方向。IC 由同一份 2026 年样本计算，属样本内统计，不能当样本外能力。</p>

<h1 class="sec">四、回测：新信号驱动仓位</h1>
<p>规则：科创 50 目标权重 = 10% + 5%×信号分（趋势确认 +1、隐含波动平静 +1、PCR 低 +1、恐慌极端 +1、过热 −2/−3），区间 3%–20%；原油 = 15% + 5%×信号分（增仓上行 +1、增仓下行 −1、跌 12% +1、跌 25% +1、20 日趋势 +0.5），区间 5%–25%；纳指按动量 5%–20%；债券按剩余额度分摊；组合回撤 2.5% 熔断（只削风险腿）；科创与原版一致做 5 日急跌止损。</p>
<div class="kpi">
<div class="card"><div class="v">{m5['total']*100:+.2f}%</div><div class="l">区间收益</div></div>
<div class="card"><div class="v">{m5['maxdd']*100:.2f}%</div><div class="l">最大回撤</div></div>
<div class="card"><div class="v">{m5['vol']*100:.1f}%</div><div class="l">年化波动</div></div>
<div class="card"><div class="v">{m5['sharpe']:.2f}</div><div class="l">夏普</div></div>
</div>

<h2 class="sub">4.1 科创50 相关调仓（全部日期）</h2>
<table>
<tr><th>日期</th><th>动作</th><th>20 日涨幅</th><th>隐含波动 z</th><th>偏度 z</th><th>状态</th></tr>
{kc_tr}
</table>
<p class="small">7 月 14 日那次买入是 20 日涨幅回落到 20.2%、隐含波动 z 降到 1.16（过热状态仍在）后按目标权重补仓，随后 7 月 16 日熔断触发又减半——这段来回是熔断阈值翻转造成的，属已知缺陷。</p>

<h1 class="sec">五、对照与稳健性</h1>
<table>
<tr><th>方案</th><th>收益</th><th>最大回撤</th><th>年化波动</th><th>夏普</th></tr>
<tr><td><b>新版：期权期货信号定仓</b></td><td class="num">{m5['total']*100:+.2f}%</td><td class="num">{m5['maxdd']*100:.2f}%</td><td class="num">{m5['vol']*100:.1f}%</td><td class="num">{m5['sharpe']:.2f}</td></tr>
<tr><td>固定配比 55/15/15/10/5 + 3% 熔断</td><td class="num">+17.50%</td><td class="num">-4.06%</td><td class="num">9.7%</td><td class="num">2.30</td></tr>
<tr><td>原版：三条动态规则</td><td class="num">+14.24%</td><td class="num">-3.78%</td><td class="num">9.2%</td><td class="num">1.96</td></tr>
<tr><td>四维度宏观标签配仓（2% 熔断）</td><td class="num">-0.20%</td><td class="num">-4.21%</td><td class="num">8.3%</td><td class="num">-0.02</td></tr>
<tr><td>四项工程改动叠加</td><td class="num">-3.50%</td><td class="num">-7.82%</td><td class="num">6.1%</td><td class="num">-0.71</td></tr>
</table>
<table>
<tr><th>稳健性</th><th>收益</th><th>回撤</th><th>说明</th></tr>
<tr><td>基准配置</td><td class="num">{m5['total']*100:+.1f}%</td><td class="num">{m5['maxdd']*100:.2f}%</td><td>熔断 2.5%、带宽 4pp、原油止损 8%</td></tr>
<tr><td>成本 10bp</td><td class="num">{c10['total']*100:+.1f}%</td><td class="num">{c10['maxdd']*100:.2f}%</td><td>换手成本加倍，收益降 0.4pp</td></tr>
<tr><td>上半年 / 下半年</td><td class="num">{H1[0]*100:+.1f}% / {H2[0]*100:+.1f}%</td><td class="num">{H1[1]*100:.2f}% / {H2[1]*100:.2f}%</td><td>收益几乎全部来自上半年</td></tr>
</table>
<table>
<tr><th>参数邻域</th><th>收益</th><th>回撤</th><th>波动</th></tr>
{nb_rows}
</table>
<p class="small">邻域 12.7%–17.5%、回撤 4.68%–5.27%：其中熔断改 3%、带宽改 3pp、原油止损收紧到 6% 会破 5% 线；参数在样本内挑选，存在选择偏差。</p>

<h1 class="sec">六、为什么没有超过固定配比</h1>
<ol>
<li><b>收益锚仍是原油。</b>全年各腿贡献：原油 +20.2pp、科创50 +4.3pp、纳指 +2.6pp、十年国债 +0.9pp。期权信号改善的是科创 50 这条腿（1.8→4.3pp），体量太小，压不过原油的波动。</li>
<li><b>新版把回撤放大了 0.9pp。</b>原油上限提到 25%、加 8% 急跌止损，换来更陡的收益曲线，也带来更深的坑；固定配比的原油只有 15% 且不择时，回撤反而更小。</li>
<li><b>5 月出现信号抖动。</b>5 月 7–15 日与 5 月 25–29 日两组「卖 4%→买 5.1%→卖 4.8%」的来回，是过热状态机与带宽同时触发造成的噪声交易，全年 63 笔里有 8 笔集中在 5 月这两段，来回方向相反。</li>
<li><b>期权信号有 60 日前置。</b>z 值需要 60 个交易日，1–3 月没有可用的期权信号，那三个月走的是基准权重，等于「新信号只覆盖了 4 月以后」。</li>
</ol>

<h1 class="sec">七、数据分层与局限</h1>
<table>
<tr><th>层级</th><th>内容</th></tr>
<tr><td>真实数据</td><td>32 只 ETF 日线（新浪）；上交所 5 个标的每日认购/认沽成交量与未平仓（2026-01-05 至 11-06，203 天全覆盖）；逐合约 DELTA 与隐含波动（13.8 万条）；SC/IF/IH/IC/IM/AU/CU 主力连续含成交量与持仓量（各 203 天）</td></tr>
<tr><td>代理</td><td>偏度用「Δ 区间中位 IV 之差」近似 25Δ 风险逆转，未做严格 delta 插值；组合波动率用 60 日协方差；美股波动用标普 20 日已实现波动（VIX 三源均 403）</td></tr>
<tr><td>工程假设</td><td>信号到仓位的线性映射、3 日信号平滑、过热状态机「连续 2 日进入 + 最少 10 日」、5 日急跌止损、2.5% 熔断只削风险腿、单边 5bp 成本</td></tr>
<tr><td>不可得</td><td>ETF 无成分股财务数据；国债期货基差需 CTD 券与转换因子；无分钟级成交与逐笔委托；无个股层面持仓；期权无完整历史链（仅有交易所日统计与风险指标）</td></tr>
</table>
<div class="warn">
<b>需要提醒的三件事。</b>①样本只有 203 个交易日、单一自然年，任何「跑赢」都不构成能力证明；②原版 −4.39% 的回撤（原文图表口径）与这里的 −4.96% 一样，都没经历过真正的流动性危机（2020-03 那种全资产同跌），不能当实盘抗压证明；③参数在同一样本内挑选，邻域里已有三个组合破 5% 线，说明结论对参数敏感。
</div>

<h1 class="sec">八、下一步该动哪里</h1>
<ul>
<li><b>先把 5 月的噪声交易堵掉</b>：过热状态进入后禁止反向加仓（单向棘轮），或把带宽从 4pp 提到 6pp，预期减少 6–8 笔换手，代价是 4 月那波晚 2–3 天。</li>
<li><b>把期权信号用在原油上</b>：SC 有真实持仓量但无期权，若能补到原油期权（或上海原油期货 PCR 代理），原油这条 20pp 的腿才可能被信号约束，而不是只靠「跌深加仓」。</li>
<li><b>做真正的样本外</b>：用 2025 年参数、2026 年检验，或用 2024 年做训练、2026 年做检验。当前所有结论都是样本内。</li>
</ul>

<footer>
数据截止 2026-11-06 收盘；区间 2026-01-05 至 2026-11-06（203 个交易日）。<br>
本内容由人工智能基于公开数据回测生成，仅供研究与交流，不构成任何投资建议、收益承诺或交易依据。市场有风险，投资需谨慎。历史回测结果不代表未来表现，样本期短、参数经样本内挑选，存在过拟合风险。
</footer>
</div></body></html>"""
open(OUT, "w", encoding="utf-8").write(HTML)
print("写出", OUT, len(HTML), "字符")
