"""
交易所回购利率采集器
====================
采集交易所国债逆回购利率数据，支持四级降级。

覆盖数据：
- GC001（1天期交易所国债逆回购利率）
- GC007（7天期交易所国债逆回购利率）
- FR001/FR007/FR014（回购定盘利率）

四级降级：
Tier 1: AKShare (repo_rate_query)
Tier 2: Tushare（无直接接口，自动降级）
Tier 3: Coze 数据网关（技能层兜底）
Tier 4: Web爬取（上交所/深交所）
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from ..base import BaseCollector, DataResult
from ..config import QUALITY_THRESHOLDS, logger
from ..sources import AKShareSource, TushareSource, CozeGatewaySource, WebSource


class RepoExchangeCollector(BaseCollector):
    """
    交易所回购利率采集器。

    用法：
        collector = RepoExchangeCollector()
        result = collector.collect()
        repo_data = result.data  # dict with gc001, gc007, history
    """

    data_type: str = "repo_exchange"
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
        """判断回购利率数据是否充足。"""
        if not result.success or result.data is None:
            return False

        data = result.data

        if isinstance(data, dict):
            # 至少有 gc001 或 gc007
            if data.get("gc001") is not None or data.get("gc007") is not None:
                return True
            latest = data.get("latest", {})
            if latest and any(v is not None for v in latest.values()):
                return True

        if hasattr(data, "shape"):
            return data.shape[0] >= self.min_data_count

        return result.data_count >= self.min_data_count

    def collect(
        self,
        trade_date: Optional[str] = None,
        lookback_days: int = 20,
        **kwargs,
    ) -> DataResult:
        """
        获取交易所回购利率数据。

        Args:
            trade_date: 查询日期（YYYYMMDD），默认今日
            lookback_days: 历史回溯天数

        Returns:
            DataResult.data: dict — 回购利率数据
                - date: 查询日期 (YYYYMMDD)
                - latest: dict
                    - FR001: 1天回购定盘利率(%)
                    - FR007: 7天回购定盘利率(%)
                    - FR014: 14天回购定盘利率(%)
                - gc001: GC001收盘年化利率(%)
                - gc007: GC007收盘年化利率(%)
                - history: DataFrame 近N日回购利率
        """
        if trade_date is None:
            trade_date = datetime.now().strftime("%Y%m%d")

        result = super().collect(
            trade_date=trade_date,
            lookback_days=lookback_days,
            **kwargs,
        )

        # 补充元数据
        result.meta["trade_date"] = trade_date

        return result


# ============================================================
# 便捷函数
# ============================================================

def get_repo_exchange(
    trade_date: Optional[str] = None,
    lookback_days: int = 20,
    default: Any = None,
) -> Optional[Dict[str, Any]]:
    """
    便捷函数：获取交易所回购利率数据。

    Args:
        trade_date: 查询日期（YYYYMMDD），默认今日
        lookback_days: 历史回溯天数
        default: 失败返回值

    Returns:
        dict — 回购利率数据，失败返回 default
        字段:
            - date: 日期 (YYYYMMDD)
            - latest: {"FR001": x, "FR007": x, "FR014": x}
            - gc001: GC001收盘年化利率(%)
            - gc007: GC007收盘年化利率(%)
            - history: DataFrame
    """
    try:
        collector = RepoExchangeCollector()
        result = collector.collect(
            trade_date=trade_date,
            lookback_days=lookback_days,
        )
        return result.data if result.success else default
    except Exception as e:
        logger.error(f"[get_repo_exchange] 获取失败: {e}")
        return default
