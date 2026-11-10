"""
股指期货基差采集器
===================
采集股指期货升贴水（基差）数据，支持四级降级。

覆盖品种：
- IF: 沪深300股指期货
- IC: 中证500股指期货
- IM: 中证1000股指期货

四级降级：
Tier 1: AKShare (futures_zh_daily_sina + stock_zh_index_daily)
Tier 2: Tushare (fut_daily + index_daily)
Tier 3: Coze 数据网关 (futures-zh-spot)
Tier 4: Web爬取 (新浪财经 hq.sinajs.cn)
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from ..base import BaseCollector, DataResult
from ..config import QUALITY_THRESHOLDS, logger
from ..sources import AKShareSource, TushareSource, CozeGatewaySource, WebSource


# 期货品种 → 指数映射
FUTURES_INDEX_MAP = {
    'IF': {'index_name': '沪深300', 'index_code': '000300.SH'},
    'IC': {'index_name': '中证500', 'index_code': '000905.SH'},
    'IM': {'index_name': '中证1000', 'index_code': '000852.SH'},
}


class FuturesBasisCollector(BaseCollector):
    """
    股指期货基差采集器。

    用法：
        collector = FuturesBasisCollector()
        result = collector.collect(trade_date="20260716", products=['IF', 'IC', 'IM'])
        basis_data = result.data  # list of dicts
    """

    data_type: str = "futures_basis"
    min_data_count: int = 1  # 至少1个品种有数据

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
        """判断基差数据是否充足。"""
        if not result.success or result.data is None:
            return False

        data = result.data
        if isinstance(data, list):
            return len(data) >= self.min_data_count

        return result.data_count >= self.min_data_count

    def collect(
        self,
        trade_date: Optional[str] = None,
        products: Optional[List[str]] = None,
        **kwargs,
    ) -> DataResult:
        """
        获取股指期货基差数据。

        Args:
            trade_date: 交易日期（YYYYMMDD格式），默认当日
            products: 期货品种列表，默认 ['IF', 'IC', 'IM']

        Returns:
            DataResult.data: list[dict] — 基差数据列表
                每项包含:
                - product: 品种代码 (IF/IC/IM)
                - index_name: 指数名称
                - contract_type: 合约类型 (主力连续/当月合约等)
                - futures_price: 期货价格
                - spot_price: 现货价格
                - basis: 基差 (期货-现货)
                - basis_rate: 基差率 (%)
                - basis_rate_annual: 年化基差率 (%)
                - rank_252: 252日分位 (None)
        """
        # 标准化日期
        if trade_date is None:
            trade_date = datetime.now().strftime("%Y%m%d")

        # 标准化品种列表
        if products is None:
            products = ['IF', 'IC', 'IM']
        else:
            products = [p.upper() for p in products if p.upper() in FUTURES_INDEX_MAP]

        if not products:
            return DataResult(
                data_type=self.data_type,
                success=False,
                error="无有效的期货品种",
            )

        logger.info(
            f"[futures_basis] 交易日期={trade_date}, 品种={','.join(products)}"
        )

        result = super().collect(
            trade_date=trade_date,
            products=products,
            **kwargs,
        )

        # 补充元数据
        result.meta["trade_date"] = trade_date
        result.meta["products"] = products

        return result


def get_futures_basis(
    trade_date: Optional[str] = None,
    products: Optional[List[str]] = None,
    **kwargs,
) -> list:
    """
    便捷函数：获取股指期货基差数据。

    Args:
        trade_date: 交易日期（YYYYMMDD），默认当日
        products: 期货品种列表

    Returns:
        list[dict] — 基差数据，失败返回空list
    """
    try:
        collector = FuturesBasisCollector()
        result = collector.collect(
            trade_date=trade_date,
            products=products,
            **kwargs,
        )
        return result.data if result.data else []
    except Exception as e:
        logger.error(f"[futures_basis] 获取失败: {e}")
        return []
