"""
data_hub.collectors 采集器包
=============================
各类数据的采集器，实现四级降级逻辑。

可用采集器：
- IndexComponentsCollector: 指数成分股采集器
- OptionDataCollector: 期权数据采集器
- StockDailyCollector: A股日线行情采集器
- FuturesBasisCollector: 股指期货基差采集器
- FuturesContractDailyCollector: 单个期货合约历史日线采集器（指定具体合约，非主力连续）
- InterestRateCollector: 利率数据采集器
- EtfShareCollector: ETF份额数据采集器
- MarginTradingCollector: 两融数据采集器
- SectorFlowCollector: 板块资金流采集器
- CentralBankOMOCollector: 央行OMO操作数据采集器
- GlobalMacroCollector: 海外宏观数据采集器
- RepoExchangeCollector: 交易所回购利率采集器
- TreasuryFuturesCollector: 国债期货历史数据采集器
- SizeStyleCollector: 大小盘对比指标采集器
- StockSpotCollector: A股实时行情采集器
- IndexSpotCollector: 指数实时行情采集器
- NorthboundFlowCollector: 北向资金数据采集器
- EquityPledgeCollector: 股权质押数据采集器
- OptionBoardCollector: 期权T型报价采集器（option_finance_board）
- OptionUnderlyingQuoteCollector: 期权标的当日行情采集器
- OptionCffexZz1000ListCollector: 中金所中证1000股指期权合约月份采集器
- OptionCffexZz1000SpotCollector: 中金所中证1000股指期权T型报价采集器
- IndexSpotSinaCollector: 新浪指数实时行情采集器
- EtfSpotEmCollector: ETF实时行情采集器
"""

from .index_components import (
    IndexComponentsCollector,
    get_index_components,
    get_top2000_stocks,
    INDEX_CODE_MAP,
)
from .option_data import (
    OptionDataCollector,
    get_option_data,
    get_all_option_contracts,
    OPTION_UNDERLYINGS,
    OPTION_SYMBOL_ALIASES,
)
from .option_iv_history import (
    OptionIvHistoryCollector,
    get_option_iv_history,
    get_iv_ma_data,
    iv_bar_visual,
    IV_INDEX_MAP,
    IV_FUND_MAP,
    IV_EXCHANGE_MAP,
)
from .stock_daily import (
    StockDailyCollector,
    get_stock_daily,
    get_market_daily,
    get_stock_daily_batch,
    get_trade_dates,
)
from .stock_spot import (
    StockSpotCollector,
    get_stock_spot,
)
from .index_spot import (
    IndexSpotCollector,
    get_index_spot,
    MAJOR_INDEX_CODES,
)
from .index_daily import (
    IndexDailyCollector,
    get_index_daily,
)
from .futures_basis import (
    FuturesBasisCollector,
    get_futures_basis,
    FUTURES_INDEX_MAP,
)
from .futures_contract_daily import (
    FuturesContractDailyCollector,
    get_futures_contract_daily,
    normalize_contract,
    normalize_futures_contract_df,
)
from .interest_rate import (
    InterestRateCollector,
    get_interest_rate,
    get_shibor,
    get_lpr,
    get_hibor,
    get_bond_yield,
    get_cd_rate_1y,
    SUPPORTED_RATE_TYPES,
    SUB_TYPE_MAP,
)
from .etf_share import (
    EtfShareCollector,
    get_etf_share,
    WIDE_BASE_ETFS,
)
from .margin_trading import (
    MarginTradingCollector,
    get_margin_trading,
)
from .sector_flow import (
    SectorFlowCollector,
    get_sector_flow,
    get_hot_sector_flow,
    HOT_TECH_SECTORS,
)
from .central_bank_omo import (
    CentralBankOMOCollector,
    get_central_bank_omo,
)
from .global_macro import (
    GlobalMacroCollector,
    get_global_macro,
)
from .repo_exchange import (
    RepoExchangeCollector,
    get_repo_exchange,
)
from .treasury_futures import (
    TreasuryFuturesCollector,
    get_treasury_futures,
    TREASURY_FUTURES_MAP,
)
from .etf_daily import (
    EtfDailyCollector,
    get_etf_daily,
)
from .size_style import (
    SizeStyleCollector,
    get_size_style,
    SIZE_STYLE_INDEX_MAP,
    BASE_DATE as SIZE_STYLE_BASE_DATE,
)
from .northbound_flow import (
    NorthboundFlowCollector,
    get_northbound_flow,
)
from .equity_pledge import (
    EquityPledgeCollector,
    get_equity_pledge,
)
from .research_report import (
    ResearchReportCollector,
)
from .news import (
    NewsCollector,
)
from .option_board import (
    OptionBoardCollector,
    OptionUnderlyingQuoteCollector,
    OptionCffexZz1000ListCollector,
    OptionCffexZz1000SpotCollector,
    IndexSpotSinaCollector,
    EtfSpotEmCollector,
    get_option_board,
    get_option_underlying_quote,
    get_option_cffex_zz1000_list,
    get_option_cffex_zz1000_spot,
    get_index_spot_sina,
    get_etf_spot_em,
)
from .option_chain import (
    OPTION_CHAIN_TARGETS,
    CHAIN_COLUMNS,
    get_option_chain,
    get_all_option_chains,
)
from .option_chain_realtime import (
    REALTIME_OPTION_TARGETS,
    CORE_TARGET_KEYS,
    REALTIME_CHAIN_COLUMNS,
    get_realtime_option_chain,
    get_all_realtime_option_chains,
    get_realtime_futures,
    is_trading_hours,
)

__all__ = [
    # 指数成分股
    "IndexComponentsCollector",
    "get_index_components",
    "get_top2000_stocks",
    "INDEX_CODE_MAP",
    # 期权数据
    "OptionDataCollector",
    "get_option_data",
    "get_all_option_contracts",
    "OPTION_UNDERLYINGS",
    "OPTION_SYMBOL_ALIASES",
    # 期权IV历史
    "OptionIvHistoryCollector",
    "get_option_iv_history",
    "get_iv_ma_data",
    "iv_bar_visual",
    "IV_INDEX_MAP",
    "IV_FUND_MAP",
    "IV_EXCHANGE_MAP",
    # A股日线
    "StockDailyCollector",
    "get_stock_daily",
    "get_market_daily",
    # A股实时行情
    "StockSpotCollector",
    "get_stock_spot",
    # 指数实时行情
    "IndexSpotCollector",
    "get_index_spot",
    "MAJOR_INDEX_CODES",
    # 指数日线行情
    "IndexDailyCollector",
    "get_index_daily",
    # 股指期货基差
    "FuturesBasisCollector",
    "get_futures_basis",
    "FUTURES_INDEX_MAP",
    # 单个期货合约历史日线
    "FuturesContractDailyCollector",
    "get_futures_contract_daily",
    "normalize_contract",
    "normalize_futures_contract_df",
    # 利率数据
    "InterestRateCollector",
    "get_interest_rate",
    "get_shibor",
    "get_lpr",
    "get_hibor",
    "get_bond_yield",
    "get_cd_rate_1y",
    "SUPPORTED_RATE_TYPES",
    "SUB_TYPE_MAP",
    # ETF份额
    "EtfShareCollector",
    "get_etf_share",
    "WIDE_BASE_ETFS",
    # 两融数据
    "MarginTradingCollector",
    "get_margin_trading",
    # 板块资金流
    "SectorFlowCollector",
    "get_sector_flow",
    "get_hot_sector_flow",
    "HOT_TECH_SECTORS",
    # 央行OMO操作
    "CentralBankOMOCollector",
    "get_central_bank_omo",
    # 海外宏观
    "GlobalMacroCollector",
    "get_global_macro",
    # 交易所回购利率
    "RepoExchangeCollector",
    "get_repo_exchange",
    # 国债期货历史
    "TreasuryFuturesCollector",
    "get_treasury_futures",
    "TREASURY_FUTURES_MAP",
    # ETF日线
    "EtfDailyCollector",
    "get_etf_daily",
    # 大小盘对比
    "SizeStyleCollector",
    "get_size_style",
    "SIZE_STYLE_INDEX_MAP",
    "SIZE_STYLE_BASE_DATE",
    # 北向资金
    "NorthboundFlowCollector",
    "get_northbound_flow",
    # 股权质押
    "EquityPledgeCollector",
    "get_equity_pledge",
    # 券商研报
    "ResearchReportCollector",
    # 新闻
    "NewsCollector",
    # 期权原始行情（业务脚本统一入口）
    "OptionBoardCollector",
    "OptionUnderlyingQuoteCollector",
    "OptionCffexZz1000ListCollector",
    "OptionCffexZz1000SpotCollector",
    "IndexSpotSinaCollector",
    "EtfSpotEmCollector",
    "get_option_board",
    "get_option_underlying_quote",
    "get_option_cffex_zz1000_list",
    "get_option_cffex_zz1000_spot",
    "get_index_spot_sina",
    "get_etf_spot_em",
    # 期权全量链（5大宽基统一入口）
    "OPTION_CHAIN_TARGETS",
    "CHAIN_COLUMNS",
    "get_option_chain",
    "get_all_option_chains",
    # 实时期权链（盘中实时持仓量OI，新浪首源，多源冗余预留）
    "REALTIME_OPTION_TARGETS",
    "CORE_TARGET_KEYS",
    "REALTIME_CHAIN_COLUMNS",
    "get_realtime_option_chain",
    "get_all_realtime_option_chains",
    "get_realtime_futures",
    "is_trading_hours",
]
