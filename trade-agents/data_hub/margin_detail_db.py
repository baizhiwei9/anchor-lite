"""
个股两融明细 + 流通市值公共接口（data_hub）
================================================
为两融风险监控提供个股级别的两融明细和流通市值数据，统一走 data_hub 四级降级。

数据库位置：<codeact>/output/margin_detail.db
数据表：
  margin_detail(trade_date, ts_code, rzye, rzmre, rqye, rzrqye, rzche, rqcl, source, updated_at)
  daily_basic(trade_date, ts_code, close, circ_mv, total_mv, source, updated_at)
  stock_basic(ts_code, symbol, name, market, list_status, updated_at)

单位：
  - 金额：元（rzye/rzmre/rqye/rzrqye/rzche）
  - circ_mv / total_mv：万元（Tushare 原始单位）
  - close：元

用法：
    from data_hub.margin_detail_db import (
        get_margin_detail, get_daily_basic, get_stock_list,
        sync_margin_detail, sync_daily_basic,
    )

    md = get_margin_detail('20261022')          # 返回 DataFrame
    db = get_daily_basic('20261022')            # 返回 DataFrame
    stocks = get_stock_list()                   # 返回 DataFrame（A 股列表，剔除 ETF/基金/北交所）
"""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

from .config import logger

# ============================================================
# 数据库路径
# ============================================================
_DB_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'output',
)
DB_PATH = os.path.join(_DB_DIR, 'margin_detail.db')

# 建表 SQL
_MARGIN_DETAIL_TABLE = """
CREATE TABLE IF NOT EXISTS margin_detail (
    trade_date  TEXT NOT NULL,
    ts_code     TEXT NOT NULL,
    rzye        REAL,
    rzmre       REAL,
    rqye        REAL,
    rzrqye      REAL,
    rzche       REAL,
    rqcl        REAL,
    source      TEXT,
    updated_at  TEXT,
    PRIMARY KEY (trade_date, ts_code)
);
"""

_DAILY_BASIC_TABLE = """
CREATE TABLE IF NOT EXISTS daily_basic (
    trade_date  TEXT NOT NULL,
    ts_code     TEXT NOT NULL,
    close       REAL,
    circ_mv     REAL,
    total_mv    REAL,
    source      TEXT,
    updated_at  TEXT,
    PRIMARY KEY (trade_date, ts_code)
);
"""

_STOCK_BASIC_TABLE = """
CREATE TABLE IF NOT EXISTS stock_basic (
    ts_code      TEXT PRIMARY KEY,
    symbol       TEXT,
    name         TEXT,
    market       TEXT,
    list_status  TEXT,
    updated_at   TEXT
);
"""

_INDEX_SQLS = [
    "CREATE INDEX IF NOT EXISTS idx_md_trade_date ON margin_detail(trade_date);",
    "CREATE INDEX IF NOT EXISTS idx_md_ts_code ON margin_detail(ts_code);",
    "CREATE INDEX IF NOT EXISTS idx_db_trade_date ON daily_basic(trade_date);",
    "CREATE INDEX IF NOT EXISTS idx_db_ts_code ON daily_basic(ts_code);",
]

# 全市场每日流通市值汇总表（轻量级，仅存每日总计，不存个股明细）
_TOTAL_CIRC_MV_HISTORY_TABLE = """
CREATE TABLE IF NOT EXISTS total_circ_mv_history (
    trade_date       TEXT PRIMARY KEY,
    total_circ_mv    REAL,
    stock_count      INTEGER,
    source           TEXT,
    updated_at       TEXT
);
"""


# ============================================================
# 连接与建表
# ============================================================

def _connect() -> sqlite3.Connection:
    os.makedirs(_DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute(_MARGIN_DETAIL_TABLE)
    conn.execute(_DAILY_BASIC_TABLE)
    conn.execute(_STOCK_BASIC_TABLE)
    conn.execute(_TOTAL_CIRC_MV_HISTORY_TABLE)
    for sql in _INDEX_SQLS:
        conn.execute(sql)
    return conn


def _f(v) -> Optional[float]:
    if v is None:
        return None
    try:
        fv = float(v)
        return None if fv != fv else fv
    except (TypeError, ValueError):
        return None


# ============================================================
# Tushare 代理（走 data_hub.tushare_config 统一入口）
# ============================================================

def _get_pro():
    """通过 data_hub 统一配置获取 Tushare pro 实例。"""
    from .tushare_config import get_tushare_pro
    return get_tushare_pro()


def _ts_call(func_name: str, **kwargs) -> Optional[pd.DataFrame]:
    """通过 data_hub 调用 Tushare 接口（含节流+重试）。"""
    pro = _get_pro()
    if pro is None:
        return None
    bound = getattr(pro, func_name, None)
    if bound is None:
        logger.warning(f'[margin_detail_db] Tushare 无接口: {func_name}')
        return None
    for attempt in range(3):
        try:
            time.sleep(0.55)
            df = bound(**kwargs)
            return df
        except Exception as e:
            logger.warning(f'[margin_detail_db] Tushare {func_name} attempt {attempt+1}: {e}')
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    return None


# ============================================================
# 同步：个股两融明细
# ============================================================

def sync_margin_detail(trade_date: str) -> Dict[str, Any]:
    """
    同步某日个股两融明细到 SQLite。

    四级降级：
    Tier 1: Tushare margin_detail（via data_hub 代理）
    Tier 2: AKShare stock_margin_detail_sse + stock_margin_detail_szse
    Tier 3: 无（跳过）
    Tier 4: 无（跳过）

    Args:
        trade_date: YYYYMMDD

    Returns:
        dict: {'success': bool, 'rows': int, 'source': str}
    """
    result = {'success': False, 'rows': 0, 'source': ''}
    td = str(trade_date).replace('-', '')[:8]

    # 先检查是否已有数据
    conn = _connect()
    try:
        existing = pd.read_sql_query(
            "SELECT COUNT(*) as cnt FROM margin_detail WHERE trade_date = ?",
            conn, params=[td]
        )
        if existing.iloc[0]['cnt'] > 100:
            result['success'] = True
            result['rows'] = int(existing.iloc[0]['cnt'])
            result['source'] = 'cache'
            conn.close()
            return result
    except Exception:
        pass
    conn.close()

    df = None
    source = ''

    # Tier 1: Tushare margin_detail
    try:
        logger.info(f'[margin_detail_db] Tier 1: Tushare margin_detail({td})')
        df = _ts_call('margin_detail', trade_date=td)
        if df is not None and not df.empty:
            source = 'tushare:margin_detail'
            logger.info(f'[margin_detail_db] Tushare 返回 {len(df)} 行')
    except Exception as e:
        logger.warning(f'[margin_detail_db] Tushare margin_detail 失败: {e}')

    # Tier 2: AKShare
    if df is None or (df is not None and df.empty):
        try:
            logger.info(f'[margin_detail_db] Tier 2: AKShare stock_margin_detail_sse/szse')
            import akshare as ak
            frames = []
            # 上交所
            try:
                df_sh = ak.stock_margin_detail_sse(date=td)
                if df_sh is not None and not df_sh.empty:
                    frames.append(('SH', df_sh))
            except Exception as e:
                logger.warning(f'[margin_detail_db] AKShare SSE detail 失败: {e}')
            # 深交所
            try:
                df_sz = ak.stock_margin_detail_szse(date=td)
                if df_sz is not None and not df_sz.empty:
                    frames.append(('SZ', df_sz))
            except Exception as e:
                logger.warning(f'[margin_detail_db] AKShare SZSE detail 失败: {e}')

            if frames:
                df = _normalize_akshare_detail(frames, td)
                if df is not None and not df.empty:
                    source = 'akshare:margin_detail'
        except Exception as e:
            logger.warning(f'[margin_detail_db] AKShare margin_detail 失败: {e}')

    if df is None or df.empty:
        result['error'] = '所有数据源均未返回个股两融明细'
        return result

    # 入库
    rows = _save_margin_detail(df, td, source)
    result['success'] = rows > 0
    result['rows'] = rows
    result['source'] = source
    return result


def _normalize_akshare_detail(frames: list, trade_date: str) -> Optional[pd.DataFrame]:
    """标准化 AKShare 个股两融明细。"""
    all_rows = []
    for exchange, df in frames:
        if df is None or df.empty:
            continue
        out = df.copy()
        # AKShare SSE 列名可能是中文
        col_map = {}
        for c in out.columns:
            cl = c.lower().strip()
            if '代码' in c or 'code' in cl or 'ts_code' in cl:
                col_map[c] = 'stock_code'
            elif '融资余额' in c and '买入' not in c and '偿还' not in c:
                col_map[c] = 'rzye'
            elif '融资买入' in c:
                col_map[c] = 'rzmre'
            elif '融券余额' in c and '卖出' not in c:
                col_map[c] = 'rqye'
            elif '融资融券余额' in c:
                col_map[c] = 'rzrqye'
            elif '融资偿还' in c:
                col_map[c] = 'rzche'
            elif '融券偿还' in c:
                col_map[c] = 'rqcl'
        out = out.rename(columns=col_map)

        if 'stock_code' not in out.columns:
            continue

        # 转换 ts_code 格式
        if exchange == 'SH':
            out['ts_code'] = out['stock_code'].astype(str).str.zfill(6) + '.SH'
        else:
            out['ts_code'] = out['stock_code'].astype(str).str.zfill(6) + '.SZ'

        out['trade_date'] = trade_date
        all_rows.append(out)

    if not all_rows:
        return None
    combined = pd.concat(all_rows, ignore_index=True)
    return combined


def _save_margin_detail(df: pd.DataFrame, trade_date: str, source: str) -> int:
    """写入 margin_detail 表。"""
    td = str(trade_date).replace('-', '')[:8]
    now = datetime.now().isoformat(timespec='seconds')

    rows = []
    for _, r in df.iterrows():
        ts_code = r.get('ts_code', '')
        if not ts_code:
            continue
        rows.append((
            td,
            str(ts_code),
            _f(r.get('rzye')),
            _f(r.get('rzmre')),
            _f(r.get('rqye')),
            _f(r.get('rzrqye')),
            _f(r.get('rzche')),
            _f(r.get('rqcl')),
            source,
            now,
        ))

    if not rows:
        return 0

    upsert_sql = """
    INSERT INTO margin_detail (trade_date, ts_code, rzye, rzmre, rqye, rzrqye, rzche, rqcl, source, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(trade_date, ts_code) DO UPDATE SET
        rzye=excluded.rzye, rzmre=excluded.rzmre, rqye=excluded.rqye,
        rzrqye=excluded.rzrqye, rzche=excluded.rzche, rqcl=excluded.rqcl,
        source=excluded.source, updated_at=excluded.updated_at
    """

    with _connect() as conn:
        conn.executemany(upsert_sql, rows)
        conn.commit()

    logger.info(f'[margin_detail_db] 写入 margin_detail {len(rows)} 行 ({td})')
    return len(rows)


# ============================================================
# 同步：daily_basic（流通市值 / 收盘价）
# ============================================================

def sync_daily_basic(trade_date: str) -> Dict[str, Any]:
    """
    同步某日全市场 daily_basic（收盘价 + 流通市值 + 总市值）。

    四级降级：
    Tier 1: Tushare daily_basic（via data_hub 代理）
    Tier 2: AKShare stock_zh_a_spot_em（仅当日，无历史）
    Tier 3/4: 跳过

    Args:
        trade_date: YYYYMMDD

    Returns:
        dict: {'success': bool, 'rows': int, 'source': str}
    """
    result = {'success': False, 'rows': 0, 'source': ''}
    td = str(trade_date).replace('-', '')[:8]

    # 检查缓存
    conn = _connect()
    try:
        existing = pd.read_sql_query(
            "SELECT COUNT(*) as cnt FROM daily_basic WHERE trade_date = ?",
            conn, params=[td]
        )
        if existing.iloc[0]['cnt'] > 100:
            result['success'] = True
            result['rows'] = int(existing.iloc[0]['cnt'])
            result['source'] = 'cache'
            conn.close()
            return result
    except Exception:
        pass
    conn.close()

    df = None
    source = ''

    # Tier 1: Tushare daily_basic（带重试，防IP限频）
    import time as _time_mod
    _daily_basic_delays = [0, 15, 30]  # 首次立即，后续间隔递增
    for _attempt in range(3):
        _delay = _daily_basic_delays[_attempt]
        if _delay > 0:
            logger.info(f'[margin_detail_db] Tushare daily_basic 等待{_delay}秒后重试...')
            _time_mod.sleep(_delay)
        try:
            logger.info(f'[margin_detail_db] Tier 1: Tushare daily_basic({td}) attempt {_attempt+1}')
            df = _ts_call(
                'daily_basic',
                trade_date=td,
                fields='ts_code,trade_date,close,circ_mv,total_mv',
            )
            if df is not None and not df.empty:
                source = 'tushare:daily_basic'
                logger.info(f'[margin_detail_db] Tushare daily_basic 返回 {len(df)} 行')
                break
            else:
                logger.warning(f'[margin_detail_db] Tushare daily_basic 返回空')
        except Exception as e:
            logger.warning(f'[margin_detail_db] Tushare daily_basic 失败: {e}')
    else:
        df = None

    # Tier 2: AKShare spot（仅当天）
    if df is None or (df is not None and df.empty):
        today_str = datetime.now().strftime('%Y%m%d')
        if td == today_str:
            try:
                logger.info(f'[margin_detail_db] Tier 2: AKShare stock_zh_a_spot_em')
                import akshare as ak
                spot_sh = ak.stock_sh_a_spot_em()
                spot_sz = ak.stock_sz_a_spot_em()
                frames = [f for f in [spot_sh, spot_sz] if f is not None and not f.empty]
                if frames:
                    df = _normalize_akshare_spot(frames, td)
                    if df is not None and not df.empty:
                        source = 'akshare:spot_em'
            except Exception as e:
                logger.warning(f'[margin_detail_db] AKShare spot 失败: {e}')

    if df is None or df.empty:
        result['error'] = '所有数据源均未返回 daily_basic'
        return result

    rows = _save_daily_basic(df, td, source)
    result['success'] = rows > 0
    result['rows'] = rows
    result['source'] = source
    return result


def _normalize_akshare_spot(frames: list, trade_date: str) -> Optional[pd.DataFrame]:
    """标准化 AKShare spot 数据为 daily_basic 格式。"""
    all_rows = []
    for df in frames:
        out = df.copy()
        col_map = {}
        for c in out.columns:
            if '代码' in c or 'code' in c.lower():
                col_map[c] = 'stock_code'
            elif '最新价' in c or '收盘' in c:
                col_map[c] = 'close'
            elif '流通市值' in c:
                col_map[c] = 'circ_mv'
            elif '总市值' in c:
                col_map[c] = 'total_mv'
        out = out.rename(columns=col_map)
        if 'stock_code' in out.columns:
            # AKShare 代码 → ts_code：根据代码首位推断交易所
            out['ts_code'] = out['stock_code'].apply(
                lambda x: str(x).zfill(6) + ('.SH' if str(x).startswith('6') else '.SZ')
            )
            out['trade_date'] = trade_date
            all_rows.append(out)

    if not all_rows:
        return None
    combined = pd.concat(all_rows, ignore_index=True)
    return combined


def _save_daily_basic(df: pd.DataFrame, trade_date: str, source: str) -> int:
    """写入 daily_basic 表。"""
    td = str(trade_date).replace('-', '')[:8]
    now = datetime.now().isoformat(timespec='seconds')

    rows = []
    for _, r in df.iterrows():
        ts_code = r.get('ts_code', '')
        if not ts_code:
            continue
        rows.append((
            td,
            str(ts_code),
            _f(r.get('close')),
            _f(r.get('circ_mv')),
            _f(r.get('total_mv')),
            source,
            now,
        ))

    if not rows:
        return 0

    upsert_sql = """
    INSERT INTO daily_basic (trade_date, ts_code, close, circ_mv, total_mv, source, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(trade_date, ts_code) DO UPDATE SET
        close=excluded.close, circ_mv=excluded.circ_mv, total_mv=excluded.total_mv,
        source=excluded.source, updated_at=excluded.updated_at
    """

    with _connect() as conn:
        conn.executemany(upsert_sql, rows)
        conn.commit()

    logger.info(f'[margin_detail_db] 写入 daily_basic {len(rows)} 行 ({td})')
    return len(rows)


# ============================================================
# 同步：股票列表
# ============================================================

def sync_stock_list() -> Dict[str, Any]:
    """同步 A 股股票列表（剔除 ETF/基金/北交所）。"""
    result = {'success': False, 'rows': 0}

    df = _ts_call(
        'stock_basic',
        exchange='',
        list_status='L',
        fields='ts_code,symbol,name,market',
    )
    if df is None or df.empty:
        result['error'] = 'Tushare stock_basic 返回空'
        return result

    # 只保留沪深 A 股
    df = df[df['ts_code'].str.endswith(('.SH', '.SZ'))].reset_index(drop=True)

    now = datetime.now().isoformat(timespec='seconds')
    rows = []
    for _, r in df.iterrows():
        rows.append((
            str(r['ts_code']),
            str(r.get('symbol', '')),
            str(r.get('name', '')),
            str(r.get('market', '')),
            'L',
            now,
        ))

    upsert_sql = """
    INSERT INTO stock_basic (ts_code, symbol, name, market, list_status, updated_at)
    VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT(ts_code) DO UPDATE SET
        symbol=excluded.symbol, name=excluded.name, market=excluded.market,
        list_status=excluded.list_status, updated_at=excluded.updated_at
    """

    with _connect() as conn:
        conn.executemany(upsert_sql, rows)
        conn.commit()

    result['success'] = True
    result['rows'] = len(rows)
    return result


# ============================================================
# 查询（公共接口）
# ============================================================

def get_margin_detail(trade_date: str) -> Optional[pd.DataFrame]:
    """
    获取某日个股两融明细（优先缓存，缓存未命中则同步）。

    Args:
        trade_date: YYYYMMDD

    Returns:
        DataFrame: ts_code, rzye, rzmre, rqye, rzrqye, rzche, rqcl
        若数据不可用返回 None
    """
    td = str(trade_date).replace('-', '')[:8]

    # 先查缓存
    df = _query_margin_detail_cache(td)
    if df is not None and not df.empty:
        return df

    # 缓存未命中，同步
    sync_result = sync_margin_detail(td)
    if sync_result.get('success'):
        return _query_margin_detail_cache(td)

    return None


def _query_margin_detail_cache(trade_date: str) -> Optional[pd.DataFrame]:
    """从缓存查询个股两融明细。"""
    td = str(trade_date).replace('-', '')[:8]
    try:
        with _connect() as conn:
            df = pd.read_sql_query(
                "SELECT trade_date, ts_code, rzye, rzmre, rqye, rzrqye, rzche, rqcl "
                "FROM margin_detail WHERE trade_date = ?",
                conn, params=[td]
            )
        return df if not df.empty else None
    except Exception as e:
        logger.warning(f'[margin_detail_db] 查询缓存失败: {e}')
        return None


def get_daily_basic(trade_date: str) -> Optional[pd.DataFrame]:
    """
    获取某日全市场 daily_basic（收盘价 + 流通市值 + 总市值）。

    Args:
        trade_date: YYYYMMDD

    Returns:
        DataFrame: ts_code, close, circ_mv, total_mv
        若数据不可用返回 None
    """
    td = str(trade_date).replace('-', '')[:8]

    # 先查缓存
    df = _query_daily_basic_cache(td)
    if df is not None and not df.empty:
        return df

    # 缓存未命中，同步
    sync_result = sync_daily_basic(td)
    if sync_result.get('success'):
        return _query_daily_basic_cache(td)

    return None


def _query_daily_basic_cache(trade_date: str) -> Optional[pd.DataFrame]:
    """从缓存查询 daily_basic。"""
    td = str(trade_date).replace('-', '')[:8]
    try:
        with _connect() as conn:
            df = pd.read_sql_query(
                "SELECT trade_date, ts_code, close, circ_mv, total_mv "
                "FROM daily_basic WHERE trade_date = ?",
                conn, params=[td]
            )
        return df if not df.empty else None
    except Exception as e:
        logger.warning(f'[margin_detail_db] 查询 daily_basic 缓存失败: {e}')
        return None


def get_stock_list() -> Optional[pd.DataFrame]:
    """
    获取 A 股股票列表（剔除 ETF/基金/北交所）。

    Returns:
        DataFrame: ts_code, symbol, name, market
    """
    # 先查缓存
    try:
        with _connect() as conn:
            df = pd.read_sql_query(
                "SELECT ts_code, symbol, name, market FROM stock_basic WHERE list_status = 'L'",
                conn
            )
        if not df.empty:
            return df
    except Exception:
        pass

    # 缓存为空，同步
    sync_result = sync_stock_list()
    if sync_result.get('success'):
        with _connect() as conn:
            df = pd.read_sql_query(
                "SELECT ts_code, symbol, name, market FROM stock_basic WHERE list_status = 'L'",
                conn
            )
        return df if not df.empty else None

    return None


def get_total_circ_mv(trade_date: str) -> Optional[float]:
    """
    获取某日全市场流通市值合计（元）。
    优先查 total_circ_mv_history 缓存；未命中再走 daily_basic。
    circ_mv 单位为万元，返回转换为元。
    """
    # 先查轻量汇总表
    td = str(trade_date).replace('-', '')[:8]
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT total_circ_mv FROM total_circ_mv_history WHERE trade_date = ?",
                (td,)
            ).fetchone()
            if row and row[0]:
                return float(row[0])
    except Exception:
        pass

    # 回退：走 daily_basic
    df = get_daily_basic(trade_date)
    if df is None or df.empty:
        return None
    a_share = df[df['ts_code'].str.endswith(('.SH', '.SZ'))]
    if a_share.empty:
        return None
    total = float(a_share['circ_mv'].dropna().sum())
    return total * 1e4 if total > 0 else None


# ============================================================
# 全市场流通市值历史：回填 + 查询
# ============================================================

def get_total_circ_mv_history_df() -> pd.DataFrame:
    """
    查询全市场流通市值历史，返回 DataFrame(trade_date, total_circ_mv)。
    total_circ_mv 单位：元。
    """
    try:
        with _connect() as conn:
            df = pd.read_sql_query(
                "SELECT trade_date, total_circ_mv FROM total_circ_mv_history ORDER BY trade_date",
                conn
            )
        return df
    except Exception:
        return pd.DataFrame(columns=['trade_date', 'total_circ_mv'])


def _sync_one_date_circ_mv(trade_date: str) -> Optional[float]:
    """
    同步单日全市场流通市值到 total_circ_mv_history 表。
    调用 Tushare daily_basic，仅取 circ_mv 字段求和，不存个股明细。
    返回总流通市值（元），失败返回 None。
    """
    td = str(trade_date).replace('-', '')[:8]

    # 检查缓存
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT total_circ_mv FROM total_circ_mv_history WHERE trade_date = ?",
                (td,)
            ).fetchone()
            if row and row[0]:
                return float(row[0])
    except Exception:
        pass

    # 调用 Tushare daily_basic 仅取必要字段
    df = _ts_call(
        'daily_basic',
        trade_date=td,
        fields='ts_code,trade_date,circ_mv',
    )
    if df is None or df.empty:
        return None

    # 只保留沪深 A 股
    a_share = df[df['ts_code'].str.endswith(('.SH', '.SZ'))]
    if a_share.empty:
        return None

    total_wan = float(a_share['circ_mv'].dropna().sum())
    total_yuan = total_wan * 1e4  # 万→元
    if total_yuan <= 0:
        return None

    stock_count = len(a_share)

    # 写入汇总表
    now = datetime.now().isoformat(timespec='seconds')
    upsert_sql = """
    INSERT INTO total_circ_mv_history (trade_date, total_circ_mv, stock_count, source, updated_at)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(trade_date) DO UPDATE SET
        total_circ_mv=excluded.total_circ_mv,
        stock_count=excluded.stock_count,
        source=excluded.source,
        updated_at=excluded.updated_at
    """
    with _connect() as conn:
        conn.execute(upsert_sql, (td, total_yuan, stock_count, 'tushare:daily_basic', now))
        conn.commit()

    return total_yuan


def backfill_total_circ_mv_history(
    trade_dates: list = None,
    lookback_years: int = 3,
    margin_db_path: str = None,
) -> dict:
    """
    一次性回填全市场流通市值历史。
    
    Args:
        trade_dates: 指定需要回填的交易日列表(YYYYMMDD)。
                     若为 None，则从 margin_history.db 读取近 lookback_years 年交易日。
        lookback_years: 当 trade_dates 为 None 时的回溯年数。
        margin_db_path: margin_history.db 路径（用于获取交易日列表）。
    
    Returns:
        dict: {'total': int, 'success': int, 'failed': int, 'skipped': int, 
               'elapsed_sec': float, 'date_range': [start, end]}
    """
    result = {
        'total': 0, 'success': 0, 'failed': 0, 'skipped': 0,
        'elapsed_sec': 0, 'date_range': ['', ''],
    }

    # 获取需要回填的日期列表
    if trade_dates is None:
        # 从 margin_history.db 获取近 N 年交易日
        if margin_db_path is None:
            margin_db_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                'output', 'margin_history.db'
            )
        try:
            conn = sqlite3.connect(margin_db_path, timeout=30)
            cutoff = (datetime.now() - timedelta(days=lookback_years * 365)).strftime('%Y%m%d')
            # 表名可能是 margin_daily 或 margin_history
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]
            table_name = 'margin_daily' if 'margin_daily' in tables else 'margin_history'
            rows = conn.execute(
                f"SELECT DISTINCT trade_date FROM {table_name} WHERE trade_date >= ? ORDER BY trade_date",
                (cutoff,)
            ).fetchall()
            conn.close()
            trade_dates = [r[0] for r in rows]
        except Exception as e:
            logger.error(f'[margin_detail_db] 读取 margin_history 失败: {e}')
            return result

    if not trade_dates:
        logger.warning('[margin_detail_db] 无需要回填的日期')
        return result

    # 检查已存在的日期（跳过）
    existing_dates = set()
    try:
        with _connect() as conn:
            for r in conn.execute("SELECT trade_date FROM total_circ_mv_history").fetchall():
                existing_dates.add(r[0])
    except Exception:
        pass

    dates_to_fill = [d for d in trade_dates if str(d).replace('-', '')[:8] not in existing_dates]
    result['total'] = len(trade_dates)
    result['skipped'] = len(trade_dates) - len(dates_to_fill)

    if not dates_to_fill:
        logger.info(f'[margin_detail_db] 所有 {result["total"]} 个日期已存在，无需回填')
        result['date_range'] = [str(trade_dates[0]), str(trade_dates[-1])]
        return result

    logger.info(f'[margin_detail_db] 开始回填流通市值历史: {len(dates_to_fill)} 个日期需同步 '
                f'(跳过已存在 {result["skipped"]} 个)')

    start_time = time.time()
    dates_to_fill.sort()

    for i, td in enumerate(dates_to_fill):
        td_str = str(td).replace('-', '')[:8]
        try:
            mv = _sync_one_date_circ_mv(td_str)
            if mv:
                result['success'] += 1
                if (i + 1) % 50 == 0 or (i + 1) == len(dates_to_fill):
                    elapsed = time.time() - start_time
                    logger.info(f'[margin_detail_db] 回填进度: {i+1}/{len(dates_to_fill)} '
                                f'({elapsed:.0f}s)')
            else:
                result['failed'] += 1
                logger.warning(f'[margin_detail_db] 回填 {td_str} 失败（数据为空）')
        except Exception as e:
            result['failed'] += 1
            logger.warning(f'[margin_detail_db] 回填 {td_str} 异常: {e}')

    result['elapsed_sec'] = time.time() - start_time
    result['date_range'] = [str(dates_to_fill[0]), str(dates_to_fill[-1])]
    logger.info(f'[margin_detail_db] 回填完成: 成功 {result["success"]}, '
                f'失败 {result["failed"]}, 耗时 {result["elapsed_sec"]:.0f}s')
    return result


def ensure_total_circ_mv_for_date(trade_date: str) -> Optional[float]:
    """
    确保某日的全市场流通市值已入库，返回总流通市值（元）。
    每日增量更新入口：先查缓存，未命中则同步单日。
    """
    return _sync_one_date_circ_mv(trade_date)


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'backfill_circ_mv':
        # 一次性回填: python margin_detail_db.py backfill_circ_mv [lookback_years]
        years = int(sys.argv[2]) if len(sys.argv) > 2 else 3
        print(f'=== 回填全市场流通市值历史（近{years}年） ===')
        r = backfill_total_circ_mv_history(lookback_years=years)
        print(f'  结果: {r}')
    else:
        td = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime('%Y%m%d')
        print(f'=== 同步个股两融明细 ({td}) ===')
        r1 = sync_margin_detail(td)
        print(f'  结果: {r1}')

        print(f'\n=== 同步 daily_basic ({td}) ===')
        r2 = sync_daily_basic(td)
        print(f'  结果: {r2}')

        print(f'\n=== 同步股票列表 ===')
        r3 = sync_stock_list()
        print(f'  结果: {r3}')

        print(f'\n=== 查询个股两融明细 ===')
        md = get_margin_detail(td)
        if md is not None:
            print(f'  {len(md)} 行')
            print(md.head())
        else:
            print('  无数据')

        print(f'\n=== 查询 daily_basic ===')
        db = get_daily_basic(td)
        if db is not None:
            print(f'  {len(db)} 行')
            print(db.head())
        else:
            print('  无数据')

        print(f'\n=== 全市场流通市值 ===')
        mv = get_total_circ_mv(td)
        print(f'  {mv}')

        print(f'\n=== 流通市值历史 ===')
        hist = get_total_circ_mv_history_df()
        print(f'  {len(hist)} 条记录')
        if not hist.empty:
            print(f'  范围: {hist["trade_date"].iloc[0]} ~ {hist["trade_date"].iloc[-1]}')
            print(hist.tail(5).to_string())
