#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
盘中期权情绪指标采样（Vol PCR / 25Δ 偏度）—— 5 指数统一真源
================================================================
背景（主人 2026-10-23 明确）：
  「就是中证 1000、中证 500、科创 50、沪深 300、创业板这 5 个指数我需要。不需要上证 50。」
  需要盘中每 15 分钟同时采 5 个指数的 vol_pcr / iv_skew，并做私聊告警：
    · PCR 四条：vol_pcr >= 1.2 / vol_pcr < 0.3 / 250 日分位 > 80% / 250 日分位 < 20%
    · 偏度两条：250 日分位 > 80% / < 20%
  判定一律用「当日盘中极值」（最大值/最小值），与 VIX 口径一致。

标的与期权链来源（严禁用 ETF 替代中证1000）：
  ┌──────────┬──────────┬──────────────────┬─────────────────────────────┐
  │ 指数      │ 期权标的  │ 日频 underlying   │ 盘中链来源                    │
  ├──────────┼──────────┼──────────────────┼─────────────────────────────┤
  │ 沪深300   │ 300ETF期权│ 510300           │ 新浪 CON_OP 实时链（含成交量） │
  │ 中证500   │ 500ETF期权│ 510500           │ 新浪 CON_OP 实时链（含成交量） │
  │ 创业板    │ 创业板ETF │ 159915           │ 新浪 CON_OP 实时链（含成交量） │
  │ 科创50    │ 科创50ETF │ 588000           │ 新浪 CON_OP 实时链（含成交量） │
  │ 中证1000  │ MO 股指期权│ 000852           │ 中金所官方实时行情（含成交量） │
  └──────────┴──────────┴──────────────────┴─────────────────────────────┘
  说明：新浪 CFFEX openapi 的 MO 实时链无成交量/成交额字段，若用它算 vol_pcr 会恒为缺失；
        故 MO 盘中链改走「中金所官方实时行情」http://www.cffex.com.cn/quote_MO.txt
        （字段：合约,持仓量,成交量,最新价,涨跌,买价,买量,卖价,卖量），含成交量。
        现货指数仍取新浪 sh000852。**不回落任何 ETF 标的。**

口径（与日频 option_metrics_history._compute_one 完全一致，同一套 option_metrics 函数）：
  vol_pcr  = option_metrics.calc_pcr(contracts, "volume")
  iv_skew  = 25Δ put_iv − 25Δ call_iv（原始差值，小数，可负）
  其余 atm_iv / oi_pcr / amt_pcr 同源；cboe_skew 仅参考，不入告警。

数据库：<codeact>/output/vix_history.db（与自算 VIX 同库，单一真源库）
  表 option_intraday：盘中 5 指数每 15 分钟一条（保留最近 120 交易日）
  表 option_daily   ：中证1000(MO) 日频历史（用于 250 日分位）；4 ETF 日频复用
                    市场情绪监控 style_drift.db 的 daily_metrics，不重复落库。

纪律：取数失败一律标记缺失，绝不编造、绝不回落其它标的。
"""
from __future__ import annotations

import os
import re
import math
import time
import sqlite3
from datetime import datetime, date
from typing import Any, Dict, List, Optional

try:
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None

# ============================================================
# 路径
# ============================================================
_HERE = os.path.dirname(os.path.abspath(__file__))       # .../data_hub/collectors
_DATAHUB = os.path.dirname(_HERE)                         # .../data_hub
_SCRIPTS = os.path.dirname(_DATAHUB)                      # .../scripts
_DB_DIR = os.path.join(_SCRIPTS, "output")
DB_PATH = os.path.join(_DB_DIR, "vix_history.db")

# 4 ETF 日频指标（复用，不重复落库）
STYLE_DRIFT_DB = "/Coze/Drive/蓁宝/所有对话/主对话/市场情绪监控/data/options_cache/style_drift.db"
_STYLE_DRIFT_DB_FALLBACK = "/opt/coze/.fuse_drive/蓁宝/所有对话/主对话/市场情绪监控/data/options_cache/style_drift.db"

# ============================================================
# 计算层：复用统一 option_metrics（口径唯一）
# ============================================================
try:
    from ..option_metrics import (
        standardize_contracts,
        compute_metrics_from_data,
        calc_25d_risk_reversal,
    )
except Exception:  # pragma: no cover - 脚本直挂兜底
    import sys
    sys.path.insert(0, _SCRIPTS)
    from data_hub.option_metrics import (  # type: ignore
        standardize_contracts,
        compute_metrics_from_data,
        calc_25d_risk_reversal,
    )

IV_VALID_MIN = 0.03
IV_VALID_MAX = 0.80

# 2026-11-09 修复：开盘 09:30「无成交快照」闸门
#   全链「有成交量」合约数低于该阈值时，判定为开盘瞬间快照未就绪：
#   价格全为昨收结转、成交量为 0 → vol_pcr / iv_skew / atm_iv 一律置 None。
#   实测 09:30 时点 MO 主力系列成交合约数 = 0；09:45 及之后为数十个，阈值 5 有充足余量。
MIN_TRADED_CONTRACTS = 5

INTRADAY_KEEP_DAYS = 120        # 盘中采样保留最近 120 交易日
DAILY_PCT_WINDOW = 250          # 分位窗口（交易日）
MIN_PCT_SAMPLE = 120            # 分位最小样本，不足则标注 data_insufficient

# ============================================================
# 5 指数标的注册表（无上证50）
# ============================================================
OPTION_TARGETS: Dict[str, Dict[str, Any]] = {
    "300ETF": {
        "label": "沪深300", "underlying": "510300", "index": "000300.SH",
        "kind": "etf", "rt_key": "300ETF", "spot_quote": "sh510300", "exchange": "SSE",
    },
    "500ETF": {
        "label": "中证500", "underlying": "510500", "index": "000905.SH",
        "kind": "etf", "rt_key": "500ETF", "spot_quote": "sh510500", "exchange": "SSE",
    },
    "创业板ETF": {
        "label": "创业板", "underlying": "159915", "index": "399006.SZ",
        "kind": "etf", "rt_key": "创业板ETF", "spot_quote": "sz159915", "exchange": "SZSE",
    },
    "科创50ETF": {
        "label": "科创50", "underlying": "588000", "index": "000688.SH",
        "kind": "etf", "rt_key": "科创50ETF", "spot_quote": "sh588000", "exchange": "SSE",
    },
    "MO": {
        "label": "中证1000", "underlying": "000852", "index": "000852.SH",
        "kind": "cffex", "prefix": "MO", "spot_quote": "sh000852", "exchange": "CFFEX",
    },
}
OPTION_KEYS: List[str] = list(OPTION_TARGETS.keys())
KEY_LABEL: Dict[str, str] = {k: v["label"] for k, v in OPTION_TARGETS.items()}
KEY_UNDERLYING: Dict[str, str] = {k: v["underlying"] for k, v in OPTION_TARGETS.items()}

# 中金所官方实时行情（含成交量）——MO 专用
_CFFEX_QUOTE_URL = "http://www.cffex.com.cn/quote_{prefix}.txt"


def _f(v) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _third_friday(yymm: str) -> str:
    """YYMM → 合约月第三个周五（YYYYMMDD），中金所股指期权到期日规则。"""
    yy = 2000 + int(yymm[:2])
    mm = int(yymm[2:])
    first = date(yy, mm, 1)
    first_fri = 1 + ((4 - first.weekday()) % 7)   # 周五 weekday=4
    return date(yy, mm, first_fri + 14).strftime("%Y%m%d")


# ============================================================
# 主力系列筛选（2026-10-23 新增：中证1000 走 MO 主力系列）
# ============================================================
def select_main_series(chain: List[Dict[str, Any]], kind: str = "cffex"
                       ) -> List[Dict[str, Any]]:
    """主力系列筛选：按到期月分组，取 (成交量 + 持仓量) 最大的月份，仅保留该月合约。

    中金所 MO（中证1000 股指期权）按用户要求使用「主力系列」：
    近月主力 = 成交+持仓量最大的到期月，严禁用全系列合并冒充。

    仅对 kind == "cffex" 生效；ETF 保持全系列口径（与 style_drift 日频一致）。
    返回 (筛选后链, 主力月份 YYYYMM)；无法判定时原样返回、月份为 None。
    """
    if kind != "cffex" or not chain:
        return list(chain or []), None
    grp: Dict[str, Dict[str, float]] = {}
    for c in chain:
        m = str(c.get("maturity_date") or c.get("expiry") or "")[:6]
        if len(m) != 6:
            continue
        g = grp.setdefault(m, {"vol": 0.0, "oi": 0.0})
        g["vol"] += float(c.get("vol") or 0)
        g["oi"] += float(c.get("oi") or 0)
    if not grp:
        return list(chain), None
    best = max(grp.items(), key=lambda kv: kv[1]["vol"] + kv[1]["oi"])[0]
    kept = [c for c in chain
            if str(c.get("maturity_date") or c.get("expiry") or "")[:6] == best]
    return (kept or list(chain)), best


# ============================================================
# 盘中链取数
# ============================================================
def fetch_cffex_chain(prefix: str = "MO", retries: int = 3) -> List[Dict[str, Any]]:
    """中金所官方实时行情 → 标准合约 list[dict]（含成交量 vol）。

    字段：合约代码,持仓量,成交量,最新价,涨跌,买价,买量,卖价,卖量
    """
    try:
        import requests
    except Exception:  # pragma: no cover
        return []
    url = _CFFEX_QUOTE_URL.format(prefix=prefix)
    text = None
    for i in range(retries):
        try:
            r = requests.get(url, timeout=15)
            r.encoding = "utf-8"
            if r.status_code == 200 and r.text and r.text.strip():
                text = r.text
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.6 * (i + 1))
    if not text:
        return []
    today = date.today()
    chain: List[Dict[str, Any]] = []
    pat = re.compile(r"^([A-Za-z]+)(\d{4})-([CP])-(\d+(?:\.\d+)?)$")
    for line in text.strip().splitlines()[1:]:
        p = line.split(",")
        if len(p) < 9:
            continue
        m = pat.match(p[0].strip())
        if not m:
            continue
        yymm, cp, strike = m.group(2), m.group(3).upper(), m.group(4)
        try:
            maturity = _third_friday(yymm)
            dte = (datetime.strptime(maturity, "%Y%m%d").date() - today).days
        except Exception:  # noqa: BLE001
            continue
        if dte < 0:
            continue   # 已到期合约跳过
        chain.append({
            "ts_code": p[0].strip(), "name": p[0].strip(), "call_put": cp,
            "exercise_price": _f(strike), "maturity_date": maturity, "dte": dte,
            "close": _f(p[3]), "vol": _f(p[2]), "oi": _f(p[1]), "amount": None,
            "bid": _f(p[5]), "ask": _f(p[7]),
            "bid_vol": _f(p[6]), "ask_vol": _f(p[8]),
            "source": "cffex_official",
        })
    return chain


def fetch_intraday_chain(key: str) -> Dict[str, Any]:
    """取单标的盘中链 + 现货价。

    Returns: {key, chain, spot, source, error}
    """
    cfg = OPTION_TARGETS.get(key)
    if not cfg:
        return {"key": key, "chain": [], "spot": None, "source": None,
                "error": "未知标的: %s" % key}
    if cfg["kind"] == "cffex":
        chain = fetch_cffex_chain(cfg.get("prefix", "MO"))
        spot = None
        try:
            from .option_chain_realtime import get_realtime_spot
            spot = (get_realtime_spot(cfg["spot_quote"]) or {}).get("spot")
        except Exception:  # noqa: BLE001
            try:
                from data_hub.collectors.option_chain_realtime import get_realtime_spot  # type: ignore
                spot = (get_realtime_spot(cfg["spot_quote"]) or {}).get("spot")
            except Exception:  # noqa: BLE001
                spot = None
        return {"key": key, "chain": chain, "spot": spot,
                "source": "cffex_official" if chain else None,
                "error": None if chain else "cffex 官方行情为空"}
    # ETF：新浪 CON_OP 实时链（含成交量）
    try:
        try:
            from .option_chain_realtime import get_realtime_option_chain
        except Exception:  # noqa: BLE001
            from data_hub.collectors.option_chain_realtime import get_realtime_option_chain  # type: ignore
        r = get_realtime_option_chain(cfg["rt_key"]) or {}
        return {"key": key, "chain": r.get("chain") or [], "spot": r.get("spot"),
                "source": r.get("source"), "error": r.get("error")}
    except Exception as e:  # noqa: BLE001
        return {"key": key, "chain": [], "spot": None, "source": None,
                "error": "%s: %s" % (type(e).__name__, e)}


# ============================================================
# 指标计算（与 option_metrics_history._compute_one 同口径）
# ============================================================
def _clean_iv(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if IV_VALID_MIN <= v <= IV_VALID_MAX else None


def compute_metrics_from_chain(chain: List[Dict[str, Any]], spot: float,
                               exchange: str, td: str,
                               main_series: bool = True) -> Optional[Dict[str, Any]]:
    """由单标的盘中链算全套指标（vol_pcr / iv_skew / atm_iv / oi_pcr / amt_pcr）。

    计算路径与 option_metrics_history._compute_one 完全一致：
      standardize_contracts → compute_metrics_from_data(r=0.025) + calc_25d_risk_reversal

    2026-10-23：中金所 MO（中证1000）默认按「主力系列」口径计算
    （main_series=True 时先 select_main_series 只保留成交+持仓量最大的到期月），
    与 option_daily 日频历史口径一致；ETF 不受影响（仍全系列，对齐 style_drift）。
    """
    if not chain or not spot or spot <= 0 or pd is None:
        return None
    if main_series and str(exchange).upper() == "CFFEX":
        chain, _main_month = select_main_series(chain, "cffex")
        if not chain:
            return None
    try:
        df = pd.DataFrame(chain)
    except Exception:  # noqa: BLE001
        return None
    contracts = standardize_contracts(df, source="tushare", exchange=exchange, trade_date=td)
    if not contracts:
        return None

    # ---- 数据质量闸门（2026-11-09 修复 09:30 开盘误报 PCR<0.3 / 偏度极值）----
    # 现象：中证1000(MO) 每个交易日 09:30 采样点 vol_pcr 恒为 0.0，iv_skew 极端失真
    #       （11-06 09:30 = -0.1927、11-09 09:30 = 0.1638，09:45 后收敛到 0.00~0.17）。
    # 根因：开盘瞬间 MO 全链成交量为 0（实测 11 个交易日 09:30 全为 0）。
    #       · calc_pcr 在 call/put 成交总量均 <=0 时返回 0.0（不是 None），
    #         下游把 0.0 当有效值 → 「盘中最小值 0.000 < 0.3」恒成立 → 误报；
    #       · 同一快照全部价格均为昨收结转、无真实成交，25Δ 偏度由陈旧价格外推 → 失真。
    # 处理：全链无成交或有效成交合约数不足 → vol_pcr / iv_skew / atm_iv / 25Δ IV
    #       一律置 None（缺失），绝不落 0；oi_pcr 只依赖持仓量，不受成交影响，保留。
    # 影响面：仅「全链几乎无成交」时触发；09:45 及之后（MO 主力系列成交合约数
    #       数十个、成交量数千手）与 4 个 ETF 均不触发。
    total_vol = 0.0
    traded_n = 0
    for _c in contracts:
        try:
            _v = float(getattr(_c, "volume", 0) or 0)
        except (TypeError, ValueError):
            _v = 0.0
        total_vol += _v
        if _v > 0:
            traded_n += 1
    quote_ready = bool(total_vol > 0 and traded_n >= MIN_TRADED_CONTRACTS)

    try:
        m = compute_metrics_from_data(df, spot, source="tushare", exchange=exchange,
                                      trade_date=td, r=0.025)
    except Exception:  # noqa: BLE001
        return None
    try:
        rr = calc_25d_risk_reversal(contracts, spot, trade_date=td, r=0.025) or {}
    except Exception:  # noqa: BLE001
        rr = {}
    atm = getattr(m, "atm_iv", None)
    put25 = rr.get("put25_iv")
    call25 = rr.get("call25_iv")
    skew_raw = None
    if put25 is not None and call25 is not None:
        skew_raw = put25 - call25
    # vol_pcr：0 / inf / 非数值 一律视为缺失（calc_pcr 无成交时返回 0.0）
    vol_pcr = getattr(m, "vol_pcr", None)
    try:
        vol_pcr = float(vol_pcr) if vol_pcr is not None else None
    except (TypeError, ValueError):
        vol_pcr = None
    if vol_pcr is not None and (not math.isfinite(vol_pcr) or vol_pcr <= 0):
        vol_pcr = None
    if not quote_ready:
        vol_pcr = None
        atm = None
        put25 = None
        call25 = None
        skew_raw = None
    amt_pcr = None
    try:
        call_amt = sum(c.amount for c in contracts if c.opt_type == "C")
        put_amt = sum(c.amount for c in contracts if c.opt_type == "P")
        if call_amt and call_amt > 0:
            amt_pcr = round(put_amt / call_amt, 4)
    except Exception:  # noqa: BLE001
        amt_pcr = None
    return {
        "atm_iv": _clean_iv(atm),
        "put_25d_iv": _clean_iv(put25),
        "call_25d_iv": _clean_iv(call25),
        "iv_skew": (round(float(skew_raw), 6) if skew_raw is not None else None),
        "cboe_skew": getattr(m, "cboe_skew", None),
        "vol_pcr": vol_pcr,
        "oi_pcr": getattr(m, "oi_pcr", None),
        "amt_pcr": amt_pcr,
        "spot_price": round(float(spot), 4),
    }


# ============================================================
# DB：option_intraday / option_daily（与 vix_history.db 同库）
# ============================================================
def _connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_tables(db_path: str = DB_PATH) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS option_intraday (
                trade_date  TEXT,
                underlying  TEXT,
                sample_time TEXT,
                vol_pcr     REAL,
                iv_skew     REAL,
                atm_iv      REAL,
                oi_pcr      REAL,
                amt_pcr     REAL,
                spot        REAL,
                source      TEXT,
                updated_at  TEXT,
                PRIMARY KEY (trade_date, underlying, sample_time)
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS option_daily (
                trade_date   TEXT,
                underlying   TEXT,
                spot_price   REAL,
                atm_iv       REAL,
                put_25d_iv   REAL,
                call_25d_iv  REAL,
                iv_skew      REAL,
                cboe_skew    REAL,
                vol_pcr      REAL,
                oi_pcr       REAL,
                amt_pcr      REAL,
                vix          REAL,
                data_source  TEXT,
                updated_at   TEXT,
                PRIMARY KEY (trade_date, underlying)
            )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_optintraday_u "
                     "ON option_intraday(underlying, trade_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_optdaily_u "
                     "ON option_daily(underlying, trade_date)")
        conn.commit()
    finally:
        conn.close()


def _norm_date(d) -> str:
    s = str(d).replace("-", "").replace("/", "")[:8]
    return s


def _norm_time(t) -> str:
    st = str(t)
    if "T" in st:
        st = st.split("T", 1)[1]
    if " " in st:
        st = st.split(" ", 1)[1]
    return st[:5]


def upsert_option_intraday_rows(rows: List[Dict[str, Any]],
                                db_path: str = DB_PATH,
                                max_retries: int = 6,
                                prune: bool = True) -> int:
    """批量写入 option_intraday。rows 元素：{trade_date, underlying, sample_time,
    vol_pcr, iv_skew, atm_iv, oi_pcr, amt_pcr, spot, source}"""
    if not rows:
        return 0
    ensure_tables(db_path)
    payload = []
    for r in rows:
        payload.append((
            _norm_date(r["trade_date"]), str(r["underlying"]), _norm_time(r["sample_time"]),
            r.get("vol_pcr"), r.get("iv_skew"), r.get("atm_iv"), r.get("oi_pcr"),
            r.get("amt_pcr"), (r.get("spot") or r.get("spot_price")), r.get("source"),
            datetime.now().isoformat(timespec="seconds"),
        ))
    last_err = None
    for attempt in range(max_retries + 1):
        conn = None
        try:
            conn = _connect(db_path)
            conn.executemany(
                """INSERT OR REPLACE INTO option_intraday
                   (trade_date, underlying, sample_time, vol_pcr, iv_skew, atm_iv,
                    oi_pcr, amt_pcr, spot, source, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""", payload)
            conn.commit()
            if prune:
                _prune_option_intraday(conn, INTRADAY_KEEP_DAYS)
            return len(payload)
        except sqlite3.OperationalError as e:
            last_err = e
            if attempt < max_retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
    raise last_err


def _prune_option_intraday(conn: sqlite3.Connection, keep_days: int = INTRADAY_KEEP_DAYS) -> int:
    rows = conn.execute(
        "SELECT DISTINCT trade_date FROM option_intraday ORDER BY trade_date DESC LIMIT ?",
        (keep_days,)).fetchall()
    if not rows:
        return 0
    cutoff = rows[-1][0]
    cur = conn.execute("DELETE FROM option_intraday WHERE trade_date < ?", (cutoff,))
    conn.commit()
    return cur.rowcount or 0


def query_option_intraday_series(underlying: str, trade_date: str,
                                 db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    ensure_tables(db_path)
    td = _norm_date(trade_date)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """SELECT sample_time, vol_pcr, iv_skew, atm_iv, oi_pcr, amt_pcr, spot, source
               FROM option_intraday WHERE underlying=? AND trade_date=? ORDER BY sample_time""",
            (str(underlying), td)).fetchall()
        return [{"sample_time": r[0], "vol_pcr": r[1], "iv_skew": r[2], "atm_iv": r[3],
                 "oi_pcr": r[4], "amt_pcr": r[5], "spot": r[6], "source": r[7]}
                for r in rows]
    finally:
        conn.close()


def _pcr_valid(v) -> bool:
    """vol_pcr 是否可用：None / 0 / 负数 / inf / nan 均视为无效（2026-11-09）。"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return False
    return v == v and v > 0 and v != float("inf")


def _extreme(series, field, mode):
    vals = [s for s in series if s.get(field) is not None]
    if not vals:
        return None, None
    best = (max if mode == "max" else min)(vals, key=lambda s: s[field])
    return best[field], best["sample_time"]


def get_option_intraday_extremes(trade_date: str,
                                 db_path: str = DB_PATH) -> Dict[str, Dict[str, Any]]:
    """5 标的当日盘中极值（判定口径：最大值/最小值，与 VIX 一致）。"""
    out: Dict[str, Dict[str, Any]] = {}
    for key, cfg in OPTION_TARGETS.items():
        u = cfg["underlying"]
        try:
            series = query_option_intraday_series(u, trade_date, db_path=db_path) or []
        except Exception:  # noqa: BLE001
            series = []
        # PCR 极值：只认 >0 的有效值（0 / None 一律排除，2026-11-09）
        pcr_series = [s for s in series if _pcr_valid(s.get("vol_pcr"))]
        valid_pcr = pcr_series
        # 偏度极值：只取「成交量维度有效」的样本（2026-11-09）
        #   开盘无成交快照的 iv_skew 由昨收陈旧价格外推（实测 09:30 出现 -0.1927 /
        #   0.1638，而 09:45 之后收敛到 -0.021~0.167），与 PCR=0 同源失真；
        #   vol_pcr 缺失即判定该快照未就绪，其 skew 不参与极值/分位判断。
        valid_skew = [s for s in series
                      if s.get("iv_skew") is not None
                      and _pcr_valid(s.get("vol_pcr"))]
        vmax, t_vmax = _extreme(pcr_series, "vol_pcr", "max")
        vmin, t_vmin = _extreme(pcr_series, "vol_pcr", "min")
        smax, t_smax = _extreme(valid_skew, "iv_skew", "max")
        smin, t_smin = _extreme(valid_skew, "iv_skew", "min")
        last = series[-1] if series else {}
        out[key] = {
            "underlying": u, "label": cfg["label"],
            "vol_pcr_max": vmax, "vol_pcr_max_time": t_vmax,
            "vol_pcr_min": vmin, "vol_pcr_min_time": t_vmin,
            "vol_pcr_last": last.get("vol_pcr"), "vol_pcr_last_time": last.get("sample_time"),
            "iv_skew_max": smax, "iv_skew_max_time": t_smax,
            "iv_skew_min": smin, "iv_skew_min_time": t_smin,
            "iv_skew_last": last.get("iv_skew"), "iv_skew_last_time": last.get("sample_time"),
            "atm_iv": last.get("atm_iv"), "oi_pcr": last.get("oi_pcr"),
            "amt_pcr": last.get("amt_pcr"),
            "sample_count": len(series),
            "pcr_sample_count": len(valid_pcr),
            "skew_sample_count": len(valid_skew),
            "source": last.get("source"),
        }
    return out


def get_option_intraday_stats(db_path: str = DB_PATH) -> Dict[str, Any]:
    ensure_tables(db_path)
    conn = _connect(db_path)
    try:
        n = conn.execute("SELECT COUNT(*) FROM option_intraday").fetchone()[0]
        rng = conn.execute("SELECT MIN(trade_date), MAX(trade_date) FROM option_intraday").fetchone()
        nd = conn.execute("SELECT COUNT(DISTINCT trade_date) FROM option_intraday").fetchone()[0]
        return {"rows": n, "start": rng[0], "end": rng[1], "trade_days": nd}
    finally:
        conn.close()


# ============================================================
# 日频序列（250 日分位来源）
#   · MO(000852)：本库 option_daily
#   · 4 ETF     ：复用 市场情绪监控 style_drift.db daily_metrics
# ============================================================
def _style_drift_path() -> str:
    for p in (STYLE_DRIFT_DB, _STYLE_DRIFT_DB_FALLBACK):
        if os.path.exists(p):
            return p
    return STYLE_DRIFT_DB


def upsert_option_daily_rows(rows: List[Dict[str, Any]], db_path: str = DB_PATH) -> int:
    if not rows:
        return 0
    ensure_tables(db_path)
    payload = []
    for r in rows:
        payload.append((
            _norm_date(r["trade_date"]), str(r["underlying"]), r.get("spot_price"),
            r.get("atm_iv"), r.get("put_25d_iv"), r.get("call_25d_iv"), r.get("iv_skew"),
            r.get("cboe_skew"), r.get("vol_pcr"), r.get("oi_pcr"), r.get("amt_pcr"),
            r.get("vix"), r.get("data_source"),
            datetime.now().isoformat(timespec="seconds"),
        ))
    conn = _connect(db_path)
    try:
        conn.executemany(
            """INSERT OR REPLACE INTO option_daily
               (trade_date, underlying, spot_price, atm_iv, put_25d_iv, call_25d_iv,
                iv_skew, cboe_skew, vol_pcr, oi_pcr, amt_pcr, vix, data_source, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", payload)
        conn.commit()
        return len(payload)
    finally:
        conn.close()


def get_daily_metric_series(underlying: str, window: int = DAILY_PCT_WINDOW,
                            db_path: str = DB_PATH) -> Dict[str, List[Dict[str, Any]]]:
    """取单标的最近 window 个交易日的日频 vol_pcr / iv_skew 序列（升序）。

    Returns: {'vol_pcr': [{trade_date, value}...], 'iv_skew': [...]}
    """
    u = str(underlying)
    rows = []
    if u == "000852":
        ensure_tables(db_path)
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                """SELECT trade_date, vol_pcr, iv_skew FROM option_daily
                   WHERE underlying=? ORDER BY trade_date DESC LIMIT ?""",
                (u, window)).fetchall()
        finally:
            conn.close()
    else:
        p = _style_drift_path()
        if os.path.exists(p):
            conn = sqlite3.connect(p, timeout=30)
            try:
                rows = conn.execute(
                    """SELECT trade_date, vol_pcr, iv_skew FROM daily_metrics
                       WHERE underlying=? ORDER BY trade_date DESC LIMIT ?""",
                    (u, window)).fetchall()
            except Exception:  # noqa: BLE001
                rows = []
            finally:
                conn.close()
    rows = list(reversed(rows))
    return {
        "vol_pcr": [{"trade_date": r[0], "value": r[1]} for r in rows if r[1] is not None],
        "iv_skew": [{"trade_date": r[0], "value": r[2]} for r in rows if r[2] is not None],
    }


def percentile_rank(series_values: List[float], value: Optional[float]) -> Optional[float]:
    """百分位（0~100）：series 中严格小于 value 的占比。"""
    if value is None:
        return None
    vals = [v for v in series_values if v is not None]
    if not vals:
        return None
    below = sum(1 for v in vals if v < value)
    return round(100.0 * below / len(vals), 1)


def get_option_metrics_percentiles(trade_date: str, window: int = DAILY_PCT_WINDOW,
                                   db_path: str = DB_PATH) -> Dict[str, Dict[str, Any]]:
    """5 标的当日「盘中极值」的 250 日分位（PCR 最大值/最小值、偏度最大值/最小值）。"""
    ext = get_option_intraday_extremes(trade_date, db_path=db_path)
    out: Dict[str, Dict[str, Any]] = {}
    for key, cfg in OPTION_TARGETS.items():
        u = cfg["underlying"]
        try:
            ser = get_daily_metric_series(u, window=window, db_path=db_path)
        except Exception:  # noqa: BLE001
            ser = {"vol_pcr": [], "iv_skew": []}
        pcr_vals = [x["value"] for x in ser.get("vol_pcr", [])]
        skew_vals = [x["value"] for x in ser.get("iv_skew", [])]
        e = ext.get(key) or {}
        out[key] = {
            "label": cfg["label"], "underlying": u,
            "vol_pcr_max": e.get("vol_pcr_max"),
            "vol_pcr_min": e.get("vol_pcr_min"),
            "iv_skew_max": e.get("iv_skew_max"),
            "iv_skew_min": e.get("iv_skew_min"),
            "vol_pcr_max_pct": percentile_rank(pcr_vals, e.get("vol_pcr_max")),
            "vol_pcr_min_pct": percentile_rank(pcr_vals, e.get("vol_pcr_min")),
            "iv_skew_max_pct": percentile_rank(skew_vals, e.get("iv_skew_max")),
            "iv_skew_min_pct": percentile_rank(skew_vals, e.get("iv_skew_min")),
            "pcr_sample_count": len(pcr_vals),
            "skew_sample_count": len(skew_vals),
            "pcr_data_insufficient": len(pcr_vals) < MIN_PCT_SAMPLE,
            "skew_data_insufficient": len(skew_vals) < MIN_PCT_SAMPLE,
            "pcr_src": ("option_daily" if u == "000852" else "style_drift.db"),
            "skew_src": ("option_daily" if u == "000852" else "style_drift.db"),
            "ext": e,
        }
    return out


# ============================================================
# 中证1000(MO) 日频历史回补（Tushare 原始行情 → 统一 option_metrics 自算）
# ------------------------------------------------------------
# 口径：调用 option_metrics_history._compute_one（与 4 ETF 日频同一函数），
#       name_keys=['中证1000']（中金所股指期权中文名），exchange='CFFEX'。
#       opt_daily 一次可拉区间但上限 15000 行，故按交易日分块 + 超限自动二分。
# 代理 IP 超限（ttx.dailyfetch.top 共享配额，重置窗口约 30s）：
#       探活本身也耗配额 → 进入 proxy 后不再重复探活；
#       数据调用统一走 _patient_call（分钟级退避），每次成功调用后留 gap 秒。
# 断点续跑：已存在 option_daily 的交易日默认跳过（force=True 可重算）。
# ============================================================
_IP_WAITS = (30, 45, 60, 90, 120, 150, 180, 210)


def _ensure_proxy(max_tries: int = 8, wait: float = 8.0):
    """探活到 Tushare 代理模式（代理版 token 才有数据；官方版返回空）。

    探活本身也消耗代理配额，故：进程内已处于 proxy 时直接复用，不重复探活。
    """
    import data_hub.tushare_config as tc
    from data_hub.sources.tushare_source import TushareSource
    s = None
    for _ in range(max_tries):
        if s is None and tc.get_pro_mode() == "proxy":
            return TushareSource(), "proxy"
        if s is not None and tc.get_pro_mode() == "proxy":
            return s, "proxy"
        tc.reset_tushare_pro()
        s = TushareSource()
        s._get_pro()
        if tc.get_pro_mode() == "proxy":
            return s, "proxy"
        time.sleep(wait)
    return s, tc.get_pro_mode()


def _patient_call(src, api: str, tries: int = 8, gap: float = 6.0,
                  verbose: bool = False, **params):
    """代理 IP 超限时按分钟级退避重试；每次成功调用后留 gap 秒间隔。"""
    last = None
    for i in range(tries):
        try:
            r = src._call_pro_api(api, **params)
            if gap:
                time.sleep(gap)
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            msg = str(e)
            if ("ip超限" in msg) or ("多个ip" in msg):
                w = _IP_WAITS[min(i, len(_IP_WAITS) - 1)]
                if verbose:
                    print("  [mo-backfill] %s IP 超限，等待 %ds 重试(%d/%d)"
                          % (api, w, i + 1, tries), flush=True)
                time.sleep(w)
                continue
            raise
    raise last


def _chunk(seq, n):
    return [seq[i:i + n] for i in range(0, len(seq), n)]


def _existing_days(db_path: str = DB_PATH) -> set:
    try:
        ensure_tables(db_path)
        conn = _connect(db_path)
        try:
            return {r[0] for r in conn.execute(
                "SELECT trade_date FROM option_daily WHERE underlying='000852'").fetchall()}
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return set()


def backfill_mo_daily(start: str = "20250102", end: str = "20261022",
                      chunk_days: int = 15, force: bool = False,
                      verbose: bool = True, basic=None, spot_map=None,
                      src=None) -> Dict[str, Any]:
    """回补中证1000(MO) 日频 vol_pcr / iv_skew / atm_iv 等 → option_daily 表。"""
    import pandas as pd  # noqa: F811
    from data_hub.collectors.option_metrics_history import _compute_one

    report = {"written": 0, "days": 0, "start": start, "end": end,
              "mode": None, "errors": [], "skipped": [], "chunks": 0,
              "days_total": 0, "days_existing": 0}
    if src is None:
        src, mode = _ensure_proxy()
    else:
        import data_hub.tushare_config as _tc
        mode = _tc.get_pro_mode()
    report["mode"] = mode
    if mode != "proxy":
        report["errors"].append("Tushare 未进入 proxy 模式（mode=%s）" % mode)
        return report

    # 1) 交易日历
    cal = _patient_call(src, "trade_cal", exchange="SSE", start_date=start,
                        end_date=end, verbose=verbose)
    if cal is None or cal.empty:
        report["errors"].append("trade_cal 为空")
        return report
    tds = sorted(str(d) for d in cal[cal["is_open"] == 1]["cal_date"].tolist())
    tds = [d for d in tds if start <= d <= end]
    report["days_total"] = len(tds)
    if verbose:
        print("  [mo-backfill] 交易日 %d 天（%s ~ %s）mode=%s"
              % (len(tds), tds[0] if tds else "-", tds[-1] if tds else "-", mode),
              flush=True)

    # 断点续跑：跳过已有
    done = set() if force else _existing_days()
    report["days_existing"] = len([d for d in tds if d in done])
    todo_days = [d for d in tds if d not in done]
    if verbose and done:
        print("  [mo-backfill] 已有 %d 天，待补 %d 天"
              % (report["days_existing"], len(todo_days)), flush=True)

    # 2) CFFEX 合约信息（一次；允许外部预取复用）
    if basic is None:
        basic = _patient_call(
            src, "opt_basic", exchange="CFFEX", verbose=verbose,
            fields="ts_code,name,exercise_price,list_date,delist_date,maturity_date,opt_type")
    if basic is None or basic.empty:
        report["errors"].append("CFFEX opt_basic 为空")
        return report
    basic = basic.copy()
    basic["list_date"] = basic["list_date"].fillna("")
    basic["delist_date"] = basic["delist_date"].fillna("99999999")
    mo_basic = basic[basic["name"].astype(str).str.contains("中证1000", na=False)]
    if verbose:
        print("  [mo-backfill] CFFEX 合约 %d 条，其中中证1000 %d 条"
              % (len(basic), len(mo_basic)), flush=True)
    if mo_basic.empty:
        report["errors"].append("CFFEX opt_basic 无中证1000 合约")
        return report

    # 3) 现货（中证1000 指数日线，一次；允许外部预取复用）
    if spot_map is None:
        spot_df = _patient_call(src, "index_daily", ts_code="000852.SH",
                                start_date=start, end_date=end, verbose=verbose)
        spot_map = {}
        if spot_df is not None and not spot_df.empty:
            for _, r in spot_df.iterrows():
                spot_map[str(r["trade_date"])] = float(r["close"])
    if verbose:
        print("  [mo-backfill] 现货 000852.SH %d 天" % len(spot_map), flush=True)
    if not spot_map:
        report["errors"].append("index_daily 000852.SH 为空")
        return report

    # 4) 分块拉 opt_daily（上限 15000 行，超限自动二分）+ 即算即写
    def _cp(nm):
        nm = str(nm)
        return "P" if "沽" in nm else ("C" if "购" in nm else "")

    def _compute_write(days):
        day_df = daily_all[daily_all["trade_date"].isin(days)]
        if day_df.empty:
            return 0
        rows = []
        for td in days:
            spot = spot_map.get(td)
            if not spot:
                report["skipped"].append("%s(无现货)" % td)
                continue
            day = day_df[day_df["trade_date"] == td]
            if day.empty:
                report["skipped"].append("%s(无期权日线)" % td)
                continue
            alive = mo_basic[(mo_basic["list_date"] <= td) & (mo_basic["delist_date"] >= td)]
            if alive.empty:
                report["skipped"].append("%s(无存续合约)" % td)
                continue
            merged = day.merge(
                alive[["ts_code", "name", "exercise_price", "maturity_date", "opt_type"]],
                on="ts_code", how="inner")
            if merged.empty:
                report["skipped"].append("%s(合并为空)" % td)
                continue
            merged = merged.copy()
            merged["call_put"] = merged["name"].map(_cp)
            row = _compute_one(merged, ["中证1000"], "CFFEX", spot, td)
            if not row:
                report["skipped"].append("%s(计算为空)" % td)
                continue
            row["underlying"] = "000852"
            rows.append(row)
        if rows:
            n = upsert_option_daily_rows(rows)
            report["written"] += n
            report["days"] += len(rows)
            if verbose:
                print("  [mo-backfill] +%d 天（累计 %d 天）"
                      % (len(rows), report["days"]), flush=True)
        return len(rows)

    daily_all = None
    stack = [(g[0], g[-1]) for g in _chunk(todo_days, chunk_days)]
    guard = 0
    while stack and guard < 900:
        guard += 1
        d1, d2 = stack.pop(0)
        try:
            df = _patient_call(src, "opt_daily", start_date=d1, end_date=d2,
                               exchange="CFFEX", verbose=verbose)
        except Exception as e:  # noqa: BLE001
            report["errors"].append("opt_daily %s~%s 失败: %s" % (d1, d2, str(e)[:80]))
            continue
        if df is None or df.empty:
            report["skipped"].append("%s~%s(空)" % (d1, d2))
            continue
        df = df.copy()
        df["trade_date"] = df["trade_date"].astype(str)
        if len(df) >= 15000 and d1 != d2:
            sub = [d for d in todo_days if d1 <= d <= d2]
            if len(sub) > 1:
                h = len(sub) // 2
                stack.insert(0, (sub[h], d2))
                stack.insert(0, (d1, sub[h - 1]))
                continue
        daily_all = df if daily_all is None else pd.concat([daily_all, df], ignore_index=True)
        report["chunks"] += 1
        _compute_write([d for d in todo_days if d1 <= d <= d2])
        daily_all = None  # 释放，避免内存累积

    if verbose:
        print("  [mo-backfill] 完成：写入 %d 天，跳过 %d 条，错误 %d 条"
              % (report["days"], len(report["skipped"]), len(report["errors"])),
              flush=True)
    return report


# ============================================================
# MO 日频历史回补（中金所官方日行情 XML）
# ------------------------------------------------------------
# 走 HTTP 直连 cffex.com.cn，不占用 Tushare 代理配额；
# 计算仍走 compute_metrics_from_chain（与盘中表同一函数），保证口径一致。
# ============================================================
_CFFEX_DAILY_URL = "http://www.cffex.com.cn/sj/hqsj/rtj/{yyyymm}/{dd}/index.xml"


def fetch_cffex_daily_chain(td: str, prefix: str = "MO", retries: int = 3,
                            timeout: int = 20) -> List[Dict[str, Any]]:
    """中金所官方日行情（某交易日全部合约）→ 标准合约 list[dict]。

    XML 字段：instrumentid / closeprice / settlementprice / volume /
             openinterest / turnover / expiredate
    价格取 closeprice（<=0 时退回 settlementprice），与 Tushare opt_daily close 口径一致。
    """
    try:
        import requests
    except Exception:  # pragma: no cover
        return []
    url = _CFFEX_DAILY_URL.format(yyyymm=td[:6], dd=td[6:8])
    text = None
    for i in range(retries):
        try:
            r = requests.get(url, timeout=timeout)
            r.encoding = "gb2312"
            if r.status_code == 200 and r.text and "<dailydata>" in r.text:
                text = r.text
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.8 * (i + 1))
    if not text:
        return []
    pat = re.compile(r"^([A-Za-z]+)(\d{4})-([CP])-(\d+(?:\.\d+)?)$")
    chain: List[Dict[str, Any]] = []
    for blk in re.findall(r"<dailydata>(.*?)</dailydata>", text, re.S):
        def _g(tag, _b=blk):
            m = re.search(r"<%s>(.*?)</%s>" % (tag, tag), _b, re.S)
            return m.group(1).strip() if m else ""
        code = _g("instrumentid")
        m = pat.match(code)
        if not m:
            continue
        if _g("productid").upper() != prefix.upper():
            continue
        close = _f(_g("closeprice"))
        settle = _f(_g("settlementprice"))
        px = close if (close and close > 0) else settle
        expiry = _g("expiredate") or _third_friday(m.group(2))
        try:
            dte = (datetime.strptime(expiry, "%Y%m%d").date()
                   - datetime.strptime(td, "%Y%m%d").date()).days
        except Exception:  # noqa: BLE001
            dte = None
        chain.append({
            "ts_code": code, "name": code, "call_put": m.group(3).upper(),
            "exercise_price": _f(m.group(4)), "maturity_date": expiry, "dte": dte,
            "close": px, "vol": _f(_g("volume")), "oi": _f(_g("openinterest")),
            "amount": _f(_g("turnover")), "source": "cffex_daily_xml",
        })
    return chain


def _ak_spot_map(start: str, end: str) -> Dict[str, float]:
    """AKShare 中证1000 指数日线（一次调用拿全历史）→ {YYYYMMDD: close}。"""
    out: Dict[str, float] = {}
    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol="sh000852")
    except Exception:  # noqa: BLE001
        return out
    if df is None or df.empty:
        return out
    for _, r in df.iterrows():
        d = str(r["date"]).replace("-", "")[:8]
        try:
            c = float(r["close"])
        except (TypeError, ValueError):
            continue
        if start <= d <= end and c > 0:
            out[d] = c
    return out


def backfill_mo_daily_cffex(start: str = "20250102", end: str = "20261022",
                            force: bool = False, verbose: bool = True,
                            spot_map: Optional[Dict[str, float]] = None,
                            gap: float = 0.30,
                            flush_every: int = 30) -> Dict[str, Any]:
    """中证1000(MO) 日频 vol_pcr / iv_skew / atm_iv 等 → option_daily 表。

    数据源：中金所官方日行情 XML + AKShare 000852 现货；计算走
    compute_metrics_from_chain（与盘中 option_intraday 同一函数）。幂等：已存在日跳过。
    """
    ensure_tables()
    rep: Dict[str, Any] = {"written": 0, "days": 0, "start": start, "end": end,
                           "source": "cffex_daily_xml", "errors": [], "skipped": [],
                           "days_total": 0, "days_existing": 0}
    if spot_map is None:
        spot_map = _ak_spot_map(start, end)
    rep["spot_days"] = len(spot_map)
    if not spot_map:
        rep["errors"].append("AKShare 000852 现货为空")
        return rep
    tds = sorted(d for d in spot_map if start <= d <= end)
    rep["days_total"] = len(tds)
    done = set() if force else _existing_days()
    rep["days_existing"] = len([d for d in tds if d in done])
    todo = [d for d in tds if d not in done]
    if verbose:
        print("  [mo-cffex] 交易日 %d 天，已有 %d 天，待补 %d 天"
              % (len(tds), rep["days_existing"], len(todo)), flush=True)
    rows: List[Dict[str, Any]] = []

    def _flush():
        if rows:
            rep["written"] += upsert_option_daily_rows(rows)
            rep["days"] += len(rows)
            if verbose:
                print("  [mo-cffex] +%d 天（累计 %d 天）" % (len(rows), rep["days"]),
                      flush=True)
            rows.clear()

    for i, td in enumerate(todo):
        chain = fetch_cffex_daily_chain(td)
        if not chain:
            rep["skipped"].append("%s(无日行情)" % td)
            continue
        met = compute_metrics_from_chain(chain, spot_map[td], "CFFEX", td)
        if not met:
            rep["skipped"].append("%s(计算为空)" % td)
            continue
        row = dict(met)
        row["trade_date"] = td
        row["underlying"] = "000852"
        row["data_source"] = "cffex_daily_xml"
        rows.append(row)
        if len(rows) >= flush_every:
            _flush()
        time.sleep(gap)
    _flush()
    if verbose:
        print("  [mo-cffex] 完成：写入 %d 天，跳过 %d 条，错误 %d 条"
              % (rep["days"], len(rep["skipped"]), len(rep["errors"])), flush=True)
    return rep
