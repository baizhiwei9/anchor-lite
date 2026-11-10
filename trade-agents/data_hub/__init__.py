"""
data_hub — 统一数据采集层
==========================

为多个量化监控脚本提供统一的数据获取入口，支持四级数据源降级：
    第1级：AKShare（免费开源，优先）
    第2级：Tushare（专业财经数据，需token）
    第3级：专业技能（如同花顺iFinD等已安装技能，更稳定更丰富）
    第4级：网页爬取（公开网页，最后兜底）

核心特性：
- 四级降级：一级失败自动降级到下一级，逐级降级直到四级
- Tushare全局串行：所有Tushare调用间隔≥0.55s，避免触发频率限制
- 技能层兜底：通过已注册技能CLI获取数据，稳定性高
- 宁空勿假：每级都有重试，失败有明确日志，绝不造假
- 零LLM依赖：纯Python实现，无需AI模型

快速使用：
    from data_hub import get_data

    # 指数成分股
    codes = get_data("index_components", index_code="000300.SH")

    # 期权数据
    option_data = get_data("option_data", exchange="SSE", underlying="510300")

    # A股日线（单只）
    df = get_data("stock_daily", ts_code="000001.SZ", start_date="20260101", end_date="20260716")

    # A股日线（全市场某日）
    df = get_data("stock_daily", trade_date="20260716")

    # 股指期货基差
    basis = get_data("futures_basis", trade_date="20260716")

    # 单个期货合约历史日线（指定具体合约，如 IF2609，非主力连续）
    fdf = get_data("futures_contract_daily", contract="IF2609",
                   start_date="20260101", end_date="20260922")

    # SHIBOR利率
    shibor = get_data("interest_rate", rate_type="shibor")

    # ETF份额
    df = get_data("etf_share", ts_code="510300.SH", lookback_days=10)

    # 两融数据
    df = get_data("margin_trading", market="all", lookback_days=10)

    # 板块资金流
    df = get_data("sector_flow", sector_type="industry")

    # 央行OMO操作
    omo = get_data("central_bank_omo")

    # 海外宏观数据
    global_data = get_data("global_macro")

    # 交易所回购利率
    repo = get_data("repo_exchange")

    # 国债期货历史
    tf = get_data("treasury_futures", symbol="TL")

    # 大小盘对比
    ss = get_data("size_style")

数据类型清单：
- index_components: 指数成分股
- option_data: 期权数据
- stock_daily: A股日线行情
- stock_spot: A股实时行情
- index_spot: 指数实时行情
- futures_basis: 股指期货基差
- futures_contract_daily: 单个期货合约历史日线（指定具体合约，如 IF2609，非主力连续）
- interest_rate: 利率数据（shibor/lpr/hibor/bond_yield/cd_rate_1y）
- etf_share: ETF份额数据
- margin_trading: 两融（融资融券）数据
- sector_flow: 板块资金流数据
- central_bank_omo: 央行OMO操作数据
- global_macro: 海外宏观数据（DXY/美债/原油/CNH）
- repo_exchange: 交易所回购利率
- treasury_futures: 国债期货历史数据
- size_style: 大小盘对比指标
- option_board: 期权T型报价（option_finance_board）
- option_underlying_quote: 期权标的当日行情
- option_cffex_zz1000_list: 中金所中证1000股指期权合约月份列表
- option_cffex_zz1000_spot: 中金所中证1000股指期权T型报价
- index_spot_sina: 新浪指数实时行情（全表）
- etf_spot_em: ETF实时行情（全表）
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import os
import socket

# ============================================================
# 全局 IPv4 强制（模块导入即生效）
# 解决 Tushare/AKShare 因 IPv6 多出口IP导致"IP数量超限"问题
# ============================================================
if os.environ.get("DATA_HUB_FORCE_IPV4", "1") != "0":
    _orig_getaddrinfo = socket.getaddrinfo

    def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)

    socket.getaddrinfo = _ipv4_getaddrinfo


from .base import DataResult, AllSourcesFailedError, DataHubError
from .config import logger, TUSHARE_CONFIG, QUALITY_THRESHOLDS, TIMEOUT_CONFIG
from .collectors import (
    IndexComponentsCollector,
    OptionDataCollector,
    OptionIvHistoryCollector,
    StockDailyCollector,
    StockSpotCollector,
    IndexSpotCollector,
    IndexDailyCollector,
    FuturesBasisCollector,
    FuturesContractDailyCollector,
    InterestRateCollector,
    EtfShareCollector,
    MarginTradingCollector,
    SectorFlowCollector,
    CentralBankOMOCollector,
    GlobalMacroCollector,
    RepoExchangeCollector,
    TreasuryFuturesCollector,
    EtfDailyCollector,
    SizeStyleCollector,
    NorthboundFlowCollector,
    EquityPledgeCollector,
    ResearchReportCollector,
    NewsCollector,
    OptionBoardCollector,
    OptionUnderlyingQuoteCollector,
    OptionCffexZz1000ListCollector,
    OptionCffexZz1000SpotCollector,
    IndexSpotSinaCollector,
    EtfSpotEmCollector,
    get_index_components,
    get_top2000_stocks,
    get_option_data,
    get_all_option_contracts,
    get_option_iv_history,
    get_iv_ma_data,
    iv_bar_visual,
    get_stock_daily,
    get_market_daily,
    get_stock_daily_batch,
    get_trade_dates,
    get_stock_spot,
    get_index_spot,
    get_index_daily,
    get_futures_basis,
    get_futures_contract_daily,
    normalize_contract,
    normalize_futures_contract_df,
    get_interest_rate,
    get_shibor,
    get_lpr,
    get_hibor,
    get_bond_yield,
    get_cd_rate_1y,
    get_etf_share,
    get_margin_trading,
    get_sector_flow,
    get_hot_sector_flow,
    get_central_bank_omo,
    get_global_macro,
    get_repo_exchange,
    get_treasury_futures,
    get_etf_daily,
    get_size_style,
    get_northbound_flow,
    get_equity_pledge,
    get_option_board,
    get_option_underlying_quote,
    get_option_cffex_zz1000_list,
    get_option_cffex_zz1000_spot,
    get_index_spot_sina,
    get_etf_spot_em,
    OPTION_CHAIN_TARGETS,
    CHAIN_COLUMNS,
    get_option_chain,
    get_all_option_chains,
    REALTIME_OPTION_TARGETS,
    CORE_TARGET_KEYS,
    REALTIME_CHAIN_COLUMNS,
    get_realtime_option_chain,
    get_all_realtime_option_chains,
    get_realtime_futures,
    is_trading_hours,
    INDEX_CODE_MAP,
    OPTION_UNDERLYINGS,
    OPTION_SYMBOL_ALIASES,
    FUTURES_INDEX_MAP,
    SUPPORTED_RATE_TYPES,
    SUB_TYPE_MAP,
    WIDE_BASE_ETFS,
    HOT_TECH_SECTORS,
    TREASURY_FUTURES_MAP,
    SIZE_STYLE_INDEX_MAP,
    SIZE_STYLE_BASE_DATE,
    MAJOR_INDEX_CODES,
)
from .sources import AKShareSource, TushareSource, CozeGatewaySource, WebSource
from .option_metrics import (
    OptionMetrics,
    OptionContract,
    implied_vol,
    calc_all_option_metrics,
    compute_metrics_from_data,
    standardize_contracts,
    get_atm_iv,
    calc_iv_skew,
    calc_cboe_skew,
    calc_pcr,
    calc_term_structure,
    calc_vix,
    # === 偏度唯一真源（2026-11-09 口径统一）===
    # 业务代码计算 25Δ 偏度 / Risk Reversal，一律用 calc_25d_risk_reversal；
    # 方向判断用的 CBOE SKEW 一律用 calc_cboe_skew_directional。
    # calc_iv_skew / calc_cboe_skew 为简化版/库落库专用，禁止新业务使用。
    calc_25d_risk_reversal,
    calc_cboe_skew_directional,
    bs_delta,
    delta_to_strike,
    CBOE_SKEW_DIRECTIONAL_BASE,
    CBOE_SKEW_DIRECTIONAL_COEF,
)
from .daily_cache import (
    get_cache_dir,
    is_cache_available,
    save_cache,
    load_cache,
    save_dataframe,
    load_dataframe,
    get_latest_trade_date,
    fetch_daily_data,
    CACHE_ROOT,
)
from .margin_db import (
    sync_margin_history,
    backfill_from_tushare,
    save_margin_rows,
    query_margin,
    get_latest_margin,
    get_margin_percentile,
    get_db_stats,
    DB_PATH as MARGIN_DB_PATH,
)
from .margin_detail_db import (
    get_margin_detail,
    get_daily_basic,
    get_stock_list,
    get_total_circ_mv,
    sync_margin_detail,
    sync_daily_basic,
    sync_stock_list,
    DB_PATH as MARGIN_DETAIL_DB_PATH,
)

__version__ = "1.6.0"
__all__ = [
    # 统一入口
    "get_data",
    "get_data_result",
    # 两融历史库
    "sync_margin_history",
    "backfill_from_tushare",
    "save_margin_rows",
    "query_margin",
    "get_latest_margin",
    "get_margin_percentile",
    "get_db_stats",
    "MARGIN_DB_PATH",
    # 个股两融明细 + 流通市值
    "get_margin_detail",
    "get_daily_basic",
    "get_stock_list",
    "get_total_circ_mv",
    "sync_margin_detail",
    "sync_daily_basic",
    "sync_stock_list",
    "MARGIN_DETAIL_DB_PATH",
    # 结果与异常
    "DataResult",
    "AllSourcesFailedError",
    "DataHubError",
    # 配置
    "logger",
    "TUSHARE_CONFIG",
    "QUALITY_THRESHOLDS",
    "TIMEOUT_CONFIG",
    # 采集器类
    "IndexComponentsCollector",
    "OptionDataCollector",
    "StockDailyCollector",
    "StockSpotCollector",
    "IndexSpotCollector",
    "IndexDailyCollector",
    "FuturesBasisCollector",
    "FuturesContractDailyCollector",
    "InterestRateCollector",
    "EtfShareCollector",
    "MarginTradingCollector",
    "SectorFlowCollector",
    "CentralBankOMOCollector",
    "GlobalMacroCollector",
    "RepoExchangeCollector",
    "TreasuryFuturesCollector",
    "SizeStyleCollector",
    "NorthboundFlowCollector",
    "EquityPledgeCollector",
    "ResearchReportCollector",
    "NewsCollector",
    "OptionBoardCollector",
    "OptionUnderlyingQuoteCollector",
    "OptionCffexZz1000ListCollector",
    "OptionCffexZz1000SpotCollector",
    "IndexSpotSinaCollector",
    "EtfSpotEmCollector",
    # 便捷函数
    "get_index_components",
    "get_top2000_stocks",
    "get_option_data",
    "get_all_option_contracts",
    "get_option_iv_history",
    "get_iv_ma_data",
    "iv_bar_visual",
    "get_stock_daily",
    "get_market_daily",
    "get_stock_daily_batch",
    "get_trade_dates",
    "get_stock_spot",
    "get_index_spot",
    "get_futures_basis",
    "get_futures_contract_daily",
    "normalize_contract",
    "normalize_futures_contract_df",
    "get_interest_rate",
    "get_shibor",
    "get_lpr",
    "get_hibor",
    "get_bond_yield",
    "get_cd_rate_1y",
    "get_etf_share",
    "get_margin_trading",
    "get_sector_flow",
    "get_hot_sector_flow",
    "get_central_bank_omo",
    "get_global_macro",
    "get_repo_exchange",
    "get_treasury_futures",
    "get_etf_daily",
    "get_size_style",
    # 期权原始行情（业务脚本统一入口）
    "get_option_board",
    "get_option_underlying_quote",
    "get_option_cffex_zz1000_list",
    "get_option_cffex_zz1000_spot",
    "get_index_spot_sina",
    "get_etf_spot_em",
    # 5大宽基期权全量链统一入口
    "OPTION_CHAIN_TARGETS",
    "CHAIN_COLUMNS",
    "get_option_chain",
    "get_all_option_chains",
    # 实时期权链（盘中实时持仓量OI）
    "REALTIME_OPTION_TARGETS",
    "CORE_TARGET_KEYS",
    "REALTIME_CHAIN_COLUMNS",
    "get_realtime_option_chain",
    "get_all_realtime_option_chains",
    "get_realtime_futures",
    "is_trading_hours",
    # 数据源类
    "AKShareSource",
    "TushareSource",
    "CozeGatewaySource",
    "WebSource",
    # 期权指标计算
    "OptionMetrics",
    "OptionContract",
    "implied_vol",
    "calc_all_option_metrics",
    "compute_metrics_from_data",
    "standardize_contracts",
    "get_atm_iv",
    "calc_iv_skew",
    "calc_cboe_skew",
    "calc_pcr",
    "calc_term_structure",
    "calc_vix",
    # 偏度唯一真源（2026-11-09）
    "calc_25d_risk_reversal",
    "calc_cboe_skew_directional",
    "bs_delta",
    "delta_to_strike",
    "CBOE_SKEW_DIRECTIONAL_BASE",
    "CBOE_SKEW_DIRECTIONAL_COEF",
    # 每日缓存
    "get_cache_dir",
    "is_cache_available",
    "save_cache",
    "load_cache",
    "save_dataframe",
    "load_dataframe",
    "get_latest_trade_date",
    "fetch_daily_data",
    "CACHE_ROOT",
    # 常量映射
    "INDEX_CODE_MAP",
    "OPTION_UNDERLYINGS",
    "OPTION_SYMBOL_ALIASES",
    "FUTURES_INDEX_MAP",
    "SUPPORTED_RATE_TYPES",
    "SUB_TYPE_MAP",
    "WIDE_BASE_ETFS",
    "HOT_TECH_SECTORS",
    "TREASURY_FUTURES_MAP",
    "SIZE_STYLE_INDEX_MAP",
    "SIZE_STYLE_BASE_DATE",
    "MAJOR_INDEX_CODES",
]


# 采集器注册表
_COLLECTOR_REGISTRY: Dict[str, type] = {
    "index_components": IndexComponentsCollector,
    "option_data": OptionDataCollector,
    "option_iv_history": OptionIvHistoryCollector,
    "stock_daily": StockDailyCollector,
    "stock_spot": StockSpotCollector,
    "index_spot": IndexSpotCollector,
    "index_daily": IndexDailyCollector,
    "futures_basis": FuturesBasisCollector,
    "futures_contract_daily": FuturesContractDailyCollector,
    "interest_rate": InterestRateCollector,
    "etf_share": EtfShareCollector,
    "margin_trading": MarginTradingCollector,
    "sector_flow": SectorFlowCollector,
    "central_bank_omo": CentralBankOMOCollector,
    "global_macro": GlobalMacroCollector,
    "repo_exchange": RepoExchangeCollector,
    "treasury_futures": TreasuryFuturesCollector,
    "etf_daily": EtfDailyCollector,
    "size_style": SizeStyleCollector,
    "northbound_flow": NorthboundFlowCollector,
    "equity_pledge": EquityPledgeCollector,
    "research_report": ResearchReportCollector,
    "news": NewsCollector,
    "option_board": OptionBoardCollector,
    "option_underlying_quote": OptionUnderlyingQuoteCollector,
    "option_cffex_zz1000_list": OptionCffexZz1000ListCollector,
    "option_cffex_zz1000_spot": OptionCffexZz1000SpotCollector,
    "index_spot_sina": IndexSpotSinaCollector,
    "etf_spot_em": EtfSpotEmCollector,
}

# 单例采集器缓存
_collector_instances: Dict[str, Any] = {}


def _get_collector(data_type: str, **kwargs) -> Any:
    """
    获取或创建指定类型的采集器实例。

    Args:
        data_type: 数据类型
        **kwargs: 传递给采集器构造函数的参数

    Returns:
        采集器实例
    """
    collector_cls = _COLLECTOR_REGISTRY.get(data_type)
    if collector_cls is None:
        raise ValueError(
            f"不支持的数据类型: {data_type}\n"
            f"支持的类型: {list(_COLLECTOR_REGISTRY.keys())}"
        )

    # 简单单例（无特殊参数时复用）
    instance_key = data_type
    if not kwargs and instance_key in _collector_instances:
        return _collector_instances[instance_key]

    collector = collector_cls(**kwargs)

    if not kwargs:
        _collector_instances[instance_key] = collector

    return collector


def get_data_result(data_type: str, **kwargs) -> DataResult:
    """
    获取数据，返回完整的 DataResult 对象（包含来源、时间戳、元数据等）。

    Args:
        data_type: 数据类型（index_components / option_data / stock_daily / futures_basis）
        **kwargs: 传递给对应采集器的参数

    Returns:
        DataResult — 完整的数据结果对象

    Raises:
        ValueError: 不支持的数据类型
        AllSourcesFailedError: 所有数据源均失败
    """
    collector = _get_collector(data_type)
    return collector.collect(**kwargs)


def get_data(data_type: str, default: Any = None, **kwargs) -> Any:
    """
    获取数据（便捷函数，直接返回数据本体，失败返回默认值）。

    Args:
        data_type: 数据类型
        default: 失败时返回的默认值（默认 None）
        **kwargs: 传递给对应采集器的参数

    Returns:
        数据本体（list / DataFrame / dict 等），失败返回 default

    示例：
        # 指数成分股
        codes = get_data("index_components", index_code="000300.SH")

        # 期权数据
        option = get_data("option_data", exchange="SSE", underlying="510300")

        # 日线行情
        df = get_data("stock_daily", ts_code="000001.SZ", start_date="20260101", end_date="20260716")

        # 股指期货基差
        basis = get_data("futures_basis", trade_date="20260716")

        # 央行OMO
        omo = get_data("central_bank_omo")

        # 海外宏观
        global_data = get_data("global_macro")

        # 回购利率
        repo = get_data("repo_exchange")

        # 国债期货
        tf = get_data("treasury_futures", symbol="TL")

        # 大小盘
        ss = get_data("size_style")
    """
    try:
        result = get_data_result(data_type, **kwargs)
        return result.data if result.success else default
    except AllSourcesFailedError as e:
        logger.error(f"[get_data] {e}")
        return default
    except Exception as e:
        logger.error(f"[get_data] 获取 {data_type} 异常: {e}")
        return default


# ============================================================
# 通用缓存层（data_hub.cache）
# ------------------------------------------------------------
# 其他业务/API 也能直接用：
#   from data_hub import cache_or_fetch, cached
#   df = cache_or_fetch("option", "board:510300", fetch, ttl="session")
#
# 放在文件末尾导入，避免与上面的数据源/采集器产生循环依赖。
# 详见 data_hub/cache/README.md
# ============================================================
from .cache import (                     # noqa: E402,F401
    cache_clear,
    cache_delete,
    cache_evict,
    cache_exists,
    cache_get,
    cache_keys,
    cache_maintain,
    cache_or_fetch,
    cache_purge,
    cache_set,
    cache_stats,
    cached,
    clear_failure_backoff,
    list_tasks as list_prefetch_tasks,
    register_builtin_tasks,
    run_prefetch,
)

__all__ += [
    "cache_get", "cache_set", "cache_delete", "cache_exists",
    "cache_or_fetch", "cached",
    "cache_stats", "cache_purge", "cache_evict", "cache_clear",
    "cache_maintain", "cache_keys",
    "run_prefetch", "register_builtin_tasks", "list_prefetch_tasks",
    "clear_failure_backoff",
]
