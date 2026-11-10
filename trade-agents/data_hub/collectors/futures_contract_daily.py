"""
单个期货合约历史日线采集器
==========================
采集**指定具体合约**（如 IF2609，注意不是主力连续 IF0）的历史日线行情，
支持按 start_date / end_date 区间取数，四级降级。

四级降级：
Tier 1: AKShare (futures_zh_daily_sina，新浪财经源)
Tier 2: Tushare (fut_daily)
Tier 3: Coze Gateway（技能CLI兜底，暂无对应技能，返回失败由上层降级）
Tier 4: Web爬取 (新浪财经期货 JSONP K线接口)

输出统一 DataFrame：
    date(YYYYMMDD 字符串), open, high, low, close, volume, oi
按 date 升序排列。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional, Union

import pandas as pd

from ..base import BaseCollector, DataResult
from ..config import logger
from .. import config
from ..sources import AKShareSource, TushareSource, CozeGatewaySource, WebSource


# 中金所产品前缀 → 交易所后缀（期货）
_CFFEX_PRODUCTS = ('IF', 'IC', 'IM', 'IH', 'T', 'TF', 'TS', 'TL')
# 上期所产品前缀
_SHFE_PRODUCTS = (
    'CU', 'AL', 'ZN', 'PB', 'NI', 'SN',
    'AU', 'AG',
    'RB', 'HC', 'SS',
    'FU', 'BU', 'RU', 'SP', 'AO', 'BR',
    'WR',
)
# 大商所产品前缀
_DCE_PRODUCTS = (
    'A', 'B', 'M', 'Y', 'P', 'C', 'CS', 'JD', 'LH',
    'L', 'V', 'PP', 'J', 'JM', 'I', 'EG', 'EB', 'PG',
    'RR', 'FB', 'BB',
)
# 郑商所产品前缀
_CZCE_PRODUCTS = (
    'WH', 'PM', 'RI', 'RS', 'JR', 'LR', 'AP', 'CJ', 'UR', 'SA', 'PF', 'PK', 'SH',
    'SR', 'CF', 'CY', 'TA', 'MA', 'FG', 'SF', 'SM', 'OI', 'RM', 'ZC',
)
# 广期所产品前缀
_GFEX_PRODUCTS = ('SI', 'LC', 'PS')


def _split_contract(contract: str):
    """解析合约代码，返回 (产品前缀, 合约主体, 交易所后缀或None)。

    示例：
        'IF2609'      -> ('IF', 'IF2609', None)
        'IF2609.CFX'  -> ('IF', 'IF2609', 'CFX')
    """
    raw = str(contract).strip().upper()
    suffix = None
    body = raw
    if '.' in raw:
        body, suffix = raw.split('.', 1)
        suffix = suffix.strip().upper() or None
    # 提取字母前缀作为产品
    product = ''
    for ch in body:
        if ch.isalpha():
            product += ch
        else:
            break
    return product, body, suffix


def infer_tushare_exchange(product: str) -> str:
    """根据产品前缀推断 tushare 期货交易所后缀。

    股指期货默认 CFX（中金所）。其余按交易所产品表匹配。
    """
    p = (product or '').upper()
    if p in _CFFEX_PRODUCTS:
        return 'CFX'
    if p in _SHFE_PRODUCTS:
        return 'SHF'
    if p in _DCE_PRODUCTS:
        return 'DCE'
    if p in _CZCE_PRODUCTS:
        return 'ZCE'
    if p in _GFEX_PRODUCTS:
        return 'GFE'
    # 兜底：股指/国债类默认中金所
    return 'CFX'


def normalize_contract(contract: str) -> dict:
    """标准化合约代码。

    Returns:
        dict: {
            'raw': 原始输入,
            'sina': 新浪/akshare 合约代码（如 IF2609）,
            'ts_code': tushare 合约代码（如 IF2609.CFX）,
            'product': 产品前缀（如 IF）,
        }
    """
    product, body, suffix = _split_contract(contract)
    if not body:
        raise ValueError(f"无法解析期货合约代码: {contract}")

    # 已显式带后缀：以用户后缀为准（同时兼容 .CFFEX 写法）
    if suffix:
        ts_suffix = 'CFX' if suffix in ('CFFEX', 'CFEX') else suffix
        ts_code = f"{body}.{ts_suffix}"
    else:
        ts_code = f"{body}.{infer_tushare_exchange(product)}"

    return {
        'raw': str(contract).strip(),
        'sina': body,
        'ts_code': ts_code,
        'product': product,
    }


def _normalize_date(date_str: str) -> str:
    """标准化日期格式为 YYYYMMDD。"""
    date_str = str(date_str).strip()
    if len(date_str) == 8 and date_str.isdigit():
        return date_str
    if '-' in date_str:
        return date_str.replace('-', '')
    if '/' in date_str:
        return date_str.replace('/', '')
    return date_str


def normalize_futures_contract_df(df: pd.DataFrame) -> pd.DataFrame:
    """把各数据源原始 DataFrame 统一为标准输出。

    标准列: date(YYYYMMDD str), open, high, low, close, volume, oi
    按 date 升序。
    """
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame(columns=['date', 'open', 'high', 'low', 'close', 'volume', 'oi'])

    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]

    # 日期列：date / trade_date / d / day
    date_col = None
    for c in ('date', 'trade_date', 'd', 'day'):
        if c in df.columns:
            date_col = c
            break
    if date_col is None:
        return pd.DataFrame(columns=['date', 'open', 'high', 'low', 'close', 'volume', 'oi'])

    # 统一日期列名为 date
    if date_col != 'date':
        df = df.rename(columns={date_col: 'date'})

    # 持仓量列：oi / hold / p
    if 'oi' not in df.columns:
        for c in ('hold', 'p', 'open_interest'):
            if c in df.columns:
                df['oi'] = df[c]
                break
        else:
            df['oi'] = 0

    # 成交量列：volume / vol / v
    if 'volume' not in df.columns:
        for c in ('vol', 'v'):
            if c in df.columns:
                df['volume'] = df[c]
                break
        else:
            df['volume'] = 0

    # 统一日期为 YYYYMMDD 字符串
    df['date'] = pd.to_datetime(df['date'], errors='coerce').dt.strftime('%Y%m%d')
    df = df[df['date'].notna() & (df['date'] != 'NaT')]

    out_cols = ['date', 'open', 'high', 'low', 'close', 'volume', 'oi']
    for c in ('open', 'high', 'low', 'close', 'volume', 'oi'):
        if c not in df.columns:
            df[c] = pd.NA
        df[c] = pd.to_numeric(df[c], errors='coerce')

    df = df[out_cols].sort_values('date').reset_index(drop=True)
    return df


class FuturesContractDailyCollector(BaseCollector):
    """
    单个期货合约历史日线采集器（指定具体合约，非主力连续）。

    用法：
        collector = FuturesContractDailyCollector()
        result = collector.collect(
            contract="IF2609",
            start_date="20260101",
            end_date="20260922",
        )
        df = result.data
    """

    data_type: str = "futures_contract_daily"
    min_data_count: int = 1  # 单合约至少1条

    def __init__(
        self,
        source_priority: Optional[List[str]] = None,
        tushare_token: Optional[str] = None,
    ):
        # 默认优先级：akshare > tushare > coze_gateway > web
        # 受 config.USE_TUSHARE 开关控制，为 False 时直接跳过 tushare 层级
        if source_priority is None:
            source_priority = ["akshare"]
            if config.USE_TUSHARE:
                source_priority.append("tushare")
            source_priority.extend(["coze_gateway", "web"])

        super().__init__(source_priority=source_priority)

        # 注册四级数据源
        self.register_source(AKShareSource())
        self.register_source(TushareSource(token=tushare_token))
        self.register_source(CozeGatewaySource())
        self.register_source(WebSource())

    def is_data_sufficient(self, result: DataResult) -> bool:
        """判断合约日线数据是否充足。"""
        return result.data_count >= self.min_data_count

    def collect(
        self,
        contract: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        lookback: Optional[int] = None,
        **kwargs,
    ) -> DataResult:
        """
        获取单个期货合约的历史日线行情。

        Args:
            contract: 合约代码，支持 'IF2609' 或带交易所后缀 'IF2609.CFX'
            start_date: 开始日期（YYYYMMDD）
            end_date: 结束日期（YYYYMMDD）
            lookback: 回溯自然日数（从 end_date 或当日往前推）

        Returns:
            DataResult.data: pd.DataFrame
                标准列 date(YYYYMMDD), open, high, low, close, volume, oi，按 date 升序
        """
        if not contract:
            return DataResult(
                data_type=self.data_type,
                success=False,
                error="合约代码 contract 为空",
            )

        code_info = normalize_contract(contract)

        if start_date:
            start_date = _normalize_date(start_date)
        if end_date:
            end_date = _normalize_date(end_date)

        # 计算日期范围（lookback 优先）
        if lookback and lookback > 0:
            ref_date = end_date or datetime.now().strftime("%Y%m%d")
            ref_date = _normalize_date(ref_date)
            ref_dt = datetime.strptime(ref_date, "%Y%m%d")
            start_dt = ref_dt - timedelta(days=lookback + 10)  # 多取10天防假日
            computed_start = start_dt.strftime("%Y%m%d")
            if not start_date or computed_start < start_date:
                start_date = computed_start
            if not end_date:
                end_date = ref_date

        logger.info(
            f"[futures_contract_daily] 合约={code_info['sina']} "
            f"(ts={code_info['ts_code']}), start={start_date or '-'}, "
            f"end={end_date or '-'}"
        )

        result = super().collect(
            contract=code_info['raw'],
            sina_symbol=code_info['sina'],
            ts_code=code_info['ts_code'],
            start_date=start_date,
            end_date=end_date,
            **kwargs,
        )

        # 补充元数据
        result.meta["contract"] = code_info['raw']
        result.meta["sina_symbol"] = code_info['sina']
        result.meta["ts_code"] = code_info['ts_code']
        result.meta["start_date"] = start_date or ""
        result.meta["end_date"] = end_date or ""

        return result


def get_futures_contract_daily(
    contract: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    lookback: Optional[int] = None,
    **kwargs,
) -> pd.DataFrame:
    """
    便捷函数：获取单个期货合约的历史日线行情（指定具体合约，非主力连续）。

    Args:
        contract: 合约代码，支持 'IF2609' 或带交易所后缀 'IF2609.CFX'
        start_date: 开始日期（YYYYMMDD，也支持 YYYY-MM-DD）
        end_date: 结束日期（YYYYMMDD，也支持 YYYY-MM-DD）
        lookback: 回溯自然日数

    Returns:
        pd.DataFrame: 标准列 date(YYYYMMDD 字符串), open, high, low, close,
        volume, oi，按 date 升序；失败返回空 DataFrame（列结构一致）。
    """
    empty = pd.DataFrame(columns=['date', 'open', 'high', 'low', 'close', 'volume', 'oi'])
    try:
        collector = FuturesContractDailyCollector()
        result = collector.collect(
            contract=contract,
            start_date=start_date,
            end_date=end_date,
            lookback=lookback,
            **kwargs,
        )
        if result.success and result.data is not None:
            return result.data
        return empty
    except Exception as e:
        logger.error(f"[futures_contract_daily] 获取失败: {e}")
        return empty
