"""
大小盘对比指标采集器
====================
采集中证2000/沪深300归一化比值等大小盘风格指标，支持四级降级。

覆盖数据：
- 中证2000/沪深300归一化比值（基期2021-12-31=100）
- 小市值指数行情
- 大小盘轮动信号

四级降级：
Tier 1: AKShare (stock_zh_index_daily / stock_zh_index_hist_csindex)
Tier 2: Tushare（index_daily 接口）
Tier 3: Coze 数据网关（技能层兜底）
Tier 4: Web爬取（中证指数官网等）
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

from ..base import BaseCollector, DataResult
from ..config import QUALITY_THRESHOLDS, logger
from ..sources import AKShareSource, TushareSource, CozeGatewaySource, WebSource


# 指数代码映射
SIZE_STYLE_INDEX_MAP = {
    "csi2000": "932000",   # 中证2000
    "csi300": "000300",    # 沪深300
    "csi1000": "000906",   # 中证1000（小市值代理）
    "csi500": "000905",    # 中证500
}

# 归一化基期
BASE_DATE = "20211231"


class SizeStyleCollector(BaseCollector):
    """
    大小盘对比指标采集器。

    用法：
        collector = SizeStyleCollector()
        result = collector.collect()
        data = result.data  # dict with ratio, small_cap_index, etc.
    """

    data_type: str = "size_style"
    min_data_count: int = 10  # 至少10日数据

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
        """判断大小盘数据是否充足。"""
        if not result.success or result.data is None:
            return False

        data = result.data

        if isinstance(data, dict):
            # 至少有 ratio
            if data.get("ratio") is not None:
                return True
            history = data.get("history")
            if history is not None and hasattr(history, "shape"):
                return history.shape[0] >= self.min_data_count

        if hasattr(data, "shape"):
            return data.shape[0] >= self.min_data_count

        return result.data_count >= self.min_data_count

    def collect(
        self,
        lookback_days: int = 60,
        base_date: str = BASE_DATE,
        **kwargs,
    ) -> DataResult:
        """
        获取大小盘对比指标数据。

        Args:
            lookback_days: 回溯天数，默认60
            base_date: 归一化基期，默认20211231

        Returns:
            DataResult.data: dict — 大小盘对比数据
                - base_date: 归一化基期 (YYYYMMDD)
                - ratio: 最新中证2000/沪深300归一化比值 (float, 基期=100)
                - ratio_date: 最新日期 (YYYY-MM-DD)
                - small_cap_close: 中证2000最新收盘价 (float)
                - large_cap_close: 沪深300最新收盘价 (float)
                - history: DataFrame
                  列: date, csi2000_close, csi300_close, ratio
                - ratio_change_5d: 5日比值变化 (float, %)
                - ratio_change_20d: 20日比值变化 (float, %)
        """
        result = super().collect(
            lookback_days=lookback_days,
            base_date=base_date,
            **kwargs,
        )

        # 补充元数据
        result.meta["base_date"] = base_date
        result.meta["lookback_days"] = lookback_days

        return result


# ============================================================
# 便捷函数
# ============================================================

def get_size_style(
    lookback_days: int = 60,
    base_date: str = BASE_DATE,
    default: Any = None,
) -> Optional[Dict[str, Any]]:
    """
    便捷函数：获取大小盘对比指标数据。

    Args:
        lookback_days: 回溯天数
        base_date: 归一化基期
        default: 失败返回值

    Returns:
        dict — 大小盘对比数据，失败返回 default
        字段:
            - base_date: 归一化基期
            - ratio: 中证2000/沪深300归一化比值(基期=100)
            - ratio_date: 最新日期
            - small_cap_close: 中证2000收盘价
            - large_cap_close: 沪深300收盘价
            - history: DataFrame
            - ratio_change_5d: 5日变化(%)
            - ratio_change_20d: 20日变化(%)
    """
    try:
        collector = SizeStyleCollector()
        result = collector.collect(
            lookback_days=lookback_days,
            base_date=base_date,
        )
        return result.data if result.success else default
    except Exception as e:
        logger.error(f"[get_size_style] 获取失败: {e}")
        return default
