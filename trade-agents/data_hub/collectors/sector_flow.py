"""
板块资金流数据采集器
======================
采集行业/概念板块资金流数据，支持四级降级。

覆盖数据：
- 板块主力净流入额
- 板块涨跌幅
- 领涨股
- 板块资金流占全市场比例

四级降级：
Tier 1: AKShare (stock_sector_fund_flow_rank)
Tier 2: Tushare （无直接接口，自动降级）
Tier 3: Coze 数据网关（技能层兜底）
Tier 4: Web爬取（东方财富，最后兜底）
"""

from __future__ import annotations

from typing import List, Optional

import pandas as pd

from ..base import BaseCollector, DataResult
from ..config import QUALITY_THRESHOLDS, logger
from ..sources import AKShareSource, TushareSource, CozeGatewaySource, WebSource


# 热门科技板块关键词（用于热门板块筛选）
HOT_TECH_SECTORS = [
    'CPO', '光模块', 'AI硬件', '人工智能', '半导体', '芯片',
    '存储芯片', '算力', '数据中心', '服务器',
]


class SectorFlowCollector(BaseCollector):
    """
    板块资金流数据采集器。

    用法：
        collector = SectorFlowCollector()

        # 行业板块资金流
        result = collector.collect(sector_type='industry')

        # 概念板块资金流
        result = collector.collect(sector_type='concept')
    """

    data_type: str = "sector_flow"
    min_data_count: int = 5  # 至少5个板块数据

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
        """判断板块资金流数据是否充足。"""
        if not result.success or result.data is None:
            return False

        data = result.data

        if hasattr(data, "shape"):
            return data.shape[0] >= self.min_data_count

        return result.data_count >= self.min_data_count

    def collect(
        self,
        sector_type: str = 'industry',
        trade_date: Optional[str] = None,
        **kwargs,
    ) -> DataResult:
        """
        获取板块资金流数据。

        Args:
            sector_type: 板块类型（industry 行业 / concept 概念）
            trade_date: 交易日期（YYYYMMDD），默认今日

        Returns:
            DataResult.data: pd.DataFrame — 板块资金流数据
                列: sector_name, main_net_inflow, main_net_inflow_pct,
                     change_pct, company_count, leading_stock, flow_pct_of_total
        """
        return super().collect(
            sector_type=sector_type,
            trade_date=trade_date,
            **kwargs,
        )


# ============================================================
# 便捷函数
# ============================================================

def get_sector_flow(
    sector_type: str = 'industry',
    trade_date: str = None,
    default=None,
) -> Optional[pd.DataFrame]:
    """
    便捷函数：获取板块资金流数据。

    Args:
        sector_type: 板块类型（industry / concept）
        trade_date: 交易日期
        default: 失败返回值

    Returns:
        pd.DataFrame or default
    """
    try:
        collector = SectorFlowCollector()
        result = collector.collect(
            sector_type=sector_type,
            trade_date=trade_date,
        )
        return result.data if result.success else default
    except Exception as e:
        logger.error(f"[get_sector_flow] 获取失败: {e}")
        return default


def get_hot_sector_flow(
    sector_keywords: List[str] = None,
    sector_type: str = 'concept',
    trade_date: str = None,
    default=None,
) -> Optional[pd.DataFrame]:
    """
    便捷函数：获取指定热门板块的资金流数据。

    Args:
        sector_keywords: 板块关键词列表，如 ['CPO', '半导体', 'AI']
        sector_type: 板块类型
        trade_date: 交易日期
        default: 失败返回值

    Returns:
        pd.DataFrame — 筛选后的热门板块资金流数据
    """
    df = get_sector_flow(sector_type=sector_type, trade_date=trade_date)
    if df is None or df.empty:
        return default

    if sector_keywords is None:
        sector_keywords = HOT_TECH_SECTORS

    # 按关键词筛选
    mask = pd.Series(False, index=df.index)
    if 'sector_name' in df.columns:
        for kw in sector_keywords:
            mask = mask | df['sector_name'].astype(str).str.contains(kw, na=False)

    result = df[mask].reset_index(drop=True)
    return result if not result.empty else default
