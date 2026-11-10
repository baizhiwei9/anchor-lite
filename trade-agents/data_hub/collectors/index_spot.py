"""
指数实时行情采集器
=================
采集A股主要指数实时行情数据，支持四级降级。

四级降级：
Tier 1: AKShare (stock_zh_index_spot_em / stock_zh_index_spot_sina)
Tier 2: Tushare (realtime_quote 指数实时)
Tier 3: Coze Gateway（技能CLI兜底）
Tier 4: Web层直接请求新浪 hq.sinajs.cn 接口
"""

from __future__ import annotations

from typing import List, Optional

import pandas as pd

from ..base import BaseCollector, DataResult
from ..config import QUALITY_THRESHOLDS, logger
from ..sources import AKShareSource, TushareSource, CozeGatewaySource, WebSource


# 常用主要指数代码（新浪格式）
MAJOR_INDEX_CODES = [
    'sh000001',  # 上证指数
    'sh000300',  # 沪深300
    'sh000905',  # 中证500
    'sh000852',  # 中证1000
    'sh000016',  # 上证50
    'sh000688',  # 科创50
    'sz399001',  # 深证成指
    'sz399006',  # 创业板指
    'sz399106',  # 深证综指
]


class IndexSpotCollector(BaseCollector):
    """
    指数实时行情采集器。

    用法：
        collector = IndexSpotCollector()
        result = collector.collect()  # 主要指数
        result = collector.collect(index_codes=['sh000001', 'sz399006'])
    """

    data_type: str = "index_spot"
    min_data_count: int = 5  # 至少5个主要指数

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
        """判断指数数据是否充足。"""
        return result.data_count >= self.min_data_count

    def collect(
        self,
        index_codes: Optional[List[str]] = None,
        **kwargs,
    ) -> DataResult:
        """
        获取指数实时行情。

        Args:
            index_codes: 指数代码列表（新浪格式如 sh000001，或tushare格式）
                         None=获取主要指数

        Returns:
            DataResult.data: pd.DataFrame — 指数实时行情
                列: ts_code, symbol, name, price, change, change_pct,
                    open, high, low, prev_close, volume, amount
        """
        result = super().collect(index_codes=index_codes, **kwargs)

        result.meta["index_codes"] = index_codes or MAJOR_INDEX_CODES
        return result


def get_index_spot(
    index_codes: Optional[List[str]] = None,
    **kwargs,
) -> pd.DataFrame:
    """
    便捷函数：获取指数实时行情。

    Args:
        index_codes: 指数代码列表（None=主要指数）

    Returns:
        pd.DataFrame — 指数实时行情，失败返回空DataFrame
    """
    try:
        collector = IndexSpotCollector()
        result = collector.collect(index_codes=index_codes, **kwargs)
        return result.data if result.data is not None else pd.DataFrame()
    except Exception as e:
        logger.error(f"[index_spot] 获取失败: {e}")
        return pd.DataFrame()
