#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Anchor-Lite 应用程序入口
========================

主程序: long2022.py (全区间引擎: 中美宏观锚 + 债券衰退切换 + 风格选股 + 期权风控)
本入口: 读 VERSION.json 冻结参数 → 跑策略 → 出 Markdown 报告

用法:
  python3 app.py                    # 运行并写 reports/strategy_report.md
  python3 app.py --print-only       # 只打印到终端
  python3 app.py --out x.md         # 指定输出文件

依赖: numpy, pandas (纯 Markdown, 无图表, 无 LLM)
"""
import os
import sys
import json
import argparse
import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import numpy as np
import pandas as pd
import long2022 as L


def load_cfg():
    """VERSION.json key_params → long2022.run() 参数"""
    cfg = json.load(open(os.path.join(BASE, "VERSION.json")))["key_params"]
    return dict(
        stock_cap=cfg.get("stock_cap", 0.3),
        stock_base=cfg.get("stock_base", 0.42),
        dd=cfg.get("dd", 0.016),
        dd2=cfg.get("dd2", 0.02),
        nas_indep=cfg.get("nas_indep", True),
        reset_mode=cfg.get("reset_mode", "all3"),
        sel_freq="W" if cfg.get("sel_freq") == "weekly" else "M",
        check_freq="D" if cfg.get("check_freq") == "daily" else "W",
        cost=cfg.get("cost_bps", 5),
    )


ASSET_CN = {
    "hs300": "沪深300", "zz500": "中证500", "cyb": "创业板", "kc50": "科创50",
    "dividend": "红利", "nasdaq": "纳指", "oil": "原油",
    "cn_bond10": "中债10年", "us_bond": "美债", "gold": "黄金",
}


def build_markdown(m, trail, sel_log, cfg, gen_time):
    L = ["# Anchor-Lite 策略运行报告", ""]
    L.append(f"- 生成时间: {gen_time}")
    L.append(f"- 引擎: long2022.py (中美宏观锚 + 债券衰退切换 + 风格选股 + 期权风控)")
    L.append(f"- 区间: {trail['date'].iloc[0]} ~ {trail['date'].iloc[-1]}")
    L.append("")
    L.append("## 一、核心指标")
    L.append("")
    L.append("| 指标 | 数值 |")
    L.append("|---|---|")
    L.append(f"| 累计收益 | {m['total']*100:+.2f}% |")
    L.append(f"| 年化收益 (CAGR) | {m['cagr']*100:+.2f}% |")
    L.append(f"| 年化波动率 | {m['vol']*100:.2f}% |")
    L.append(f"| Sharpe | {m['sharpe']:.2f} |")
    L.append(f"| 最大回撤 | {m['maxdd']*100:.2f}% |")
    L.append(f"| 交易次数 | {m['n_trades']} |")
    L.append("")
    L.append("## 二、分年度收益")
    L.append("")
    L.append("| 年份 | 收益 | 年内最大回撤 |")
    L.append("|---|---|---|")
    t = trail.copy()
    t["year"] = pd.to_datetime(t["date"]).dt.year
    for y, g in t.groupby("year"):
        nav = (1 + g["ret"]).cumprod()
        dd = (nav / nav.cummax() - 1).min()
        L.append(f"| {y} | {(nav.iloc[-1]-1)*100:+.2f}% | {dd*100:.2f}% |")
    L.append("")
    L.append("## 三、当前持仓（最新交易日）")
    L.append("")
    last = t.iloc[-1]
    L.append(f"- 数据截至: {last['date']}")
    L.append("")
    L.append("| 资产 | 权重 |")
    L.append("|---|---|")
    rows = []
    for col, cn in [("w_nas", "纳指"), ("w_hs300", "沪深300"), ("w_zz500", "中证500"),
                    ("w_cyb", "创业板"), ("w_kc50", "科创50"), ("oil", "原油"),
                    ("cn", "中债"), ("us", "美债")]:
        w = float(last[col])
        if abs(w) > 1e-4:
            rows.append(f"| {cn} | {w*100:+.1f}% |")
    if not rows:
        rows.append("| 现金 | 100.0% |")
    L.extend(rows)
    L.append("")
    L.append(f"- 股票腿合计: {float(last['eq'])*100:.1f}% | 债券腿(中债+美债): {float(last['cn']+last['us'])*100:.1f}%")
    L.append(f"- 回撤闸门 br: {float(last['br']):.0f} (0=熔断清风险腿, 1=正常)")
    L.append("")
    L.append("## 四、最新选股（风格动量）")
    L.append("")
    if sel_log:
        k = list(sel_log)[-1]
        top, mom = sel_log[k]
        top_cn = [ASSET_CN.get(a, a) for a in top]
        mom_str = "、".join(f"{ASSET_CN.get(a, a)} {v*100:+.1f}%" for a, v in mom.items())
        L.append(f"- 日期: {k}")
        L.append(f"- 选中: {', '.join(top_cn)}")
        L.append(f"- 动量: {mom_str}")
    else:
        L.append("- 无")
    L.append("")
    L.append("## 五、运行配置")
    L.append("")
    L.append("```")
    for k, v in cfg.items():
        L.append(f"{k} = {v}")
    L.append("```")
    L.append("")
    L.append("---")
    L.append("> 研究分享, 不构成投资建议。历史回测不代表未来表现。")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(BASE, "reports", "strategy_report.md"))
    ap.add_argument("--print-only", action="store_true")
    args = ap.parse_args()

    cfg = load_cfg()
    print("运行配置:", cfg)
    print("运行 long2022.run() ...")
    m, trail, sel_log, trades = L.run(**cfg)
    print("指标:", {k: (round(v, 4) if isinstance(v, (int, float)) else v) for k, v in m.items()})

    gen_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    md = build_markdown(m, trail, sel_log, cfg, gen_time)

    if args.print_only:
        print(md)
    else:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w") as f:
            f.write(md)
        print(f"报告已写入: {args.out}")


if __name__ == "__main__":
    main()
