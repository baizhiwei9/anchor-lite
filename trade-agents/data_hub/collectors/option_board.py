"""
期权原始行情采集器（业务脚本统一入口）
=====================================
为「市场情绪监控 / 中线偏好监控」等业务脚本提供原先直连 akshare 的
期权原始行情接口，输出结构与 akshare 原生函数完全一致，使上层解析逻辑零改动。

覆盖接口：
    get_option_board(symbol, end_month)        -> pd.DataFrame  T型报价
    get_option_underlying_quote(symbol)        -> pd.DataFrame  标的当日行情
    get_option_cffex_zz1000_list()             -> dict          中金所MO合约月份列表
    get_option_cffex_zz1000_spot(symbol)       -> pd.DataFrame  中金所MO T型报价
    get_index_spot_sina(index_codes)           -> pd.DataFrame  新浪指数实时行情
    get_etf_spot_em(etf_codes)                 -> pd.DataFrame  ETF实时行情

四级降级：
    Tier 1: AKShare（原生函数，字段/行数与原直连完全一致）
    Tier 2: Tushare（无对应现货/报价接口，跳过）
    Tier 3: Coze Gateway（技能CLI兜底）
    Tier 4: Web（新浪 hq.sinajs.cn / 官网，列名对齐 akshare）
"""

from __future__ import annotations

from typing import List, Optional

import pandas as pd

from ..base import BaseCollector, DataResult
from ..config import logger
from ..sources import AKShareSource, CozeGatewaySource, TushareSource, WebSource


class _OptionQuoteBaseCollector(BaseCollector):
    """期权原始行情采集器基类：注册四级数据源 + 非空判定。"""

    min_data_count: int = 1

    def __init__(
        self,
        source_priority: Optional[List[str]] = None,
        tushare_token: Optional[str] = None,
    ):
        super().__init__(source_priority=source_priority)

        # 注册四级数据源
        self.register_source(AKShareSource())
        self.register_source(TushareSource(token=tushare_token))
        self.register_source(CozeGatewaySource())
        self.register_source(WebSource())

    def is_data_sufficient(self, result: DataResult) -> bool:
        """DataFrame 看行数，dict/list 看元素数，非空即视为充足。"""
        return result.data_count >= self.min_data_count


class OptionBoardCollector(_OptionQuoteBaseCollector):
    """期权T型报价采集器（akshare: option_finance_board）。"""

    data_type: str = "option_board"

    def collect(self, symbol: str, end_month: Optional[str] = None, **kwargs) -> DataResult:
        return super().collect(symbol=symbol, end_month=end_month, **kwargs)


class OptionUnderlyingQuoteCollector(_OptionQuoteBaseCollector):
    """期权标的当日行情采集器（akshare: option_finance_sse_underlying）。"""

    data_type: str = "option_underlying_quote"

    def collect(self, symbol: str, **kwargs) -> DataResult:
        return super().collect(symbol=symbol, **kwargs)


class OptionCffexZz1000ListCollector(_OptionQuoteBaseCollector):
    """中金所中证1000股指期权合约月份列表采集器。"""

    data_type: str = "option_cffex_zz1000_list"

    def collect(self, **kwargs) -> DataResult:
        return super().collect(**kwargs)


class OptionCffexZz1000SpotCollector(_OptionQuoteBaseCollector):
    """中金所中证1000股指期权T型报价采集器。"""

    data_type: str = "option_cffex_zz1000_spot"

    def collect(self, symbol: str, **kwargs) -> DataResult:
        return super().collect(symbol=symbol, **kwargs)


class IndexSpotSinaCollector(_OptionQuoteBaseCollector):
    """新浪指数实时行情采集器（akshare: stock_zh_index_spot_sina）。"""

    data_type: str = "index_spot_sina"

    def collect(self, index_codes: Optional[List[str]] = None, **kwargs) -> DataResult:
        return super().collect(index_codes=index_codes, **kwargs)


class EtfSpotEmCollector(_OptionQuoteBaseCollector):
    """ETF实时行情采集器（akshare: fund_etf_spot_em）。"""

    data_type: str = "etf_spot_em"

    def collect(self, etf_codes: Optional[List[str]] = None, **kwargs) -> DataResult:
        return super().collect(etf_codes=etf_codes, **kwargs)


# ============================================================
# 便捷函数（失败统一返回空，不抛异常）
# ============================================================

def _empty_if_failed(result: DataResult, tag: str):
    if result is not None and result.success and result.data is not None:
        return result.data
    err = getattr(result, "error", "") if result is not None else "无结果"
    logger.warning(f"[{tag}] 全部数据源失败: {err}")
    return None


def get_option_board(
    symbol: str,
    end_month: Optional[str] = None,
    source_priority: Optional[List[str]] = None,
) -> pd.DataFrame:
    """期权T型报价（失败返回空 DataFrame）。"""
    try:
        result = OptionBoardCollector(source_priority=source_priority).collect(
            symbol=symbol, end_month=end_month
        )
        data = _empty_if_failed(result, "option_board")
        return data if data is not None else pd.DataFrame()
    except Exception as e:
        logger.error(f"[option_board] 获取失败: {e}")
        return pd.DataFrame()


def get_option_underlying_quote(
    symbol: str,
    source_priority: Optional[List[str]] = None,
) -> pd.DataFrame:
    """期权标的当日行情（失败返回空 DataFrame）。"""
    try:
        result = OptionUnderlyingQuoteCollector(source_priority=source_priority).collect(
            symbol=symbol
        )
        data = _empty_if_failed(result, "option_underlying_quote")
        return data if data is not None else pd.DataFrame()
    except Exception as e:
        logger.error(f"[option_underlying_quote] 获取失败: {e}")
        return pd.DataFrame()


def get_option_cffex_zz1000_list(
    source_priority: Optional[List[str]] = None,
) -> dict:
    """中金所中证1000股指期权合约月份列表（失败返回空 dict）。"""
    try:
        result = OptionCffexZz1000ListCollector(source_priority=source_priority).collect()
        data = _empty_if_failed(result, "option_cffex_zz1000_list")
        return data if data is not None else {}
    except Exception as e:
        logger.error(f"[option_cffex_zz1000_list] 获取失败: {e}")
        return {}


def get_option_cffex_zz1000_spot(
    symbol: str,
    source_priority: Optional[List[str]] = None,
) -> pd.DataFrame:
    """中金所中证1000股指期权T型报价（失败返回空 DataFrame）。"""
    try:
        result = OptionCffexZz1000SpotCollector(source_priority=source_priority).collect(
            symbol=symbol
        )
        data = _empty_if_failed(result, "option_cffex_zz1000_spot")
        return data if data is not None else pd.DataFrame()
    except Exception as e:
        logger.error(f"[option_cffex_zz1000_spot] 获取失败: {e}")
        return pd.DataFrame()


def get_index_spot_sina(
    index_codes: Optional[List[str]] = None,
    source_priority: Optional[List[str]] = None,
) -> pd.DataFrame:
    """新浪指数实时行情（失败返回空 DataFrame）。

    列: 代码(sh000300) / 名称 / 最新价 / 涨跌额 / 涨跌幅 / 昨收 / 今开 / 最高 / 最低 / 成交量 / 成交额
    """
    try:
        result = IndexSpotSinaCollector(source_priority=source_priority).collect(
            index_codes=index_codes
        )
        data = _empty_if_failed(result, "index_spot_sina")
        return data if data is not None else pd.DataFrame()
    except Exception as e:
        logger.error(f"[index_spot_sina] 获取失败: {e}")
        return pd.DataFrame()


def get_etf_spot_em(
    etf_codes: Optional[List[str]] = None,
    source_priority: Optional[List[str]] = None,
) -> pd.DataFrame:
    """ETF实时行情（失败返回空 DataFrame）。

    列: 代码(510300，不带交易所前缀) / 名称 / 最新价 / ...
    """
    try:
        result = EtfSpotEmCollector(source_priority=source_priority).collect(
            etf_codes=etf_codes
        )
        data = _empty_if_failed(result, "etf_spot_em")
        return data if data is not None else pd.DataFrame()
    except Exception as e:
        logger.error(f"[etf_spot_em] 获取失败: {e}")
        return pd.DataFrame()
