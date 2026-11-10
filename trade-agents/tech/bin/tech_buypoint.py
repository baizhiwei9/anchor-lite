#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""买点分析 · 指数 / 行业 / 概念 / 个股 —— 一个入口全覆盖
=====================================================================
口径（大佬 2026-11-10 最新定稿，覆盖 11-09）：
  · 买点优先级：① 三买 > ② 一买（**必须 MA20 向上**） > ③ 二买（**恢复，不再丢弃**）
  · 卖点：① 三卖 > ② 一卖 > ③ 二卖（2026-11-10 新增正式输出）
  · 三买 = 离开中枢后回踩不破 ZG（结构已确认）
  · 一买 = 底背驰 + MA20 斜率 > 0
  · 二买 = 回踩不破一买低点（MA20 向上只加分，不作硬门槛）
  · 三卖 = 向下离开中枢后反抽不回 ZD
  · 闸门：买/卖点年龄 / 距离 / 结构未破 / 方向确认
  · 全流程纯 Python，不依赖任何大模型 / LLM API，0 token

四类标的（--kind 自动识别，也可手动指定）：
  ① 指数     沪深300 / 中证500 / 上证50 / 创业板指 / 科创50 / 000905.SH ...
              → 单个标的：走 tech_summary 四层分析
              → 指数成分：扫成分股买点
  ② 行业     传媒 / 游戏 / 出版 / 银行 ...（申万三级层级自动匹配）
              → 行业指数走势 + 成分股买点扫描
  ③ 概念     文化传媒概念 / 手机游戏 / 短剧游戏 ...（同花顺 412 个概念）
              → 概念指数走势 + 成分股买点扫描
  ④ 个股     600519 / 茅台
              → 单只四层分析

用法：
  python3 bin/tech_buypoint.py --target 传媒              # 行业：指数 + 成分股买点
  python3 bin/tech_buypoint.py --target 文化传媒概念       # 概念：指数 + 成分股买点
  python3 bin/tech_buypoint.py --target 中证500           # 指数：成分股扫买点
  python3 bin/tech_buypoint.py --target 600519            # 个股：单只分析
  python3 bin/tech_buypoint.py --target 传媒 --push       # 推飞书卡片
  python3 bin/tech_buypoint.py --target 传媒 --no-scan    # 只看板块指数，不扫成分股
  python3 bin/tech_buypoint.py --list-concepts 游戏        # 列出匹配的概念名
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from datetime import datetime

# ── 全局硬规矩：禁 IPv6（容器 IPv6 不通会静默卡死）──
_orig_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig_gai(*a, **k) if r[0] == socket.AF_INET]

import re  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WS, "bin"))

import chan_smc_index as C                    # noqa: E402
import chan_ma_trend_v2 as M                  # noqa: E402
import datahub as DH                          # noqa: E402
import tech_summary as TS                     # noqa: E402
import smc_core as S                       # noqa: E402  # VWAP 预过滤用
from tech_summary import (                    # noqa: E402
    ma20_state, rank_buys, best_buy, PRI_MARK, BUY_PRIORITY, MA20_UP_BARS,
)  # noqa: F401

SW_MEMBER = os.path.join(WS, ".cache", "sw_member_all.json")
DAILY_DIR = os.path.join(WS, ".cache", "holding_deep", "daily")
MKT_DIR = os.path.join(WS, ".cache", "market")
NAMES_CSV = os.path.join(WS, ".cache", "stock_names.csv")

# ── 买点有效性闸门（大佬 2026-11-09：优先三买，一买须 MA20 向上）──
#   光有「历史上出现过买点」没用 —— 得现价还在买点附近才能下手
BUY_NEAR_UP = float(os.environ.get("SCAN_BUY_NEAR_UP", "10"))     # 现价高于买点多少%以内算「还在买点」
BUY_NEAR_DN = float(os.environ.get("SCAN_BUY_NEAR_DN", "3"))      # 现价低于买点多少%以内算「刚破但可接受」
TB_MAX_AGE = int(os.environ.get("SCAN_TB_MAX_AGE", "90"))          # 三买最大年龄（根）
TB_NEAR = float(os.environ.get("SCAN_TB_NEAR", "10"))              # 三买：现价高于回踩低点%以内
YB_MAX_AGE = int(os.environ.get("SCAN_YB_MAX_AGE", "40"))          # 一买最大年龄（根）
# ── 二买（2026-11-10 大佬：恢复，不再丢弃；优先级排第三）──
SCAN_EB_MAX_AGE = int(os.environ.get("SCAN_EB_MAX_AGE", "60"))      # 二买最大年龄（根）
SCAN_EB_NEAR = float(os.environ.get("SCAN_EB_NEAR", "10"))          # 二买：现价高于二买低点%以内
# ── 卖点（2026-11-10 新增：三卖 / 一卖 / 二卖）──
SCAN_SELL_MAX_AGE = int(os.environ.get("SCAN_SELL_MAX_AGE", "90"))  # 卖点最大年龄（根）
SCAN_SELL_NEAR = float(os.environ.get("SCAN_SELL_NEAR", "6"))       # 卖点：现价距卖点%以内（涨破=失效）
SELL_BREAK_UP = 1.03      # 现价涨破卖点 3% → 卖点失效
SELL_BREAK_DN = 1.005     # 卖点之后收盘涨破该点 0.5% → 结构破坏
SELL_MARK = {1: "①三卖", 2: "②一卖", 3: "③二卖"}
# 小时线（次级）单独一套参数：60m 的「根」比日线快得多，年龄/距离都要放宽
H1_TB_MAX_AGE = int(os.environ.get("SCAN_H1_TB_MAX_AGE", "120"))
H1_TB_NEAR = float(os.environ.get("SCAN_H1_TB_NEAR", "8"))
H1_YB_MAX_AGE = int(os.environ.get("SCAN_H1_YB_MAX_AGE", "60"))

RED, GRN = "#d62728", "#2ca02c"


def log(*a):
    print(*a, file=sys.stderr)


# ══════════════════════════ 概念（同花顺）══════════════════════════
THS_MAP = os.path.join(WS, ".cache", "ths_index_map.json")
THS_MEMBER_CACHE = os.path.join(WS, ".cache", "ths_member")


def load_concept_map():
    """同花顺概念名 → ts_code（885xxx.TI），412 个"""
    try:
        return json.load(open(THS_MAP, encoding="utf-8"))
    except Exception:
        return {}


def resolve_concept(name, cmap):
    """概念名解析：精确 → 去「概念/指数」后缀 → 包含模糊

    返回 (ts_code, 显示名, 匹配级别)
    """
    if not cmap:
        return None, "", ""
    if name in cmap:
        return cmap[name], name, "精确"
    # 去掉常见后缀再试
    base = name
    for suf in ("概念", "指数", "板块"):
        if base.endswith(suf) and len(base) > len(suf):
            b2 = base[:-len(suf)]
            if b2 in cmap:
                return cmap[b2], b2, "去后缀"
            for k, v in cmap.items():
                if k == b2 + suf:
                    return v, k, "去后缀"
    # 包含匹配（名字越长越优先）
    hits = [(k, v) for k, v in cmap.items() if name in k or k in name]
    if hits:
        hits.sort(key=lambda x: -len(x[0]))
        return hits[0][1], hits[0][0], "模糊"
    return None, "", ""


def concept_members(ts_code):
    """概念成分股（带磁盘缓存，7天）"""
    import time as _t
    os.makedirs(THS_MEMBER_CACHE, exist_ok=True)
    fp = os.path.join(THS_MEMBER_CACHE, ts_code.replace(".", "_") + ".json")
    if os.path.exists(fp) and (_t.time() - os.path.getmtime(fp)) / 86400 < 7:
        try:
            return json.load(open(fp, encoding="utf-8"))
        except Exception:
            pass
    try:
        from tushare_init import ts_call
        m = ts_call("ths_member", ts_code=ts_code)
        if m is None or not len(m):
            return []
        out = [[str(r["con_code"]), str(r.get("con_name", ""))] for _, r in m.iterrows()]
        json.dump(out, open(fp, "w", encoding="utf-8"), ensure_ascii=False)
        return out
    except Exception as e:
        log("[概念] 成分取数失败 %s: %s" % (ts_code, str(e)[:60]))
        return []


# 宽基指数 → 同花顺「成份股」概念（tushare index_weight 常超时，用这个兜底）
IDX_TO_THS = {
    "000300.SH": "883300.TI",   # 沪深300样本股
    "000905.SH": "883304.TI",   # 中证500成份股
    "000016.SH": "883301.TI",   # 上证50样本股
}


def _ak_timeout(fn, *a, timeout=25, **k):
    """akshare 调用带硬超时（它内部 requests 没有 timeout，会静默挂死）"""
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as _TE
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        return ex.submit(fn, *a, **k).result(timeout=timeout)
    except _TE:
        log("[指数] akshare 超时 %ss" % timeout)
        return None
    except Exception as e:
        log("[指数] akshare 失败 %s" % str(e)[:80])
        return None
    finally:
        ex.shutdown(wait=False)


def index_members(ts_code):
    """指数成分股：中证官网（精确）→ tushare index_weight → 同花顺成份股概念

    ⚠️ 同花顺「中证500成份股」概念实际给的是 552 只（含大量非成分股），
       只能当最后兜底，不能当准；中证官网 index_stock_cons_csindex 是 500 只，才准。
    """
    sym = str(ts_code).split(".")[0]
    # ① 中证官网（只有中证系指数有）
    try:
        import akshare as ak
        d = _ak_timeout(ak.index_stock_cons_csindex, symbol=sym)
        if d is not None and len(d):
            ccol = next((c for c in d.columns if "成分券代码" in c), None)
            ncol = next((c for c in d.columns if "成分券名称" in c), None)
            if ccol:
                out = []
                for _, r in d.iterrows():
                    c = str(r[ccol]).zfill(6)
                    c = DH.to_ts(DH.norm(c))
                    out.append([c, str(r[ncol]) if ncol else ""])
                if out:
                    log("[指数] 中证官网 %d 只" % len(out))
                    return out
    except Exception as e:
        log("[指数] 中证官网失败 %s" % str(e)[:60])
    # ② tushare index_weight
    try:
        from tushare_init import ts_call
        m = ts_call("index_weight", index_code=ts_code)
        if m is not None and len(m):
            d = m.sort_values("trade_date")
            last = str(d["trade_date"].iloc[-1])
            codes = sorted(set(d[d["trade_date"].astype(str) == last]["con_code"]))
            if codes:
                return [[c, ""] for c in codes]
    except Exception as e:
        log("[指数] index_weight 失败 %s: %s" % (ts_code, str(e)[:50]))
    # ③ 同花顺兜底（数量可能不准）
    ths = IDX_TO_THS.get(ts_code)
    if ths:
        log("[指数] ⚠️ 退回同花顺成份股概念 %s（数量可能不准）" % ths)
        return concept_members(ths)
    return []


def sector_index_daily(kind, code, name=""):
    """板块指数日线：行业=申万 sw_daily / 概念=同花顺 ths_daily

    → DataFrame[dt, open, high, low, close, volume] 或 None
    """
    try:
        if kind == "concept":
            from tushare_init import ts_call
            d = ts_call("ths_daily", ts_code=code, start_date="20240101")
            if d is None or not len(d):
                return None
            d = d.rename(columns={"trade_date": "dt", "vol": "volume"})
            d["dt"] = pd.to_datetime(d["dt"].astype(str), format="%Y%m%d")
            d = d.sort_values("dt").reset_index(drop=True)
            for c in ("open", "high", "low", "close"):
                d[c] = pd.to_numeric(d[c], errors="coerce")
            if "volume" not in d.columns:
                d["volume"] = 0.0
            return d[["dt", "open", "high", "low", "close", "volume"]].dropna(subset=["close"]).reset_index(drop=True)
        if kind == "index":
            # 指数自身日线：走 tech_summary 的数据链路（tushare index_daily / datahub 三级）
            tg = TS.resolve(code)
            d = TS.load_day(tg)
            if d is None or len(d) < 60:
                return None
            d = d.copy()
            if "volume" not in d.columns:
                d["volume"] = d.get("vol", 0.0)
            return d[["dt", "open", "high", "low", "close", "volume"]].reset_index(drop=True)
        if kind == "industry":
            from weekly_zs import fetch_sw_daily
            d = fetch_sw_daily(code)
            if d is None or not len(d):
                return None
            d = d.copy()
            d["dt"] = pd.to_datetime(d["dt"])
            if "volume" not in d.columns:
                d["volume"] = 0.0
            return d.sort_values("dt").reset_index(drop=True)
    except Exception as e:
        log("[板块] 指数取数失败 %s %s: %s" % (kind, code, str(e)[:70]))
    return None


# ══════════════════════════ 行业成分 ══════════════════════════
_RAW_L2 = set()


def load_sw():
    """→ (l1→[codes], l2→[codes], {code: {l1,l2,l3}})

    注意：sw_member_all.json 的 l2 里带罗马数字后缀（游戏Ⅱ/电视广播Ⅱ），
    用户口语一般说「游戏」「电视广播」，所以再做一份**去后缀索引**。
    """
    raw = json.load(open(SW_MEMBER, encoding="utf-8"))
    by_l1, by_l2, by_l3, meta = {}, {}, {}, {}
    for code, v in raw.items():
        l1, l2, l3 = v.get("l1"), v.get("l2"), v.get("l3")
        meta[code] = v
        if l1:
            by_l1.setdefault(l1, []).append(code)
        if l2:
            _RAW_L2.add(l2)
            by_l2.setdefault(l2, []).append(code)
            by_l2.setdefault(strip_roman(l2), []).append(code)
        if l3:
            by_l3.setdefault(l3, []).append(code)
    return by_l1, by_l2, by_l3, meta


def strip_roman(s):
    """游戏Ⅱ → 游戏；电视广播Ⅲ → 电视广播"""
    for suf in ("Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ"):
        if s and s.endswith(suf):
            return s[:-1]
    return s


SW_INDEX_CACHE = os.path.join(WS, ".cache", "sw_index_map.json")


def load_sw_index_map():
    """申万行业名 → 指数代码（801xxx.SI），合并 L1(31) / L2(134) / L3(346)"""
    if os.path.exists(SW_INDEX_CACHE):
        try:
            m = json.load(open(SW_INDEX_CACHE, encoding="utf-8"))
            if len(m) > 100:
                return m
        except Exception:
            pass
    out = {}
    for lv in ("L1", "L2", "L3"):
        p = os.path.join(WS, ".cache/longxia/sw_classify_%s.json" % lv)
        if not os.path.exists(p):
            continue
        try:
            for code, nm in json.load(open(p, encoding="utf-8")).items():
                if str(code).startswith("80"):
                    out[str(nm)] = str(code)
                    out.setdefault(strip_roman(str(nm)), str(code))
        except Exception:
            pass
    if out:
        try:
            json.dump(out, open(SW_INDEX_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
        except Exception:
            pass
    return out


def resolve_industry(name, by_l1, by_l2, by_l3):
    """→ (codes, 级别文案, 匹配方式)  匹配方式：精确 / 去后缀 / 模糊"""
    for lvl, bag in (("一级", by_l1), ("二级", by_l2), ("三级", by_l3)):
        if name in bag:
            how = "精确"
            # 二级里带罗马数字的（游戏Ⅱ→游戏 走「去后缀」索引）
            if lvl == "二级" and name not in _RAW_L2:
                how = "去后缀"
            return sorted(set(bag[name])), lvl, how
    # 模糊：包含关系
    cands = []
    for lvl, bag in (("一级", by_l1), ("二级", by_l2), ("三级", by_l3)):
        for k in bag:
            if name in k or k in name:
                cands.append((lvl, k, len(bag[k])))
    if not cands:
        return [], "", ""
    cands.sort(key=lambda x: -x[2])
    lvl, k, _ = cands[0]
    bag = {"一级": by_l1, "二级": by_l2, "三级": by_l3}[lvl]
    return sorted(set(bag[k])), "%s（匹配「%s」）" % (lvl, k), "模糊"


def name_map():
    m = {}
    if os.path.exists(NAMES_CSV):
        try:
            d = pd.read_csv(NAMES_CSV)
            m = dict(zip(d["ts_code"], d["name"]))
        except Exception:
            pass
    p = os.path.join(WS, ".cache", "tech_stock_names.json")
    if os.path.exists(p):
        try:
            m.update(json.load(open(p, encoding="utf-8")))
        except Exception:
            pass
    # 兜底：sw_member_all.json 里已带每只股的 name（bootstrap_cache 一定会生成）
    if len(m) < 1000:
        try:
            for code, v in json.load(open(SW_MEMBER, encoding="utf-8")).items():
                nm = (v or {}).get("name")
                if code and nm and code not in m:
                    m[code] = nm
        except Exception:
            pass
    return m


# ══════════════════════════ 日线数据 ══════════════════════════
def _today_hist():
    """当日全市场日线（含 OHLCV）→ DataFrame(ts_code, dt, ...)"""
    days = sorted([x for x in os.listdir(MKT_DIR) if x.isdigit()]) if os.path.isdir(MKT_DIR) else []
    frames = []
    for d8 in days[-6:]:
        fp = os.path.join(MKT_DIR, d8, "daily_hist.csv")
        if not os.path.exists(fp):
            continue
        try:
            t = pd.read_csv(fp)
            if "close" in t.columns and t["close"].notna().sum() > 100:
                frames.append(t)
        except Exception:
            pass
    if not frames:
        return None
    t = pd.concat(frames, ignore_index=True)
    t["dt"] = pd.to_datetime(t["trade_date"].astype(str), format="%Y%m%d")
    return t[["ts_code", "dt", "open", "high", "low", "close", "vol", "amount"]]


def _fetch_full_daily(code, start="20200101"):
    """从 tushare 拉全量日线（用于修补本地缓存缺口）→ DataFrame 或 None"""
    try:
        from tushare_init import ts_call
        d = ts_call("daily", ts_code=code, start_date=start)
        if d is None or not len(d):
            return None
        d = d.rename(columns={"trade_date": "dt", "vol": "volume"})
        d["dt"] = pd.to_datetime(d["dt"].astype(str), format="%Y%m%d")
        d = d.sort_values("dt").reset_index(drop=True)
        for c in ("open", "high", "low", "close", "volume"):
            if c in d.columns:
                d[c] = pd.to_numeric(d[c], errors="coerce")
        if "volume" not in d.columns:
            d["volume"] = 0.0
        return d[["dt", "open", "high", "low", "close", "volume"]].dropna(subset=["close"])
    except Exception as e:
        log("[数据] %s 全量拉取失败 %s" % (code, str(e)[:60]))
        return None


def load_daily(code, hist):
    """个股日线：本地缓存 + 当日 hist 增量补齐

    返回 DataFrame[dt, open, high, low, close, volume]（升序）或 None
    """
    p = os.path.join(DAILY_DIR, code + ".csv")
    d = None
    if os.path.exists(p):
        try:
            d = pd.read_csv(p, parse_dates=["dt"])
            d = d.sort_values("dt").reset_index(drop=True)
        except Exception:
            d = None
    # 本地完全没有该标的缓存（首次部署 / 新上市）→ 从 tushare 拉一次全量并落盘
    #   （只在该 csv 不存在时触发，已有缓存的标的走下面的增量补齐逻辑，不额外耗配额）
    if d is None:
        d = _fetch_full_daily(code)
        if d is not None and len(d) > 200:
            try:
                os.makedirs(DAILY_DIR, exist_ok=True)
                d.to_csv(p, index=False)
            except Exception:
                pass
            log("[数据] %s 无本地缓存 → 已从 tushare 拉全量 %d 根" % (code, len(d)))
        else:
            d = None
    add = hist[hist["ts_code"] == code] if hist is not None else None
    if add is not None and len(add):
        add = add[["dt", "open", "high", "low", "close", "vol"]].rename(columns={"vol": "volume"})
        if d is None:
            d = add.sort_values("dt").reset_index(drop=True)
        else:
            last = d["dt"].iloc[-1]
            new = add[add["dt"] > last]
            # 缺口判定：新数据第一天与本地最后一天的日历差；>4 天基本等于缺了 ≥2 个交易日
            #   （周五→次周五 = 7 天；正常相邻交易日只差 1~3 天）
            gap_days = (new["dt"].iloc[0] - last).days if len(new) else 0
            if gap_days > 4:
                # ⚠️ 中间隔了 ≥2 个交易日 = 本地缓存中间有缺口，直接拼会造出假结构
                #    （实测风语筑本地停 10-29，直接拼 11-09 会凭空冒出「三买 13.30」假信号）
                #    → 先用 tech_daily（1200 根全量）兜底，它通常是完整的
                td = os.path.join(WS, ".cache/tech_daily",
                                  code.replace(".", "_") + ".csv")
                fixed = None
                if os.path.exists(td):
                    try:
                        t2 = pd.read_csv(td, parse_dates=["dt"])
                        if len(t2) > 200 and t2["dt"].iloc[-1] >= add["dt"].max():
                            t2 = t2.sort_values("dt").reset_index(drop=True)
                            if "volume" not in t2.columns:
                                t2["volume"] = t2.get("vol", 0.0)
                            fixed = t2[["dt", "open", "high", "low", "close", "volume"]]
                            log("[数据] %s 本地缓存缺口（%s→%s），改用 tech_daily 全量 %d 根"
                                % (code, last.date(), add["dt"].max().date(), len(t2)))
                    except Exception:
                        fixed = None
                if fixed is None:
                    # 本地没有全量缓存 → 现拉一次完整日线（tushare），补进 .cache/tech_daily
                    fixed = _fetch_full_daily(code)
                    if fixed is not None and len(fixed) > 200:
                        try:
                            fixed.to_csv(td, index=False)
                        except Exception:
                            pass
                        log("[数据] %s 缺口 → 已从 tushare 补全 %d 根" % (code, len(fixed)))
                    else:
                        fixed = None
                if fixed is not None:
                    d = fixed
                else:
                    # 实在拿不到全量：不拼（拼出来的结构是假的），直接用本地
                    log("[数据] ⚠️ %s 缺口且补全失败，跳过该标的（避免假信号）" % code)
                    return None
            elif len(new) == 1:
                d = pd.concat([d[["dt", "open", "high", "low", "close",
                                  "volume" if "volume" in d.columns else "vol"]]
                               .rename(columns={"vol": "volume"}), new],
                              ignore_index=True).sort_values("dt").reset_index(drop=True)
    if d is None or len(d) < 120:
        return None
    if "volume" not in d.columns:
        d["volume"] = d.get("vol", 0.0)
    d = d.dropna(subset=["close"]).reset_index(drop=True)
    # 可选截断（默认不截断）：SARCH_DAY_BARS 设定后，扫描与出图都吃同一份长度
    if DAY_BARS and len(d) > DAY_BARS:
        d = d.tail(DAY_BARS).reset_index(drop=True)
    return d


# ══════════════════════════ 小时线（次级确认）══════════════════════════
#   大佬 2026-11-09 20:57：「优先是日线级别出现买点，然后才是小时线级别」
#   → 日线买点 = 主判定（必须有）；小时线买点 = 次级（日线有买点时再叠加看，给精确入场）
#   → 小时线口径沿用 chan_smc_index.H1_ALLOWED_BUY（只认三买，及一买+MA10金叉）

H1_N = 400            # 取多少根 60m（够 160 日均线级别结构）
H1_MIN_BARS = 120

# ⚠️ 日线取多少根：缠论结构对长度极度敏感（实测风语筑 441根→三买13.30；1200根→无三买因为回踩低点
#    被判成 11.11 且现价已跑远 25%）。扫描器和出图**必须用同一份数据**，否则报告和图对不上。
DAY_BARS = int(os.environ.get("SCAN_DAY_BARS", "0"))   # 0 = 用本地全部（不截断）


def _sina_code(ts_code):
    """600519.SH → sh600519；000905.SH → sh000905；300750.SZ → sz300750"""
    c, _, mk = str(ts_code).upper().partition(".")
    mk = mk.lower()
    if mk not in ("sh", "sz", "bj"):
        mk = "sh" if str(c).startswith(("6", "5", "0")) else "sz"
    return mk + c


def load_h1(ts_code, kind="stock"):
    """60 分钟线 → DataFrame[dt,open,high,low,close,volume] 或 None

    ⚠️ minute_hub 磁盘缓存 key 不含 n：长度不够必须 cache=False 强制刷新
    """
    try:
        import minute_hub as mh
        code = _sina_code(ts_code)
        d = mh.get_kline(code, "60", n=H1_N, kind=kind, cache=True, cache_ttl_min=20)
        if d is None or len(d) < H1_MIN_BARS:
            d = mh.get_kline(code, "60", n=H1_N, kind=kind, cache=False)
        if d is None or len(d) < H1_MIN_BARS:
            return None
        d = d.rename(columns={"day": "dt"})
        d["dt"] = pd.to_datetime(d["dt"])
        if "volume" not in d.columns:
            d["volume"] = 0.0
        d = d[["dt", "open", "high", "low", "close", "volume"]].dropna(subset=["close"])
        return d.sort_values("dt").reset_index(drop=True)
    except Exception:
        return None


def h1_buys(ts_code, kind="stock"):
    """小时线买点（次级）：只认三买 + 一买且 MA10>MA20

    返回 {"ok":bool, "best":dict|None, "all":[...], "ma10","ma20","close","dt"}
    """
    d = load_h1(ts_code, kind)
    if d is None or len(d) < H1_MIN_BARS:
        return {"ok": False, "best": None, "all": [], "err": "小时线数据不足"}
    cl = d["close"].astype(float).values
    close = float(cl[-1])
    ma20 = ma20_state(d)
    tb = find_third_buys(d, cl, max_age=H1_TB_MAX_AGE, near=H1_TB_NEAR)
    yb = []
    c = d["close"].astype(float)
    ma10v = float(c.rolling(10).mean().iloc[-1]) if len(c) >= 10 else None
    ma20v = float(c.rolling(20).mean().iloc[-1]) if len(c) >= 20 else None
    golden = bool(ma10v is not None and ma20v is not None and ma10v > ma20v)
    if golden:
        # 小时线一买：MA10>MA20（金叉）才认，这是 chan_smc_index.H1_ALLOWED_BUY 的口径
        for b in (find_first_buys(d, cl, max_age=H1_YB_MAX_AGE, near=TB_NEAR)):
            yb.append(b)
    cands = sorted(tb + yb, key=lambda x: (not x["accept"], x["pri"], x.get("age", 0)))
    best = next((x for x in cands if x["accept"]), None)
    return {"ok": True, "best": best, "all": cands, "close": close,
            "ma10": ma10v, "ma20": ma20v, "golden": golden,
            "dt": str(d["dt"].iloc[-1])[:16], "n": len(d), "ma20_state": ma20}


# ══════════════════════════ 单只判定 ══════════════════════════
def analyse_one(code, nm, d, with_h1=True):
    """一只股票的买点判定（口径与 tech_summary 一致 + 买点有效性闸门）

    日线 = 主判定（决定 accept）；小时线 = 次级确认（只做参考加分，不改变日线结论）
    """
    bars = C.bars_of(d)
    fx = C.chan_read(bars, "day")
    if not fx.get("ok"):
        return {"code": code, "name": nm, "ok": False, "err": "缠论数据不足"}
    ma20 = ma20_state(d)
    cl = d["close"].astype(float).values
    close = float(cl[-1])
    prev = float(cl[-2]) if len(cl) > 1 else close
    pct = (close / prev - 1) * 100 if prev else 0.0

    # ── 三买：专用扫描（chan_read 只看最后一个中枢，会漏掉绝大多数）──
    tb_all = find_third_buys(d, cl)
    # ── 一买：用 chan_read 结果 + 距离/新鲜度闸门（必须 MA20 向上）──
    yb_all = []
    eb_all = []
    for b in (fx.get("buy_list") or []):
        if b.get("tag") == "一买":
            yb_all.append(_gate_y1(b, close, ma20))
        elif b.get("tag") == "二买":
            # 2026-11-10 大佬：二买恢复，按独立闸门（MA20 向上只加分，不作为硬门槛）
            eb_all.append(_gate_y2(b, close, ma20))

    cands = tb_all + yb_all + eb_all
    acc = [x for x in cands if x["accept"]]
    acc.sort(key=lambda x: (x["pri"], x.get("age", 0)))
    best = acc[0] if acc else None
    ranked = sorted(cands, key=lambda x: (not x["accept"], x["pri"], x.get("age", 0)))

    # ── 卖点：三卖（全中枢扫描）+ 一卖/二卖（chan_read 的 sell_list 过闸）──
    sells_ranked = find_sells(d, fx, close, ma20)
    best_sell = next((x for x in sells_ranked if x["accept"]), None)

    dd = M.add_ind(d)
    al = M.ma_alignment(dd)
    ma20v = float(dd["ma20"].iloc[-1]) if "ma20" in dd.columns and pd.notna(dd["ma20"].iloc[-1]) else None
    ma160v = float(dd["ma160"].iloc[-1]) if "ma160" in dd.columns and pd.notna(dd["ma160"].iloc[-1]) else None

    # ── 小时线次级确认（只在日线有买点时才算数，但数据照样取，便于展示）──
    h1 = None
    if with_h1:
        try:
            h1 = h1_buys(code, "index" if is_index_code(code) else "stock")
        except Exception:
            h1 = None

    return {
        "code": code, "name": nm, "ok": True,
        "h1": h1,
        "dt": str(d["dt"].iloc[-1])[:10], "close": close, "pct": pct,
        "stage": fx.get("stage"), "zpos": fx.get("zpos"), "div": fx.get("div"),
        "zs": fx.get("zs"), "ma20": ma20, "align": al,
        "ma20v": ma20v, "ma160v": ma160v,
        "above_ma20": (ma20v is not None and close > ma20v),
        "above_ma160": (ma160v is not None and close > ma160v),
        "buys": ranked, "best": best,
        "sells": fx.get("sell_list") or [],          # 保留旧字段（不破坏其它调用方）
        "sells_ranked": sells_ranked, "best_sell": best_sell,
        "n_bars": len(d),
    }


def find_third_buys(d, cl, max_age=None, near=None):
    """三买扫描：**遍历所有中枢**（不是只看最后一个），找「向上离开后回踩不破 ZG」

    有效性闸门：
      ① 买点年龄 ≤ TB_MAX_AGE 根
      ② 回踩低点之后**收盘从未跌破**该低点（结构未破）
      ③ 现价在回踩低点上方 0~TB_NEAR% 内（已跑远的就不算可买）
    """
    mb = C.merge_bars(C.bars_of(d))
    if len(mb) < 20:
        return []
    bi = C.build_bi(mb)
    if len(bi) < 6:
        return []
    segs = C.segs_from_bi(bi)
    n = len(segs)
    nmb = len(mb)
    seen = set()
    out = []
    for i in range(n - 3, -1, -1):
        w = segs[i:i + 3]
        if len(w) < 3:
            continue
        zg = min(x["hi"] for x in w)
        zd = max(x["lo"] for x in w)
        if not zg > zd:
            continue
        for k in range(i + 3, n - 1):
            if not (segs[k]["up"] and segs[k]["hi"] > zg):
                continue
            r = segs[k + 1]
            if r["up"] or r["lo"] <= zg:
                continue
            key = (round(zg, 2), round(r["lo"], 2))     # 同一中枢+同一回踩低点只留一条
            if key in seen:
                continue
            seen.add(key)
            lvl = round(float(r["lo"]), 2)
            age = nmb - 1 - r["i1"]
            # ② 结构未破：回踩低点之后**再未跌破**（用合并K线的低点，索引与 segs 同空间）
            #    ⚠️ 不能用 cl：cl 是原始K线索引，r["i1"] 是合并后索引，两者不同空间
            after = [x[1] for x in mb[r["i1"]:]]
            broke = bool(after and min(after) < lvl * 0.995)
            gap = (cl[-1] / lvl - 1) * 100 if lvl > 0 else 0.0
            accept, why = True, "离开中枢后回踩不破 ZG（%.2f）" % zg
            if broke:
                accept, why = False, "回踩低点已被跌破（结构破坏）"
            elif age > (TB_MAX_AGE if max_age is None else max_age):
                accept, why = False, "买点太老（%d根前 > %d）" % (
                    age, TB_MAX_AGE if max_age is None else max_age)
            elif gap > (TB_NEAR if near is None else near):
                accept, why = False, "已跑远（现价比买点高 %.1f%%）" % gap
            elif gap < -BUY_NEAR_DN:
                accept, why = False, "已跌破买点 %.1f%%" % abs(gap)
            elif cl[-1] <= zg:
                accept, why = False, "已跌回中枢内（现价 %.2f ≤ ZG %.2f，三买失效）" % (cl[-1], zg)
            out.append({"tag": "三买", "pri": 1, "level": lvl, "age": age,
                        "zg": round(float(zg), 2), "zd": round(float(zd), 2),
                        "gap": gap, "accept": accept, "why": why,
                        "sustained": age > 25})
            break
    # ⚠️ 按 level 去重：同一个买点价可能挂在多个重叠中枢上（实测风语筑 13.30 出现 7 次）
    uniq = {}
    for x in out:
        k = round(float(x["level"]), 2)
        cur = uniq.get(k)
        if cur is None:
            uniq[k] = x
        elif (not cur["accept"] and x["accept"]) or \
                (cur["accept"] == x["accept"] and x.get("age", 999) < cur.get("age", 999)):
            uniq[k] = x
    out = list(uniq.values())
    out.sort(key=lambda x: (not x["accept"], x["age"]))
    return out


# 大佬 2026-11-09 20:54 定稿：「优先三买，然后是一买，**二买就不要了**」
# 大佬 2026-11-10 更新（覆盖上条）：**二买恢复**，优先级排第三（三买 > 一买 > 二买）
#   → 二买不再丢弃，走 _gate_y2 独立闸门（MA20 向上只加分，不作硬门槛）
DROP_BUY_TAGS = set()      # 曾为 {"二买"}；2026-11-10 起不再丢弃任何买点类型


def find_first_buys(d, cl, max_age=None, near=None):
    """独立扫描一买（底背驰段），小时线专用

    日线走 chan_read 的 buy_list（口径统一）；小时线这里按「下跌段价格新低 + 力度衰减」判底背驰。
    """
    _ma = YB_MAX_AGE if max_age is None else max_age
    _nr = BUY_NEAR_UP if near is None else near
    mb = C.merge_bars(C.bars_of(d))
    if len(mb) < 20:
        return []
    bi = C.build_bi(mb)
    if len(bi) < 6:
        return []
    segs = C.segs_from_bi(bi)
    n = len(segs)
    amp = lambda s: abs(s["to"] - s["from"])
    out, seen = [], set()
    for k in range(n - 1, max(-1, n - 12), -1):
        s = segs[k]
        if s["up"]:
            continue
        same = [j for j in range(k) if not segs[j]["up"]]
        if not same:
            continue
        p = segs[same[-1]]
        if not (s["to"] < p["to"] and amp(s) < amp(p)):
            continue
        lvl = round(float(s["to"]), 2)
        if lvl <= 0 or lvl in seen:
            continue
        seen.add(lvl)
        age = len(mb) - 1 - s["i1"]
        gap = (cl[-1] / lvl - 1) * 100
        accept, why = True, "底背驰（力度衰减，MA10>MA20 确认）"
        if age > _ma:
            accept, why = False, "买点太老（%d根前 > %d）" % (age, _ma)
        elif gap > _nr:
            accept, why = False, "已跑远（现价比一买高 %.1f%%）" % gap
        elif gap < -BUY_NEAR_DN:
            accept, why = False, "已跌破一买 %.1f%%" % abs(gap)
        out.append({"tag": "一买", "pri": 2, "level": lvl, "age": age,
                    "gap": gap, "accept": accept, "why": why, "sustained": False})
    out.sort(key=lambda x: (not x["accept"], x["age"]))
    return out


def _gate_y1(b, close, ma20):
    """一买闸门：必须 MA20 向上（大佬硬要求）+ 还在买点附近 + 新鲜"""
    lvl = float(b["level"])
    if lvl <= 0:      # 脏数据保护（除权/停牌产生 0 价笔端点）
        return {"tag": "一买", "pri": 2, "level": 0.0, "age": int(b.get("age") or 0),
                "gap": 0.0, "accept": False, "why": "买点价格异常（0）", "sustained": False}
    age = int(b.get("age") or 0)
    gap = (close / lvl - 1) * 100 if lvl > 0 else 0.0
    up = bool(ma20.get("up"))
    accept, why = True, "MA20 向上 %+.2f%% ✓" % (ma20.get("slope") or 0.0)
    if not up:
        accept, why = False, "MA20 未向上（斜率 %+.2f%%）→ 一买不认" % (ma20.get("slope") or 0.0)
    elif age > YB_MAX_AGE:
        accept, why = False, "买点太老（%d根前 > %d）" % (age, YB_MAX_AGE)
    elif gap > BUY_NEAR_UP:
        accept, why = False, "已跑远（现价比一买高 %.1f%%）" % gap
    elif gap < -BUY_NEAR_DN:
        accept, why = False, "已跌破一买 %.1f%%" % abs(gap)
    return {"tag": "一买", "pri": 2, "level": round(lvl, 2), "age": age,
            "gap": gap, "accept": accept, "why": why,
            "sustained": bool(b.get("sustained"))}


def _gate_y2(b, close, ma20):
    """二买闸门（2026-11-10 大佬：二买恢复，不再丢弃）

    闸门（硬门槛）：
      ① 年龄 ≤ SCAN_EB_MAX_AGE 根
      ② 现价不得跌破二买低点超过 3%（跌破=结构破坏，不认）
      ③ 现价距买点 +SCAN_EB_NEAR% 以内
    与一买的区别（注释写清）：
      · 一买 **必须 MA20 向上**（硬门槛，不过就不认）
      · 二买 **MA20 向上只加分、不作硬门槛** —— 二买本身就处在回踩确认阶段，
        均线往往还没拐头，强行要求 MA20 向上会把真二买全部误杀。
        因此这里 MA20 未向上仅体现在 why 文本上（标注「MA20 未向上（仅加分不否）」），
        accept 不受其影响。
    返回结构与 _gate_y1 完全一致（tag/pri=3/level/age/gap/accept/why/sustained）。
    """
    lvl = float(b.get("level") or 0)
    if lvl <= 0:      # 脏数据保护（除权/停牌产生 0 价笔端点）
        return {"tag": "二买", "pri": 3, "level": 0.0, "age": int(b.get("age") or 0),
                "gap": 0.0, "accept": False, "why": "买点价格异常（0）", "sustained": False}
    age = int(b.get("age") or 0)
    gap = (close / lvl - 1) * 100 if lvl > 0 else 0.0
    up = bool((ma20 or {}).get("up"))
    slope = (ma20 or {}).get("slope") or 0.0
    ma_bit = ("MA20 向上 %+.2f%% ✓" % slope) if up else \
             ("MA20 未向上 %+.2f%%（二买仅加分，不作硬门槛）" % slope)
    accept, why = True, "回踩不破一买低点（二买）｜ %s" % ma_bit
    if age > SCAN_EB_MAX_AGE:
        accept, why = False, "买点太老（%d根前 > %d）" % (age, SCAN_EB_MAX_AGE)
    elif gap < -BUY_NEAR_DN:
        accept, why = False, "已跌破二买 %.1f%%（结构破坏）" % abs(gap)
    elif gap > SCAN_EB_NEAR:
        accept, why = False, "已跑远（现价比二买高 %.1f%%）" % gap
    return {"tag": "二买", "pri": 3, "level": round(lvl, 2), "age": age,
            "gap": gap, "accept": accept, "why": why,
            "sustained": bool(b.get("sustained"))}


def find_third_sells(d, cl, max_age=None, near=None):
    """三卖扫描：对称于 find_third_buys() —— **遍历所有中枢**

    结构：向下离开中枢（segs[k] 向下且 lo < zd）后反抽（segs[k+1] 向上且 hi < zd）
          → 三卖 = 反抽高点 hi（反抽不回中枢 = 三卖确认）

    有效性闸门：
      ① 卖点年龄 ≤ SCAN_SELL_MAX_AGE 根
      ② 反抽之后收盘**从未涨破**该高点（涨破 = 卖点失效）
      ③ 现价 ≤ 三卖价 × 1.03（涨破 3% = 卖点失效）
      ④ 现价距卖点 ≤ SCAN_SELL_NEAR%（跑太远就不算「可卖位置」）
    """
    _ma = SCAN_SELL_MAX_AGE if max_age is None else max_age
    _nr = SCAN_SELL_NEAR if near is None else near
    mb = C.merge_bars(C.bars_of(d))
    if len(mb) < 20:
        return []
    bi = C.build_bi(mb)
    if len(bi) < 6:
        return []
    segs = C.segs_from_bi(bi)
    n = len(segs)
    nmb = len(mb)
    seen = set()
    out = []
    for i in range(n - 3, -1, -1):
        w = segs[i:i + 3]
        if len(w) < 3:
            continue
        zg = min(x["hi"] for x in w)
        zd = max(x["lo"] for x in w)
        if not zg > zd:
            continue
        for k in range(i + 3, n - 1):
            if segs[k]["up"] or not (segs[k]["lo"] < zd):      # 必须向下离开中枢
                continue
            r = segs[k + 1]
            if not r["up"] or not (r["hi"] < zd):              # 反抽必须向上且不回中枢
                continue
            key = (round(zg, 2), round(r["hi"], 2))
            if key in seen:
                continue
            seen.add(key)
            lvl = round(float(r["hi"]), 2)
            age = nmb - 1 - r["i1"]
            # ② 结构未破：反抽高点之后再未涨破（合并K线空间）
            after = [x[0] for x in mb[r["i1"]:]]
            broke = bool(after and max(after) > lvl * SELL_BREAK_DN)
            gap = (cl[-1] / lvl - 1) * 100 if lvl > 0 else 0.0
            accept, why = True, "向下离开中枢后反抽不回 ZD（%.2f）" % zd
            if broke:
                accept, why = False, "反抽高点已被涨破（卖点失效）"
            elif age > _ma:
                accept, why = False, "卖点太老（%d根前 > %d）" % (age, _ma)
            elif cl[-1] > lvl * SELL_BREAK_UP:
                accept, why = False, "已涨破三卖 %.1f%%（卖点失效）" % (cl[-1] / lvl * 100 - 100)
            elif gap > _nr:
                accept, why = False, "现价高于卖点 %.1f%%（未到卖点）" % gap
            elif gap < -_nr:
                accept, why = False, "已跌破卖点 %.1f%%（离卖点太远，参考意义降低）" % abs(gap)
            out.append({"tag": "三卖", "pri": 1, "level": lvl, "age": age,
                        "zg": round(float(zg), 2), "zd": round(float(zd), 2),
                        "gap": gap, "accept": accept, "why": why,
                        "sustained": age > 25})
            break
    # 按 level 去重（同一卖点价可能挂在多个重叠中枢上）
    uniq = {}
    for x in out:
        k = round(float(x["level"]), 2)
        cur = uniq.get(k)
        if cur is None:
            uniq[k] = x
        elif (not cur["accept"] and x["accept"]) or \
                (cur["accept"] == x["accept"] and x.get("age", 999) < cur.get("age", 999)):
            uniq[k] = x
    out = list(uniq.values())
    out.sort(key=lambda x: (not x["accept"], x["age"]))
    return out


def _gate_sell(b, close, ma20=None, tag=None):
    """一卖 / 二卖闸门（来自 chan_read 的 sell_list）

    闸门（2026-11-10 大佬追加口径：**一卖必须叠加 MA20 向下同向确认**，对称于一买须 MA20 向上）：
      ① 年龄 ≤ SCAN_SELL_MAX_AGE 根
      ② **一卖：MA20 必须向下**（slope < 0，硬门槛；只背驰而均线还向上 → 不认）
         二卖：MA20 向下**仅加分，不作硬门槛**（与二买对称）
      ③ 现价未涨破卖点（涨破 3% = 失效）
      ④ 现价距卖点 ≤ SCAN_SELL_NEAR%（上下都限）
    买点看「现价要高于买点」，卖点对称看「现价要低于卖点」，gap 沿用同一公式便于对比。
    """
    tag = tag or b.get("tag")
    pri = 2 if tag == "一卖" else 3        # 一卖 > 二卖（见 find_sells 注释）
    lvl = float(b.get("level") or 0)
    if lvl <= 0:
        return {"tag": tag, "pri": pri, "level": 0.0, "age": int(b.get("age") or 0),
                "gap": 0.0, "accept": False, "why": "卖点价格异常（0）", "sustained": False}
    age = int(b.get("age") or 0)
    gap = (close / lvl - 1) * 100
    m = ma20 or {}
    # down 优先用 ma20_state 的 down 字段（tech_summary 已按 TECH_MA20_DN_BARS 单独算 slope_dn）
    down = bool(m.get("down", (m.get("slope_dn", m.get("slope")) or 0.0) < 0))
    slope = m.get("slope_dn", m.get("slope")) or 0.0      # 拒绝文案用与 down 同源的斜率
    ma_bit = (("MA20 向下 %+.2f%% ✓" % slope) if down
              else ("MA20 未向下 %+.2f%%" % slope))
    if tag == "二卖":
        # 二卖：MA20 向下只加分
        ma_bit = (("MA20 向下 %+.2f%% ✓" % slope) if down
                  else ("MA20 未向下 %+.2f%%（二卖仅加分，不作硬门槛）" % slope))
    accept, why = True, "顶背驰/次高点结构成立｜%s" % ma_bit
    if age > SCAN_SELL_MAX_AGE:
        accept, why = False, "卖点太老（%d根前 > %d）" % (age, SCAN_SELL_MAX_AGE)
    elif tag == "一卖" and not down:
        # 硬门槛：一卖只背驰、均线还在向上 → 不认（对齐一买的 MA20 向上要求）
        accept, why = False, "MA20 未向下（斜率 %+.2f%%）→ 一卖不认" % slope
    elif close > lvl * SELL_BREAK_UP:
        accept, why = False, "已涨破%s %.1f%%（卖点失效）" % (tag, gap)
    elif gap > SCAN_SELL_NEAR:
        accept, why = False, "现价高于卖点 %.1f%%（未到卖点）" % gap
    elif gap < -SCAN_SELL_NEAR:
        accept, why = False, "已跌破卖点 %.1f%%（离卖点太远，参考意义降低）" % abs(gap)
    return {"tag": tag, "pri": pri, "level": round(lvl, 2), "age": age,
            "gap": gap, "accept": accept, "why": why,
            "sustained": bool(b.get("sustained"))}


def find_sells(d, fx, close, ma20=None):
    """卖点汇总（2026-11-10 新增）：① 三卖（全中枢扫描）② 一卖 ③ 二卖

    pri 口径（按结构强度，注释写清）：
      · pri=1 → 三卖：向下离开中枢后反抽不回中枢，结构确认度最高（对称于三买）；**不强制 MA20**（结构已确认）
      · pri=2 → 一卖：顶背驰 + **MA20 必须向下**（硬门槛，2026-11-10 追加：对称于一买须 MA20 向上）
      · pri=3 → 二卖：一卖后次高点（最弱，仅作提醒）；MA20 向下**仅加分**（与二买对称）
    返回列表（含被否），排序：能用的在前，再按 pri、年龄。
    """
    cl = d["close"].astype(float).values
    out = list(find_third_sells(d, cl))
    # 一卖硬门槛/二卖加分：把 MA20 方向传进闸门（ma20 为 None 时从 d 现算，避免调用方漏传）
    if ma20 is None:
        try:
            ma20 = ma20_state(d)
        except Exception:
            ma20 = {}
    for s in (fx.get("sell_list") or []):
        if s.get("tag") in ("一卖", "二卖"):
            out.append(_gate_sell(s, close, ma20))
    # ⚠️ 按价格去重：同一价位可能同时被「三卖扫描」和 chan_read 的「一卖/二卖」命中
    #   （如茅台 1359.60 两边都出）→ 保留 pri 更高（数值更小）的那条，避免报告重复刷屏
    uniq = {}
    for x in out:
        k = round(float(x.get("level") or 0), 2)
        cur = uniq.get(k)
        if cur is None:
            uniq[k] = x
        elif (not cur["accept"] and x["accept"]) or \
                (cur["accept"] == x["accept"] and x.get("pri", 9) < cur.get("pri", 9)):
            uniq[k] = x
    out = list(uniq.values())
    out.sort(key=lambda x: (not x["accept"], x["pri"], x.get("age", 0)))
    return out


def score_quality(r):
    """质量分：站上 MA20 +1、站上 MA160 +1、多头排列 +2、买点新鲜(≤10根) +1

    小时线（次级）加分：小时线也有买点 +1，小时线三买 +1（日线优先，小时线只做叠加）
    """
    s = 0
    if r.get("above_ma20"):
        s += 1
    if r.get("above_ma160"):
        s += 1
    if r.get("align", {}).get("state") == "多头排列":
        s += 2
    if (r.get("best", {}) or {}).get("age", 999) <= 10:
        s += 1
    h1 = r.get("h1") or {}
    if h1.get("ok") and h1.get("best"):
        s += 1
        if h1["best"].get("pri") == 1:
            s += 1
    return s


def h1_extra(r):
    """小时线补充信息（MA10/MA20 金叉状态）"""
    h1 = r.get("h1") or {}
    if not h1.get("ok"):
        return ""
    bits = ["MA10>MA20 金叉" if h1.get("golden") else "MA10<MA20 未金叉"]
    b = h1.get("best")
    if b and (h1.get("ma20_state") or {}).get("up"):
        bits.append("h1 MA20 向上")
    return ("（%s）" % "、".join(bits)) if bits else ""


def h1_tag(r):
    """小时线次级标签（日线有买点时叠加显示）"""
    h1 = r.get("h1") or {}
    if not h1.get("ok"):
        return "h1 无数据"
    b = h1.get("best")
    if not b:
        return "h1 无买点"
    return "h1 %s %.2f（%d根前）" % (PRI_MARK.get(b["pri"], b["tag"]), b["level"], b.get("age", 0))


def qual_tags(r):
    tags = []
    if r.get("above_ma20"):
        tags.append("站上MA20")
    if r.get("above_ma160"):
        tags.append("站上MA160")
    if r.get("align", {}).get("state") == "多头排列":
        tags.append("多头排列")
    return ("｜" + "、".join(tags)) if tags else ""


def grade(r):
    """给个股打个一句话结论（用于排序/展示）

    排序键（大佬 2026-11-09 20:57：日线优先、小时线次之）：
      日线三买(1) < 日线一买(2) < 日线有买点但小时线也确认(仍按日线级) … 无买点(7+)
    """
    if not r.get("ok"):
        return "数据不足", 9
    bb = r.get("best")
    if bb:
        pri = bb.get("pri", 9)
        tag = PRI_MARK.get(pri, bb.get("tag"))
        extra = []
        if r.get("above_ma20"):
            extra.append("站上MA20")
        if r.get("above_ma160"):
            extra.append("站上MA160")
        if r.get("align", {}).get("state") == "多头排列":
            extra.append("多头排列")
        h1 = r.get("h1") or {}
        h1b = h1.get("best") if h1.get("ok") else None
        if h1b:
            extra.append("h1" + PRI_MARK.get(h1b["pri"], h1b["tag"]))
        return "%s ✓ %.2f（%d根前）%s" % (tag, bb["level"], bb.get("age", 0),
                                         ("｜" + "、".join(extra)) if extra else ""), pri
    # 2026-11-10：卖点优先于「买点被否」提示（否则「有卖点，观望」永远被前两条抢走、永不触发）
    #   卖点是风险信号，必须显式告知，不能只写「一买被否」就完事
    bs = r.get("best_sell")
    if bs:
        return "有卖点，观望（%s %.2f）" % (bs.get("tag"), bs.get("level", 0)), 6
    # 无可买：看有没有被否掉的
    for b in (r.get("buys") or []):
        if b.get("tag") == "一买":
            return "一买被否（%s）" % (b.get("why") or "").split("（")[0], 5
    for b in (r.get("buys") or []):
        if b.get("tag") == "三买":
            return "三买被否（%s）" % (b.get("why") or "").split("（")[0], 6
    # 二买被否（优先级最低）
    for b in (r.get("buys") or []):
        if b.get("tag") == "二买":
            return "二买被否（%s）" % (b.get("why") or "").split("（")[0], 6
    if r.get("sells"):
        return "有卖点，观望", 6
    return "无买点", 7


# ══════════════════════════ 报告 ══════════════════════════
def render(txt_head, rows, meta_lines):
    L = []
    A = L.append
    A(txt_head)
    for x in meta_lines:
        A(x)
    A("")

    ok = [r for r in rows if r.get("ok")]
    buy = [r for r in ok if r.get("best")]
    buy.sort(key=lambda r: (r["best"].get("pri", 9), -r["close"]))
    p1 = [r for r in buy if r["best"].get("pri") == 1]
    p2 = [r for r in buy if r["best"].get("pri") == 2]
    p3 = [r for r in buy if r["best"].get("pri") == 3]
    sell = [r for r in ok if r.get("best_sell")]

    A("═══ 一、结论 ═══")
    A("  口径：① 三买 > ② 一买（须 MA20 向上）> ③ 二买（胜率最低）｜ 卖点：三卖 > 一卖（须 MA20 向下）> 二卖")
    A("  扫描 %d 只，有可用买点 %d 只（三买 %d / 一买 %d / 二买 %d），有卖点 %d 只"
      % (len(ok), len(buy), len(p1), len(p2), len(p3), len(sell)))
    if buy:
        buy_q = sorted(buy, key=lambda r: (-score_quality(r), r["best"].get("pri", 9)))
        A("  首选：" + "、".join("%s(%s)" % (r["name"] or r["code"], PRI_MARK.get(r["best"]["pri"], ""))
                               for r in buy_q[:5]))
    else:
        A("  首选：无（本行业当前没有三买、二买，也没有 MA20 向上的一买）")
    if sell:
        A("  ⛔ 卖点警示：" + "、".join("%s(%s)" % (r["name"] or r["code"],
                                               SELL_MARK.get(r["best_sell"].get("pri", 9),
                                                             r["best_sell"].get("tag", "")))
                                       for r in sell[:5]))
    A("")

    A("═══ 二、① 三买（优先级最高）═══")
    if p1:
        for r in sorted(p1, key=lambda x: -score_quality(x)):
            b = r["best"]
            A("  🟢🌙 买点 %s %s ｜ 收 %.2f（%+.2f%%）｜ 三买 %.2f（%d根前，现价+%.1f%%）%s"
              % (r["name"] or r["code"], r["code"], r["close"], r["pct"], b["level"],
                 b.get("age", 0), b.get("gap", 0), qual_tags(r)))
            A("      该三买中枢 %.2f~%.2f ｜ 现价%s ZG ｜ %s ｜ MA20 %s ｜ MA160 %s"
              % (b.get("zd", 0), b.get("zg", 0),
                 "站上" if r["close"] > b.get("zg", 0) else "跌破",
                 r["align"].get("state", "-"),
                 ("%.2f（%s）" % (r["ma20v"], "上方" if r["above_ma20"] else "下方")) if r["ma20v"] else "-",
                 ("%.2f（%s）" % (r["ma160v"], "上方" if r["above_ma160"] else "下方")) if r["ma160v"] else "-"))
            A("      ⏱ 小时线（次级）：%s%s" % (h1_tag(r), h1_extra(r)))
    else:
        A("  无")
    A("")

    A("═══ 三、② 一买（须 MA20 向上）═══")
    if p2:
        for r in sorted(p2, key=lambda x: -score_quality(x)):
            b = r["best"]
            A("  🟢🌙 买点 %s %s ｜ 收 %.2f（%+.2f%%）｜ 一买 %.2f（%d根前，现价+%.1f%%）%s"
              % (r["name"] or r["code"], r["code"], r["close"], r["pct"], b["level"],
                 b.get("age", 0), b.get("gap", 0), qual_tags(r)))
            A("      %s ｜ MA20 斜率 %+.2f%% ✓ ｜ %s"
              % (r["stage"], (r["ma20"].get("slope") or 0.0), r["align"].get("state", "-")))
    else:
        A("  无")
    A("")

    # 被否掉的一买：按真实原因分组（不能一律写「MA20 未向上」，那样正斜率也会被误标）
    denied = [r for r in ok if not r.get("best")
              and any(b.get("tag") == "一买" for b in (r.get("buys") or []))]
    def _reason_key(why):
        if why.startswith("MA20 未向上"):
            return "MA20 未向上（一买硬门槛不过）"
        if why.startswith("买点太老"):
            return "买点太老（超过 %d 根）" % YB_MAX_AGE
        if why.startswith("已跑远"):
            return "已跑远（现价高出买点 >%d%%）" % BUY_NEAR_UP
        if why.startswith("已跌破"):
            return "已跌破买点"
        return "其他"
    grp = {}
    for r in denied:
        b = next(x for x in r["buys"] if x.get("tag") == "一买")
        grp.setdefault(_reason_key(b.get("why") or ""), []).append((r, b))

    A("═══ 四、③ 二买（胜率最低·仅作参考）═══")
    if p3:
        A("  ⚠️ 二买胜率最低（本质是「一买之后的回踩」，趋势未确认转多，假突破/直接破位多），必须配 MA20 向上/量能/板块强度等其他确认，不要单凭二买下单")
        for r in sorted(p3, key=lambda x: -score_quality(x)):
            b = r["best"]
            A("  🟢🌙 买点 %s %s ｜ 收 %.2f（%+.2f%%）｜ 二买 %.2f（%d根前，现价+%.1f%%）%s"
              % (r["name"] or r["code"], r["code"], r["close"], r["pct"], b["level"],
                 b.get("age", 0), b.get("gap", 0), qual_tags(r)))
            A("      MA20 %s ｜ %s ｜ 注：二买不要求 MA20 向上（仅加分）"
              % (("%.2f（%s，斜率 %+.2f%%）" % (r["ma20v"], "向上" if r["ma20"].get("up") else "未向上",
                                              r["ma20"].get("slope") or 0.0)) if r["ma20v"] else "-",
                 r["align"].get("state", "-")))
    else:
        A("  无")
    A("")

    A("═══ 五、⛔ 卖点（三卖 / 一卖 / 二卖）═══")
    if sell:
        for r in sorted(sell, key=lambda x: (x["best_sell"].get("pri", 9), x["best_sell"].get("age", 0))):
            s = r["best_sell"]
            gap = s.get("gap", 0)
            # gap>0 = 现价高于卖点（卖点被涨破，已失效）；gap<0 = 现价仍在卖点下方（结构未破）
            if gap > 3:
                state = "⛔ 已涨破卖点 %.1f%%（卖点失效）" % gap
            elif gap > 0:
                state = "现价已到卖点上方 %.1f%%（卖点边缘，警惕失效）" % gap
            elif abs(gap) < 0.05:
                state = "现价正踩在卖点上，结构未破（最直接的减仓位）"
            else:
                state = "现价仍在卖点下方 %.1f%%，结构未破" % abs(gap)
            A("  ⛔ %s %s ｜ 收 %.2f（%+.2f%%）｜ %s %.2f（%d根前，现价距卖点 %+.1f%%）｜ %s"
              % (r["name"] or r["code"], r["code"], r["close"], r["pct"],
                 s.get("tag"), s.get("level", 0), s.get("age", 0), gap, state))
            A("      %s ｜ MA20 %s"
              % (s.get("why") or "-",
                 ("%.2f（%s）" % (r["ma20v"], "上方" if r["above_ma20"] else "下方")) if r["ma20v"] else "-"))
    else:
        A("  无")
    # 卖点被否：按原因归类列出来（一卖最常见的是「MA20 未向下」）
    sdeny = [r for r in ok if not r.get("best_sell") and (r.get("sells_ranked") or [])]
    if sdeny:
        A("  ▸ 卖点被否（%d 只，按原因）" % len(sdeny))
        sgrp = {}
        for r in sdeny:
            for x in r["sells_ranked"]:
                if x.get("accept"):
                    continue
                # 只抹掉「带 % 的数字」用于归组，避免把 MA20 里的 20 也吃掉
                w = re.sub(r"\s*[+-]?\d+(?:\.\d+)?%", "", (x.get("why") or "").split("（")[0]).strip()
                sgrp.setdefault((x.get("tag"), w or "其他"), []).append((r, x))
        for (tg, w), items in sorted(sgrp.items(), key=lambda kv: -len(kv[1]))[:4]:
            # 按股票去重计数（同一只票可能有多个同因卖点，别算成多只）
            uniq_r, samples, seen_n = [], [], set()
            for r, x in items:
                if r["code"] in seen_n:
                    continue
                seen_n.add(r["code"])
                samples.append("%s %.2f" % ((r["name"] or r["code"])[:6], x["level"]))
            A("      %s %s —— %d只：%s" % (
                tg, w, len(seen_n), "、".join(samples[:4])))
    A("")

    A("═══ 六、见过一买但被否（按原因归类）═══")
    if grp:
        for why, items in sorted(grp.items(), key=lambda kv: -len(kv[1])):
            A("  ▸ %s —— %d只" % (why, len(items)))
            items.sort(key=lambda x: abs(x[1].get("gap", 0)))
            for r, b in items[:5]:
                A("      %s %s ｜ 一买 %.2f（%d根前）｜ 现价%+.1f%% ｜ MA20 %+.2f%%"
                  % ((r["name"] or "")[:8], r["code"], b["level"], b.get("age", 0),
                     b.get("gap", 0), r["ma20"].get("slope") or 0.0))
            if len(items) > 5:
                A("      …… 另 %d 只" % (len(items) - 5))
    else:
        A("  无")
    A("")

    # 全量清单（表格）
    A("═══ 七、全量明细（按结论排序）═══")
    ranked_all = sorted(ok, key=lambda r: (grade(r)[1], -score_quality(r)))
    for r in ranked_all:
        g, _ = grade(r)
        A("  %-8s %-9s 收%8.2f %+6.2f%%  %s"
          % ((r["name"] or "")[:8], r["code"], r["close"], r["pct"], g))
    A("")
    A("⚠️ 技术结构扫描，不构成投资建议。")
    return "\n".join(L)


# ══════════════════════════ 飞书推送 ══════════════════════════
def push_feishu(title, txt, imgs=None, chat=None, template="blue"):
    import feishu_card as FC
    try:
        _c = json.load(open(os.path.join(os.path.dirname(WS), "openclaw.json"),
                            encoding="utf-8"))["channels"]["feishu"]
        chat = chat or _c.get("defaultChat") or FC.DEFAULT_CHAT
    except Exception:
        chat = chat or FC.DEFAULT_CHAT
    secs, cur = [], []
    for ln in txt.split("\n"):
        if ln.startswith("═══") and cur:
            secs.append("\n".join(cur).strip())
            cur = [ln]
        else:
            cur.append(ln)
    if cur:
        secs.append("\n".join(cur).strip())
    blocks = []
    for i, s in enumerate(secs):
        if i:
            blocks.append({"type": "hr"})
        blocks.append({"type": "md", "text": s})
    return FC.send_card(title, blocks, imgs=imgs, chat=chat, template=template,
                        sign="📎 来自 OpenClaw.Deepseek"), chat


# ══════════════════════════ 板块自身结构 ══════════════════════════
def _sector_block(kind, name, code, scan_codes_, names):
    """板块指数自身的结构结论（行业 / 概念 / 指数成分 都走这里）"""
    d = sector_index_daily(kind, code, name)
    if d is None or len(d) < 60:
        return {"ok": False, "name": name, "code": code, "err": "板块指数数据不足"}
    fx = C.chan_read(C.bars_of(d), "day")
    ma20 = ma20_state(d)
    dd = M.add_ind(d)
    al = M.ma_alignment(dd)
    close = float(d["close"].iloc[-1])
    prev = float(d["close"].iloc[-2]) if len(d) > 1 else close
    tb = find_third_buys(d, d["close"].astype(float).values)
    # 2026-11-10 定稿：行业闸门 = 日线只认三买 + 周线一二三买都认
    # 一买/二买从「周线」提取（不再用日线），日线只保留三买 tb
    yb, eb, wb3 = [], [], []
    try:
        ww = C.resample_w(d)
        if len(ww) >= 24:
            fx_w = C.chan_read(C.bars_of(ww), "week")
            for b in (fx_w.get("buy_list") or []):
                tag = b.get("tag")
                if tag == "一买":
                    yb.append(_gate_y1(b, close, ma20))
                elif tag == "二买":
                    eb.append(_gate_y2(b, close, ma20))
                elif tag == "三买":
                    lvl = float(b.get("level") or 0)
                    gap = (close / lvl - 1) * 100 if lvl > 0 else 0.0
                    accept = (gap <= TB_NEAR) and (gap >= -BUY_NEAR_DN)
                    wb3.append({"tag": "三买", "pri": 1, "level": round(lvl, 2),
                                "age": int(b.get("age") or 0), "gap": gap,
                                "accept": accept,
                                "why": ("周线三买（离开中枢后回踩不破ZG）%+.1f%%" % gap) if accept
                                       else ("已跑远（现价比三买高 %.1f%%）" % gap if gap > TB_NEAR
                                             else "已跌破三买 %.1f%%" % abs(gap)),
                                "sustained": bool(b.get("sustained"))})
    except Exception as _e:
        log("[周线买点] %s 提取失败 %s" % (code, str(_e)[:60]))
    cands = sorted(tb + wb3 + yb + eb, key=lambda x: (not x["accept"], x["pri"], x.get("age", 0)))
    best = next((x for x in cands if x["accept"]), None)
    sells = find_sells(d, fx, close, ma20)
    best_sell = next((x for x in sells if x["accept"]), None)
    return {"ok": True, "kind": kind, "name": name, "code": code,
            "dt": str(d["dt"].iloc[-1])[:10], "close": close,
            "pct": (close / prev - 1) * 100 if prev else 0.0,
            "stage": fx.get("stage"), "zpos": fx.get("zpos"), "zs": fx.get("zs"),
            "div": fx.get("div"), "ma20": ma20, "align": al,
            "buys": cands, "best": best,
            "sells_ranked": sells, "best_sell": best_sell,
            "n_members": len(scan_codes_),
            "n_bars": len(d)}


def render_sector(sec, rows, title, gate_override=False):
    """板块 + 成分股 合并报告

    ⚠️ 大佬 2026-11-09 20:54 定稿流程：
       「首先我让你分析行业有没有买点，如果行业有买点了，然后你再分析个股」
       → **行业（板块）自身没买点 = 直接不下钻**，个股部分不输出
       → 只有 ①三买 / ②一买 算买点，二买已丢弃
    """
    L = []
    A = L.append
    A(title)
    A("口径：① 三买 > ② 一买（须 MA20 向上）> ③ 二买（胜率最低）｜ 卖点：三卖 > 一卖（须 MA20 向下）> 二卖 ｜ 行业无买点则不下钻个股")
    A("级别：**行业闸门 = 日线只认三买 + 周线一二三买都认**；个股层面日线为主、小时线次级确认")
    A("")
    A("═══ 一、行业（板块）自身有没有买点 ═══")
    gate_ok = bool(gate_override)
    if sec.get("ok"):
        b = sec.get("best")
        A("  %s（%s）收 %.2f（%+.2f%%）｜ %s"
          % (sec["name"], sec["code"], sec["close"], sec["pct"], sec.get("stage") or "-"))
        A("  中枢 %s ｜ 均线排列 %s ｜ MA20 %s"
          % (("%.2f~%.2f" % sec["zs"]) if sec.get("zs") else "-",
             sec["align"].get("state", "-"),
             ("%.2f（%+.2f%% / %s）" % (sec["ma20"].get("ma20") or 0, sec["ma20"].get("slope") or 0.0,
                                       "向上" if sec["ma20"].get("up") else "未向上"))
             if sec["ma20"].get("ok") else "-"))
        A("  ⚠️ 中枢下方" if sec.get("zpos") == "below" else
          ("  ✅ 中枢上方" if sec.get("zpos") == "above" else "  在中枢内"))
        if b:
            gate_ok = True
            A("  🌙 行业买点：%s %.2f（%d根前，现价%+.1f%%）→ ✅ 过闸，下钻个股"
              % (PRI_MARK.get(b["pri"], b["tag"]), b["level"], b.get("age", 0), b.get("gap", 0)))
        else:
            A("  🌙 行业买点：无")
            A("  ⛔ 闸门未过 → 不分析个股（行业自身都没有买点，个股买点不具板块效应）")
            A("     想强行看个股：加 --force-scan")
            # 列出被否原因，方便判断要不要换参数
            seen_key, denied = set(), []
            for x in (sec.get("buys") or []):
                if x.get("accept"):
                    continue
                key = (x["tag"], round(float(x["level"]), 2))
                if key in seen_key:
                    continue
                seen_key.add(key)
                denied.append(x)
            if denied:
                A("  （行业层面见到但被否：%s）"
                  % "；".join("%s %.2f → %s" % (PRI_MARK.get(x["pri"], x["tag"]), x["level"],
                                               (x.get("why") or "").split("（")[0])
                              for x in denied[:3]))
        # ── 板块自身卖点结论（2026-11-10 新增）──
        bs = sec.get("best_sell")
        if bs:
            A("  ⛔ 板块自身有卖点：%s %.2f（%d根前，现价距卖点 %+.1f%%）"
              % (bs.get("tag"), bs.get("level", 0), bs.get("age", 0), bs.get("gap", 0)))
        else:
            A("  ⛔ 板块自身卖点：无")
    else:
        A("  （%s）" % sec.get("err", "数据不足"))
        if gate_ok:
            A("  → 无板块指数，但显式指定了代码清单，直接扫个股")
        else:
            A("  ⛔ 行业指数取不到 → 不下钻个股")
    A("")

    if not gate_ok:
        A("═══ 二、个股 ═══")
        A("  跳过（行业未过闸）")
        A("")
        A("⚠️ 技术结构扫描，不构成投资建议。")
        return "\n".join(L)

    A("═══ 二、个股买点（行业已过闸）═══")

    ok = [r for r in rows if r.get("ok")]
    buy = [r for r in ok if r.get("best")]
    p1 = [r for r in buy if r["best"].get("pri") == 1]
    p2 = [r for r in buy if r["best"].get("pri") == 2]
    p3 = [r for r in buy if r["best"].get("pri") == 3]
    sell = [r for r in ok if r.get("best_sell")]

    A("  成分 %d 只，有买点 %d 只（三买 %d / 一买 %d / 二买 %d），有卖点 %d 只"
      % (len(ok), len(buy), len(p1), len(p2), len(p3), len(sell)))
    if buy:
        buy_q = sorted(buy, key=lambda r: (r["best"].get("pri", 9), -score_quality(r)))
        A("  🌙 可买个股（三买优先）：" + "、".join(
            "%s(%s)" % (r["name"] or r["code"], PRI_MARK.get(r["best"]["pri"], ""))
            for r in buy_q))
        dual = [r for r in buy_q if (r.get("h1") or {}).get("best")]
        if dual:
            A("  ✅ 日线+小时线双确认（%d 只，最优先）：%s"
              % (len(dual), "、".join("%s(%s/%s)" % (
                  r["name"] or r["code"],
                  PRI_MARK.get(r["best"]["pri"], ""),
                  PRI_MARK.get(r["h1"]["best"]["pri"], "")) for r in dual)))
        else:
            A("  ⚠️ 无日线+小时线双确认的个股（日线有买点，但小时线还没跟上）")
    else:
        A("  🌙 可买个股：无（既没有三买、二买，也没有 MA20 向上的一买）")
    if sell:
        A("  ⛔ 卖点警示：" + "、".join("%s(%s)" % (r["name"] or r["code"],
                                               SELL_MARK.get(r["best_sell"].get("pri", 9),
                                                             r["best_sell"].get("tag", "")))
                                       for r in sell[:8]))
    A("")

    A("═══ 三、① 三买（优先级最高）═══")
    if p1:
        for r in sorted(p1, key=lambda x: -score_quality(x)):
            b = r["best"]
            A("  🟢🌙 买点 %s %s ｜ 收 %.2f（%+.2f%%）｜ 三买 %.2f（%d根前，现价+%.1f%%）%s"
              % (r["name"] or r["code"], r["code"], r["close"], r["pct"], b["level"],
                 b.get("age", 0), b.get("gap", 0), qual_tags(r)))
            A("      该三买中枢 %.2f~%.2f ｜ 现价%s ZG ｜ %s ｜ MA20 %s ｜ MA160 %s"
              % (b.get("zd", 0), b.get("zg", 0),
                 "站上" if r["close"] > b.get("zg", 0) else "跌破",
                 r["align"].get("state", "-"),
                 ("%.2f（%s）" % (r["ma20v"], "上方" if r["above_ma20"] else "下方")) if r["ma20v"] else "-",
                 ("%.2f（%s）" % (r["ma160v"], "上方" if r["above_ma160"] else "下方")) if r["ma160v"] else "-"))
            A("      ⏱ 小时线（次级）：%s%s" % (h1_tag(r), h1_extra(r)))
    else:
        A("  无")
    A("")

    A("═══ 四、② 一买（须 MA20 向上）═══")
    if p2:
        for r in sorted(p2, key=lambda x: -score_quality(x)):
            b = r["best"]
            A("  🟢🌙 买点 %s %s ｜ 收 %.2f（%+.2f%%）｜ 一买 %.2f（%d根前，现价+%.1f%%）%s"
              % (r["name"] or r["code"], r["code"], r["close"], r["pct"], b["level"],
                 b.get("age", 0), b.get("gap", 0), qual_tags(r)))
            A("      %s ｜ MA20 斜率 %+.2f%% ✓ ｜ %s"
              % (r["stage"], (r["ma20"].get("slope") or 0.0), r["align"].get("state", "-")))
    else:
        A("  无")
    A("")

    A("═══ 五、③ 二买（胜率最低·仅作参考）═══")
    if p3:
        A("  ⚠️ 二买胜率最低（本质是「一买之后的回踩」，趋势未确认转多，假突破/直接破位多），必须配 MA20 向上/量能/板块强度等其他确认，不要单凭二买下单")
        for r in sorted(p3, key=lambda x: -score_quality(x)):
            b = r["best"]
            A("  🟢🌙 买点 %s %s ｜ 收 %.2f（%+.2f%%）｜ 二买 %.2f（%d根前，现价+%.1f%%）%s"
              % (r["name"] or r["code"], r["code"], r["close"], r["pct"], b["level"],
                 b.get("age", 0), b.get("gap", 0), qual_tags(r)))
            A("      MA20 %s ｜ %s ｜ 注：二买不要求 MA20 向上（仅加分）"
              % (("%.2f（%s，斜率 %+.2f%%）" % (r["ma20v"], "向上" if r["ma20"].get("up") else "未向上",
                                              r["ma20"].get("slope") or 0.0)) if r["ma20v"] else "-",
                 r["align"].get("state", "-")))
    else:
        A("  无")
    A("")

    A("═══ 六、⛔ 卖点（三卖 / 一卖 / 二卖）═══")
    if sell:
        for r in sorted(sell, key=lambda x: (x["best_sell"].get("pri", 9), x["best_sell"].get("age", 0))):
            s = r["best_sell"]
            gap = s.get("gap", 0)
            # gap>0 = 现价高于卖点（卖点被涨破，已失效）；gap<0 = 现价仍在卖点下方（结构未破）
            if gap > 3:
                state = "⛔ 已涨破卖点 %.1f%%（卖点失效）" % gap
            elif gap > 0:
                state = "现价已到卖点上方 %.1f%%（卖点边缘，警惕失效）" % gap
            elif abs(gap) < 0.05:
                state = "现价正踩在卖点上，结构未破（最直接的减仓位）"
            else:
                state = "现价仍在卖点下方 %.1f%%，结构未破" % abs(gap)
            A("  ⛔ %s %s ｜ 收 %.2f（%+.2f%%）｜ %s %.2f（%d根前，现价距卖点 %+.1f%%）｜ %s"
              % (r["name"] or r["code"], r["code"], r["close"], r["pct"],
                 s.get("tag"), s.get("level", 0), s.get("age", 0), gap, state))
            A("      %s" % (s.get("why") or "-"))
    else:
        A("  无")
    # 卖点被否：按原因归类（重点看「MA20 未向下」——一卖硬门槛不过的）
    sdeny = [r for r in ok if not r.get("best_sell") and (r.get("sells_ranked") or [])]
    if sdeny:
        A("  ▸ 卖点被否（%d 只，按原因）" % len(sdeny))
        sgrp = {}
        for r in sdeny:
            for x in r["sells_ranked"]:
                if x.get("accept"):
                    continue
                # 只抹掉「带 % 的数字」用于归组，避免把 MA20 里的 20 也吃掉
                w = re.sub(r"\s*[+-]?\d+(?:\.\d+)?%", "", (x.get("why") or "").split("（")[0]).strip()
                sgrp.setdefault((x.get("tag"), w or "其他"), []).append((r, x))
        for (tg, w), items in sorted(sgrp.items(), key=lambda kv: -len(kv[1]))[:4]:
            # 按股票去重计数（同一只票可能有多个同因卖点，别算成多只）
            uniq_r, samples, seen_n = [], [], set()
            for r, x in items:
                if r["code"] in seen_n:
                    continue
                seen_n.add(r["code"])
                samples.append("%s %.2f" % ((r["name"] or r["code"])[:6], x["level"]))
            A("      %s %s —— %d只：%s" % (
                tg, w, len(seen_n), "、".join(samples[:4])))
    A("")

    # 被否
    denied = [r for r in ok if not r.get("best")
              and any(b.get("tag") in ("一买",) for b in (r.get("buys") or []))]

    def _reason_key(why):
        if why.startswith("MA20 未向上"):
            return "MA20 未向上（一买硬门槛不过）"
        if why.startswith("买点太老"):
            return "买点太老（超过 %d 根）" % YB_MAX_AGE
        if why.startswith("已跑远"):
            return "已跑远（现价高出买点 >%d%%）" % BUY_NEAR_UP
        if why.startswith("已跌破"):
            return "已跌破买点"
        return "其他"
    grp = {}
    for r in denied:
        b = next(x for x in (r.get("buys") or []) if x.get("tag") == "一买")
        grp.setdefault(_reason_key(b.get("why") or ""), []).append((r, b))
    A("═══ 七、见过一买但被否（按原因归类）═══")
    if grp:
        for why, items in sorted(grp.items(), key=lambda kv: -len(kv[1])):
            A("  ▸ %s —— %d只" % (why, len(items)))
            items.sort(key=lambda x: abs(x[1].get("gap", 0)))
            for r, b in items[:5]:
                A("      %s %s ｜ 一买 %.2f（%d根前）｜ 现价%+.1f%% ｜ MA20 %+.2f%%"
                  % ((r["name"] or "")[:8], r["code"], b["level"], b.get("age", 0),
                     b.get("gap", 0), r["ma20"].get("slope") or 0.0))
            if len(items) > 5:
                A("      …… 另 %d 只" % (len(items) - 5))
    else:
        A("  无")
    A("")

    A("═══ 八、全量明细（按结论排序）═══")
    for r in sorted(ok, key=lambda x: (grade(x)[1], -score_quality(x))):
        g, _ = grade(r)
        A("  %-8s %-9s 收%8.2f %+6.2f%%  %s"
          % ((r["name"] or "")[:8], r["code"], r["close"], r["pct"], g))
    bad = [r for r in rows if not r.get("ok")]
    if bad:
        A("  （另有 %d 只停牌/数据不足）" % len(bad))
    A("")
    A("⚠️ 技术结构扫描，不构成投资建议。")
    return "\n".join(L)


def chart_one(code, nm, d, h1=None, n_day=120, n_h1=110, n_ma=260):
    """给单只股票出买点图（日线 / 小时线 / 均线版 三面板）→ 路径 或 None

    ⚠️ h1 必须是 **DataFrame**（分钟线原始数据），不能传 h1_buys() 的判定 dict
    """
    try:
        import buypoint_chart as BC
        if h1 is None or not hasattr(h1, "columns") or len(h1) < 60:
            h1 = load_h1(code, "index" if is_index_code(code) else "stock")
        disp = ("%s %s" % (nm or "", code)).strip()
        return BC.draw_buypoint_chart(disp, d, h1, None, n_day, n_h1, n_ma)
    except Exception as e:
        log("[图] %s 出图失败 %s" % (code, str(e)[:90]))
        return None


def chart_sector(kind, name, code, n_day=120, n_ma=300):
    """给板块指数出图 → 路径 或 None"""
    try:
        import buypoint_chart as BC
        d = sector_index_daily(kind, code, name)
        if d is None or len(d) < 80:
            return None
        h1 = None
        if kind in ("industry", "concept") and str(code).endswith(".SI"):
            pass    # 申万/同花顺指数无 60m 分时，跳过小时线面板
        return BC.draw_buypoint_chart("%s（%s）" % (name, code), d, h1, None, n_day, 110, n_ma)
    except Exception as e:
        log("[图] 板块 %s 出图失败 %s" % (name, str(e)[:90]))
        return None


def scan_codes(codes, names=None):
    """批量扫成分股买点（先 MA40 预过滤，再下钻，省时）"""
    names = names or {}
    hist = _today_hist()
    rows = []
    skipped = 0
    for i, c in enumerate(codes, 1):
        nm = names.get(c) or name_map().get(c, "")
        d = load_daily(c, hist)
        if d is None:
            rows.append({"code": c, "name": nm, "ok": False, "err": "日线不足/停牌"})
            continue
        try:
            # ── 预过滤（2026-11-10 大佬要求：先过滤再下钻，大幅省时）──
            #   ① 站上 MA40 且 MA40 向上（近 6 根斜率 > 0）
            #   ② 站上 VWAP 中线（现价 > VWAP，即 sigma > 0）
            cl = d["close"].astype(float)
            if len(cl) < 45:
                rows.append({"code": c, "name": nm, "ok": False, "err": "数据不足"})
                continue
            ma40 = cl.rolling(40).mean()
            close = float(cl.iloc[-1])
            ma40_now = float(ma40.iloc[-1])
            ma40_prev = float(ma40.iloc[-6]) if len(ma40) >= 6 else ma40_now
            above_ma40 = close > ma40_now
            up_ma40 = ma40_now > ma40_prev
            if not (above_ma40 and up_ma40):
                skipped += 1
                rows.append({"code": c, "name": nm, "ok": False,
                             "err": "MA40过滤未过（站上%s/向上%s）" % (above_ma40, up_ma40)})
                continue
            # VWAP 中线过滤
            try:
                vw = S.vwap_tracks(d)
                vwap_now = float(vw["vwap"][-1])
                above_vwap = close > vwap_now
            except Exception:
                above_vwap = True   # VWAP 算不出来不拦截
            if not above_vwap:
                skipped += 1
                rows.append({"code": c, "name": nm, "ok": False,
                             "err": "VWAP中线过滤未过（现价%.2f ≤ VWAP%.2f）" % (close, vwap_now)})
                continue
            rows.append(analyse_one(c, nm, d))
        except Exception as e:
            rows.append({"code": c, "name": nm, "ok": False, "err": str(e)[:60]})
        if i % 25 == 0:
            log("[进度] %d/%d（MA40 过滤掉 %d）" % (i, len(codes), skipped))
    return rows


def is_index_code(code):
    """指数代码规则（比 datahub.is_index 可靠）

    沪市：000xxx 为指数（个股是 600/601/603/605/688/689）
    深市：399xxx 为指数（个股是 000/001/002/003/300/301）
    其它：.CSI / .TI 指数，899xxx 北证指数
    """
    c, _, mk = str(code).upper().partition(".")
    if mk in ("CSI", "TI"):
        return True
    if len(c) != 6 or not c.isdigit():
        return False
    if mk == "SH":
        return c.startswith("000") or c.startswith("950")
    if mk == "SZ":
        return c.startswith("399")
    if mk == "BJ":
        return c.startswith("899")
    return False


def classify_target(t):
    """自动识别标的类型 → (kind, name, code, extra)

    优先级（避免「游戏」被模糊概念「手机游戏」抢走）：
      精确概念 > 精确行业(一级/二级/三级) > 指数中文名 > 模糊行业 > 模糊概念
    """
    t = str(t).strip()
    # ① 纯代码 = 个股（或指数代码）
    #   ⚠️ 不能用 datahub.is_index()：它内部判断失效，600519.SH 也会返回 True
    if re.fullmatch(r"\d{6}(\.(SH|SZ|BJ|CSI))?", t.upper()):
        code = t.upper()
        if "." not in code:
            code = DH.to_ts(DH.norm(code))
        if is_index_code(code):
            try:
                tg = TS.resolve(code)
                return "index", tg["disp"], tg["ts"], {"tg": tg}
            except Exception:
                pass
        return "stock", "", code, {}

    # ② 指数中文名（先试，中文宽指名最准）
    try:
        tg = TS.resolve(t)
        if tg.get("kind") == "index" and tg.get("disp"):
            return "index", tg["disp"], tg["ts"], {"tg": tg}
    except Exception:
        pass

    # ②.5 中文股票名（风语筑 / 贵州茅台）
    nm_map = name_map()
    hits = [c for c, n in nm_map.items() if n == t]
    if len(hits) == 1:
        return "stock", t, hits[0], {}
    if len(hits) > 1:
        return "stock", t, sorted(hits)[0], {"alts": sorted(hits)}

    # ③ 行业
    by_l1, by_l2, by_l3, _meta = load_sw()
    ind_codes, lvl, how_ind = resolve_industry(t, by_l1, by_l2, by_l3)

    # ④ 概念
    cmap = load_concept_map()
    cc, cn, how_c = resolve_concept(t, cmap)

    if how_ind == "精确":
        return "industry", t, None, {"codes": ind_codes, "lvl": lvl}
    if how_c == "精确":
        return "concept", cn, cc, {}
    if how_ind == "去后缀":
        return "industry", t, None, {"codes": ind_codes, "lvl": lvl}
    if how_c == "去后缀":
        return "concept", cn, cc, {}
    if ind_codes:
        return "industry", t, None, {"codes": ind_codes, "lvl": lvl}
    if cc:
        return "concept", cn, cc, {}
    raise SystemExit("无法识别标的：%s（可用 --list-concepts 关键词 查概念名）" % t)


def scan_all_industries(a):
    """全行业闸门扫描：哪些行业自身有买点（就输出这些，不下钻个股）"""
    by_l1, by_l2, by_l3, _m = load_sw()
    swmap = load_sw_index_map()
    src = []
    if a.level in ("l1", "all"):
        src += [(nm, "一级") for nm in by_l1]
    if a.level in ("l2", "all"):
        src += [(strip_roman(nm), "二级") for nm in by_l2]
    seen, rows = set(), []
    for nm, lvl in src:
        if nm in seen:
            continue
        seen.add(nm)
        ic = swmap.get(nm) or swmap.get(strip_roman(nm))
        if not ic:
            continue
        sec = _sector_block("industry", nm, ic, [], {})
        if not sec.get("ok"):
            continue
        b = sec.get("best")
        rows.append({"name": nm, "lvl": lvl, "code": ic, "close": sec["close"],
                     "pct": sec["pct"], "stage": sec.get("stage"),
                     "zpos": sec.get("zpos"), "ma20": sec["ma20"],
                     "align": sec["align"].get("state"),
                     "best": b})

    hit = [r for r in rows if r["best"]]
    hit.sort(key=lambda r: (r["best"]["pri"], r["name"]))
    p1 = [r for r in hit if r["best"]["pri"] == 1]
    p2 = [r for r in hit if r["best"]["pri"] == 2]

    L = []
    A = L.append
    A("🌙 全行业买点闸门扫描 · %s" % datetime.now().strftime("%Y-%m-%d %H:%M"))
    A("口径：① 三买 > ② 一买（须 MA20 向上）> ③ 二买（胜率最低）｜ 卖点：三卖 > 一卖（须 MA20 向下）> 二卖")
    A("用途：找出「行业自身有买点」的行业 → 这些行业才值得下钻个股")
    A("")
    A("═══ 一、结论 ═══")
    A("  扫 %d 个行业，有买点 %d 个（①三买 %d / ②一买 %d）"
      % (len(rows), len(hit), len(p1), len(p2)))
    A("")

    def _line(r, mark):
        b = r["best"]
        return ("  🌙 %-10s %-4s 收%9.2f（%+.2f%%）｜ %s %.2f（%d根前，现价%+.1f%%）｜ %s"
                % (r["name"], r["lvl"], r["close"], r["pct"], mark, b["level"],
                   b.get("age", 0), b.get("gap", 0), r.get("stage") or "-"))

    A("═══ 二、① 三买优先（结构确认度最高）═══")
    if p1:
        for r in p1:
            A(_line(r, "三买"))
    else:
        A("  无")
    A("")
    A("═══ 三、② 一买（MA20 已向上）═══")
    if p2:
        for r in p2:
            A(_line(r, "一买"))
    else:
        A("  无")
    A("")
    miss = [r for r in rows if not r["best"]]
    A("═══ 四、无买点的行业（不下钻）═══")
    A("  " + "、".join(r["name"] for r in miss) if miss else "  无")
    A("")
    A("⚠️ 技术结构扫描，不构成投资建议。")
    txt = "\n".join(L)
    print(txt)
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        open(a.out, "w", encoding="utf-8").write(txt + "\n")
        log("[存档]", a.out)
    if a.push:
        ok, chat = push_feishu("🌙 全行业买点闸门扫描 · %s"
                               % datetime.now().strftime("%m-%d"), txt, None, a.chat,
                               "green" if hit else "blue")
        log("[飞书]", "已推送" if ok else "推送失败", "→", chat)
        return 0 if ok else 2
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", "-t", default=None,
                    help="指数/行业/概念/个股，如 中证500 / 传媒 / 文化传媒概念 / 600519")
    ap.add_argument("--kind", choices=["auto", "index", "industry", "concept", "stock"], default="auto")
    ap.add_argument("--codes", default=None, help="直接给代码，逗号分隔（跳过标的解析）")
    ap.add_argument("--no-scan", action="store_true", help="只分析板块自身，不扫成分股")
    ap.add_argument("--force-scan", action="store_true",
                    help="行业自身无买点也强制扫成分股（默认：行业有买点才下钻）")
    ap.add_argument("--list-concepts", default=None, nargs="?", const="", metavar="关键词",
                    help="列出（匹配的）同花顺概念名")
    ap.add_argument("--scan-industries", action="store_true",
                    help="扫全部申万行业，列出哪些行业自身有买点（第一步，不扫个股）")
    ap.add_argument("--level", choices=["l1", "l2", "all"], default="all",
                    help="--scan-industries 的层级范围（默认 all）")
    ap.add_argument("--chart", action="store_true",
                    help="出图（日线/小时线/均线版 三面板一张图）")
    ap.add_argument("--chart-top", type=int, default=3,
                    help="给前 N 只可买个股出图（默认 3，0=不出）")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--chat", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    if a.list_concepts is not None:
        cmap = load_concept_map()
        kw = a.list_concepts
        hits = [(k, v) for k, v in cmap.items() if (not kw or kw in k)]
        print("同花顺概念 %d 个%s：" % (len(hits), ("（含「%s」）" % kw) if kw else ""))
        for k, v in sorted(hits):
            print("  %-16s %s" % (k, v))
        return 0

    if a.scan_industries:
        return scan_all_industries(a)

    if a.codes:
        # ⚠️ 6 位纯代码要归一成 ts_code（缓存文件名就是 ts_code：600519.SH.csv）
        _cs = []
        for c in a.codes.split(","):
            c = c.strip().upper()
            if not c:
                continue
            if re.fullmatch(r"\d{6}", c):
                try:
                    c = DH.to_ts(DH.norm(c))
                except Exception:
                    pass
            _cs.append(c)
        kind, name, code, extra = "codes", "自定义代码", None, {"codes": _cs}
    else:
        if not a.target:
            raise SystemExit("需要 --target（指数/行业/概念/个股）或 --codes")
        kind, name, code, extra = classify_target(a.target)
        if a.kind != "auto":
            kind = a.kind
    log("[标的] kind=%s name=%s code=%s" % (kind, name, code))

    # ── ④ 个股：单只四层分析 ──
    if kind == "stock":
        # ⚠️ 个股一律把**解析后的 ts_code** 传给 tech_summary（中文名/6位代码都归一）
        import tech_summary as TS2
        _tgt = code or a.target
        _argv = ["tech_summary.py", "--target", _tgt]
        if a.push:
            _argv.append("--push")
        if a.chat:
            _argv += ["--chat", a.chat]
        if a.out:
            _argv += ["--out", a.out]
        _old = sys.argv
        sys.argv = _argv
        try:
            return TS2.main()
        finally:
            sys.argv = _old
    if kind == "index" and not extra.get("codes"):
        # 指数：先给指数自身四层，再（默认）扫成分股
        import tech_summary as TS2
        if not a.no_scan:
            im = index_members(code)
            if im:
                names = {c: n for c, n in im}
                sec = _sector_block("index", name, code, [c for c, _ in im], names)
                gate = bool(sec.get("ok") and sec.get("best")) or a.force_scan
                if gate:
                    log("[闸门] ✅ 指数自身有买点 → 下钻成分股" if sec.get("best")
                        else "[闸门] --force-scan 强制下钻")
                    rows = scan_codes([c for c, _ in im], names)
                else:
                    log("[闸门] ⛔ 指数自身无买点 → 不扫成分股")
                    rows = []
                txt = render_sector(sec, rows, "🌙 指数成分买点 · %s · %s"
                                    % (name, datetime.now().strftime("%Y-%m-%d %H:%M")))
                print(txt)
                if a.out:
                    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
                    open(a.out, "w", encoding="utf-8").write(txt + "\n")
                    log("[存档]", a.out)
                if a.push:
                    ok, chat = push_feishu("🌙 指数成分买点 · %s" % name, txt, None, a.chat,
                                           "green" if any(r.get("best") for r in rows) else "blue")
                    log("[飞书]", "已推送" if ok else "推送失败", "→", chat)
                    return 0 if ok else 2
                return 0
        sys.argv = ["tech_summary.py", "--target", a.target] + \
                   (["--push"] if a.push else []) + \
                   (["--chat", a.chat] if a.chat else []) + \
                   (["--out", a.out] if a.out else [])
        return TS2.main()

    # ── ② 行业 / ③ 概念 / codes ──
    if kind == "industry":
        ind_codes = extra.get("codes") or []
        swmap = load_sw_index_map()
        icode = swmap.get(name) or swmap.get(strip_roman(name))
        names = {}
        log("[行业] %s %s ｜ 成分 %d 只 ｜ 指数 %s"
            % (name, extra.get("lvl", ""), len(ind_codes), icode or "无"))
    elif kind == "concept":
        mem = concept_members(code)
        ind_codes = [c for c, _ in mem]
        names = {c: n for c, n in mem}
        icode = code
        log("[概念] %s %s ｜ 成分 %d 只" % (name, code, len(ind_codes)))
    else:
        ind_codes = extra["codes"]
        names, icode = {}, None
        log("[代码] %d 只" % len(ind_codes))

    if not ind_codes:
        raise SystemExit("没有成分股可扫")

    # ── 第一步：先判行业/板块自己有没有买点（大佬 2026-11-09：行业有买点才下钻个股）──
    if kind == "index":
        sec_kind, sec_code = "index", extra.get("tg", {}).get("ts") or code
    else:
        sec_kind, sec_code = kind, icode
    sec = _sector_block(sec_kind, name, sec_code, ind_codes, names) if sec_code else \
        {"ok": False, "err": "无板块指数"}

    gate = bool(sec.get("ok") and sec.get("best"))
    if a.force_scan:
        gate = True
        log("[闸门] --force-scan 强制下钻个股")
    elif kind == "codes":
        gate = True        # 显式代码清单无板块指数可判，直接扫
        log("[闸门] --codes 显式清单 → 直接扫个股")
    elif gate:
        log("[闸门] ✅ 行业自身有买点（%s %.2f）→ 下钻个股"
            % (PRI_MARK.get(sec["best"]["pri"], sec["best"]["tag"]), sec["best"]["level"]))
    else:
        log("[闸门] ⛔ 行业自身无买点 → 不扫个股")

    # ── 第二步：过闸才扫个股 ──
    rows = scan_codes(ind_codes, names) if gate else []
    # ⚠️ --codes 不带板块指数（sec.ok=False）：显式给定的代码清单直接扫，报告需按此下钻
    txt = render_sector(sec, rows, "🌙 %s买点 · %s · %s"
                        % ({"industry": "行业", "concept": "概念"}.get(kind, ""), name,
                           datetime.now().strftime("%Y-%m-%d %H:%M")),
                        gate_override=(a.force_scan or (kind == "codes" and bool(ind_codes))))

    # ── 出图：板块指数 + 前 N 只可买个股 ──
    imgs = []
    if a.chart and gate:
        p_sec = chart_sector(sec_kind, name, sec_code) if sec_code else None
        if p_sec:
            imgs.append(p_sec)
            log("[图] 板块 %s → %s" % (name, p_sec))
        picks = [r for r in rows if r.get("best")]
        picks.sort(key=lambda r: (r["best"].get("pri", 9), -score_quality(r)))
        n_top = a.chart_top if a.chart_top > 0 else 0
        hist2 = _today_hist()
        for r in picks[:n_top]:
            dd = load_daily(r["code"], hist2)
            if dd is None:
                continue
            p1 = chart_one(r["code"], r["name"], dd, None)
            if p1:
                imgs.append(p1)
                log("[图] %s %s → %s" % (r["name"], r["code"], p1))
    if imgs:
        txt += "\n\n📎 图（%d 张）：\n" % len(imgs) + "\n".join(os.path.basename(x) for x in imgs)
    print(txt)
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        open(a.out, "w", encoding="utf-8").write(txt + "\n")
        log("[存档]", a.out)
    if a.push:
        nbuy = len([r for r in rows if r.get("best")])
        ok, chat = push_feishu("🌙 %s买点 · %s" % ({"industry": "行业", "concept": "概念"}.get(kind, ""), name),
                               txt, imgs or None, a.chat, "green" if nbuy else "blue")
        log("[飞书]", "已推送" if ok else "推送失败", "→", chat)
        if not ok:
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
