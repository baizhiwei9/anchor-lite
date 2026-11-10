"""
data_hub 配置模块
=================
统一管理数据源优先级、超时、阈值等配置。
所有可调参数集中在此处，便于全局调整。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import List, Optional, Dict


# ============================================================
# 数据源优先级定义
# ============================================================

# 四级数据源名称（优先级从高到低）
SOURCE_TIER_1 = "akshare"       # 一级：AKShare（东财数据源，免费无限制）
SOURCE_TIER_2 = "tushare"       # 二级：Tushare（专业财经数据，有频率限制）【已禁用，见 use_tushare 开关】
SOURCE_TIER_3 = "coze_gateway"  # 三级：Coze数据网关（技能CLI调用AKShare，稳定兜底）
SOURCE_TIER_4 = "web"           # 四级：网站爬取（官方网站/新浪，最后兜底）

# 是否启用 Tushare 数据源
# 2026-08-18：切换为 proxy 模式，代理已在 tushare_config.py 中配置并验证可用，
# 解决了沙箱出口IP不固定导致的"IP数量超限"问题。
USE_TUSHARE: bool = True

# 默认降级顺序（动态生成：USE_TUSHARE=False 时自动跳过 tushare）
def _build_default_priority() -> List[str]:
    priority = [SOURCE_TIER_1]
    if USE_TUSHARE:
        priority.append(SOURCE_TIER_2)
    priority.extend([SOURCE_TIER_3, SOURCE_TIER_4])
    return priority

DEFAULT_SOURCE_PRIORITY: List[str] = _build_default_priority()


# ============================================================
# 数据质量阈值
# ============================================================

@dataclass
class QualityThreshold:
    """数据质量阈值，用于判断是否需要降级。"""
    # 指数成分股最少数量（低于此数视为数据不足）
    # 注意：科创50只有50只，创业板指只有100只，阈值需覆盖小指数
    min_index_components: int = 30
    # 期权合约最少数量
    min_option_contracts: int = 10
    # 日线行情最少记录数（按股票数×交易日数估算）
    min_daily_records: int = 50
    # ETF最少数量
    min_etf_count: int = 5


QUALITY_THRESHOLDS = QualityThreshold()


# ============================================================
# 超时与重试配置
# ============================================================

@dataclass
class TimeoutConfig:
    """超时与重试配置。"""
    # HTTP 请求超时（秒）
    http_timeout: int = 15
    # 单数据源最大重试次数
    max_retries: int = 2
    # 重试间隔基数（秒），指数退避
    retry_backoff: float = 1.0
    # Tushare 全局调用最小间隔（秒）—— 避免触发频率限制
    tushare_min_interval: float = 0.2
    # Tushare 首次调用前随机抖动最小/最大秒数（错开不同任务并发）
    tushare_initial_jitter_min: float = 0.0
    tushare_initial_jitter_max: float = 60.0
    # AKShare 调用间隔（秒）
    akshare_min_interval: float = 0.1
    # Coze数据网关 调用间隔（秒）
    coze_gateway_min_interval: float = 0.3
    # Coze数据网关 CLI 超时（秒）
    coze_gateway_timeout: int = 30


TIMEOUT_CONFIG = TimeoutConfig()


# ============================================================
# Tushare 配置
# ============================================================

@dataclass
class TushareConfig:
    """Tushare 相关配置。"""
    # Token 从环境变量读取，也可直接设置
    token: str = field(default_factory=lambda: os.environ.get("TUSHARE_TOKEN", "***"))
    # API 地址
    api_url: str = "https://ttx.dailyfetch.top/"
    # 最后调用时间戳（全局串行控制）
    _last_call_ts: float = 0.0
    # 是否已完成首次调用抖动（进程级）
    _initial_jitter_done: bool = False

    @property
    def is_available(self) -> bool:
        return bool(self.token)

    def wait_for_rate_limit(self) -> None:
        """全局串行控制：确保两次 Tushare 调用间隔 >= min_interval。"""
        elapsed = time.time() - self._last_call_ts
        if elapsed < TIMEOUT_CONFIG.tushare_min_interval:
            time.sleep(TIMEOUT_CONFIG.tushare_min_interval - elapsed)
        self._last_call_ts = time.time()

    def wait_initial_jitter(self) -> None:
        """
        首次 Tushare 调用前的随机抖动，错开不同定时任务的并发时间。

        利用沙箱多IP轮动特性，让不同进程的首次请求在时间上散开，
        减少同一时刻多IP并发导致的"IP数量超限"。
        只在进程第一次调用时执行一次。
        """
        if self._initial_jitter_done:
            return
        import random
        jitter_seconds = random.uniform(
            TIMEOUT_CONFIG.tushare_initial_jitter_min,
            TIMEOUT_CONFIG.tushare_initial_jitter_max,
        )
        logger.debug(
            f"[tushare] 首次调用前随机抖动 {jitter_seconds:.1f}s，错开并发"
        )
        time.sleep(jitter_seconds)
        self._initial_jitter_done = True


TUSHARE_CONFIG = TushareConfig()


# ============================================================
# IP 超限重试配置
# ============================================================

@dataclass
class IPLimitRetryConfig:
    """
    Tushare IP 数量超限自动重试配置。

    沙箱出口 IP 有 8 个动态轮动，遇到"IP数量超限"时，
    等待几秒后重试可能刚好换 IP 从而成功。
    """
    # 最大重试次数（不含首次调用）
    max_retries: int = 3
    # 每次重试间隔（秒），按顺序递增
    retry_intervals: List[float] = field(default_factory=lambda: [3.0, 5.0, 8.0])
    # 触发 IP 超限重试的错误关键词（不区分大小写）
    ip_limit_keywords: List[str] = field(default_factory=lambda: [
        "ip数量超限",
        "ip 数量超限",
        "ip数量超过",
        "最大数量为",
        "too many ip",
        "ip limit",
        "ip数量",
    ])
    # 是否启用 IP 超限自动重试
    enabled: bool = True

    def is_ip_limit_error(self, error_msg: str) -> bool:
        """判断错误信息是否为 IP 数量超限错误。"""
        if not error_msg:
            return False
        msg_lower = error_msg.lower()
        for keyword in self.ip_limit_keywords:
            if keyword.lower() in msg_lower:
                return True
        return False


IP_LIMIT_RETRY_CONFIG = IPLimitRetryConfig()


# ============================================================
# 分时采集批次定义
# ============================================================

@dataclass
class BatchSchedule:
    """分时采集批次定义。"""
    # 批次名称
    name: str
    # 目标采集时间（24小时制，HH:MM）
    target_time: str
    # 该批次包含的数据类型
    data_types: List[str]


# 三个采集批次
BATCH_SCHEDULES: List[BatchSchedule] = [
    BatchSchedule(
        name="midday",
        target_time="11:30",
        data_types=["stock_realtime", "etf_realtime", "money_flow_realtime"],
    ),
    BatchSchedule(
        name="post_close",
        target_time="15:30",
        data_types=[
            "index_components", "stock_daily", "option_data",
            "futures_data", "etf_daily",
        ],
    ),
    BatchSchedule(
        name="evening",
        target_time="17:00",
        data_types=["money_flow_daily", "macro_data"],
    ),
]


# 数据时效性阈值（秒）—— 超过此时间视为过期，触发补采
DATA_FRESHNESS_SECONDS: dict = {
    "stock_realtime": 30 * 60,       # 实时行情：30分钟
    "etf_realtime": 30 * 60,         # ETF实时：30分钟
    "money_flow_realtime": 60 * 60,  # 实时资金流：60分钟
    "stock_daily": 6 * 3600,         # 日线行情：6小时（盘后用）
    "index_components": 24 * 3600,   # 成分股：24小时
    "option_data": 6 * 3600,         # 期权数据：6小时
    "futures_data": 6 * 3600,        # 期货数据：6小时
    "etf_daily": 6 * 3600,           # ETF日线：6小时
    "money_flow_daily": 12 * 3600,   # 日线资金流：12小时
    "macro_data": 24 * 3600,         # 宏观数据：24小时
    # 新增数据类型
    "central_bank_omo": 12 * 3600,   # 央行OMO操作：12小时（每日操作盘后更新）
    "global_macro": 6 * 3600,        # 海外宏观数据：6小时
    "repo_exchange": 30 * 60,        # 交易所回购利率：30分钟（盘中实时）
    "treasury_futures": 6 * 3600,    # 国债期货历史：6小时（盘后更新）
    "size_style": 6 * 3600,          # 大小盘对比指标：6小时
}


# ============================================================
# 数据类型 sub_type 映射
# ============================================================

@dataclass
class SubTypeConfig:
    """子类型配置，用于数据类型内的细分项映射。"""
    data_type: str
    sub_type: str
    description: str
    # 数据源优先级（None表示使用默认）
    source_priority: Optional[List[str]] = None


# 利率数据子类型配置
INTEREST_RATE_SUB_TYPES: List[SubTypeConfig] = [
    SubTypeConfig(
        data_type="interest_rate",
        sub_type="cd_rate_1y",
        description="1年期同业存单利率（代理：中债商业银行普通债收益率曲线(AAA) 1年期）",
    ),
    SubTypeConfig(
        data_type="interest_rate",
        sub_type="shibor_on",
        description="SHIBOR隔夜利率",
    ),
    SubTypeConfig(
        data_type="interest_rate",
        sub_type="lpr_1y",
        description="1年期LPR贷款市场报价利率",
    ),
    SubTypeConfig(
        data_type="interest_rate",
        sub_type="lpr_5y",
        description="5年期LPR贷款市场报价利率",
    ),
]

# 所有 sub_type 的快速查找表
SUB_TYPE_LOOKUP: Dict[str, SubTypeConfig] = {
    cfg.sub_type: cfg for cfg in INTEREST_RATE_SUB_TYPES
}


# ============================================================
# 日志配置
# ============================================================

import logging

def setup_logger(name: str = "data_hub", level: int = logging.INFO) -> logging.Logger:
    """配置并返回 data_hub 日志器。"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(level)
    handler = logging.StreamHandler()
    handler.setLevel(level)
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


logger = setup_logger()
