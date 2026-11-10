#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SMC 引擎配置（公开版 · 仅保留纯计算所需部分）

⚠️ 本文件已剥离所有私有信息（本机绝对路径 / 群 ID / webhook / Coze 凭据）。
   tech_summary / tech_buypoint 只用到 LEVEL_MAP 与 base_freq 两处。
   如果你从本机完整版复制了 smc_config.py，这里会被覆盖，属于正常。
"""
import os

BASE = os.environ.get("TECH_WS") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SMC_DIR = os.path.join(BASE, "smc")
CACHE = os.path.join(SMC_DIR, "cache")

# 级别映射（原始设计 4 节）
#   base = 母数据频率（从该频率向各角色聚合）
#   股票/ETF 用「日线」级别、期货用「小时」级别
LEVEL_MAP = {
    "h1":   dict(base="60m", htf="day",   mtf="h2",   sub="m30"),
    "day":  dict(base="1d",  htf="month", mtf="week", sub="day"),
    "week": dict(base="1d",  htf="month", mtf="week", sub="day"),
}

TRADE_TF = "h1"                                  # 默认 / 期货
TRADE_TF_BY_KIND = {"stock": "day", "fut": "h1"}


def trade_tf(it):
    """某标的的交易级别：股票/ETF=日线(day)，期货=小时(h1)。"""
    return TRADE_TF_BY_KIND.get(it.get("kind"), TRADE_TF)


def level_of(it):
    """某标的的交易级别：股票/ETF=day（日线），期货=h1（小时）。"""
    return TRADE_TF_BY_KIND.get(it.get("kind"), TRADE_TF)


def base_freq(level):
    """某级别对应的母数据频率。"""
    return LEVEL_MAP[level].get("base", "60m")
