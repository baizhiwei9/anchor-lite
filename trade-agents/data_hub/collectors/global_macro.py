"""
海外宏观数据采集器
==================
采集海外宏观经济指标，支持四级降级。

覆盖数据：
- 美元指数(DXY)
- 2年美债利率
- 10年美债利率
- 布伦特原油价格
- 离岸人民币(CNH)

四级降级：
Tier 1: AKShare (bond_zh_us_rate / index_global_hist_em / forex_spot_em)
Tier 2: Tushare（us_tycr 美债收益率曲线）→ Web（FRED CSV 接口）
Tier 3: Coze 数据网关（技能层兜底）
Tier 4: Web爬取（FRED / TradingEconomics 等）
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

from ..base import BaseCollector, DataResult
from ..config import QUALITY_THRESHOLDS, logger
from ..sources import AKShareSource, TushareSource, CozeGatewaySource, WebSource


class GlobalMacroCollector(BaseCollector):
    """
    海外宏观数据采集器。

    用法：
        collector = GlobalMacroCollector()
        result = collector.collect()
        data = result.data  # dict with dxy, us_2y, us_10y, brent, cnh, us_treasury_history
    """

    data_type: str = "global_macro"
    min_data_count: int = 3  # 至少3个指标

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
        """判断海外宏观数据是否充足。"""
        if not result.success or result.data is None:
            return False

        data = result.data

        if isinstance(data, dict):
            # 至少有3个非None的指标值
            valid_count = sum(
                1 for k in ["dxy", "us_2y", "us_10y", "brent", "cnh"]
                if data.get(k) is not None
            )
            return valid_count >= self.min_data_count

        return result.data_count >= self.min_data_count

    def collect(
        self,
        trade_date: Optional[str] = None,
        us_treasury_lookback_days: int = 20,
        **kwargs,
    ) -> DataResult:
        """
        获取海外宏观数据。

        Args:
            trade_date: 查询日期（YYYYMMDD），默认今日
            us_treasury_lookback_days: 美债历史回溯天数（用于计算20日上涨天数占比）

        Returns:
            DataResult.data: dict — 海外宏观数据
                - date: 查询日期 (YYYYMMDD)
                - dxy: 美元指数 (float, None=不可得)
                - us_2y: 2年美债利率 (%) (float, None=不可得)
                - us_10y: 10年美债利率 (%) (float, None=不可得)
                - brent: 布伦特原油价格 (USD/bbl) (float, None=不可得)
                - cnh: 离岸人民币 USD/CNH (float, None=不可得)
                - us_2y_history: DataFrame 最近N日2年美债利率
                - us_10y_history: DataFrame 最近N日10年美债利率
                - us_10y_up_ratio_20d: 10年美债近20日上涨天数占比 (float, 0~1)
        """
        if trade_date is None:
            trade_date = datetime.now().strftime("%Y%m%d")

        result = super().collect(
            trade_date=trade_date,
            us_treasury_lookback_days=us_treasury_lookback_days,
            **kwargs,
        )

        # 补充元数据
        result.meta["trade_date"] = trade_date
        result.meta["us_treasury_lookback_days"] = us_treasury_lookback_days

        return result


# ============================================================
# 便捷函数
# ============================================================

def get_global_macro(
    trade_date: Optional[str] = None,
    us_treasury_lookback_days: int = 20,
    default: Any = None,
) -> Optional[Dict[str, Any]]:
    """
    便捷函数：获取海外宏观数据。

    Args:
        trade_date: 查询日期（YYYYMMDD），默认今日
        us_treasury_lookback_days: 美债历史回溯天数
        default: 失败返回值

    Returns:
        dict — 海外宏观数据，失败返回 default
        字段:
            - date: 日期 (YYYYMMDD)
            - dxy: 美元指数
            - us_2y: 2年美债利率(%)
            - us_10y: 10年美债利率(%)
            - brent: 布伦特原油(USD/bbl)
            - cnh: 离岸人民币 USD/CNH
            - us_2y_history: DataFrame
            - us_10y_history: DataFrame
            - us_10y_up_ratio_20d: 10年美债20日上涨天数占比
    """
    try:
        collector = GlobalMacroCollector()
        result = collector.collect(
            trade_date=trade_date,
            us_treasury_lookback_days=us_treasury_lookback_days,
        )
        return result.data if result.success else default
    except Exception as e:
        logger.error(f"[get_global_macro] 获取失败: {e}")
        return default
