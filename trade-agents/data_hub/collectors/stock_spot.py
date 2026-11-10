"""
A股实时行情采集器
=================
采集A股实时行情数据，支持四级降级。

支持模式：
1. 全市场实时行情（默认，覆盖沪深北交易所）
2. 指定股票代码列表的批量实时行情

四级降级：
Tier 1: AKShare (stock_zh_a_spot_em / stock_zh_a_spot)
Tier 2: Tushare (realtime_quote 接口，需指定ts_code_list)
Tier 3: Coze Gateway（技能CLI兜底）
Tier 4: Web层直接请求新浪分页接口
"""

from __future__ import annotations

from typing import List, Optional

import pandas as pd

from ..base import BaseCollector, DataResult
from ..config import QUALITY_THRESHOLDS, logger
from ..sources import AKShareSource, TushareSource, CozeGatewaySource, WebSource


class StockSpotCollector(BaseCollector):
    """
    A股实时行情采集器。

    用法：
        collector = StockSpotCollector()
        result = collector.collect()  # 全市场实时行情
        result = collector.collect(ts_code_list=['000001.SZ', '600519.SH'])  # 批量
    """

    data_type: str = "stock_spot"
    min_data_count: int = 3000  # 全市场至少3000只才算合格

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
        """判断实时行情数据是否充足。"""
        return result.data_count >= self.min_data_count

    def collect(
        self,
        market: str = "all",
        ts_code_list: Optional[List[str]] = None,
        **kwargs,
    ) -> DataResult:
        """
        获取A股实时行情。

        Args:
            market: 市场（all/SH/SZ/BJ），默认 all
            ts_code_list: 目标股票代码列表（tushare格式）；None=全市场

        Returns:
            DataResult.data: pd.DataFrame — 实时行情数据
                列包含: ts_code, symbol, name, price, change, change_pct,
                       open, high, low, prev_close, volume, amount, turnover
        """
        if ts_code_list:
            # 批量模式：降低阈值到列表长度的 80%
            expected = max(1, int(len(ts_code_list) * 0.8))
            original_min = self.min_data_count
            self.min_data_count = expected
            try:
                result = super().collect(
                    market=market,
                    ts_code_list=ts_code_list,
                    **kwargs,
                )
            finally:
                self.min_data_count = original_min
        else:
            # 全市场模式
            result = super().collect(
                market=market,
                ts_code_list=ts_code_list,
                **kwargs,
            )

        # 补充元数据
        result.meta["market"] = market
        result.meta["ts_code_count"] = len(ts_code_list) if ts_code_list else 0
        result.meta["mode"] = "batch" if ts_code_list else "all"

        return result


def get_stock_spot(
    market: str = "all",
    ts_code_list: Optional[List[str]] = None,
    **kwargs,
) -> pd.DataFrame:
    """
    便捷函数：获取A股实时行情。

    Args:
        market: 市场类型
        ts_code_list: 目标股票代码列表（None=全市场）

    Returns:
        pd.DataFrame — 实时行情数据，失败返回空DataFrame
    """
    try:
        collector = StockSpotCollector()
        result = collector.collect(market=market, ts_code_list=ts_code_list, **kwargs)
        return result.data if result.data is not None else pd.DataFrame()
    except Exception as e:
        logger.error(f"[stock_spot] 获取失败: {e}")
        return pd.DataFrame()
