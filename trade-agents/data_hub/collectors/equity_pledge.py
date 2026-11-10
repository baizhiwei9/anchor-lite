"""
股权质押数据采集器
==================
采集A股股权质押比例数据，支持四级降级。

覆盖数据：
- 股票代码、简称
- 质押比例
- 质押股数、质押市值
- 质押笔数

四级降级：
Tier 1: AKShare (stock_gpzy_pledge_ratio_em)
Tier 2: Tushare (pledge_stat 接口) — USE_TUSHARE 开关控制
Tier 3: Coze 数据网关（技能层兜底）
Tier 4: Web爬取（东方财富等，最后兜底）
"""

from __future__ import annotations

from typing import List, Optional

import pandas as pd

from ..base import BaseCollector, DataResult
from ..config import QUALITY_THRESHOLDS, logger
from ..sources import AKShareSource, TushareSource, CozeGatewaySource, WebSource


class EquityPledgeCollector(BaseCollector):
    """
    股权质押数据采集器。

    用法：
        collector = EquityPledgeCollector()
        result = collector.collect(end_date='20260723')
    """

    data_type: str = "equity_pledge"
    min_data_count: int = 10

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
        """判断股权质押数据是否充足。"""
        if not result.success or result.data is None:
            return False

        data = result.data

        if hasattr(data, "shape"):
            return data.shape[0] >= self.min_data_count

        return result.data_count >= self.min_data_count

    def collect(
        self,
        end_date: Optional[str] = None,
        **kwargs,
    ) -> DataResult:
        """
        获取股权质押数据。

        Args:
            end_date: 结束日期（YYYYMMDD），默认最新

        Returns:
            DataResult.data: pd.DataFrame — 股权质押数据
                列: ts_code, name, pledge_ratio(质押比例%),
                     pledge_shares(质押股数, 万股), pledge_market_value(质押市值, 万元),
                     pledge_count(质押笔数), trade_date
        """
        return super().collect(
            end_date=end_date,
            **kwargs,
        )


# ============================================================
# 便捷函数
# ============================================================

def get_equity_pledge(
    end_date: str = None,
    default=None,
) -> Optional[pd.DataFrame]:
    """
    便捷函数：获取股权质押数据。

    Args:
        end_date: 结束日期（YYYYMMDD）
        default: 失败返回值

    Returns:
        pd.DataFrame or default
    """
    try:
        collector = EquityPledgeCollector()
        result = collector.collect(end_date=end_date)
        return result.data if result.success else default
    except Exception as e:
        logger.error(f"[get_equity_pledge] 获取失败: {e}")
        return default
