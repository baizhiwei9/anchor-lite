# -*- coding: utf-8 -*-
"""
Trade Agents 多交易员视角周度分析
================================
四个交易员独立分析 → 统一多空辩论 → 报告 + 飞书推送

交易员1：券商研报分析师（基本面）—— Tushare report_rc 行业评级聚合
交易员2：技术分析师（量化动量）—— 行业/宽基 ETF 20日动量 + 均线广度
交易员3：缠论分析师（techAnly）—— 全行业买点闸门扫描（三买/一买/二买）
交易员4：策略分析师（Anchor-lite）—— positions_snapshot 当前持仓/调仓

汇总：多空辩论 → 交叉确认 → 下周方向 + 可买行业结论
推送：飞书（lark-cli），消息头标注"报告来自 hermes.DeepSeek"

周日执行：crontab 配 0 18 * * 0
"""
import os, sys, json, subprocess
import pandas as pd
import numpy as np
from datetime import datetime

# 路径配置
WS = "/opt/data/home/.coze/agents/7705405429102444827/workspace"
DATAHUB = os.path.join(WS, "datahub-three-tier")
TECHANLY = "/tmp/techAnly"
ANCHOR_LITE = "/tmp/anchor-lite"

sys.path.insert(0, DATAHUB)
sys.path.insert(0, os.path.join(TECHANLY, "bin"))
sys.path.insert(0, os.path.join(TECHANLY, "smc"))

TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")
FEISHU_CHAT = os.environ.get("FEISHU_CHAT", "")  # AI股票消息 群

RATING_SCORE = {
    '买入':3,'强烈推荐':3,'强推':3,'买入-A':3,'买入-B':3,'推荐':2.5,
    '增持':2,'优于大市':2,'跑赢行业':2,'增持-A':2,'增持-B':2,
    '谨慎增持':1.5,'谨慎推荐':1.5,'持有':1,'中性':1,'减持':0,'卖出':0,
}

def load_etf(k):
    rows = json.load(open(f"/tmp/data/{k}.json"))
    s = pd.Series({r["date"]: r["close"] for r in rows}, name=k)
    s.index = pd.to_datetime(s.index)
    return s

SECTOR_ETF = {
    '银行':'bank_etf','半导体':'semi_etf','元器件':'semi_etf','芯片':'chip_etf',
    '人工智能':'ai_etf','证券':'zz500','新能源车':'nev_etf','医药':'cyb','医疗保健':'cyb',
    '黄金':'gold','白银':'silver_etf','原油':'yuanzhi','豆粕':'soy_etf',
    '沪深300':'hs300','中证500':'zz500','中证1000':'zz1000','科创50':'kc50','创业板':'cyb',
    '纳指':'nasdaq_etf','标普500':'sp500_etf','十年国债':'gz10y','30年国债':'bond30_etf',
}

def trader1_research(n_days=7):
    """券商研报分析师：行业评级聚合"""
    from data_hub import get_data
    end = pd.Timestamp.now().strftime('%Y%m%d')
    start = (pd.Timestamp.now() - pd.Timedelta(days=n_days)).strftime('%Y%m%d')
    df = get_data('research_report', start_date=start, end_date=end)
    if df is None or len(df)==0:
        return "研报数据不足"
    import tushare as ts
    pro = ts.pro_api(TUSHARE_TOKEN)
    pro._DataApi__http_url = "https://ttx.dailyfetch.top/"
    sb = pro.stock_basic(list_status='L', fields='ts_code,industry')
    df = df.merge(sb, on='ts_code', how='left')
    df['score'] = df['rating'].map(RATING_SCORE)
    g = df.dropna(subset=['industry','score']).groupby('industry').agg(
        n_report=('ts_code','count'), avg_score=('score','mean'),
        buy_ratio=('score', lambda x:(x>=2.5).mean())).reset_index()
    g = g[g['n_report']>=3].sort_values('n_report', ascending=False)
    bullish = g[(g['avg_score']>=2.3)&(g['n_report']>=5)].sort_values('avg_score', ascending=False)
    return bullish.head(8)

def trader2_momentum():
    """技术分析师：20日动量 + 均线广度"""
    rows = []
    for name, k in SECTOR_ETF.items():
        try:
            s = load_etf(k)
            if len(s) < 25: continue
            mom20 = s.iloc[-1]/s.iloc[-21]-1
            ma20 = s.tail(20).mean()
            above = s.iloc[-1] > ma20
            rows.append(dict(industry=name, mom20=mom20, above=above))
        except: pass
    df = pd.DataFrame(rows)
    return df

def trader3_chan():
    """缠论分析师：全行业买点扫描（跑 techAnly --scan-industries）"""
    env = dict(os.environ, TUSHARE_PROXY_TOKEN=TUSHARE_TOKEN, TECH_WS=TECHANLY,
               PYTHONPATH=f"{TECHANLY}/bin:{TECHANLY}/smc")
    try:
        r = subprocess.run(
            ["/tmp/venv/bin/python", f"{TECHANLY}/bin/tech_buypoint.py",
             "--scan-industries", "--level", "l1"],
            capture_output=True, text=True, timeout=500, env=env, cwd=TECHANLY)
        out = r.stdout
    except Exception as e:
        return f"缠论扫描失败: {str(e)[:100]}"
    # 解析买点行业
    buy_lines = []
    for ln in out.split("\n"):
        ln = ln.strip()
        if ln.startswith("🌙") and ("一买" in ln or "三买" in ln or "二买" in ln):
            buy_lines.append(ln)
    return buy_lines

def trader4_anchor_lite():
    """策略分析师：Anchor-lite 当前持仓"""
    p = os.path.join(ANCHOR_LITE, "positions_snapshot.json")
    if not os.path.exists(p):
        return "Anchor-lite 持仓快照不存在"
    d = json.load(open(p))
    rows = d.get("rows", [])
    holding = [r for r in rows if r.get("weight", 0) > 1.0]
    eq = d.get("equity_total", 0)
    bond = d.get("bond_total", 0)
    return dict(asof=d.get("asof"), holding=holding, equity=eq, bond=bond)

def build_report(r1, r2, r3, r4):
    """统一多空辩论 → 报告文本"""
    L = []
    A = L.append
    A("📊 Trade Agents 多交易员视角周度分析")
    A(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')} ｜ 报告来自 hermes.DeepSeek")
    A("═"*40)

    # 交易员1 研报
    A("【交易员1·券商研报】")
    if isinstance(r1, str):
        A(f"  {r1}")
    else:
        for _, r in r1.iterrows():
            A(f"  🌙 {r['industry']}：研报{r['n_report']:.0f}篇 评级{r['avg_score']:.1f} 买入{r['buy_ratio']*100:.0f}%")

    # 交易员2 动量
    A("")
    A("【交易员2·量化动量】")
    if len(r2):
        up = r2[r2['mom20']>0].sort_values('mom20', ascending=False)
        A(f"  多头：{', '.join(f'{x.industry}({x.mom20*100:+.1f}%)' for _,x in up.head(5).iterrows()) or '无'}")
        breadth = r2['above'].mean()
        A(f"  站上20日线广度：{breadth*100:.0f}%（{'偏强' if breadth>0.6 else '偏弱' if breadth<0.4 else '均衡'}）")

    # 交易员3 缠论
    A("")
    A("【交易员3·缠论买点】")
    if isinstance(r3, str):
        A(f"  {r3}")
    else:
        for ln in r3[:8]:
            A(f"  {ln}")

    # 交易员4 Anchor-lite
    A("")
    A("【交易员4·Anchor-lite策略】")
    if isinstance(r4, str):
        A(f"  {r4}")
    else:
        A(f"  截至 {r4['asof']}：股票 {r4['equity']}% / 债券 {r4['bond']}%")
        for h in r4['holding']:
            A(f"  · {h['name']} {h['weight']:.1f}%")

    # 统一辩论
    A("")
    A("【多空辩论 · 统一结论】")
    # 交叉：研报看好 + 动量正 + 缠论有买点
    research_bull = set(r1['industry'].tolist()) if not isinstance(r1,str) else set()
    mom_bull = set(r2[r2['mom20']>0]['industry'].tolist()) if len(r2) else set()
    chan_bull = set()
    if not isinstance(r3,str):
        for ln in r3:
            parts = ln.split()
            if len(parts) >= 2:
                chan_bull.add(parts[1])
    # 三多确认
    triple = set()
    for ind in research_bull | mom_bull | chan_bull:
        cnt = 0
        if ind in research_bull: cnt+=1
        if ind in mom_bull: cnt+=1
        # 缠论行业名匹配（模糊）
        if any(ind[:2] in c or c[:2] in ind for c in chan_bull): cnt+=1
        if cnt>=2:
            triple.add(ind)
    if triple:
        A(f"  ✅ 多视角共振行业：{', '.join(sorted(triple))}")
    else:
        A("  ⚠️ 无多视角共振行业，市场分歧较大")
    A("  ⚠️ 技术结构分析，不构成投资建议。")
    return "\n".join(L)

def push_feishu(text):
    """通过 lark-cli 发飞书消息"""
    # 消息头
    text = "📎 报告来自 hermes.DeepSeek\n" + text
    try:
        r = subprocess.run(
            ["lark-cli", "im", "+messages-send", "--as", "bot",
             "--chat-id", FEISHU_CHAT, "--text", text],
            capture_output=True, text=True, timeout=30)
        return r.returncode == 0
    except Exception as e:
        print("推送失败:", e)
        return False

def main(push=False):
    print("开始 Trade Agents 周度分析...")
    r1 = trader1_research()
    print("  ✅ 交易员1 券商研报完成")
    r2 = trader2_momentum()
    print("  ✅ 交易员2 量化动量完成")
    r3 = trader3_chan()
    print("  ✅ 交易员3 缠论买点完成")
    r4 = trader4_anchor_lite()
    print("  ✅ 交易员4 Anchor-lite完成")
    report = build_report(r1, r2, r3, r4)
    print("\n" + report)
    if push:
        ok = push_feishu(report)
        print(f"\n飞书推送: {'成功' if ok else '失败'}")
    # 保存报告
    out = f"/tmp/trade_agents_{datetime.now().strftime('%Y%m%d')}.txt"
    open(out, 'w').write(report)
    print(f"报告已保存: {out}")

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true")
    args = ap.parse_args()
    main(push=args.push)
