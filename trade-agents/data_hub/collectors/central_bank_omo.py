"""
央行OMO操作数据采集器
====================
采集央行公开市场操作（OMO）数据，支持四级降级。

覆盖数据：
- 当日净投放/净回笼金额
- 各期限操作量（7天逆回购/14天/28天/买断式回购等）
- 未到期OMO余额

四级降级：
Tier 1: AKShare (macro_china_central_bank_balance 或 macro_china_new_financial_credit)
Tier 2: Tushare（无直接接口，自动降级）
Tier 3: Coze 数据网关（技能层兜底）
Tier 4: Web爬取（PBOC官网 www.pbc.gov.cn，注意GBK编码）
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

from ..base import BaseCollector, DataResult
from ..config import QUALITY_THRESHOLDS, logger
from ..sources import AKShareSource, TushareSource, CozeGatewaySource, WebSource


class CentralBankOMOCollector(BaseCollector):
    """
    央行OMO操作数据采集器。

    用法：
        collector = CentralBankOMOCollector()
        result = collector.collect()
        omo_data = result.data  # dict with net_injection, operations, outstanding_balance
    """

    data_type: str = "central_bank_omo"
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
        """判断OMO数据是否充足。"""
        if not result.success or result.data is None:
            return False

        data = result.data

        # dict 格式：检查关键字段
        if isinstance(data, dict):
            # 至少有 net_injection 或 operations
            if data.get("net_injection") is not None or data.get("operations") is not None:
                return True
            if data.get("outstanding_balance") is not None:
                return True

        # DataFrame 格式
        if hasattr(data, "shape"):
            return data.shape[0] >= self.min_data_count

        return result.data_count >= self.min_data_count

    def collect(
        self,
        trade_date: Optional[str] = None,
        lookback_days: int = 30,
        **kwargs,
    ) -> DataResult:
        """
        获取央行OMO操作数据。

        Args:
            trade_date: 查询日期（YYYYMMDD），默认今日
            lookback_days: 回溯天数（用于计算未到期余额等）

        Returns:
            DataResult.data: dict — OMO数据
                - date: 查询日期 (YYYYMMDD)
                - net_injection: 当日净投放金额（亿元，正=投放，负=回笼）
                - operations: dict[期限→操作量(亿元)]
                  例: {"7天逆回购": 100, "14天逆回购": 0, "MLF": 0}
                - outstanding_balance: 未到期OMO余额（亿元，None表示不可得）
                - history: DataFrame 近N日操作明细
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
        result.meta["lookback_days"] = lookback_days

        return result


# ============================================================
# 便捷函数
# ============================================================

def get_central_bank_omo(
    trade_date: Optional[str] = None,
    lookback_days: int = 30,
    default: Any = None,
) -> Optional[Dict[str, Any]]:
    """
    便捷函数：获取央行OMO操作数据。

    Args:
        trade_date: 查询日期（YYYYMMDD），默认今日
        lookback_days: 回溯天数
        default: 失败返回值

    Returns:
        dict — OMO数据，失败返回 default
        字段:
            - date: 日期 (YYYYMMDD)
            - net_injection: 净投放额(亿元，正=投放，负=回笼)
            - operations: dict[期限→操作量(亿元)]
            - outstanding_balance: 未到期OMO余额(亿元)
            - history: DataFrame 近N日操作明细
    """
    try:
        collector = CentralBankOMOCollector()
        result = collector.collect(
            trade_date=trade_date,
            lookback_days=lookback_days,
        )
        return result.data if result.success else default
    except Exception as e:
        logger.error(f"[get_central_bank_omo] 获取失败: {e}")
        return default
