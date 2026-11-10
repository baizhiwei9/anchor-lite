"""
券商研报数据采集器
==================
采集个股券商研报（评级/盈利预测/目标价），用于周度多空辩论分析。

数据源（四级降级）：
Tier 1: Tushare（report_rc，券商研报，含评级/eps/pe/roe/tp）
Tier 2: AKShare（研报接口，自动降级）
Tier 3: Coze 数据网关（技能层兜底）
Tier 4: Web爬取（最后兜底）
"""

from __future__ import annotations

from typing import List, Optional

import pandas as pd

from ..base import BaseCollector, DataResult
from ..config import logger
from ..sources import AKShareSource, TushareSource, CozeGatewaySource, WebSource


class ResearchReportCollector(BaseCollector):
    """
    券商研报数据采集器。

    用法：
        collector = ResearchReportCollector()

        # 单日研报
        result = collector.collect(trade_date="20261109")

        # 区间研报（用于周度分析）
        result = collector.collect(start_date="20261103", end_date="20261109")
    """

    data_type: str = "research_report"
    min_data_count: int = 1

    def __init__(
        self,
        source_priority: Optional[List[str]] = None,
        tushare_token: Optional[str] = None,
    ):
        super().__init__(source_priority=source_priority)
        # 研报主要走 Tushare，AKShare 备用
        self.register_source(TushareSource(token=tushare_token))
        self.register_source(AKShareSource())
        self.register_source(CozeGatewaySource())
        self.register_source(WebSource())

    def is_data_sufficient(self, result: DataResult) -> bool:
        """研报数据充足判断：至少 1 条研报。"""
        if not result.success or result.data is None:
            return False
        if hasattr(result.data, "shape"):
            return result.data.shape[0] >= self.min_data_count
        return result.data_count >= self.min_data_count

    def collect(
        self,
        trade_date: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        **kwargs,
    ) -> DataResult:
        """
        获取券商研报数据。

        Args:
            trade_date: 单日研报日期（YYYYMMDD），与 start_date/end_date 二选一
            start_date: 起始日期（YYYYMMDD）
            end_date: 结束日期（YYYYMMDD）

        Returns:
            DataResult.data — DataFrame：个股研报（ts_code/name/report_date/
                              report_title/report_type/classify/org_name/rating/
                              eps/pe/roe/tp 等）
        """
        if not (trade_date or start_date or end_date):
            # 默认取最近一个交易日
            trade_date = pd.Timestamp.now().strftime("%Y%m%d")

        logger.info(
            f"[research_report] 查询研报: trade_date={trade_date} "
            f"start_date={start_date} end_date={end_date}"
        )

        result = super().collect(
            trade_date=trade_date,
            start_date=start_date,
            end_date=end_date,
            **kwargs,
        )

        if result.success and hasattr(result.data, "shape"):
            result.meta["n_reports"] = int(result.data.shape[0])
        return result
