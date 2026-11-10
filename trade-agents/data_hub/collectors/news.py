"""
新闻数据采集器
==============
采集个股新闻 + 央视宏观新闻，用于周度多空辩论分析（情绪面视角）。

数据源（四级降级）：
Tier 1: AKShare（stock_news_em 个股新闻 / news_cctv 央视新闻）
Tier 2: Tushare（news 接口，无权限时自动降级）
Tier 3: Coze 数据网关（技能层兜底）
Tier 4: Web爬取（最后兜底）
"""

from __future__ import annotations

from typing import List, Optional

import pandas as pd

from ..base import BaseCollector, DataResult
from ..config import logger
from ..sources import AKShareSource, TushareSource, CozeGatewaySource, WebSource


class NewsCollector(BaseCollector):
    """
    新闻数据采集器。

    用法：
        collector = NewsCollector()

        # 个股新闻
        result = collector.collect(news_type="stock", symbol="000001")

        # 央视宏观新闻
        result = collector.collect(news_type="cctv", date="20261109")
    """

    data_type: str = "news"
    min_data_count: int = 1

    def __init__(
        self,
        source_priority: Optional[List[str]] = None,
        tushare_token: Optional[str] = None,
    ):
        super().__init__(source_priority=source_priority)
        # 新闻主要走 AKShare
        self.register_source(AKShareSource())
        self.register_source(TushareSource(token=tushare_token))
        self.register_source(CozeGatewaySource())
        self.register_source(WebSource())

    def is_data_sufficient(self, result: DataResult) -> bool:
        """新闻数据充足判断：至少 1 条新闻。"""
        if not result.success or result.data is None:
            return False
        if hasattr(result.data, "shape"):
            return result.data.shape[0] >= self.min_data_count
        return result.data_count >= self.min_data_count

    def collect(
        self,
        news_type: str = "stock",
        symbol: Optional[str] = None,
        date: Optional[str] = None,
        **kwargs,
    ) -> DataResult:
        """
        获取新闻数据。

        Args:
            news_type: 新闻类型（stock 个股新闻 / cctv 央视新闻）
            symbol: 股票代码（news_type=stock 时必填，6位数字）
            date: 日期（news_type=cctv 时可选，YYYYMMDD）

        Returns:
            DataResult.data — DataFrame：新闻列表
        """
        news_type = (news_type or "stock").lower()
        if news_type == "stock":
            if not symbol:
                return DataResult(
                    data_type=self.data_type,
                    success=False,
                    error="news_type=stock 需要 symbol 参数（6位股票代码）",
                )
            logger.info(f"[news] 查询个股新闻: symbol={symbol}")
            result = super().collect(news_type="stock", symbol=symbol, **kwargs)
        elif news_type == "cctv":
            logger.info(f"[news] 查询央视新闻: date={date}")
            result = super().collect(news_type="cctv", date=date, **kwargs)
        else:
            return DataResult(
                data_type=self.data_type,
                success=False,
                error=f"不支持的新闻类型: {news_type}，支持: stock / cctv",
            )

        result.meta["news_type"] = news_type
        return result
