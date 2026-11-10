"""
ETF份额数据采集器
==================
采集ETF份额变化数据，支持四级降级。

覆盖标的：主流宽基ETF（沪深300ETF、中证1000ETF、科创50ETF等）

四级降级：
Tier 1: AKShare (fund_etf_scale_sse / fund_etf_scale_szse)
Tier 2: Tushare (fund_share 接口)
Tier 3: Coze 数据网关（技能层兜底）
Tier 4: Web爬取（交易所官网，最后兜底）
"""

from __future__ import annotations

from typing import List, Optional

import pandas as pd

from ..base import BaseCollector, DataResult
from ..config import QUALITY_THRESHOLDS, logger
from ..sources import AKShareSource, TushareSource, CozeGatewaySource, WebSource


# 常用宽基ETF代码映射
WIDE_BASE_ETFS = {
    '510300.SH': '沪深300ETF华泰柏瑞',
    '510500.SH': '中证500ETF南方',
    '512100.SH': '中证1000ETF南方',
    '588000.SH': '科创50ETF华夏',
    '510050.SH': '上证50ETF华夏',
    '159915.SZ': '创业板ETF易方达',
    '159919.SZ': '沪深300ETF嘉实',
}


class EtfShareCollector(BaseCollector):
    """
    ETF份额数据采集器。

    用法：
        collector = EtfShareCollector()

        # 单只ETF份额历史
        result = collector.collect(ts_code="510300.SH", lookback_days=10)

        # 单日数据
        result = collector.collect(ts_code="510300.SH", trade_date="20260723")
    """

    data_type: str = "etf_share"
    min_data_count: int = 1  # 至少1条份额数据

    def __init__(
        self,
        source_priority: Optional[List[str]] = None,
        tushare_token: Optional[str] = None,
    ):
        super().__init__(source_priority=source_priority)

        # 注册默认的四级数据源
        self.register_source(AKShareSource())
        self.register_source(TushareSource(token=tushare_token))
        self.register_source(CozeGatewaySource())
        self.register_source(WebSource())

    def is_data_sufficient(self, result: DataResult) -> bool:
        """判断ETF份额数据是否充足。"""
        if not result.success or result.data is None:
            return False

        data = result.data

        # DataFrame 格式
        if hasattr(data, "shape"):
            return data.shape[0] >= self.min_data_count

        return result.data_count >= self.min_data_count

    def collect(
        self,
        ts_code: Optional[str] = None,
        symbol: Optional[str] = None,
        trade_date: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        lookback_days: int = 10,
        **kwargs,
    ) -> DataResult:
        """
        获取ETF份额数据。

        Args:
            ts_code: ETF代码（tushare格式，如 510300.SH）
            symbol: ETF代码（6位数字，如 510300）
            trade_date: 单日交易日期（YYYYMMDD）
            start_date: 开始日期（YYYYMMDD）
            end_date: 结束日期（YYYYMMDD）
            lookback_days: 回溯天数（默认10天）

        Returns:
            DataResult.data: pd.DataFrame — ETF份额数据
                列: ts_code, trade_date, fund_share（份额，单位：份）
        """
        return super().collect(
            ts_code=ts_code,
            symbol=symbol,
            trade_date=trade_date,
            start_date=start_date,
            end_date=end_date,
            lookback_days=lookback_days,
            **kwargs,
        )


# ============================================================
# 便捷函数
# ============================================================

def get_etf_share(
    ts_code: str = None,
    symbol: str = None,
    trade_date: str = None,
    lookback_days: int = 10,
    default=None,
) -> Optional[pd.DataFrame]:
    """
    便捷函数：获取ETF份额数据。

    Args:
        ts_code: ETF代码（tushare格式）
        symbol: ETF代码（6位数字）
        trade_date: 交易日期
        lookback_days: 回溯天数
        default: 失败返回值

    Returns:
        pd.DataFrame or default
    """
    try:
        collector = EtfShareCollector()
        result = collector.collect(
            ts_code=ts_code,
            symbol=symbol,
            trade_date=trade_date,
            lookback_days=lookback_days,
        )
        return result.data if result.success else default
    except Exception as e:
        logger.error(f"[get_etf_share] 获取失败: {e}")
        return default
