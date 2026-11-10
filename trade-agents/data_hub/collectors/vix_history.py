#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自算 VIX 历史库与统一入口（CBOE 分钟到期法，期权链自算）
=========================================================
纪律（主人 2026-10-23 定，期权指标单一真源）：
  - VIX 必须由期权链（原始行情）在 option_metrics.calc_vix 自算（CBOE 2019
    Whitepaper 方差互换法，分钟到期时间，30 日历日插值），r=1.5%。
  - 禁止采信任何免费源"现成 VIX/QVIX 指标值"（QVIX 是民间产品，官方 iVIX
    2018-02 已停发；QVIX 数值仅可人工交叉验证参照，绝不入库、不进业务）。
  - 原始行情允许 Tushare → AKShare → 网页 多级降级；业务侧只 import 本模块。

覆盖五宽基：300ETF / 500ETF / 创业板ETF / 科创50ETF / MO(中证1000股指)。
数据库：<codeact>/output/vix_history.db。

表 vix_daily：
    trade_date TEXT YYYYMMDD（收盘口径）
    vix_key    TEXT 300ETF/500ETF/创业板ETF/科创50ETF/MO
    vix        REAL 自算 VIX（百分数）
    spot       REAL 当日标的收盘价
    source     TEXT chain:tushare（历史链）/ chain:local_opt_raw（本地缓存链）
    chain_rows INTEGER 当日参与的原始合约行数（质控）
    updated_at TEXT ISO
    PRIMARY KEY(trade_date, vix_key)

盘中接口（业务统一入口）：
    get_all_vix_percentiles(time_budget_sec=...)
        → {key: {vix, trade_date(实时时刻), percentile, p80, p95,
                 low_warning, top_warning, data_missing, stale,
                 data_insufficient, sample_count, source, detail, error}}
"""
from __future__ import annotations

import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

try:
    from ..option_metrics import standardize_contracts, calc_vix
except Exception:  # pragma: no cover
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from option_metrics import standardize_contracts, calc_vix

# 五宽基配置（2026-10-23 剔除上证50ETF 510050）：
#   history_exchange/history_name_keys：Tushare opt_basic+opt_daily 历史链识别
#   spot：现货代码（MO 用指数日线 index_daily，其余 fund_daily/daily）
#   realtime_key：option_chain_realtime 的 key
VIX_TARGETS: Dict[str, Dict[str, Any]] = {
    "300ETF": {
        "realtime_key": "300ETF",
        "exchange": "SSE", "name_keys": ["300ETF"],
        "spot": "510300.SH", "spot_kind": "fund",
        "label": "300ETF期权",
    },
    "500ETF": {
        "realtime_key": "500ETF",
        "exchange": "SSE", "name_keys": ["500ETF"],
        "spot": "510500.SH", "spot_kind": "fund",
        "label": "500ETF期权",
    },
    "创业板ETF": {
        "realtime_key": "创业板ETF",
        "exchange": "SZSE", "name_keys": ["创业板ETF"],
        "spot": "159915.SZ", "spot_kind": "fund",
        "label": "创业板ETF期权",
    },
    "科创50ETF": {
        "realtime_key": "科创50ETF",
        "exchange": "SSE", "name_keys": ["科创50"],
        "spot": "588000.SH", "spot_kind": "fund",
        "label": "科创50ETF期权",
    },
    "MO": {
        "realtime_key": "MO",
        "exchange": "CFFEX", "code_prefix": "MO",
        "spot": "000852.SH", "spot_kind": "index",
        "label": "中证1000股指期权(MO)",
    },
}
VIX_KEYS = list(VIX_TARGETS.keys())

_DB_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "output",
)
DB_PATH = os.path.join(_DB_DIR, "vix_history.db")

# style_drift.db opt_raw（4ETF 本地缓存原始链，2025-04 起；用于快速本地重算）
_STYLE_DRIFT_DB = "/Coze/Drive/蓁宝/所有对话/主对话/市场情绪监控/data/options_cache/style_drift.db"

DEFAULT_WINDOW = 250        # 近一年分位窗口
MIN_SAMPLE_COUNT = 120      # 分位最小样本，不足 → data_insufficient 不触发预警
LOW_VIX_THRESHOLD = 15.0    # 绝对低位阈值
STALE_DAYS = 7              # 收盘库最新行距今超过 7 天 → stale
DEFAULT_TIME_BUDGET = 150   # 盘中取链总预算（秒），默认低于 170s 外层保护


# ============================================================
# DB
# ============================================================

def _connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vix_daily (
            trade_date TEXT NOT NULL,
            vix_key    TEXT NOT NULL,
            vix        REAL,
            spot       REAL,
            source     TEXT,
            chain_rows INTEGER,
            updated_at TEXT,
            PRIMARY KEY (trade_date, vix_key)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vix_date ON vix_daily(trade_date)")
    # 盘中采样表（2026-10-23 新增）：交易时段每 5 分钟落一条自算 VIX + 现价，
    # 供「盘中 VIX 最大值」告警使用（收盘值会漏掉盘中峰值）。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vix_intraday (
            trade_date  TEXT NOT NULL,
            vix_key     TEXT NOT NULL,
            sample_time TEXT NOT NULL,
            vix         REAL,
            spot        REAL,
            source      TEXT,
            PRIMARY KEY (trade_date, vix_key, sample_time)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vix_intraday_key "
        "ON vix_intraday(vix_key, trade_date)"
    )
    conn.commit()
    return conn


def upsert_vix_rows(rows: List[Dict[str, Any]], db_path: str = DB_PATH,
                    max_retries: int = 6) -> int:
    """批量写入/替换 vix_daily。返回写入行数。
    FUSE 挂载盘偶发 disk I/O error，做指数退避重试。"""
    if not rows:
        return 0
    import time as _t
    payload = [
        (
            r["trade_date"], r["vix_key"], r.get("vix"), r.get("spot"),
            r.get("source"), r.get("chain_rows"),
            datetime.now().isoformat(timespec="seconds"),
        )
        for r in rows
    ]
    last_err = None
    for attempt in range(max_retries + 1):
        conn = None
        try:
            conn = _connect(db_path)
            conn.executemany(
                """
                INSERT OR REPLACE INTO vix_daily
                    (trade_date, vix_key, vix, spot, source, chain_rows, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """, payload)
            conn.commit()
            return len(rows)
        except sqlite3.OperationalError as e:
            last_err = e
            if attempt < max_retries:
                _t.sleep(1.5 * (attempt + 1))
                continue
            raise
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    raise last_err


def query_vix_series(
    vix_key: str,
    start: str = "",
    end: str = "",
    db_path: str = DB_PATH,
) -> List[Dict[str, Any]]:
    """取单指数收盘 VIX 序列（升序），只返回 vix>0 的有效行。"""
    conn = _connect(db_path)
    try:
        sql = "SELECT trade_date, vix, spot, source FROM vix_daily WHERE vix_key=? AND vix IS NOT NULL AND vix>0"
        params: List[Any] = [vix_key]
        if start:
            sql += " AND trade_date>=?"
            params.append(start.replace("-", ""))
        if end:
            sql += " AND trade_date<=?"
            params.append(end.replace("-", ""))
        sql += " ORDER BY trade_date"
        return [
            {"trade_date": r[0], "vix": r[1], "spot": r[2], "source": r[3]}
            for r in conn.execute(sql, params).fetchall()
        ]
    finally:
        conn.close()


def get_db_stats(db_path: str = DB_PATH) -> Dict[str, Dict[str, Any]]:
    """每指数行数/起止日期。"""
    conn = _connect(db_path)
    try:
        out: Dict[str, Dict[str, Any]] = {}
        for k in VIX_KEYS:
            row = conn.execute(
                "SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM vix_daily WHERE vix_key=?",
                (k,),
            ).fetchone()
            n_valid = conn.execute(
                "SELECT COUNT(*) FROM vix_daily WHERE vix_key=? AND vix IS NOT NULL AND vix>0",
                (k,),
            ).fetchone()[0]
            out[k] = {"rows": row[0] or 0, "valid_rows": n_valid,
                      "start": row[1], "end": row[2]}
        return out
    finally:
        conn.close()


# ============================================================
# 历史链：本地 opt_raw 快速重算（300/500/创业板/科创50，2025-04 起）
# ============================================================

def _norm(d: Any) -> str:
    return str(d).replace("-", "").replace("/", "")[:8]


def recompute_from_local_opt_raw(
    keys: Optional[List[str]] = None,
    start_date: str = "",
    end_date: str = "",
    db_path: str = DB_PATH,
    verbose: bool = True,
) -> List[Dict[str, Any]]:
    """用 style_drift.db 的 opt_raw 缓存历史链本地重算 VIX（零 Tushare 调用）。

    opt_raw 是标准 25 列 Tushare opt_daily 快照（ts_code/trade_date/open/high/
    low/close/pre_settle/settle/vol/amount/oi/pre_close/bid/ask ...），
    合约元数据（name/strike/maturity/购沽）需与 Tushare opt_basic join。
    """
    keys = keys or ["300ETF", "500ETF", "创业板ETF", "科创50ETF"]
    if not os.path.exists(_STYLE_DRIFT_DB):
        if verbose:
            print(f"[local] style_drift.db 不存在: {_STYLE_DRIFT_DB}")
        return []

    from ..sources.tushare_source import TushareSource

    src = TushareSource()
    rows: List[Dict[str, Any]] = []
    lcon = sqlite3.connect(_STYLE_DRIFT_DB)
    try:
        # opt_raw 覆盖日期
        date_sql = "SELECT DISTINCT trade_date FROM opt_raw"
        dparams: List[Any] = []
        if start_date:
            date_sql += " WHERE trade_date>=?"
            dparams.append(_norm(start_date))
        if end_date:
            date_sql += (" AND" if dparams else " WHERE") + " trade_date<=?"
            dparams.append(_norm(end_date))
        all_dates = sorted(r[0] for r in lcon.execute(date_sql, dparams).fetchall())
        if verbose:
            print(f"[local] opt_raw 可用日期 {len(all_dates)}：{all_dates[0] if all_dates else '-'} ~ {all_dates[-1] if all_dates else '-'}")

        for key in keys:
            cfg = VIX_TARGETS[key]
            ex = cfg["exchange"]
            try:
                fields = "ts_code,name,exercise_price,maturity_date,opt_type,list_date,delist_date"
                basic = src._call_pro_api("opt_basic", exchange=ex, fields=fields)
            except Exception as e:  # noqa
                if verbose:
                    print(f"[local] {key} opt_basic 失败: {e}")
                continue
            if basic is None or basic.empty:
                continue
            # 名称归属：去购/沽后精确匹配
            import pandas as pd
            base = basic["name"].astype(str).str.replace(r"[购沽].*$", "", regex=True)
            basic = basic.assign(_base=base)
            basic = basic[basic["_base"].isin(cfg["name_keys"])]
            if basic.empty:
                if verbose:
                    print(f"[local] {key} 名称匹配为空 {cfg['name_keys']}")
                continue
            code_meta = basic.set_index("ts_code")[["name", "exercise_price", "maturity_date"]].to_dict("index")
            valid_codes = set(code_meta)
            if verbose:
                print(f"[local] {key} {ex} 匹配合约元数据 {len(valid_codes)} 条")

            # 现货：优先 daily_metrics.spot_price（同库，口径一致）
            spot_map: Dict[str, float] = {}
            ul_code = cfg["spot"].split(".")[0]
            try:
                for td, sp in lcon.execute(
                    "SELECT trade_date, spot_price FROM daily_metrics WHERE underlying=? AND spot_price IS NOT NULL",
                    (ul_code,),
                ).fetchall():
                    spot_map[td] = float(sp)
            except Exception:
                pass

            n_key = 0
            ph = ",".join("?" * 200)
            code_list = list(valid_codes)
            for di, td in enumerate(all_dates, 1):
                merged_parts: List[Any] = []
                for i0 in range(0, len(code_list), 200):
                    chunk = code_list[i0:i0 + 200]
                    q = f"SELECT * FROM opt_raw WHERE trade_date=? AND ts_code IN ({','.join('?' * len(chunk))})"
                    part = pd.read_sql_query(q, lcon, params=[td] + chunk)
                    if not part.empty:
                        merged_parts.append(part)
                if not merged_parts:
                    continue
                day = pd.concat(merged_parts, ignore_index=True)
                meta_df = pd.DataFrame([
                    {"ts_code": c, "name": code_meta[c]["name"],
                     "exercise_price": code_meta[c]["exercise_price"],
                     "maturity_date": code_meta[c]["maturity_date"]}
                    for c in day["ts_code"].unique() if c in code_meta
                ])
                if meta_df.empty:
                    continue
                day = day.merge(meta_df, on="ts_code", how="inner")
                day["call_put"] = day["name"].astype(str).map(
                    lambda nm: "P" if "沽" in nm else ("C" if "购" in nm else ""))
                day = day[day["call_put"] != ""]
                if day.empty:
                    continue
                spot = spot_map.get(td)
                if not spot:
                    continue
                try:
                    contracts = standardize_contracts(day, source="tushare",
                                                      exchange=ex, trade_date=td)
                    d = calc_vix(contracts, spot, r=0.015, trade_date=td,
                                 return_detail=True)
                except Exception as e:  # noqa
                    if verbose:
                        print(f"  ⚠️ {key} {td} 自算异常: {e}")
                    continue
                if d.get("vix", 0) > 0 and not d.get("error"):
                    rows.append({"trade_date": td, "vix_key": key, "vix": d["vix"],
                                 "spot": spot, "source": "chain:local_opt_raw",
                                 "chain_rows": int(len(day))})
                    n_key += 1
            if verbose:
                print(f"[local] {key} 重算 {n_key} 日")
    finally:
        lcon.close()

    n = upsert_vix_rows(rows, db_path=db_path)
    if verbose:
        print(f"[local] 写入 vix_daily {n} 行")
    return rows


# ============================================================
# 历史链：Tushare 回补（MO 主力路径 + 其余补缺）
# ============================================================

def backfill_from_tushare(
    keys: Optional[List[str]] = None,
    start_date: str = "",
    end_date: str = "",
    db_path: str = DB_PATH,
    sleep_sec: float = 0.15,
    verbose: bool = True,
) -> List[Dict[str, Any]]:
    """逐日逐所 Tushare 历史链自算 VIX 回补（已存在的日期自动跳过）。

    - SSE 每日一次 opt_daily(exchange=SSE) 覆盖 50/300/500/科创50 四品种
    - SZSE 每日一次（创业板）
    - CFFEX 每日一次（MO，2022-07-22 上市起可得）
    现货整段预取（fund_daily；MO 用 index_daily）。
    """
    import pandas as pd
    from ..sources.tushare_source import TushareSource
    from .option_metrics_history import (
        _fetch_basic_exchange, _call_with_ip_retry, _norm_date,
    )

    keys = keys or VIX_KEYS
    targets = {k: VIX_TARGETS[k] for k in keys}
    s = _norm_date(start_date or (datetime.now() - timedelta(days=400)).strftime("%Y%m%d"))
    e = _norm_date(end_date or datetime.now().strftime("%Y%m%d"))

    src = TushareSource()

    # 交易日历
    cal = _call_with_ip_retry(
        src._call_pro_api, "trade_cal",
        exchange="SSE", start_date=s, end_date=e, is_open="1", fields="cal_date",
    )
    if cal is None or cal.empty:
        raise RuntimeError("trade_cal 取交易日历失败")
    all_td = sorted(cal["cal_date"].astype(str).tolist())

    # 已存在的 (key, date) 跳过
    conn = _connect(db_path)
    existing = set()
    try:
        for k in targets:
            for r in conn.execute(
                "SELECT trade_date FROM vix_daily WHERE vix_key=? AND vix IS NOT NULL AND vix>0",
                (k,),
            ).fetchall():
                existing.add((k, r[0]))
    finally:
        conn.close()

    # 合约信息（每所一次）
    basic_map: Dict[str, Any] = {}
    exchanges = sorted({v["exchange"] for v in targets.values()})
    for ex in exchanges:
        try:
            basic_map[ex] = _fetch_basic_exchange(src, ex)
            if verbose:
                print(f"[backfill] {ex} opt_basic {len(basic_map[ex])} 行")
        except Exception as ex_:  # noqa
            if verbose:
                print(f"[backfill] {ex} opt_basic 失败: {ex_}")
            basic_map[ex] = pd.DataFrame()

    # 现货整段预取
    spot_hist: Dict[str, Dict[str, float]] = {}
    for k, cfg in targets.items():
        api = "index_daily" if cfg["spot_kind"] == "index" else "fund_daily"
        try:
            df = _call_with_ip_retry(src._call_pro_api, api, ts_code=cfg["spot"],
                                     start_date=s, end_date=e)
            if (df is None or df.empty) and api == "fund_daily":
                df = _call_with_ip_retry(src._call_pro_api, "daily", ts_code=cfg["spot"],
                                         start_date=s, end_date=e)
            if df is not None and not df.empty:
                spot_hist[k] = dict(zip(df["trade_date"].astype(str), df["close"].astype(float)))
        except Exception as ex_:  # noqa
            if verbose:
                print(f"[backfill] {k} 现货 {cfg['spot']} 失败: {ex_}")
            spot_hist[k] = {}

    rows: List[Dict[str, Any]] = []
    n_days = len(all_td)
    day_cache: Dict[str, Any] = {}
    for di, td in enumerate(all_td, 1):
        if verbose and (di == 1 or di % 20 == 0 or di == n_days):
            print(f"[backfill] {td} ({di}/{n_days}) 累计 {len(rows)} 行", flush=True)
        # 当日哪些 key 还缺
        need = [k for k in targets if (k, td) not in existing]
        if not need:
            continue
        need_ex = sorted({targets[k]["exchange"] for k in need})
        for ex in need_ex:
            if ex not in day_cache or day_cache[ex][0] != td:
                try:
                    daily = _call_with_ip_retry(src._call_pro_api, "opt_daily",
                                                trade_date=td, exchange=ex)
                except Exception as ex_:  # noqa
                    if verbose:
                        print(f"  ⚠️ {ex} {td} opt_daily 失败: {ex_}")
                    daily = None
                day_cache[ex] = (td, daily)
            _, daily = day_cache[ex]
            if daily is None or daily.empty:
                continue
            basic = basic_map.get(ex)
            if basic is None or basic.empty:
                continue
            alive = basic[(basic["list_date"].fillna("") <= td)
                          & (basic["delist_date"].fillna("99999999") >= td)]
            merged = daily.merge(
                alive[["ts_code", "name", "exercise_price", "maturity_date"]],
                on="ts_code", how="inner")
            merged["call_put"] = merged["name"].astype(str).map(
                lambda nm: "P" if "沽" in nm else ("C" if "购" in nm else ""))
            for k in need:
                cfg = targets[k]
                if cfg["exchange"] != ex:
                    continue
                if ex == "CFFEX":
                    sub = merged[merged["ts_code"].astype(str).str.startswith(cfg["code_prefix"])]
                else:
                    b = merged["name"].astype(str).str.replace(r"[购沽].*$", "", regex=True)
                    sub = merged[b.isin(cfg["name_keys"])]
                if sub.empty:
                    continue
                spot = spot_hist.get(k, {}).get(td)
                if not spot or spot <= 0:
                    continue
                try:
                    contracts = standardize_contracts(sub, source="tushare",
                                                      exchange=ex, trade_date=td)
                    d = calc_vix(contracts, float(spot), r=0.015, trade_date=td,
                                 return_detail=True)
                except Exception:
                    d = {"vix": 0.0, "error": "exception"}
                if d.get("vix", 0) > 0 and not d.get("error"):
                    rows.append({"trade_date": td, "vix_key": k, "vix": d["vix"],
                                 "spot": float(spot), "source": "chain:tushare",
                                 "chain_rows": int(len(sub))})
        # 分批提交，降低长任务中断损失
        if len(rows) >= 80:
            upsert_vix_rows(rows, db_path=db_path)
            rows.clear()
        time.sleep(sleep_sec)

    n = upsert_vix_rows(rows, db_path=db_path)
    if verbose:
        print(f"[backfill] 写入 vix_daily 本批 {n} 行")
    return rows


def build_full_history(
    lookback_days: int = 740,
    db_path: str = DB_PATH,
    verbose: bool = True,
) -> Dict[str, Any]:
    """一键建库：先本地 opt_raw 重算 4ETF，再 Tushare 补 MO 与所有缺口。"""
    s = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")
    e = datetime.now().strftime("%Y%m%d")
    report: Dict[str, Any] = {"start": s, "end": e}
    try:
        recompute_from_local_opt_raw(
            keys=["300ETF", "500ETF", "创业板ETF", "科创50ETF"],
            start_date=s, end_date=e, db_path=db_path, verbose=verbose)
        report["local_recompute"] = "ok"
    except Exception as ex_:  # noqa
        report["local_recompute"] = f"fail: {ex_}"
    try:
        backfill_from_tushare(
            keys=VIX_KEYS, start_date=s, end_date=e,
            db_path=db_path, verbose=verbose)
        report["tushare_backfill"] = "ok"
    except Exception as ex_:  # noqa
        report["tushare_backfill"] = f"fail: {ex_}"
    report["stats"] = get_db_stats(db_path)
    return report


# ============================================================
# 盘中实时 VIX + 分位（业务统一入口）
# ============================================================

def _fetch_one_realtime(key: str) -> Dict[str, Any]:
    """取单个指数实时链并自算 VIX（线程内调用，线程安全：每次独立 client）。"""
    from .option_chain_realtime import get_all_realtime_option_chains
    cfg = VIX_TARGETS[key]
    t0 = time.time()
    out: Dict[str, Any] = {"key": key, "vix": None, "detail": None,
                           "source": None, "error": None, "elapsed": 0.0}
    try:
        res = get_all_realtime_option_chains([cfg["realtime_key"]])
        v = res.get(cfg["realtime_key"]) or {}
        chain = v.get("chain") or []
        spot = v.get("spot")
        if not chain or not spot:
            out["error"] = v.get("error") or "empty_chain"
            return out
        contracts = standardize_contracts(chain, source="tushare")
        d = calc_vix(contracts, float(spot), r=0.015, now=datetime.now(),
                     return_detail=True)
        out["vix"] = d.get("vix") or None
        out["detail"] = d
        out["source"] = f"chain:sina_realtime({v.get('source', '?')})"
        out["spot"] = spot
        out["chain_count"] = v.get("count", len(chain))
        if d.get("error"):
            out["error"] = d["error"]
    except Exception as e:  # noqa
        out["error"] = f"{type(e).__name__}: {e}"
    finally:
        out["elapsed"] = round(time.time() - t0, 1)
    return out


def get_all_vix_percentiles(
    keys: Optional[List[str]] = None,
    window: int = DEFAULT_WINDOW,
    time_budget_sec: float = DEFAULT_TIME_BUDGET,
    realtime: bool = True,
    db_path: str = DB_PATH,
) -> Dict[str, Dict[str, Any]]:
    """业务统一入口：五宽基「实时自算 VIX + 近一年分位 + 预警标志」。

    Args:
        keys: 指数子集，None=五宽基
        window: 分位窗口（交易日，默认250）
        time_budget_sec: 实时取链总预算（秒）；并发取链，超时则未完成的标 data_missing
        realtime: False 时只用收盘库最新值（测试/非盘中）

    Returns:
        {key: {
          vix, trade_date, percentile(0~100 或 None), p80, p95, sample_count,
          low_warning, top_warning, data_missing, stale, data_insufficient,
          low_threshold, source, spot, chain_count, detail, error, latest_db_date
        }}
    """
    keys = keys or VIX_KEYS
    result: Dict[str, Dict[str, Any]] = {}

    # 1) 历史分位（收盘库）
    hist: Dict[str, List[Dict[str, Any]]] = {}
    conn = _connect(db_path)
    try:
        for k in keys:
            rows = conn.execute(
                """SELECT trade_date, vix FROM vix_daily
                   WHERE vix_key=? AND vix IS NOT NULL AND vix>0
                   ORDER BY trade_date DESC LIMIT ?""",
                (k, window),
            ).fetchall()
            hist[k] = [{"trade_date": r[0], "vix": r[1]} for r in reversed(rows)]
            latest = conn.execute(
                "SELECT MAX(trade_date) FROM vix_daily WHERE vix_key=? AND vix IS NOT NULL",
                (k,),
            ).fetchone()[0]
            result[k] = {"_latest_db_date": latest}
    finally:
        conn.close()

    # 2) 实时自算（并发，预算保护）
    real: Dict[str, Dict[str, Any]] = {}
    if realtime:
        deadline = time.time() + max(time_budget_sec, 10)
        with ThreadPoolExecutor(max_workers=min(6, len(keys))) as pool:
            futs = {pool.submit(_fetch_one_realtime, k): k for k in keys}
            for fut in as_completed(futs, timeout=None):
                k = futs[fut]
                if time.time() > deadline:
                    real[k] = {"key": k, "vix": None,
                               "error": "time_budget_exceeded"}
                    continue
                try:
                    real[k] = fut.result(timeout=max(deadline - time.time(), 1))
                except Exception as e:  # noqa
                    real[k] = {"key": k, "vix": None,
                               "error": f"{type(e).__name__}: {e}"}
    # 非盘中：回退用收盘库最新一行
    fallback: Dict[str, Dict[str, Any]] = {}
    if not realtime:
        conn = _connect(db_path)
        try:
            for k in keys:
                r = conn.execute(
                    """SELECT trade_date, vix, spot, source FROM vix_daily
                       WHERE vix_key=? AND vix IS NOT NULL AND vix>0
                       ORDER BY trade_date DESC LIMIT 1""",
                    (k,),
                ).fetchone()
                if r:
                    fallback[k] = {"key": k, "vix": r[1], "spot": r[2],
                                   "source": r[3], "trade_date": r[0]}
        finally:
            conn.close()

    today_s = datetime.now().strftime("%Y%m%d")
    now_hm = datetime.now().strftime("%Y-%m-%d %H:%M")

    for k in keys:
        series = [x["vix"] for x in hist[k]]
        latest_db_date = result[k].pop("_latest_db_date", None)
        rt = real.get(k) or fallback.get(k) or {}
        vix = rt.get("vix")
        err = rt.get("error")

        entry: Dict[str, Any] = {
            "vix": round(float(vix), 2) if vix else None,
            "trade_date": (now_hm if realtime else rt.get("trade_date")),
            "percentile": None, "p80": None, "p95": None,
            "sample_count": len(series),
            "data_insufficient": len(series) < MIN_SAMPLE_COUNT,
            "low_threshold": LOW_VIX_THRESHOLD,
            "low_warning": False, "top_warning": False,
            "data_missing": vix is None,
            "stale": False,
            "source": rt.get("source"),
            "spot": rt.get("spot"),
            "chain_count": rt.get("chain_count"),
            "detail": rt.get("detail"),
            "elapsed": rt.get("elapsed"),
            "error": err,
            "latest_db_date": latest_db_date,
        }

        # 收盘库陈旧判定（实时成功也标注库新鲜度，便于邮件解释）
        if latest_db_date:
            try:
                age = (datetime.now() - datetime.strptime(latest_db_date, "%Y%m%d")).days
                entry["stale"] = age > STALE_DAYS
            except Exception:
                pass

        if series:
            s_sorted = sorted(series)

            def _pct(p):
                if not s_sorted:
                    return None
                idx = p * (len(s_sorted) - 1)
                lo = int(idx)
                hi = min(lo + 1, len(s_sorted) - 1)
                frac = idx - lo
                return round(s_sorted[lo] * (1 - frac) + s_sorted[hi] * frac, 2)

            entry["p80"] = _pct(0.80)
            entry["p95"] = _pct(0.95)
            if vix:
                below = sum(1 for x in series if x < vix)
                entry["percentile"] = round(100.0 * below / len(series), 1)

        # 预警：样本不足不触发（小样本保护，data_insufficient 只报不警）
        if vix and not entry["data_insufficient"]:
            if entry["percentile"] is not None and entry["percentile"] >= 95:
                entry["top_warning"] = True
            if vix < LOW_VIX_THRESHOLD:
                entry["low_warning"] = True

        result[k] = entry

    return result


def get_all_vix_percentiles_close_only(
    keys: Optional[List[str]] = None,
    window: int = DEFAULT_WINDOW,
    db_path: str = DB_PATH,
) -> Dict[str, Dict[str, Any]]:
    """非盘中便捷封装：只用收盘库（无实时取链）。"""
    return get_all_vix_percentiles(keys=keys, window=window, realtime=False,
                                   db_path=db_path)


if __name__ == "__main__":
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(description="自算 VIX 历史库与分位")
    ap.add_argument("cmd", choices=["backfill", "local", "stats", "realtime", "close"])
    ap.add_argument("--keys", default="")
    ap.add_argument("--days", type=int, default=740)
    ap.add_argument("--budget", type=float, default=DEFAULT_TIME_BUDGET)
    args = ap.parse_args()

    ks = [x for x in args.keys.split(",") if x] or None
    if args.cmd == "stats":
        print(json.dumps(get_db_stats(), ensure_ascii=False, indent=2))
    elif args.cmd == "local":
        recompute_from_local_opt_raw(keys=ks)
    elif args.cmd == "backfill":
        print(json.dumps(build_full_history(lookback_days=args.days),
                         ensure_ascii=False, indent=2, default=str))
    elif args.cmd == "realtime":
        r = get_all_vix_percentiles(keys=ks, time_budget_sec=args.budget)
        for k, v in r.items():
            d = v.get("detail") or {}
            print(f"{k:<8} VIX={v['vix']} 分位={v['percentile']} "
                  f"p80={v['p80']} p95={v['p95']} n={v['sample_count']} "
                  f"T1={d.get('t1_days')} T2={d.get('t2_days')} "
                  f"top={v['top_warning']} low={v['low_warning']} "
                  f"missing={v['data_missing']} insuff={v['data_insufficient']} "
                  f"stale={v['stale']} src={v['source']} err={v['error']}")
    elif args.cmd == "close":
        r = get_all_vix_percentiles_close_only(keys=ks)
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))


# ============================================================
# 快速区间批量回补（opt_daily start/end 批量，规避 15000 行/次上限）
# ============================================================

def _chunk_trade_dates(dates: List[str], n: int) -> List[List[str]]:
    return [dates[i:i + n] for i in range(0, len(dates), n)]


def backfill_batched(
    keys: Optional[List[str]] = None,
    start_date: str = "",
    end_date: str = "",
    db_path: str = DB_PATH,
    cffex_chunk: int = 7,
    etf_chunk: int = 10,
    verbose: bool = True,
) -> Dict[str, Any]:
    """区间批量回补：每个交易所按 cffex_chunk/etf_chunk 个交易日一批拉 opt_daily，
    内存按 (日,标的) 分组后自算。已存在的 (key,date) 跳过，可断点续跑。
    """
    import pandas as pd
    from ..sources.tushare_source import TushareSource
    from .option_metrics_history import _fetch_basic_exchange, _call_with_ip_retry, _norm_date

    keys = keys or VIX_KEYS
    targets = {k: VIX_TARGETS[k] for k in keys}
    s = _norm_date(start_date or (datetime.now() - timedelta(days=740)).strftime("%Y%m%d"))
    e = _norm_date(end_date or datetime.now().strftime("%Y%m%d"))

    src = TushareSource()
    cal = _call_with_ip_retry(
        src._call_pro_api, "trade_cal",
        exchange="SSE", start_date=s, end_date=e, is_open="1", fields="cal_date")
    if cal is None or cal.empty:
        raise RuntimeError("trade_cal 取交易日历失败")
    all_td = sorted(cal["cal_date"].astype(str).tolist())

    conn = _connect(db_path)
    existing: set = set()
    try:
        for k in targets:
            for r in conn.execute(
                "SELECT trade_date FROM vix_daily WHERE vix_key=? AND vix IS NOT NULL AND vix>0",
                (k,)).fetchall():
                existing.add((k, r[0]))
    finally:
        conn.close()

    # 现货整段预取
    spot_hist: Dict[str, Dict[str, float]] = {}
    for k, cfg in targets.items():
        api = "index_daily" if cfg["spot_kind"] == "index" else "fund_daily"
        try:
            df = _call_with_ip_retry(src._call_pro_api, api, ts_code=cfg["spot"],
                                     start_date=s, end_date=e)
            if (df is None or df.empty) and api == "fund_daily":
                df = _call_with_ip_retry(src._call_pro_api, "daily", ts_code=cfg["spot"],
                                         start_date=s, end_date=e)
            if df is not None and not df.empty:
                spot_hist[k] = dict(zip(df["trade_date"].astype(str), df["close"].astype(float)))
        except Exception as ex_:  # noqa
            if verbose:
                print(f"[batch] {k} 现货失败: {ex_}")
            spot_hist[k] = {}
        if verbose:
            print(f"[batch] 现货 {k} {len(spot_hist.get(k, {}))} 日")

    rows: List[Dict[str, Any]] = []
    written = 0
    # 按交易所分批
    for ex in sorted({v["exchange"] for v in targets.values()}):
        ex_keys = [k for k in targets if targets[k]["exchange"] == ex]
        chunk_n = cffex_chunk if ex == "CFFEX" else etf_chunk
        try:
            basic = _fetch_basic_exchange(src, ex)
        except Exception as ex_:  # noqa
            print(f"[batch] {ex} opt_basic 失败: {ex_}")
            continue
        chunks = _chunk_trade_dates(all_td, chunk_n)
        if verbose:
            print(f"[batch] {ex} {len(chunks)} 批 × {chunk_n} 日, 标的 {ex_keys}")
        for ci, grp in enumerate(chunks, 1):
            d0, d1 = grp[0], grp[-1]
            try:
                daily = _call_with_ip_retry(src._call_pro_api, "opt_daily",
                                            exchange=ex, start_date=d0, end_date=d1)
            except Exception as ex_:  # noqa
                if verbose:
                    print(f"  ⚠️ {ex} {d0}~{d1} 拉取失败: {ex_}")
                continue
            if daily is None or daily.empty or "trade_date" not in daily.columns:
                continue
            if len(daily) >= 14900 and verbose:
                print(f"  ⚠️ {ex} {d0}~{d1} 触顶 {len(daily)} 行，建议调小批次")
            alive = basic[(basic["list_date"].fillna("") <= d1)
                          & (basic["delist_date"].fillna("99999999") >= d0)]
            merged = daily.merge(
                alive[["ts_code", "name", "exercise_price", "maturity_date"]],
                on="ts_code", how="inner")
            merged["call_put"] = merged["name"].astype(str).map(
                lambda nm: "P" if "沽" in nm else ("C" if "购" in nm else ""))
            for td in sorted(merged["trade_date"].astype(str).unique()):
                day = merged[merged["trade_date"].astype(str) == td]
                for k in ex_keys:
                    if (k, td) in existing:
                        continue
                    cfg = targets[k]
                    if ex == "CFFEX":
                        sub = day[day["ts_code"].astype(str).str.startswith(cfg["code_prefix"])]
                    else:
                        b = day["name"].astype(str).str.replace(r"[购沽].*$", "", regex=True)
                        sub = day[b.isin(cfg["name_keys"])]
                    if sub.empty:
                        continue
                    spot = spot_hist.get(k, {}).get(td)
                    if not spot or spot <= 0:
                        continue
                    try:
                        contracts = standardize_contracts(sub, source="tushare",
                                                          exchange=ex, trade_date=td)
                        dd = calc_vix(contracts, float(spot), r=0.015,
                                      trade_date=td, return_detail=True)
                    except Exception:
                        dd = {"vix": 0.0, "error": "exception"}
                    if dd.get("vix", 0) > 0 and not dd.get("error"):
                        rows.append({"trade_date": td, "vix_key": k, "vix": dd["vix"],
                                     "spot": float(spot), "source": "chain:tushare",
                                     "chain_rows": int(len(sub))})
            if rows:
                written += upsert_vix_rows(rows, db_path=db_path)
                rows.clear()
                # 登记 existing，避免同批重复
                conn = _connect(db_path)
                try:
                    for k in ex_keys:
                        for r in conn.execute(
                            "SELECT trade_date FROM vix_daily WHERE vix_key=? AND vix>0",
                            (k,)).fetchall():
                            existing.add((k, r[0]))
                finally:
                    conn.close()
            if verbose and (ci % 5 == 0 or ci == len(chunks)):
                print(f"  [{ex}] 批 {ci}/{len(chunks)} 至 {d1}，累计写入 {written}", flush=True)

    return {"written": written, "stats": get_db_stats(db_path)}


# ============================================================
# 盘中采样表 vix_intraday（2026-10-23 新增）
# ------------------------------------------------------------
# 交易时段每 5 分钟落一条（trade_date, vix_key, sample_time）自算 VIX + 现价，
# 供「盘中 VIX 最大值」告警使用。收盘值只反映 15:00 那一刻，会漏掉盘中峰值
# （如 2026-10-23 深V日：中证1000 盘中最低 -4.2%、收盘 +2.38%）。
# 只保留最近 INTRADAY_KEEP_DAYS 个交易日，更早自动清理。
# ============================================================

INTRADAY_KEEP_DAYS = 120        # 盘中采样保留最近 120 个交易日


def _intraday_norm_date(d: Any) -> str:
    """统一成 YYYYMMDD。"""
    return _norm(d)


def upsert_vix_intraday_rows(
    rows: List[Dict[str, Any]],
    db_path: str = DB_PATH,
    max_retries: int = 6,
    prune: bool = True,
) -> int:
    """批量写入/替换 vix_intraday。返回写入行数。

    rows 元素：{trade_date, vix_key, sample_time, vix, spot, source}
      trade_date  : YYYYMMDD（或 YYYY-MM-DD，内部归一）
      sample_time : 'HH:MM'（或 ISO 字符串，内部取 HH:MM 部分归一）
    FUSE 挂载盘偶发 disk I/O error，做指数退避重试。
    """
    if not rows:
        return 0
    import time as _t
    payload = []
    for r in rows:
        td = _intraday_norm_date(r["trade_date"])
        st = str(r["sample_time"])
        # 只保留 HH:MM，去掉可能带的秒/日期
        if "T" in st:
            st = st.split("T", 1)[1]
        if " " in st:
            st = st.split(" ", 1)[1]
        st = st[:5]
        payload.append((td, r["vix_key"], st, r.get("vix"), r.get("spot"),
                        r.get("source")))
    last_err = None
    for attempt in range(max_retries + 1):
        conn = None
        try:
            conn = _connect(db_path)
            conn.executemany(
                """
                INSERT OR REPLACE INTO vix_intraday
                    (trade_date, vix_key, sample_time, vix, spot, source)
                VALUES (?, ?, ?, ?, ?, ?)
                """, payload)
            conn.commit()
            written = len(payload)
            if prune:
                _prune_intraday(conn, INTRADAY_KEEP_DAYS)
            return written
        except sqlite3.OperationalError as e:
            last_err = e
            if attempt < max_retries:
                _t.sleep(1.5 * (attempt + 1))
                continue
            raise
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    raise last_err


def _prune_intraday(conn: sqlite3.Connection, keep_days: int = INTRADAY_KEEP_DAYS) -> int:
    """只保留最近 keep_days 个交易日的盘中采样，返回删除行数。"""
    rows = conn.execute(
        "SELECT DISTINCT trade_date FROM vix_intraday ORDER BY trade_date DESC LIMIT ?",
        (keep_days,),
    ).fetchall()
    if not rows:
        return 0
    cutoff = rows[-1][0]
    cur = conn.execute("DELETE FROM vix_intraday WHERE trade_date < ?", (cutoff,))
    conn.commit()
    return cur.rowcount or 0


def query_intraday_series(
    vix_key: str,
    trade_date: str,
    db_path: str = DB_PATH,
) -> List[Dict[str, Any]]:
    """取单指数某日盘中采样序列（按时间升序）。"""
    td = _intraday_norm_date(trade_date)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """SELECT sample_time, vix, spot, source FROM vix_intraday
               WHERE vix_key=? AND trade_date=? ORDER BY sample_time""",
            (vix_key, td),
        ).fetchall()
        return [{"sample_time": r[0], "vix": r[1], "spot": r[2], "source": r[3]}
                for r in rows]
    finally:
        conn.close()


def get_intraday_max(
    vix_key: str,
    trade_date: str,
    db_path: str = DB_PATH,
) -> Optional[Dict[str, Any]]:
    """单指数某日盘中 VIX 最大值及其出现时刻。

    Returns:
        None（无有效采样）或
        {vix_max, sample_time_at_max, spot_at_max, sample_count, first_time,
         last_time, first_ge: {tier: 首次 >= tier 的 sample_time}}
    """
    td = _intraday_norm_date(trade_date)
    series = query_intraday_series(vix_key, td, db_path=db_path)
    valid = [s for s in series if s.get("vix") is not None]
    if not valid:
        return None
    best = max(valid, key=lambda s: s["vix"])
    return {
        "vix_max": best["vix"],
        "sample_time_at_max": best["sample_time"],
        "spot_at_max": best.get("spot"),
        "sample_count": len(valid),
        "first_time": valid[0]["sample_time"],
        "last_time": valid[-1]["sample_time"],
    }


def get_intraday_max_all(
    trade_date: str,
    db_path: str = DB_PATH,
) -> Dict[str, Optional[Dict[str, Any]]]:
    """五宽基某日盘中 VIX 最大值（缺采样的返回 None）。"""
    td = _intraday_norm_date(trade_date)
    return {k: get_intraday_max(k, td, db_path=db_path) for k in VIX_KEYS}


def first_break_time(
    vix_key: str,
    trade_date: str,
    tier: float,
    db_path: str = DB_PATH,
) -> Optional[str]:
    """某指数某日 VIX 首次 >= tier 的采样时刻（HH:MM），无则 None。"""
    for s in query_intraday_series(vix_key, trade_date, db_path=db_path):
        if s.get("vix") is not None and s["vix"] >= tier:
            return s["sample_time"]
    return None


def get_intraday_stats(db_path: str = DB_PATH) -> Dict[str, Any]:
    """盘中表概览：总行数、日期区间、每日行数（最近 5 日）。"""
    conn = _connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM vix_intraday").fetchone()[0]
        rng = conn.execute(
            "SELECT MIN(trade_date), MAX(trade_date) FROM vix_intraday"
        ).fetchone()
        days = conn.execute(
            """SELECT trade_date, COUNT(*) FROM vix_intraday
               GROUP BY trade_date ORDER BY trade_date DESC LIMIT 5"""
        ).fetchall()
        return {"total_rows": total or 0, "start": rng[0], "end": rng[1],
                "recent_days": [{"trade_date": d, "rows": n} for d, n in days]}
    finally:
        conn.close()
