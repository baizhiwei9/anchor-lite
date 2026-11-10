"""
北向资金数据采集器
==================
采集沪深港通北向资金净流入数据，支持四级降级。

覆盖数据：
- 当日成交净买额
- 买入/卖出成交额
- 历史累计净买额
- 持股市值

四级降级：
Tier 1: AKShare (stock_hsgt_hist_em)
Tier 2: Tushare (moneyflow_hsgt 接口) — USE_TUSHARE 开关控制
Tier 3: Coze 数据网关（技能层兜底）
Tier 4: Web爬取（东方财富/新浪等，最后兜底）
"""

from __future__ import annotations

from typing import List, Optional

import pandas as pd

from ..base import BaseCollector, DataResult
from ..config import QUALITY_THRESHOLDS, logger
from ..sources import AKShareSource, TushareSource, CozeGatewaySource, WebSource


class NorthboundFlowCollector(BaseCollector):
    """
    北向资金数据采集器。

    用法：
        collector = NorthboundFlowCollector()

        # 获取全部历史数据
        result = collector.collect()

        # 指定日期范围
        result = collector.collect(start_date='20260101', end_date='20260723')
    """

    data_type: str = "northbound_flow"
    min_data_count: int = 1

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
        """判断北向资金数据是否充足。"""
        if not result.success or result.data is None:
            return False

        data = result.data

        if hasattr(data, "shape"):
            return data.shape[0] >= self.min_data_count

        return result.data_count >= self.min_data_count

    def collect(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        lookback_days: int = 60,
        **kwargs,
    ) -> DataResult:
        """
        获取北向资金数据。

        Args:
            start_date: 开始日期（YYYYMMDD）
            end_date: 结束日期（YYYYMMDD）
            lookback_days: 回溯天数（默认60天，当未指定 start_date 时使用）

        Returns:
            DataResult.data: pd.DataFrame — 北向资金数据
                列: trade_date, north_money(当日成交净买额, 万元),
                     buy_amount(买入成交额), sell_amount(卖出成交额),
                     cum_net_amount(历史累计净买额), hold_value(持股市值)
        """
        return super().collect(
            start_date=start_date,
            end_date=end_date,
            lookback_days=lookback_days,
            **kwargs,
        )


# ============================================================
# 便捷函数
# ============================================================

def get_northbound_flow(
    start_date: str = None,
    end_date: str = None,
    lookback_days: int = 60,
    default=None,
) -> Optional[pd.DataFrame]:
    """
    便捷函数：获取北向资金数据。

    Args:
        start_date: 开始日期（YYYYMMDD）
        end_date: 结束日期（YYYYMMDD）
        lookback_days: 回溯天数
        default: 失败返回值

    Returns:
        pd.DataFrame or default
    """
    try:
        collector = NorthboundFlowCollector()
        result = collector.collect(
            start_date=start_date,
            end_date=end_date,
            lookback_days=lookback_days,
        )
        return result.data if result.success else default
    except Exception as e:
        logger.error(f"[get_northbound_flow] 获取失败: {e}")
        return default
