"""
两融余额历史数据库（data_hub）
================================
把全市场（沪市+深市）融资融券余额持久化到 SQLite，支持每日增量更新与历史查询。

数据库位置：<codeact>/output/margin_history.db
数据表 margin_daily：
    trade_date  TEXT PRIMARY KEY   交易日期（YYYYMMDD）
    rzye        REAL               融资余额（元）
    rzmre       REAL               融资买入额（元）
    rqye        REAL               融券余额（元）
    rzrqye      REAL               两融余额（元）
    source      TEXT               数据来源
    updated_at  TEXT               更新时间（ISO8601）

单位统一为「元」，全市场口径（沪市 + 深市）。

用法：
    from data_hub.margin_db import (
        sync_margin_history, query_margin, get_latest_margin, get_margin_percentile,
    )

    sync_margin_history()                       # 增量同步（默认回看 30 天）
    sync_margin_history(lookback_days=500)      # 首次回填历史
    df = query_margin(start_date='20260101')    # 区间查询
    latest = get_latest_margin()                # 最新一期
    pct = get_margin_percentile()               # 当前余额历史分位
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from .config import logger


# 数据库路径：<codeact>/output/margin_history.db
_DB_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'output',
)
DB_PATH = os.path.join(_DB_DIR, 'margin_history.db')

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS margin_daily (
    trade_date  TEXT PRIMARY KEY,
    rzye        REAL,
    rzmre       REAL,
    rqye        REAL,
    rzrqye      REAL,
    source      TEXT,
    updated_at  TEXT
);
"""

_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_margin_trade_date ON margin_daily(trade_date);
"""

_UPSERT_SQL = """
INSERT INTO margin_daily
    (trade_date, rzye, rzmre, rqye, rzrqye, source, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(trade_date) DO UPDATE SET
    rzye       = excluded.rzye,
    rzmre      = excluded.rzmre,
    rqye       = excluded.rqye,
    rzrqye     = excluded.rzrqye,
    source     = excluded.source,
    updated_at = excluded.updated_at
"""


# ============================================================
# 连接与建表
# ============================================================

def _connect() -> sqlite3.Connection:
    """建立连接并确保表结构存在。"""
    os.makedirs(_DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute(_TABLE_SQL)
    conn.execute(_INDEX_SQL)
    return conn


def _f(v: Any) -> Optional[float]:
    """安全转 float，失败返回 None。"""
    if v is None:
        return None
    try:
        fv = float(v)
        if fv != fv:  # NaN
            return None
        return fv
    except (TypeError, ValueError):
        return None


# ============================================================
# 写入
# ============================================================

def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """把 data_hub 返回的两融 DataFrame 标准化为入库结构。"""
    if df is None or df.empty:
        return pd.DataFrame()

    out = df.copy()

    # 若含 exchange 列且存在全市场汇总行，只保留汇总行
    if 'exchange' in out.columns:
        if (out['exchange'] == 'ALL').any():
            out = out[out['exchange'] == 'ALL']
        elif len(out['exchange'].unique()) > 1:
            # 没有汇总行时，按日期把各市场求和
            num_cols = [c for c in ['rzye', 'rzmre', 'rqye', 'rzrqye'] if c in out.columns]
            if num_cols and 'trade_date' in out.columns:
                out = out.groupby('trade_date')[num_cols].sum().reset_index()

    if 'trade_date' not in out.columns:
        return pd.DataFrame()

    # 两融余额缺失时用 融资余额 + 融券余额 补齐
    if 'rzrqye' not in out.columns and 'rzye' in out.columns and 'rqye' in out.columns:
        out['rzrqye'] = out['rzye'].fillna(0) + out['rqye'].fillna(0)

    out['trade_date'] = (
        out['trade_date'].astype(str)
        .str.replace('-', '', regex=False)
        .str.strip()
        .str[:8]
    )
    out = out[out['trade_date'].str.len() == 8]

    keep = [c for c in ['trade_date', 'rzye', 'rzmre', 'rqye', 'rzrqye'] if c in out.columns]
    return out[keep].drop_duplicates(subset=['trade_date'])


def save_margin_rows(df: pd.DataFrame, source: str = 'data_hub') -> int:
    """
    把标准化的两融数据写入数据库（按 trade_date 幂等 upsert）。

    Args:
        df: 含 trade_date / rzye / rzmre / rqye / rzrqye 的 DataFrame
        source: 数据来源标记

    Returns:
        int: 实际写入（含更新）的行数
    """
    norm = _normalize(df)
    if norm.empty:
        return 0

    now = datetime.now().isoformat(timespec='seconds')
    payload = [
        (
            row['trade_date'],
            _f(row.get('rzye')),
            _f(row.get('rzmre')),
            _f(row.get('rqye')),
            _f(row.get('rzrqye')),
            source,
            now,
        )
        for _, row in norm.iterrows()
    ]

    with _connect() as conn:
        conn.executemany(_UPSERT_SQL, payload)
        conn.commit()

    logger.info(f"[margin_db] 写入 {len(payload)} 行（{norm['trade_date'].min()} ~ {norm['trade_date'].max()}）")
    return len(payload)


def sync_margin_history(
    lookback_days: int = 30,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """
    从 data_hub 拉取全市场两融数据并增量入库。

    Args:
        lookback_days: 回看天数（默认 30 天，用于日常增量更新）
        start_date / end_date: 显式指定区间（YYYYMMDD），优先于 lookback_days
        force: 保留参数，当前 upsert 天然幂等

    Returns:
        dict: {'success': bool, 'rows': int, 'latest_date': str, 'latest_rzrqye': float}
    """
    result: Dict[str, Any] = {'success': False, 'rows': 0, 'latest_date': None, 'latest_rzrqye': None}
    try:
        from . import get_data

        df = get_data(
            'margin_trading',
            market='all',
            start_date=start_date,
            end_date=end_date,
            lookback_days=lookback_days,
        )
        if df is None or df.empty:
            result['error'] = '未获取到两融数据'
            logger.warning('[margin_db] 同步失败：data_hub 未返回数据')
            return result

        rows = save_margin_rows(df, source='data_hub:akshare')
        result['success'] = rows > 0
        result['rows'] = rows

        latest = _normalize(df).sort_values('trade_date').iloc[-1]
        result['latest_date'] = latest['trade_date']
        result['latest_rzrqye'] = _f(latest.get('rzrqye'))
        return result
    except Exception as e:
        result['error'] = f'{type(e).__name__}: {e}'
        logger.error(f'[margin_db] 同步异常: {e}')
        return result


def backfill_from_tushare(
    start_date: str = '20150101',
    end_date: Optional[str] = None,
    chunk_days: int = 365,
) -> Dict[str, Any]:
    """
    用 Tushare margin 接口回填历史（支持区间查询，比逐日拉取快得多）。

    仅保留 SSE + SZSE 汇总口径，与日常增量（akshare all）保持一致。

    Args:
        start_date: 起始日期（YYYYMMDD）
        end_date: 结束日期，默认今天
        chunk_days: 分段拉取的天数（避免单次返回行数超限）

    Returns:
        dict: {'success': bool, 'rows': int, 'start_date': str, 'end_date': str}
    """
    from datetime import timedelta

    result: Dict[str, Any] = {'success': False, 'rows': 0}
    try:
        from .tushare_config import get_tushare_pro

        pro = get_tushare_pro()
        if end_date is None:
            end_date = datetime.now().strftime('%Y%m%d')

        frames: List[pd.DataFrame] = []
        cur = datetime.strptime(start_date, '%Y%m%d')
        end_dt = datetime.strptime(end_date, '%Y%m%d')

        while cur <= end_dt:
            seg_end = min(cur + timedelta(days=chunk_days - 1), end_dt)
            try:
                seg = pro.margin(
                    start_date=cur.strftime('%Y%m%d'),
                    end_date=seg_end.strftime('%Y%m%d'),
                )
                if seg is not None and not seg.empty:
                    frames.append(seg)
            except Exception as e:
                logger.warning(f'[margin_db] Tushare 分段拉取失败 {cur:%Y%m%d}~{seg_end:%Y%m%d}: {e}')
            cur = seg_end + timedelta(days=1)

        if not frames:
            result['error'] = 'Tushare 未返回数据'
            return result

        all_df = pd.concat(frames, ignore_index=True)

        # 只保留沪深两市（与日常增量口径一致，剔除北交所）
        if 'exchange_id' in all_df.columns:
            all_df = all_df[all_df['exchange_id'].isin(['SSE', 'SZSE'])]

        num_cols = [c for c in ['rzye', 'rzmre', 'rqye', 'rzrqye'] if c in all_df.columns]
        agg = (
            all_df.groupby('trade_date')[num_cols]
            .sum()
            .reset_index()
        )

        rows = save_margin_rows(agg, source='tushare:backfill')
        result['success'] = rows > 0
        result['rows'] = rows
        result['start_date'] = str(agg['trade_date'].min())
        result['end_date'] = str(agg['trade_date'].max())
        return result
    except Exception as e:
        result['error'] = f'{type(e).__name__}: {e}'
        logger.error(f'[margin_db] 回填异常: {e}')
        return result


# ============================================================
# 查询
# ============================================================

def query_margin(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: Optional[int] = None,
) -> pd.DataFrame:
    """
    查询两融历史数据。

    Args:
        start_date / end_date: 区间（YYYYMMDD），均可省略
        limit: 最多返回条数（按日期倒序取后再升序返回）

    Returns:
        DataFrame: trade_date / rzye / rzmre / rqye / rzrqye / source / updated_at
    """
    sql = "SELECT trade_date, rzye, rzmre, rqye, rzrqye, source, updated_at FROM margin_daily"
    conds: List[str] = []
    params: List[Any] = []

    if start_date:
        conds.append('trade_date >= ?')
        params.append(str(start_date))
    if end_date:
        conds.append('trade_date <= ?')
        params.append(str(end_date))
    if conds:
        sql += ' WHERE ' + ' AND '.join(conds)

    if limit:
        sql += ' ORDER BY trade_date DESC LIMIT ?'
        params.append(int(limit))
        df = pd.read_sql_query(sql, _connect(), params=params)
        return df.sort_values('trade_date').reset_index(drop=True)

    sql += ' ORDER BY trade_date'
    return pd.read_sql_query(sql, _connect(), params=params)


def get_latest_margin() -> Optional[Dict[str, Any]]:
    """获取最新一期两融数据（含日/周/月变动）。"""
    df = query_margin(limit=30)
    if df.empty:
        return None

    df = df.sort_values('trade_date').reset_index(drop=True)
    latest = df.iloc[-1]

    def _pct_change(periods: int) -> Optional[float]:
        if len(df) <= periods:
            return None
        base = _f(df.iloc[-1 - periods]['rzrqye'])
        cur = _f(latest['rzrqye'])
        if not base or cur is None:
            return None
        return round((cur - base) / base * 100, 2)

    return {
        'trade_date': latest['trade_date'],
        'rzye': _f(latest['rzye']),
        'rzmre': _f(latest['rzmre']),
        'rqye': _f(latest['rqye']),
        'rzrqye': _f(latest['rzrqye']),
        'day_pct': _pct_change(1),
        'week_pct': _pct_change(5),
        'month_pct': _pct_change(20),
        'data_points': len(df),
    }


def get_margin_percentile(
    field: str = 'rzrqye',
    window_days: int = 0,
) -> Optional[Dict[str, Any]]:
    """
    计算最新值在历史序列中的分位。

    Args:
        field: 分位字段，默认 rzrqye（两融余额）
        window_days: 仅统计最近 N 条（0 表示全历史）

    Returns:
        dict: {'value': float, 'percentile': float, 'data_points': int}
    """
    if field not in {'rzye', 'rzmre', 'rqye', 'rzrqye'}:
        raise ValueError(f'不支持的字段: {field}')

    df = query_margin()
    if df.empty or field not in df.columns:
        return None

    series = df[field].dropna()
    if window_days and window_days > 0:
        series = series.tail(window_days)
    if series.empty:
        return None

    latest = _f(series.iloc[-1])
    if latest is None:
        return None

    pct = round(float((series <= latest).sum()) / len(series) * 100, 1)
    return {'value': latest, 'percentile': pct, 'data_points': len(series)}


def get_db_stats() -> Dict[str, Any]:
    """返回数据库概览：起止日期、记录数、最新两融余额。"""
    df = query_margin()
    if df.empty:
        return {'exists': os.path.exists(DB_PATH), 'rows': 0}
    latest = df.sort_values('trade_date').iloc[-1]
    return {
        'exists': True,
        'db_path': DB_PATH,
        'rows': len(df),
        'start_date': df['trade_date'].min(),
        'end_date': df['trade_date'].max(),
        'latest_rzrqye': _f(latest['rzrqye']),
        'updated_at': latest['updated_at'],
    }


if __name__ == '__main__':
    # 手动执行：python -m data_hub.margin_db [lookback_days]
    import sys

    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    print(f'同步两融数据（回看 {days} 天）...')
    print(sync_margin_history(lookback_days=days))
    print()
    print('数据库概览:', get_db_stats())
    print()
    print('最新一期:', get_latest_margin())
    print()
    print('历史分位:', get_margin_percentile())
