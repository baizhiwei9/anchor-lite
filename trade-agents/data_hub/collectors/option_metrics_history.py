#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
期权指标历史批量统一采集器（Tushare-only）
=========================================
定位：全系统唯一的「历史区间多日 × 多标的 × 全套期权指标」统一入口。
纪律（主人 2026-10-23 定）：
  - 期权指标值（IV / 25Δ偏度 / CBOE偏度 / PCR 等）优先直接采用 Tushare 收费端
    已计算好的指标；Tushare 没给的，用「原始行情」在 option_metrics.py 标准 BS 自算。
  - 自算所需的【原始行情】（合约信息 opt_basic、日线 opt_daily、现货价）允许多级降级：
    Tushare → AKShare → 网页爬取，三源拿到的原始行情都可用于自算。
  - 唯一禁区：不直接采信免费源"现成的指标值"（网页发布的 IV/RV 等），
    免费源只提供原始行情，指标必须自己算，保证全系统口径一致。
  - 业务脚本只调用本模块，禁止各自拼装数据源、各自重算指标。

当前实现：原始行情取 Tushare（opt_basic + opt_daily + fund_daily），IV/Skew/PCR
全部由标准 BS 自算；后续 Tushare 缺数时在 _fetch_exchange_day/_fetch_spot 内补
AKShare/网页原始行情降级即可，调用方与输出口径不变。

输出标准列（写入 daily_metrics 的口径）：
  trade_date, underlying, spot_price, atm_iv,
  put_25d_iv, call_25d_iv, iv_skew, cboe_skew,
  vol_pcr, oi_pcr, amt_pcr, vix, data_source
其中 vix = CBOE 分钟到期法自算 30 日历日期权隐含波动率（百分数），r=1.5%，
盘中口径见 vix_realtime；禁止用任何免费源现成 VIX 值（单一真源纪律）。

批量优化：同一交易所一日只拉一次 opt_basic + opt_daily（全市场），
内存按标的代码前缀过滤，4 标的 × N 日 ≈ 2N 次 opt 调用，而非 4N 次。

用法：
  from data_hub.collectors.option_metrics_history import get_option_metrics_history
  df = get_option_metrics_history('2025-08-01', '2026-10-22')   # 全部4标的
  df = get_option_metrics_history('2026-10-22', '2026-10-22', underlyings=['588000'])
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

try:
    from ..option_metrics import (
        standardize_contracts,
        compute_metrics_from_data,
        calc_25d_risk_reversal,
        calc_vix,
    )
except Exception:  # pragma: no cover - 脚本直挂兜底
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from option_metrics import (
        standardize_contracts,
        compute_metrics_from_data,
        calc_25d_risk_reversal,
        calc_vix,
    )

# 标的 → (交易所, 名称关键词列表, 现货代码)
# 注意：tushare 期权 ts_code 是数字合约号（如10012652.SH），不是 OP510300 前缀；
# 品种归属靠合约「名称」前缀识别，购/沽靠名称里的「购/沽」字判定（opt_type字段只写"ETF期权"无用）。
OPTION_METRICS_TARGETS: Dict[str, Dict] = {
    "510300": {"exchange": "SSE", "name_keys": ["300ETF"], "spot": "510300.SH", "label": "300ETF"},
    "510500": {"exchange": "SSE", "name_keys": ["500ETF"], "spot": "510500.SH", "label": "500ETF"},
    # 科创50ETF期权在SSE有两套并行合约系列："科创50"(主力，成交占~89%)与"科创板50"(旧/迷你系列)，
    # 行权价档位错开，混算会导致ATM选错、IV虚高（曾稀释成60.9%，主力单列=43.8%）。只取主力"科创50"。
    "588000": {"exchange": "SSE", "name_keys": ["科创50"], "spot": "588000.SH", "label": "科创50ETF"},
    "159915": {"exchange": "SZSE", "name_keys": ["创业板ETF"], "spot": "159915.SZ", "label": "创业板ETF"},
}

# 指标质量过滤区间（小数）。低于3%多为脏值/无报价反推失败，高于80%为历史回填脏数据
IV_VALID_MIN = 0.03
IV_VALID_MAX = 0.80

_OUT_COLUMNS = [
    "trade_date", "underlying", "spot_price", "atm_iv",
    "put_25d_iv", "call_25d_iv", "iv_skew", "cboe_skew",
    "vol_pcr", "oi_pcr", "amt_pcr", "vix", "data_source",
]


def _norm_date(d) -> str:
    """归一化为 YYYYMMDD。"""
    s = str(d).replace("-", "").replace("/", "")[:8]
    return s


def _fetch_basic_exchange(src, exchange: str) -> pd.DataFrame:
    """拉某交易所全量存续合约信息（整段区间复用，一次调用本地按日过滤）。"""
    fields = "ts_code,name,exercise_price,list_date,delist_date,maturity_date,opt_type"
    try:
        basic = src._call_pro_api("opt_basic", exchange=exchange, fields=fields)
    except Exception:
        basic = src._call_pro_api("opt_basic", fields=fields)
    if basic is None or basic.empty:
        return pd.DataFrame()
    basic = basic.copy()
    basic["list_date"] = basic["list_date"].fillna("")
    basic["delist_date"] = basic["delist_date"].fillna("99999999")
    return basic


def _fetch_exchange_day(src, exchange: str, td: str, basic: pd.DataFrame) -> pd.DataFrame:
    """
    用预取的 opt_basic 在本地过滤当日存续合约，再拉当日 opt_daily 合并。
    原始行情统一走 TushareSource._call_pro_api（限频/熔断/IP轮动），
    外层 _call_with_ip_retry 处理多 IP 超限退避；后续可在此补免费源原始行情降级。
    """
    if basic is None or basic.empty:
        return pd.DataFrame()
    alive = basic[(basic["list_date"] <= td) & (basic["delist_date"] >= td)]
    if alive.empty:
        return pd.DataFrame()
    daily = src._call_pro_api("opt_daily", trade_date=td, exchange=exchange)
    if daily is None or daily.empty:
        return pd.DataFrame()

    merged = daily.merge(
        alive[["ts_code", "name", "exercise_price", "maturity_date", "opt_type"]],
        on="ts_code", how="inner",
    )
    # tushare opt_type 只写"ETF期权"，购/沽由名称推断（购=看涨C，沽=看跌P）
    def _cp(nm: str) -> str:
        nm = str(nm)
        if "沽" in nm:
            return "P"
        if "购" in nm:
            return "C"
        return ""
    merged["call_put"] = merged["name"].map(_cp)
    return merged


def _fetch_spot_history_safe(src, spot_codes, start, end, verbose=True):
    """整段现货预取的容错包装：单个标的失败不影响其它，IP 超限带退避。"""
    out: Dict[str, pd.DataFrame] = {}
    for code in spot_codes:
        try:
            df = _call_with_ip_retry(
                src._call_pro_api, "fund_daily", ts_code=code, start_date=start, end_date=end
            )
            if df is None or df.empty:
                raise ValueError("fund_daily空")
        except Exception:
            try:
                df = _call_with_ip_retry(
                    src._call_pro_api, "daily", ts_code=code, start_date=start, end_date=end
                )
            except Exception as e:  # noqa
                if verbose:
                    print(f"  ⚠️ 现货 {code} 整段拉取失败: {e}")
                continue
        if df is not None and not df.empty:
            d = df[["trade_date", "close"]].copy()
            d["trade_date"] = d["trade_date"].astype(str)
            out[code] = d.set_index("trade_date")
    return out


def _is_ip_limit(e: Exception) -> bool:
    msg = str(e)
    return ("ip超限" in msg) or ("多个ip" in msg) or ("ip" in msg.lower() and "限" in msg)


def _call_with_ip_retry(fn, *args, max_retries: int = 4, **kwargs):
    """对 IP 超限错误做指数退避重试（多 IP 轮动场景），其它错误直接抛。"""
    waits = [8, 15, 25, 40]
    last = None
    for i in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa
            last = e
            if i < max_retries and _is_ip_limit(e):
                time.sleep(waits[min(i, len(waits) - 1)])
                continue
            raise
    raise last


def _compute_one(merged_day: pd.DataFrame, name_keys: List[str], exchange: str,
                 spot: float, td: str) -> Optional[Dict]:
    """对单一标的当日，用统一 option_metrics 计算全套指标。"""
    if merged_day is None or merged_day.empty or not spot or spot <= 0:
        return None
    nm = merged_day["name"].astype(str)
    # 精确品种名：去掉首个"购/沽"及之后部分（如"科创50沽10月1200"→"科创50"）再比对
    base = nm.str.replace(r"[购沽].*$", "", regex=True)
    mask = base.isin(name_keys)
    sub = merged_day[mask]
    if sub.empty:
        return None

    contracts = standardize_contracts(sub, source="tushare", exchange=exchange, trade_date=td)
    if not contracts:
        return None

    m = compute_metrics_from_data(
        sub, spot, source="tushare", exchange=exchange, trade_date=td, r=0.025
    )
    # 25Δ 两翼（统一函数，返回 dict）
    rr = calc_25d_risk_reversal(contracts, spot, trade_date=td, r=0.025) or {}

    atm = getattr(m, "atm_iv", None)
    put25 = rr.get("put25_iv")
    call25 = rr.get("call25_iv")
    # iv_skew 库口径 = 原始差值 put25_iv − call25_iv（小数），与 style_drift_monitor
    # fetch_daily_from_sina 落库口径一致；rr["skew"] 是 ATM 归一化偏度，不能直接灌库。
    skew_raw = None
    if put25 is not None and call25 is not None:
        skew_raw = put25 - call25

    def _clean(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return v if IV_VALID_MIN <= v <= IV_VALID_MAX else None

    # VIX：CBOE 分钟到期法，历史按当日 08:30 估值/到期日15:00结算，r=1.5%
    vix_val = 0.0
    try:
        vix_val = calc_vix(contracts, float(spot), r=0.015, trade_date=td)
    except Exception:
        vix_val = 0.0
    row = {
        "trade_date": td,
        "spot_price": round(float(spot), 4),
        "atm_iv": _clean(atm),
        "put_25d_iv": _clean(put25),
        "call_25d_iv": _clean(call25),
        # iv_skew 为原始差值（小数，可负），不套用 IV 的[0.03,0.80]清洗区间
        "iv_skew": (round(float(skew_raw), 6) if skew_raw is not None else None),
        "cboe_skew": getattr(m, "cboe_skew", None),
        "vol_pcr": getattr(m, "vol_pcr", None),
        "oi_pcr": getattr(m, "oi_pcr", None),
        "amt_pcr": None,
        "vix": round(float(vix_val), 2) if vix_val and vix_val > 0 else None,
        "data_source": "tushare",
    }
    # 成交额 PCR（统一层未直接给，用标准合约自算成交额 put/call）
    try:
        call_amt = sum(c.amount for c in contracts if c.opt_type == "C")
        put_amt = sum(c.amount for c in contracts if c.opt_type == "P")
        if call_amt > 0:
            row["amt_pcr"] = round(put_amt / call_amt, 6)
    except Exception:
        pass
    return row


def get_option_metrics_history(
    start_date,
    end_date,
    underlyings: Optional[List[str]] = None,
    *,
    trade_dates: Optional[List[str]] = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    历史区间全套期权指标统一入口（全系统唯一）。

    数据源纪律（2026-10-23 终版）：
      - 指标值优先采信 Tushare 已计算好的；
      - 需自算时用标准 BS 反推，原始行情可 Tushare → AKShare → 网页 多级降级；
      - 唯一禁区：不直接采用免费源"现成的指标值"（网页IV·RV）。
    当前实现以 Tushare 期权行情做 BS 自算（口径与 option_metrics 一致），
    AKShare/网页降级位在源数据层预留，业务侧只调本函数，不自行拼数据。

    Args:
        start_date/end_date: YYYYMMDD 或 YYYY-MM-DD
        underlyings: 标的代码列表，None=全部4个（510300/510500/588000/159915）
        trade_dates: 显式交易日列表（给定则忽略 start/end，用于调用方自备交易日历）
        verbose: 打印进度

    Returns:
        DataFrame，列见 _OUT_COLUMNS；失败日/无数据日跳过（不造假）。
    """
    from ..sources.tushare_source import TushareSource

    targets = OPTION_METRICS_TARGETS
    if underlyings:
        targets = {k: v for k, v in targets.items() if k in underlyings}
    if not targets:
        raise ValueError(f"无有效标的，可选: {list(OPTION_METRICS_TARGETS)}")

    # 交易日历
    if trade_dates is None:
        s, e = _norm_date(start_date), _norm_date(end_date)
        src0 = TushareSource()
        cal = _call_with_ip_retry(
            src0._call_pro_api, "trade_cal",
            exchange="SSE", start_date=s, end_date=e, is_open="1", fields="cal_date",
        )
        if cal is None or cal.empty:
            raise RuntimeError("trade_cal 取交易日历失败")
        trade_dates = sorted(cal["cal_date"].astype(str).tolist())
        span_start, span_end = s, e
    else:
        trade_dates = sorted({_norm_date(d) for d in trade_dates})
        span_start, span_end = trade_dates[0], trade_dates[-1]

    src = TushareSource()
    exchanges = sorted({v["exchange"] for v in targets.values()})
    spot_codes = sorted({v["spot"] for v in targets.values()})

    # 预取：每交易所合约信息一次；现货整段一次。把调用量压到 2 + 2N_spot + N日×N所
    basic_map: Dict[str, pd.DataFrame] = {}
    for ex in exchanges:
        try:
            basic_map[ex] = _call_with_ip_retry(_fetch_basic_exchange, src, ex)
            if verbose:
                print(f"  [预取] {ex} opt_basic {len(basic_map[ex])} 行")
        except Exception as e:  # noqa
            if verbose:
                print(f"  ⚠️ {ex} opt_basic 预取失败: {e}")
            basic_map[ex] = pd.DataFrame()
    spot_hist = _fetch_spot_history_safe(src, spot_codes, span_start, span_end, verbose)

    rows: List[Dict] = []
    n = len(trade_dates)
    for di, td in enumerate(trade_dates, 1):
        if verbose and (di % 10 == 0 or di == n or di == 1):
            print(f"  [期权历史指标] {td} ({di}/{n})", flush=True)
        # 每交易所当日 opt_daily 一次（带 IP 超限退避）
        ex_dfs: Dict[str, pd.DataFrame] = {}
        for ex in exchanges:
            try:
                ex_dfs[ex] = _call_with_ip_retry(
                    _fetch_exchange_day, src, ex, td, basic_map.get(ex, pd.DataFrame())
                )
            except Exception as e:  # noqa
                if verbose:
                    print(f"    ⚠️ {ex} {td} opt行情拉取失败: {e}", flush=True)
                ex_dfs[ex] = pd.DataFrame()

        for ul, cfg in targets.items():
            merged_day = ex_dfs.get(cfg["exchange"])
            spot_df = spot_hist.get(cfg["spot"])
            try:
                spot = float(spot_df.loc[td, "close"]) if spot_df is not None and td in spot_df.index else None
            except Exception:
                spot = None
            if merged_day is None or merged_day.empty or not spot:
                continue
            try:
                row = _compute_one(merged_day, cfg["name_keys"], cfg["exchange"], spot, td)
            except Exception as e:  # noqa
                if verbose:
                    print(f"    ⚠️ {ul} {td} 计算失败: {e}")
                row = None
            if row:
                row["underlying"] = ul
                rows.append(row)

    df = pd.DataFrame(rows, columns=_OUT_COLUMNS)
    return df


if __name__ == "__main__":
    import sys
    s = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y%m%d")
    e = sys.argv[2] if len(sys.argv) > 2 else s
    uls = sys.argv[3].split(",") if len(sys.argv) > 3 else None
    d = get_option_metrics_history(s, e, underlyings=uls)
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 30)
    print(d.to_string(index=False))
