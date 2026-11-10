"""
两融数据采集器
================
采集融资融券数据，支持四级降级。

覆盖数据：
- 融资余额（rzye）
- 融资买入额（rzmre）
- 融券余额（rqye）
- 两融余额（rzrqye）

四级降级：
Tier 1: AKShare (stock_margin_sse / stock_margin_szse)
Tier 2: Tushare (margin 接口)
Tier 3: Coze 数据网关（技能层兜底）
Tier 4: Web爬取（交易所官网，最后兜底）
"""

from __future__ import annotations

from typing import List, Optional

import pandas as pd

from ..base import BaseCollector, DataResult
from ..config import QUALITY_THRESHOLDS, logger
from .. import config
from ..sources import AKShareSource, TushareSource, CozeGatewaySource, WebSource


class MarginTradingCollector(BaseCollector):
    """
    两融数据采集器。

    用法：
        collector = MarginTradingCollector()

        # 沪深两市汇总（默认）
        result = collector.collect(market='all', lookback_days=10)

        # 单市场
        result = collector.collect(market='sh', start_date='20260701', end_date='20260723')
    """

    data_type: str = "margin_trading"
    min_data_count: int = 1

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

        # 注册默认的四级数据源
        self.register_source(AKShareSource())
        self.register_source(TushareSource(token=tushare_token))
        self.register_source(CozeGatewaySource())
        self.register_source(WebSource())

    def is_data_sufficient(self, result: DataResult) -> bool:
        """判断两融数据是否充足。"""
        if not result.success or result.data is None:
            return False

        data = result.data

        if hasattr(data, "shape"):
            return data.shape[0] >= self.min_data_count

        return result.data_count >= self.min_data_count

    def collect(
        self,
        trade_date: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        market: str = 'all',
        lookback_days: int = 10,
        **kwargs,
    ) -> DataResult:
        """
        获取两融数据。

        Args:
            trade_date: 单日交易日期（YYYYMMDD）
            start_date: 开始日期（YYYYMMDD）
            end_date: 结束日期（YYYYMMDD）
            market: 市场（all/sh/sz，默认all汇总两市）
            lookback_days: 回溯天数（默认10天）

        Returns:
            DataResult.data: pd.DataFrame — 两融数据
                列: trade_date, rzye(融资余额), rzmre(融资买入额),
                     rqye(融券余额), rzrqye(两融余额), exchange
        """
        return super().collect(
            trade_date=trade_date,
            start_date=start_date,
            end_date=end_date,
            market=market,
            lookback_days=lookback_days,
            **kwargs,
        )


# ============================================================
# 便捷函数
# ============================================================

def get_margin_trading(
    trade_date: str = None,
    market: str = 'all',
    lookback_days: int = 10,
    default=None,
) -> Optional[pd.DataFrame]:
    """
    便捷函数：获取两融数据。

    Args:
        trade_date: 交易日期
        market: 市场（all/sh/sz）
        lookback_days: 回溯天数
        default: 失败返回值

    Returns:
        pd.DataFrame or default
    """
    try:
        collector = MarginTradingCollector()
        result = collector.collect(
            trade_date=trade_date,
            market=market,
            lookback_days=lookback_days,
        )
        return result.data if result.success else default
    except Exception as e:
        logger.error(f"[get_margin_trading] 获取失败: {e}")
        return default
