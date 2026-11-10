"""
国债期货历史数据采集器
======================
采集国债期货（TL等）主力合约日K线数据，支持四级降级。

覆盖数据：
- TL（30年国债期货）主力合约日K线
- 20日内上涨天数占比等衍生指标

四级降级：
Tier 1: AKShare (futures_main_sina)
Tier 2: Tushare（rb_daily 等接口）→ Web
Tier 3: Coze 数据网关（技能层兜底）
Tier 4: Web爬取（新浪期货等）
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

from ..base import BaseCollector, DataResult
from ..config import QUALITY_THRESHOLDS, logger
from ..sources import AKShareSource, TushareSource, CozeGatewaySource, WebSource


# 国债期货品种映射
TREASURY_FUTURES_MAP = {
    "TL": "TL0",   # 30年国债期货主力
    "T": "T0",     # 10年国债期货主力
    "TF": "TF0",   # 5年国债期货主力
    "TS": "TS0",   # 2年国债期货主力
}


class TreasuryFuturesCollector(BaseCollector):
    """
    国债期货历史数据采集器。

    用法：
        collector = TreasuryFuturesCollector()
        result = collector.collect(symbol="TL")
        data = result.data  # dict with history, up_ratio_20d, etc.
    """

    data_type: str = "treasury_futures"
    min_data_count: int = 10  # 至少10个交易日数据

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
        """判断国债期货数据是否充足。"""
        if not result.success or result.data is None:
            return False

        data = result.data

        if isinstance(data, dict):
            history = data.get("history")
            if history is not None and hasattr(history, "shape"):
                return history.shape[0] >= self.min_data_count
            # 也可以检查 up_ratio_20d
            if data.get("up_ratio_20d") is not None:
                return True

        if hasattr(data, "shape"):
            return data.shape[0] >= self.min_data_count

        return result.data_count >= self.min_data_count

    def collect(
        self,
        symbol: str = "TL",
        lookback_days: int = 30,
        **kwargs,
    ) -> DataResult:
        """
        获取国债期货历史数据。

        Args:
            symbol: 期货品种（TL/T/TF/TS），默认TL
            lookback_days: 回溯天数，默认30（至少覆盖20个交易日）

        Returns:
            DataResult.data: dict — 国债期货数据
                - symbol: 品种代码 (str)
                - latest_close: 最新收盘价 (float)
                - latest_date: 最新日期 (str, YYYY-MM-DD)
                - up_ratio_20d: 近20个交易日上涨天数占比 (float, 0~1)
                - history: DataFrame 日K线数据
                  列: date, open, high, low, close, volume, hold
        """
        symbol = symbol.upper()
        if symbol not in TREASURY_FUTURES_MAP:
            return DataResult(
                data_type=self.data_type,
                success=False,
                error=f"不支持的国债期货品种: {symbol}，支持: {list(TREASURY_FUTURES_MAP.keys())}",
            )

        result = super().collect(
            symbol=symbol,
            lookback_days=lookback_days,
            **kwargs,
        )

        # 补充元数据
        result.meta["symbol"] = symbol
        result.meta["lookback_days"] = lookback_days

        return result


# ============================================================
# 便捷函数
# ============================================================

def get_treasury_futures(
    symbol: str = "TL",
    lookback_days: int = 30,
    default: Any = None,
) -> Optional[Dict[str, Any]]:
    """
    便捷函数：获取国债期货历史数据。

    Args:
        symbol: 期货品种（TL/T/TF/TS），默认TL
        lookback_days: 回溯天数
        default: 失败返回值

    Returns:
        dict — 国债期货数据，失败返回 default
        字段:
            - symbol: 品种代码
            - latest_close: 最新收盘价
            - latest_date: 最新日期
            - up_ratio_20d: 近20日上涨天数占比
            - history: DataFrame 日K线
    """
    try:
        collector = TreasuryFuturesCollector()
        result = collector.collect(
            symbol=symbol,
            lookback_days=lookback_days,
        )
        return result.data if result.success else default
    except Exception as e:
        logger.error(f"[get_treasury_futures] 获取失败: {e}")
        return default
