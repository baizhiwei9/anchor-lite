"""
期权数据采集器
===============
采集各交易所期权合约及行情数据，支持四级降级。

覆盖标的：
- 上交所(SSE): 300ETF(510300)、500ETF(510500)、科创50ETF(588000)
- 深交所(SZSE): 300ETF(159919)、创业板ETF(159915)
- 中金所(CFFEX): 中证1000股指期权(MO)

四级降级：
Tier 1: AKShare (option_current_day_sse/szse / option_finance_board / option_cffex_*)
Tier 2: Tushare (opt_basic + opt_daily)
Tier 3: Coze 数据网关 (option-sse-list-sina / option-cffex-list-sina 等)
Tier 4: Web爬取 (交易所官网 / 新浪财经)
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from ..base import BaseCollector, DataResult
from ..config import QUALITY_THRESHOLDS, logger
from ..sources import AKShareSource, TushareSource, CozeGatewaySource, WebSource


# 期权标的信息
OPTION_UNDERLYINGS = {
    # 上交所ETF期权
    "510300": {"name": "300ETF沪", "exchange": "SSE", "type": "ETF"},
    "510500": {"name": "500ETF沪", "exchange": "SSE", "type": "ETF"},
    "588000": {"name": "科创50ETF", "exchange": "SSE", "type": "ETF"},
    # 深交所ETF期权
    "159919": {"name": "300ETF深", "exchange": "SZSE", "type": "ETF"},
    "159915": {"name": "创业板ETF", "exchange": "SZSE", "type": "ETF"},
    # 中金所股指期权
    "MO": {"name": "中证1000股指期权", "exchange": "CFFEX", "type": "index"},
}

# 常用标的简称映射
OPTION_SYMBOL_ALIASES = {
    "300etf": "510300",
    "300ETF": "510300",
    "300etf_sh": "510300",
    "300etf_sz": "159919",
    "500etf": "510500",
    "500ETF": "510500",
    "创业板etf": "159915",
    "创业板ETF": "159915",
    "cybetf": "159915",
    "科创50etf": "588000",
    "科创50ETF": "588000",
    "kcb50etf": "588000",
    "中证1000": "MO",
    "zz1000": "MO",
    "1000mo": "MO",
    "MO": "MO",
    "mo": "MO",
}


class OptionDataCollector(BaseCollector):
    """
    期权数据采集器。

    用法：
        collector = OptionDataCollector()
        result = collector.collect(exchange="SSE", underlying="510300", trade_date="20260716")
        data = result.data  # dict with contracts, daily, underlying_price
    """

    data_type: str = "option_data"
    min_data_count: int = QUALITY_THRESHOLDS.min_option_contracts

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
        """判断期权数据是否充足。"""
        if result.data is None:
            return False

        data = result.data
        if isinstance(data, dict):
            contracts = data.get("contracts", pd.DataFrame())
            daily = data.get("daily", pd.DataFrame())
            count = 0
            if hasattr(contracts, "shape"):
                count += contracts.shape[0]
            if hasattr(daily, "shape"):
                count += daily.shape[0]
            return count >= self.min_data_count

        return result.data_count >= self.min_data_count

    def collect(
        self,
        exchange: str = "SSE",
        underlying: Optional[str] = None,
        trade_date: Optional[str] = None,
        **kwargs,
    ) -> DataResult:
        """
        获取期权数据。

        Args:
            exchange: 交易所代码（SSE/SZSE/CFFEX）
            underlying: 标的代码（如 510300、159915、MO 等），可选
            trade_date: 交易日期（YYYYMMDD格式），默认当日

        Returns:
            DataResult.data: dict with keys:
                - contracts: pd.DataFrame 合约基本信息
                - daily: pd.DataFrame 日线行情
                - underlying_price: float 标的现货价格（如有）
        """
        # 标准化日期
        if trade_date is None:
            trade_date = datetime.now().strftime("%Y%m%d")

        # 标准化交易所
        exchange = exchange.upper()

        # 标准化标的代码
        if underlying:
            underlying = self._normalize_underlying(underlying)

        logger.info(
            f"[option_data] 查询期权: exchange={exchange}, "
            f"underlying={underlying or 'all'}, trade_date={trade_date}"
        )

        result = super().collect(
            exchange=exchange,
            underlying=underlying,
            trade_date=trade_date,
            **kwargs,
        )

        # 补充元数据
        result.meta["exchange"] = exchange
        result.meta["underlying"] = underlying or "all"
        result.meta["trade_date"] = trade_date

        return result

    @staticmethod
    def _normalize_underlying(symbol: str) -> str:
        """标准化标的代码。"""
        if symbol in OPTION_SYMBOL_ALIASES:
            return OPTION_SYMBOL_ALIASES[symbol]
        # 检查是否就是代码本身
        if symbol in OPTION_UNDERLYINGS:
            return symbol
        return symbol


def get_option_data(
    exchange: str = "SSE",
    underlying: Optional[str] = None,
    trade_date: Optional[str] = None,
    **kwargs,
) -> Dict:
    """
    便捷函数：获取期权数据。

    Args:
        exchange: 交易所（SSE/SZSE/CFFEX）
        underlying: 标的代码，可选
        trade_date: 交易日期，默认当日

    Returns:
        dict with contracts, daily, underlying_price — 失败返回空dict
    """
    try:
        collector = OptionDataCollector()
        result = collector.collect(
            exchange=exchange,
            underlying=underlying,
            trade_date=trade_date,
            **kwargs,
        )
        return result.data or {}
    except Exception as e:
        logger.error(f"[option_data] 获取失败: {e}")
        return {}


def get_all_option_contracts(trade_date: Optional[str] = None, **kwargs) -> Dict[str, pd.DataFrame]:
    """
    便捷函数：获取全部交易所的期权合约数据。

    Returns:
        dict[exchange] = DataFrame of contracts
    """
    result = {}
    for exchange in ["SSE", "SZSE", "CFFEX"]:
        try:
            data = get_option_data(
                exchange=exchange,
                trade_date=trade_date,
                **kwargs,
            )
            if data and "contracts" in data and not data["contracts"].empty:
                result[exchange] = data["contracts"]
                logger.info(f"[all_options] {exchange}: {len(data['contracts'])} 只合约")
            else:
                result[exchange] = pd.DataFrame()
                logger.warning(f"[all_options] {exchange}: 无数据")
        except Exception as e:
            logger.warning(f"[all_options] {exchange} 获取失败: {e}")
            result[exchange] = pd.DataFrame()

    return result
