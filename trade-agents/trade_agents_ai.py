# -*- coding: utf-8 -*-
"""
Trade Agents 周度分析（AI 版）
=============================
数据层：纯代码（阶段一周线买点 → 阶段二MA40+VWAP下钻个股 → 研报覆盖 → Anchor持仓）
辩论层：DeepSeek 扮演多交易员辩论，生成统一报告
推送：飞书（图文卡片，带缠论图）

周日 cron 执行。
"""
import os, sys, json, subprocess
import numpy as np
import pandas as pd
from datetime import datetime

WS = "/opt/data/home/.coze/agents/7705405429102444827/workspace"
TECHANLY = "/tmp/techAnly"
ANCHOR_LITE = "/tmp/anchor-lite"
DATAHUB = os.path.join(WS, "datahub-three-tier")

sys.path.insert(0, os.path.join(TECHANLY, "bin"))
sys.path.insert(0, os.path.join(TECHANLY, "smc"))
sys.path.insert(0, DATAHUB)
sys.path.insert(0, WS)

TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")
FEISHU_CHAT = os.environ.get("FEISHU_CHAT", "")
os.environ.setdefault("TECH_WS", TECHANLY)

import trade_agents_final as TA  # 复用数据抓取
import llm_report as LR           # 复用 LLM 辩论

def main(push=False):
    print("Trade Agents 周度分析（AI版）开始...")
    print("[1/4] 阶段一：行业周线买点扫描...")
    hits1 = TA.stage1_weekly_buypoints()
    inds = [h["name"] for h in hits1]
    print(f"  过闸行业 {len(inds)} 个: {inds}")
    print("[2/4] 阶段二：下钻个股（MA40+VWAP过滤）...")
    stocks = TA.stage2_drilldown(inds)
    print(f"  可买个股 {len(stocks)} 只")
    print("[3/4] 基本面：券商研报确认...")
    research, stock_cov = TA.trader_research(inds)
    # 最终推荐 = 技术买点 + 研报覆盖
    covered = [s for s in stocks if s["code"] in stock_cov]
    print("[3.5/4] 资讯分析：对候选个股定向查新闻...")
    news_map = TA.trader_news(covered)
    print(f"  获取 {len(news_map)} 只个股新闻")

    # 组装 data context（顺序：Anchor-lite策略 → 券商研报基本面 → 技术面买点）
    PRI = {1:"①三买",2:"②一买",3:"③二买"}
    lines = []
    # 1. Anchor-lite 策略持仓（放最前）
    ap = os.path.join(ANCHOR_LITE, "positions_snapshot.json")
    if os.path.exists(ap):
        d = json.load(open(ap))
        lines.append("【Anchor-lite策略持仓】截至%s: 股票%.2f%% 债券%.2f%%" % (
            d.get('asof'), d.get('equity_total'), d.get('bond_total')))
    # 2. 券商研报·行业评级（基本面）
    lines.append("【券商研报·行业评级（基本面）】")
    for ind, r in research.items():
        flag = "🌙" if r['avg']>=2.5 else "⭐"
        lines.append(f"  {flag} {ind}: 研报{r['n']}篇 平均评级{r['avg']:.1f} 买入{r['buy']*100:.0f}%")
    # 3. 技术面：行业周线买点
    lines.append("【技术面·行业周线/日线买点】")
    for h in hits1:
        b = h["best"]
        lines.append(f"  🌙 {h['name']} {PRI.get(b['pri'],b['tag'])}@{b['level']:.0f}")
    # 4. 技术面：个股下钻
    lines.append("【技术面·个股下钻（MA40+VWAP过滤+研报覆盖）】")
    for s in covered:
        b = s["best"]
        cov = stock_cov[s["code"]]
        lines.append(f"  🌙 {PRI.get(b['pri'],b['tag'])} {s['name']}({s['code']}) 收{s['close']:.2f} 研报{cov['n']}篇 评级{cov['avg']:.1f}")
    # 5. 资讯/新闻（情绪面，只对候选个股）
    if news_map:
        lines.append("【资讯·个股新闻（情绪面，定向查询）】")
        for s in covered:
            items = news_map.get(s["code"])
            if items:
                titles = "；".join(t for t, _ in items[:3])
                lines.append(f"  🌙 {s['name']}({s['code']}): {titles}")
    data_context = "\n".join(lines)

    print("[4/4] LLM 多交易员辩论生成报告...")
    report = LR.debate_report(data_context)
    print("\n" + report)

    out = f"/tmp/trade_agents_ai_{datetime.now().strftime('%Y%m%d')}.txt"
    open(out, 'w').write(report)
    print(f"\n报告已保存: {out}")

    if push:
        # 给 covered 前 5 只出图
        print("生成个股缠论图...")
        TA.push_with_charts(report, covered)

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true")
    args = ap.parse_args()
    main(push=args.push)
