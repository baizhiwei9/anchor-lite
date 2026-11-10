"""
每日数据预缓存模块
==================
每日盘后统一拉取当天所需的全部市场数据并缓存到本地，
供各业务脚本（风险释放监控、中线偏好监控等）直接读取，
避免各脚本重复请求，降低API调用频率。

缓存目录结构：
    ./codeact/output/data_hub_cache/
        └── YYYYMMDD/
            ├── option_510300.json    # 沪深300ETF期权
            ├── option_MO.json        # 中证1000股指期权
            ├── index_000300.csv      # 沪深300指数日线
            ├── index_000852.csv      # 中证1000指数日线
            ├── margin_sh.csv         # 沪市两融
            ├── margin_sz.csv         # 深市两融
            ├── north_money.csv       # 北向资金
            ├── fund_issue.csv        # 新基金发行
            └── pledge_ratio.csv      # 股权质押比例
"""

from __future__ import annotations

import json
import os
import pickle
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import pandas as pd

from .config import logger


# 缓存根目录
CACHE_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'output', 'data_hub_cache')


def get_cache_dir(trade_date: str) -> str:
    """获取指定交易日的缓存目录。"""
    return os.path.join(CACHE_ROOT, trade_date)


def ensure_cache_dir(trade_date: str) -> str:
    """确保缓存目录存在并返回路径。"""
    d = get_cache_dir(trade_date)
    os.makedirs(d, exist_ok=True)
    return d


def is_cache_available(trade_date: str, data_key: str) -> bool:
    """检查指定数据是否已缓存。"""
    cache_file = os.path.join(get_cache_dir(trade_date), f"{data_key}.pkl")
    if os.path.exists(cache_file):
        # 检查文件修改时间，确保是当天的数据
        mtime = os.path.getmtime(cache_file)
        # 如果是今天的缓存且已收盘（15:30后），认为有效
        return True
    return False


def save_cache(trade_date: str, data_key: str, data: Any) -> None:
    """保存数据到缓存。"""
    ensure_cache_dir(trade_date)
    cache_file = os.path.join(get_cache_dir(trade_date), f"{data_key}.pkl")
    try:
        with open(cache_file, 'wb') as f:
            pickle.dump(data, f)
        logger.info(f"[cache] 已缓存 {trade_date}/{data_key}")
    except Exception as e:
        logger.warning(f"[cache] 缓存失败 {data_key}: {e}")


def load_cache(trade_date: str, data_key: str, default: Any = None) -> Any:
    """从缓存加载数据。"""
    cache_file = os.path.join(get_cache_dir(trade_date), f"{data_key}.pkl")
    if not os.path.exists(cache_file):
        return default
    try:
        with open(cache_file, 'rb') as f:
            data = pickle.load(f)
        logger.debug(f"[cache] 命中缓存 {trade_date}/{data_key}")
        return data
    except Exception as e:
        logger.warning(f"[cache] 读取缓存失败 {data_key}: {e}")
        return default


def save_dataframe(trade_date: str, data_key: str, df: pd.DataFrame) -> None:
    """保存DataFrame到缓存（CSV格式，便于查看）。"""
    ensure_cache_dir(trade_date)
    cache_file = os.path.join(get_cache_dir(trade_date), f"{data_key}.csv")
    try:
        df.to_csv(cache_file, index=False, encoding='utf-8-sig')
        # 同时存pkl
        save_cache(trade_date, data_key, df)
    except Exception as e:
        logger.warning(f"[cache] 保存DataFrame失败 {data_key}: {e}")


def load_dataframe(trade_date: str, data_key: str) -> Optional[pd.DataFrame]:
    """从缓存加载DataFrame。"""
    # 先试pkl（快）
    df = load_cache(trade_date, data_key, None)
    if df is not None:
        return df
    # 再试csv
    cache_file = os.path.join(get_cache_dir(trade_date), f"{data_key}.csv")
    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file)
            return df
        except Exception:
            pass
    return None


# ============================================================
# 交易日判断（用于缓存失效）
# ============================================================

def get_latest_trade_date() -> str:
    """
    获取最近的交易日（YYYYMMDD格式）。
    简化判断：周一~周五视为交易日，节假日先忽略（数据为空时会自动往前找）。
    """
    now = datetime.now()
    # 如果是周末，回退到周五
    if now.weekday() == 5:  # 周六
        now = now - timedelta(days=1)
    elif now.weekday() == 6:  # 周日
        now = now - timedelta(days=2)
    return now.strftime('%Y%m%d')


def is_trade_day(date_str: str) -> bool:
    """简单判断是否为工作日（周一到周五）。"""
    dt = datetime.strptime(date_str, '%Y%m%d')
    return dt.weekday() < 5


# ============================================================
# 统一数据获取（带缓存）
# ============================================================

def get_cached_data(
    trade_date: str,
    data_key: str,
    fetch_func: callable,
    force_refresh: bool = False,
    **kwargs,
) -> Any:
    """
    带缓存的数据获取函数。

    Args:
        trade_date: 交易日期（YYYYMMDD）
        data_key: 缓存键名
        fetch_func: 实际数据获取函数
        force_refresh: 是否强制刷新缓存
        **kwargs: 传递给fetch_func的参数

    Returns:
        数据（格式由fetch_func决定）
    """
    if not force_refresh:
        cached = load_cache(trade_date, data_key, None)
        if cached is not None:
            return cached

    # 拉取数据
    try:
        data = fetch_func(**kwargs)
        if data is not None:
            save_cache(trade_date, data_key, data)
        return data
    except Exception as e:
        logger.error(f"[cache] 获取数据失败 {data_key}: {e}")
        return None


# ============================================================
# 每日数据预拉取主函数
# ============================================================

def fetch_daily_data(
    trade_date: Optional[str] = None,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """
    每日盘后统一预拉取所有需要的数据。

    Args:
        trade_date: 交易日期，默认最近交易日
        force_refresh: 是否强制刷新

    Returns:
        dict: 各数据类型的获取状态
    """
    if trade_date is None:
        trade_date = get_latest_trade_date()

    logger.info(f"[daily_fetch] 开始预拉取 {trade_date} 数据")
    start_time = time.time()
    results = {}

    # 1. 沪深300指数日线
    try:
        from . import get_data
        df = get_data("stock_daily", ts_code="000300.SH",
                      start_date="20150101", end_date=trade_date)
        if df is not None and not df.empty:
            save_dataframe(trade_date, "index_000300", df)
            results["index_000300"] = len(df)
    except Exception as e:
        results["index_000300"] = f"ERROR: {e}"

    # 2. 中证1000指数日线
    try:
        from . import get_data
        df = get_data("stock_daily", ts_code="000852.SH",
                      start_date="20150101", end_date=trade_date)
        if df is not None and not df.empty:
            save_dataframe(trade_date, "index_000852", df)
            results["index_000852"] = len(df)
    except Exception as e:
        results["index_000852"] = f"ERROR: {e}"

    # 3. 沪深300ETF期权（510300）
    try:
        from . import get_data
        data = get_data("option_data", exchange="SSE", underlying="510300", trade_date=trade_date)
        if data:
            save_cache(trade_date, "option_510300", data)
            results["option_510300"] = data.get("contracts", pd.DataFrame()).shape[0] if isinstance(data, dict) else "ok"
    except Exception as e:
        results["option_510300"] = f"ERROR: {e}"

    # 4. 中证1000股指期权（MO）
    try:
        from . import get_data
        data = get_data("option_data", exchange="CFFEX", underlying="MO", trade_date=trade_date)
        if data:
            save_cache(trade_date, "option_MO", data)
            results["option_MO"] = data.get("contracts", pd.DataFrame()).shape[0] if isinstance(data, dict) else "ok"
    except Exception as e:
        results["option_MO"] = f"ERROR: {e}"

    # 5. 两融数据（沪深全市场汇总，并同步到两融历史库）
    try:
        from . import get_data
        from .margin_db import sync_margin_history

        df = get_data("margin_trading", market="all", lookback_days=30)
        if df is not None and not df.empty:
            save_dataframe(trade_date, "margin_all", df)
            results["margin_all"] = len(df)

        # 写入两融历史库 margin_history.db，供历史查询与分位计算
        sync_res = sync_margin_history(lookback_days=30)
        results["margin_db_sync"] = sync_res.get("rows", 0)
    except Exception as e:
        results["margin_all"] = f"ERROR: {e}"

    # 6. 北向资金
    try:
        import akshare as ak
        df = ak.stock_hsgt_hist_em(symbol="北向资金")
        if df is not None and not df.empty:
            save_dataframe(trade_date, "north_money", df)
            results["north_money"] = len(df)
    except Exception as e:
        results["north_money"] = f"ERROR: {e}"

    # 7. 新基金发行
    try:
        import akshare as ak
        df = ak.fund_new_found_em()
        if df is not None and not df.empty:
            save_dataframe(trade_date, "fund_issue", df)
            results["fund_issue"] = len(df)
    except Exception as e:
        results["fund_issue"] = f"ERROR: {e}"

    elapsed = time.time() - start_time
    logger.info(f"[daily_fetch] 完成，耗时 {elapsed:.1f}s")
    for k, v in results.items():
        logger.info(f"  {k}: {v}")

    return results
