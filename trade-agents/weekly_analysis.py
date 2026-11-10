# -*- coding: utf-8 -*-
"""
周度 AI 分析 —— 券商研报多空辩论 + 量化信号
借鉴 TradingAgents 的多空研究员辩论机制：
  券商分析师(基本面): 本周研报按行业聚合评级 → 乐观度
  技术分析师(量化): 行业ETF动量 + 均线趋势
  多空研究员辩论 → 下周涨跌方向思路
"""
import sys, json
import pandas as pd
import numpy as np
sys.path.insert(0, '.')
from tushare_init import pro

# ============ 1. 券商研报(基本面) ============
RATING_SCORE = {
    '买入': 3, '强烈推荐': 3, '强推': 3, '买入-A': 3, '买入-B': 3, '推荐': 2.5,
    '增持': 2, '优于大市': 2, '跑赢行业': 2, '增持-A': 2, '增持-B': 2,
    '谨慎增持': 1.5, '谨慎推荐': 1.5, '持有': 1, '中性': 1,
    '减持': 0, '卖出': 0,
}

def fetch_weekly_reports(n_days=7):
    """拉最近 n_days 的研报, 合并行业"""
    import datetime
    end = pd.Timestamp.now().strftime('%Y%m%d')
    start = (pd.Timestamp.now() - pd.Timedelta(days=n_days)).strftime('%Y%m%d')
    df = pro.report_rc(start_date=start, end_date=end)
    if df is None or len(df) == 0:
        return None
    # 行业映射
    sb = pro.stock_basic(list_status='L', fields='ts_code,industry')
    df = df.merge(sb, on='ts_code', how='left')
    df['score'] = df['rating'].map(RATING_SCORE)
    return df

def industry_sentiment(df):
    """按行业聚合: 研报覆盖数 + 平均评级分 + 买入率"""
    df = df.dropna(subset=['industry', 'score'])
    g = df.groupby('industry').agg(
        n_report=('ts_code', 'count'),
        avg_score=('score', 'mean'),
        buy_ratio=('score', lambda x: (x >= 2.5).mean()),
    ).reset_index()
    g = g[g['n_report'] >= 3]  # 至少3篇研报才统计
    g = g.sort_values('n_report', ascending=False)
    return g

# ============ 2. 量化信号(技术面) ============
def load_etf(k):
    rows = json.load(open(f"/tmp/data/{k}.json"))
    s = pd.Series({r["date"]: r["close"] for r in rows}, name=k)
    s.index = pd.to_datetime(s.index)
    return s

# 行业/宽基 ETF 动量
SECTORS = {
    '银行': 'bank_etf', '半导体': 'semi_etf', '芯片': 'chip_etf',
    '新能源车': 'nev_etf', '人工智能': 'ai_etf',
    '沪深300': 'hs300', '中证500': 'zz500', '中证1000': 'zz1000',
    '科创50': 'kc50', '创业板': 'cyb', '纳指': 'nasdaq_etf', '标普500': 'sp500_etf',
    '黄金': 'gold', '白银': 'silver_etf', '原油': 'yuanzhi', '豆粕': 'soy_etf',
    '十年国债': 'gz10y', '30年国债': 'bond30_etf',
}

def technical_view():
    """各标的 20日动量 + 均线位置, 输出技术面观点"""
    views = []
    for name, k in SECTORS.items():
        try:
            s = load_etf(k)
            mom20 = s.iloc[-1] / s.iloc[-21] - 1 if len(s) > 21 else 0
            ma20 = s.tail(20).mean()
            above_ma = s.iloc[-1] > ma20
            views.append(dict(name=name, mom20=mom20, above_ma=above_ma))
        except Exception:
            pass
    return pd.DataFrame(views)

# ============ 3. 多空辩论 ============
def debate(reports, tech):
    """输出多空辩论报告"""
    print("=" * 78)
    print("  周度 AI 分析 —— 券商研报 × 量化信号 多空辩论")
    print("  生成时间:", pd.Timestamp.now().strftime('%Y-%m-%d %H:%M'))
    print("=" * 78)

    # 券商分析师(基本面)
    print("\n【一、券商分析师（基本面）】")
    senti = industry_sentiment(reports)
    if senti is None or len(senti) == 0:
        print("  本周研报数据不足")
    else:
        top = senti.head(8)
        print(f"  {'行业':<10}{'研报数':>6}{'平均评级分':>10}{'买入占比':>10}")
        for _, r in top.iterrows():
            print(f"  {r['industry']:<10}{r['n_report']:>6}{r['avg_score']:>10.2f}{r['buy_ratio']*100:>9.0f}%")
        # 最看好的行业(研报多 + 评级高)
        bullish = senti[(senti['avg_score'] >= 2.5) & (senti['n_report'] >= 5)].head(5)
        print(f"\n  券商最看好: {', '.join(bullish['industry'].tolist()) if len(bullish) else '无明确'}")
        bearish = senti[(senti['avg_score'] < 1.5)].head(5)
        print(f"  券商最看空: {', '.join(bearish['industry'].tolist()) if len(bearish) else '无明确'}")

    # 技术分析师(量化)
    print("\n【二、技术分析师（量化信号）】")
    if len(tech) > 0:
        up = tech[tech['mom20'] > 0.02].sort_values('mom20', ascending=False)
        down = tech[tech['mom20'] < -0.02].sort_values('mom20')
        print(f"  20日动量最强: ", end='')
        for _, r in up.head(6).iterrows():
            print(f"{r['name']}({r['mom20']*100:+.1f}%) ", end='')
        print()
        print(f"  20日动量最弱: ", end='')
        for _, r in down.head(6).iterrows():
            print(f"{r['name']}({r['mom20']*100:+.1f}%) ", end='')
        print()
        # 均线多头占比(市场广度)
        breadth = tech['above_ma'].mean()
        print(f"  站上20日均线比例(广度): {breadth*100:.0f}%")
        if breadth > 0.6:
            print("  → 技术面：多头占优，市场偏强")
        elif breadth < 0.4:
            print("  → 技术面：空头占优，市场偏弱")
        else:
            print("  → 技术面：多空均衡，震荡市")

    # 多空辩论结论
    print("\n【三、多空辩论结论】")
    bull_points, bear_points = [], []
    if senti is not None and len(senti) > 0:
        buy_ratio_all = senti['avg_score'].mean()
        if buy_ratio_all >= 2.2:
            bull_points.append(f"券商整体评级偏正面(平均{senti['avg_score'].mean():.1f}分)")
        elif buy_ratio_all < 1.8:
            bear_points.append(f"券商整体评级偏谨慎(平均{senti['avg_score'].mean():.1f}分)")
    if len(tech) > 0:
        breadth = tech['above_ma'].mean()
        if breadth > 0.6:
            bull_points.append(f"市场广度强({breadth*100:.0f}%站上20日线)")
        elif breadth < 0.4:
            bear_points.append(f"市场广度弱({breadth*100:.0f}%站上20日线)")

    print("  多头观点:", '；'.join(bull_points) if bull_points else '无')
    print("  空头观点:", '；'.join(bear_points) if bear_points else '无')

    # 综合
    if len(bull_points) > len(bear_points):
        verdict = "下周偏多，建议逢低配置券商看好 + 动量强的行业"
    elif len(bear_points) > len(bull_points):
        verdict = "下周偏空，建议降低仓位，配置债券/防御品种"
    else:
        verdict = "下周震荡，建议均衡配置，精选行业，控制仓位"
    print(f"\n  >>> 下周方向思路: {verdict}")
    print("=" * 78)

if __name__ == "__main__":
    reports = fetch_weekly_reports(n_days=7)
    tech = technical_view()
    debate(reports, tech)
