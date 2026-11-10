"""
期权全量链采集器（统一入口）
============================
为主人的「5 大宽基」期权指标（25Δ偏度 / IV / PCR / 期限结构）提供
**当日全量期权链 + 标的现货价**的统一、标准化取数入口，禁止各业务脚本
各自直连 akshare/tushare 重写采集。

5 大宽基（剔除上证50）：
    沪深300   -> SSE  ETF 期权  opt_code 前缀 OP510300.SH ，现货 510300
    中证500   -> SSE  ETF 期权  opt_code 前缀 OP510500.SH ，现货 510500
    创业板    -> SZSE ETF 期权  opt_code 前缀 OP159915.SZ ，现货 159915
    科创50    -> SSE  ETF 期权  opt_code 前缀 OP588000.SH ，现货 588000
    中证1000  -> CFFEX 股指期权 ts_code 前缀 MO            ，现货指数 000852

四级降级（宁慢勿空，绝不造假）：
    Tier 1: Tushare opt_basic + opt_daily（全量、含精确到期日/持仓量，已验证最稳）
    Tier 2: AKShare 期权 T 型看板（仅沪市 300/500/科创50；深市与1000看板无数据）
    Tier 3: Coze 网关 / 新浪 hq 快照（兜底，尽力而为）
    Tier 4: 全部失败 -> 返回空 DataFrame + spot=None，由上层标 N/A

返回的标准化 DataFrame 列（可直接喂 data_hub.option_metrics）：
    ts_code, call_put(C/P), exercise_price, maturity_date(YYYYMMDD),
    close, vol, oi, amount, dte, source
另在便捷函数中返回现货价 spot_price。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from ..config import logger


# ============================================================
# 标的注册表：5 大宽基
# ============================================================

OPTION_CHAIN_TARGETS: Dict[str, Dict] = {
    "300ETF": {
        "label": "沪深300",
        "exchange": "SSE",
        # tushare 过滤方式：('opt_prefix', 前缀) 或 ('ts_prefix', 前缀)
        "filter": ("opt_prefix", "OP510300"),
        "spot_kind": "etf",
        "spot_code": "510300",
        "board_name": "华泰柏瑞沪深300ETF期权",
        "strike_scale": 1.0,   # ETF 行权价即元
        "multiplier": 10000,
    },
    "500ETF": {
        "label": "中证500",
        "exchange": "SSE",
        "filter": ("opt_prefix", "OP510500"),
        "spot_kind": "etf",
        "spot_code": "510500",
        "board_name": "南方中证500ETF期权",
        "strike_scale": 1.0,
        "multiplier": 10000,
    },
    "创业板ETF": {
        "label": "创业板",
        "exchange": "SZSE",
        "filter": ("opt_prefix", "OP159915"),
        "spot_kind": "etf",
        "spot_code": "159915",
        "board_name": "易方达创业板ETF期权",
        "strike_scale": 1.0,
        "multiplier": 10000,
    },
    "科创50ETF": {
        "label": "科创50",
        "exchange": "SSE",
        "filter": ("opt_prefix", "OP588000"),
        "spot_kind": "etf",
        "spot_code": "588000",
        "board_name": "华夏科创50ETF期权",
        "strike_scale": 1.0,
        "multiplier": 10000,
    },
    "中证1000": {
        "label": "中证1000",
        "exchange": "CFFEX",
        "filter": ("ts_prefix", "MO"),
        "spot_kind": "index",
        "spot_code": "sh000852",
        "spot_ts_code": "000852.SH",
        "board_name": "中证1000股指期权",
        "strike_scale": 1.0,   # 指数点，行权价已是点
        "multiplier": 100,     # MO 合约乘数 100 元/点
    },
}

# 标准输出列
CHAIN_COLUMNS = [
    "ts_code", "call_put", "exercise_price", "maturity_date",
    "close", "vol", "oi", "amount", "dte", "source",
]


def _standardize_tushare(merged: pd.DataFrame, trade_date: str,
                         strike_scale: float) -> pd.DataFrame:
    """将 opt_basic + opt_daily 合并结果标准化。"""
    if merged is None or merged.empty:
        return pd.DataFrame(columns=CHAIN_COLUMNS)

    td = pd.Timestamp(trade_date)
    rows = []
    for _, r in merged.iterrows():
        try:
            cp = str(r.get("call_put", "")).strip().upper()
            if cp not in ("C", "P"):
                name = str(r.get("name", ""))
                cp = "C" if ("购" in name or "看涨" in name) else "P"
            maturity = str(r.get("maturity_date", "")).split(".")[0]
            strike = float(r.get("exercise_price", 0) or 0) * strike_scale
            close = float(r.get("close", 0) or 0)
            if strike <= 0 or close <= 0 or not re.match(r"\d{8}", maturity):
                continue
            dte = max((pd.Timestamp(maturity) - td).days, 0)
            rows.append({
                "ts_code": str(r.get("ts_code", "")),
                "call_put": cp,
                "exercise_price": strike,
                "maturity_date": maturity,
                "close": close,
                "vol": float(r.get("vol", 0) or 0),
                "oi": float(r.get("oi", 0) or 0),
                "amount": float(r.get("amount", 0) or 0),
                "dte": dte,
                "source": "tushare",
            })
        except Exception:
            continue
    return pd.DataFrame(rows, columns=CHAIN_COLUMNS)


def _fetch_tushare_chain(cfg: Dict, trade_date: str) -> pd.DataFrame:
    """Tier1: Tushare opt_basic + opt_daily 全量链。"""
    try:
        from ..sources.tushare_source import TushareSource
    except Exception as e:  # pragma: no cover
        logger.debug(f"[option_chain] TushareSource 不可用: {e}")
        return pd.DataFrame(columns=CHAIN_COLUMNS)

    try:
        src = TushareSource()
        pro = src._get_pro()
        exch = cfg["exchange"]
        kind, prefix = cfg["filter"]

        src._rate_limit()
        basic = pro.opt_basic(exchange=exch)
        if basic is None or basic.empty:
            return pd.DataFrame(columns=CHAIN_COLUMNS)

        if kind == "opt_prefix":
            basic = basic[basic["opt_code"].astype(str).str.startswith(prefix)]
        else:  # ts_prefix（CFFEX: MO）
            basic = basic[basic["ts_code"].astype(str).str.startswith(prefix)]
        # 只保留未到期合约
        basic = basic[basic["maturity_date"].astype(str) >= trade_date]
        if basic.empty:
            return pd.DataFrame(columns=CHAIN_COLUMNS)

        # opt_daily 偶发空响应（多标的串行/代理限流时），重试 2 次
        daily = None
        for _retry in range(3):
            src._rate_limit()
            try:
                daily = pro.opt_daily(exchange=exch, start_date=trade_date, end_date=trade_date)
            except Exception as _de:
                logger.debug(f"[option_chain] opt_daily {cfg['label']} 重试{_retry+1}: {_de}")
                daily = None
            if daily is not None and not daily.empty:
                break
        if daily is None or daily.empty:
            return pd.DataFrame(columns=CHAIN_COLUMNS)

        merged = basic.merge(daily, on="ts_code", how="inner")
        df = _standardize_tushare(merged, trade_date, cfg.get("strike_scale", 1.0))
        if not df.empty:
            logger.info(f"[option_chain] Tushare {cfg['label']}: {len(df)} 条当日合约")
        return df
    except Exception as e:
        logger.debug(f"[option_chain] Tushare 取数失败 {cfg['label']}: {e}")
        return pd.DataFrame(columns=CHAIN_COLUMNS)


def _fetch_board_chain(cfg: Dict, trade_date: str) -> pd.DataFrame:
    """Tier2: AKShare T 型看板（仅沪市部分标的，无 oi，单月）。"""
    if cfg["exchange"] != "SSE":
        return pd.DataFrame(columns=CHAIN_COLUMNS)
    try:
        from .option_board import get_option_board
        from calendar import monthrange

        ym = pd.Timestamp(trade_date).strftime("%y%m")
        board = get_option_board(symbol=cfg["board_name"], end_month=ym)
        if board is None or board.empty:
            ym2 = (pd.Timestamp(trade_date) + pd.offsets.MonthBegin(1)).strftime("%y%m")
            board = get_option_board(symbol=cfg["board_name"], end_month=ym2)
        if board is None or board.empty:
            return pd.DataFrame(columns=CHAIN_COLUMNS)

        td = pd.Timestamp(trade_date)
        rows = []
        for _, r in board.iterrows():
            m = re.match(
                r"(\d{6})([CP])(\d{2})(\d{2})[A-Z]?(\d+)",
                str(r.get("合约交易代码", "")),
            )
            if not m:
                continue
            _, cp, yy, mm, _ = m.groups()
            _, last_day = monthrange(2000 + int(yy), int(mm))
            fridays = [
                d for d in range(1, last_day + 1)
                if pd.Timestamp(2000 + int(yy), int(mm), d).weekday() == 4
            ]
            # T 型看板为当月，第四周周三为行权日；用第四周五近似到期边界
            maturity = pd.Timestamp(2000 + int(yy), int(mm), fridays[3]).strftime("%Y%m%d")
            rows.append({
                "ts_code": str(r.get("合约交易代码", "")),
                "call_put": cp,
                "exercise_price": float(r.get("行权价", 0) or 0),
                "maturity_date": maturity,
                "close": float(r.get("当前价", 0) or 0),
                "vol": float(r.get("数量", 0) or 0),
                "oi": 0.0,
                "amount": 0.0,
                "dte": max((pd.Timestamp(maturity) - td).days, 0),
                "source": "akshare_board",
            })
        df = pd.DataFrame(rows, columns=CHAIN_COLUMNS)
        df = df[(df["close"] > 0) & (df["exercise_price"] > 0)]
        if not df.empty:
            logger.info(f"[option_chain] 看板 {cfg['label']}: {len(df)} 条")
        return df
    except Exception as e:
        logger.debug(f"[option_chain] 看板取数失败 {cfg['label']}: {e}")
        return pd.DataFrame(columns=CHAIN_COLUMNS)


def get_spot_price(cfg: Dict) -> Optional[float]:
    """获取标的现货价（ETF 走东财，指数走新浪）。"""
    try:
        if cfg["spot_kind"] == "etf":
            from .option_board import get_etf_spot_em
            df = get_etf_spot_em(etf_codes=[cfg["spot_code"]])
            if df is not None and not df.empty:
                col = "最新价" if "最新价" in df.columns else df.columns[2]
                val = pd.to_numeric(df.iloc[0].get(col), errors="coerce")
                if pd.notna(val) and float(val) > 0:
                    return float(val)
        else:
            # 指数现货：优先 tushare 指数日线（量级准确、可对齐期权交易日），
            # 新浪实时表曾返回脏值（中证1000 返回 3658，真值约 6780），仅作降级。
            ts_code = cfg.get("spot_ts_code")
            tdate = cfg.get("_trade_date")
            if ts_code:
                try:
                    from ..sources.tushare_source import TushareSource
                    pro = TushareSource()._get_pro()
                    end = tdate or datetime.now().strftime("%Y%m%d")
                    start = (datetime.strptime(end, "%Y%m%d") - pd.Timedelta(days=10)).strftime("%Y%m%d")
                    d = pro.index_daily(ts_code=ts_code, start_date=start, end_date=end)
                    if d is not None and len(d):
                        d = d.sort_values("trade_date")
                        val = pd.to_numeric(d.iloc[-1].get("close"), errors="coerce")
                        if pd.notna(val) and float(val) > 0:
                            return float(val)
                except Exception as e:
                    logger.debug(f"[option_chain] tushare指数现货失败 {cfg['label']}: {e}")
            from .option_board import get_index_spot_sina
            df = get_index_spot_sina(index_codes=[cfg["spot_code"]])
            if df is not None and not df.empty:
                row = df.iloc[0]
                code_col = next((c for c in ("代码", "code", "symbol") if c in df.columns), None)
                if code_col is not None:
                    want = cfg["spot_code"].lstrip("shsz")
                    hit = df[df[code_col].astype(str).str.contains(want)]
                    if len(hit):
                        row = hit.iloc[0]
                for col in ("最新价", "close", "现价"):
                    if col in df.columns:
                        val = pd.to_numeric(row.get(col), errors="coerce")
                        if pd.notna(val) and float(val) > 0:
                            return float(val)
    except Exception as e:
        logger.debug(f"[option_chain] 现货价失败 {cfg['label']}: {e}")
    return None


def get_option_chain(
    target: str,
    trade_date: Optional[str] = None,
    *,
    with_spot: bool = False,
):
    """
    统一入口：获取单个宽基标的的当日全量期权链。

    Args:
        target: 标的键名，见 OPTION_CHAIN_TARGETS
                （300ETF / 500ETF / 创业板ETF / 科创50ETF / 中证1000），
                也接受中文别名（沪深300/中证500/创业板/科创50/中证1000）。
        trade_date: YYYYMMDD，默认当日。
        with_spot: True 时返回 (chain_df, spot_price)，否则只返回 chain_df。

    Returns:
        pd.DataFrame（标准列 CHAIN_COLUMNS）；无数据返回空 DataFrame。
        with_spot=True 时返回 (df, spot_or_None)。
    """
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y%m%d")

    cfg = _resolve_target(target)
    if cfg is None:
        logger.warning(f"[option_chain] 未知标的: {target}")
        empty = pd.DataFrame(columns=CHAIN_COLUMNS)
        return (empty, None) if with_spot else empty

    # Tier1 tushare
    chain = _fetch_tushare_chain(cfg, trade_date)
    # Tier2 看板（仅当 tushare 空；看板无 oi 且单月，作为兜底）
    if chain.empty:
        chain = _fetch_board_chain(cfg, trade_date)

    if with_spot:
        cfg = dict(cfg)
        cfg["_trade_date"] = trade_date
    spot = get_spot_price(cfg) if with_spot else None
    if with_spot:
        return chain, spot
    return chain


def _resolve_target(target: str) -> Optional[Dict]:
    """解析标的键名/中文别名。"""
    if target in OPTION_CHAIN_TARGETS:
        return OPTION_CHAIN_TARGETS[target]
    alias = {
        "沪深300": "300ETF", "300": "300ETF", "510300": "300ETF",
        "中证500": "500ETF", "500": "500ETF", "510500": "500ETF",
        "创业板": "创业板ETF", "创业板指": "创业板ETF", "159915": "创业板ETF",
        "科创50": "科创50ETF", "科创板50": "科创50ETF", "588000": "科创50ETF",
        "1000": "中证1000", "中证1000指数": "中证1000", "MO": "中证1000",
    }
    key = alias.get(str(target).strip())
    return OPTION_CHAIN_TARGETS.get(key) if key else None


def get_all_option_chains(
    trade_date: Optional[str] = None,
    *,
    with_spot: bool = True,
) -> Dict[str, Dict]:
    """
    一次性获取 5 大宽基全部期权链。

    Returns:
        {target_key: {"label", "chain": DataFrame, "spot": float|None,
                      "count": int, "source": str}}
    """
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y%m%d")
    out: Dict[str, Dict] = {}
    for key, cfg in OPTION_CHAIN_TARGETS.items():
        chain, spot = get_option_chain(key, trade_date, with_spot=True)
        out[key] = {
            "label": cfg["label"],
            "chain": chain,
            "spot": spot,
            "count": 0 if chain is None else len(chain),
            "source": (chain.iloc[0]["source"] if chain is not None and not chain.empty else None),
        }
    return out
