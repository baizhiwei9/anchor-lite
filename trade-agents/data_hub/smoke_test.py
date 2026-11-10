"""
data_hub 全链路冒烟测试
=======================
对每个采集器 × 每个数据源层级进行连通性测试，验证数据通路正常。

测试原则：
- 快：每个用例只拉取最小数据量（1只股票/3天/小指数等）
- 全：覆盖所有采集器 + 所有注册的数据源层级
- 清：每个用例明确✅/❌，失败区分"数据源挂了"vs"代码bug"

用法：
    python smoke_test.py                 # 全部测试
    python smoke_test.py --collector stock_daily  # 只测某个采集器
    python smoke_test.py --source akshare         # 只测某个数据源
    python smoke_test.py --output result.json     # 指定输出文件
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

# 确保 data_hub 可导入
_script_dir = os.path.dirname(os.path.abspath(__file__))
_parent_dir = os.path.dirname(_script_dir)
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

from data_hub.base import (
    BaseCollector,
    DataResult,
    AllSourcesFailedError,
    DataInsufficientError,
    SourceUnavailableError,
)
from data_hub.config import logger, QUALITY_THRESHOLDS


# ============================================================
# 测试结果数据结构
# ============================================================

FAILURE_SOURCE_DOWN = "source_down"       # 数据源挂了（网络/服务不可用）
FAILURE_CODE_BUG = "code_bug"             # 代码有 bug（异常/类型错误等）
FAILURE_DATA_INSUFFICIENT = "data_insufficient"  # 数据不足（接口通但返回少）
FAILURE_NOT_IMPLEMENTED = "not_implemented"      # 该数据源未实现此采集类型


@dataclass
class TestCaseResult:
    """单个测试用例结果。"""
    collector: str              # 采集器名称
    source: str                 # 数据源名称
    source_tier: int            # 数据源层级
    success: bool               # 是否通过
    duration_ms: int            # 耗时（毫秒）
    data_count: int = 0         # 返回数据量
    failure_type: str = ""      # 失败类型
    error_msg: str = ""         # 错误信息
    error_traceback: str = ""   # 完整栈（仅代码bug时保留）


@dataclass
class SmokeTestSummary:
    """冒烟测试汇总。"""
    timestamp: str = ""
    total_cases: int = 0
    passed: int = 0
    failed: int = 0
    pass_rate: float = 0.0
    total_duration_ms: int = 0
    by_tier: Dict[str, Dict[str, int]] = field(default_factory=dict)
    by_collector: Dict[str, Dict[str, int]] = field(default_factory=dict)
    failed_cases: List[Dict[str, Any]] = field(default_factory=list)
    all_cases: List[Dict[str, Any]] = field(default_factory=list)


# ============================================================
# 采集器测试配置
# ============================================================
# 每个采集器定义最小冒烟测试参数，确保请求量小、响应快

def _recent_trade_date(days_back: int = 5) -> str:
    """获取最近的交易日（往前推几天避开周末/假日）。"""
    d = datetime.now() - timedelta(days=days_back)
    return d.strftime("%Y%m%d")


# 测试用例定义：(collector_name, collector_class, test_kwargs, description)
# test_kwargs 是传给 collector.collect() 的参数，尽量最小化
def _get_test_suites() -> List[Dict[str, Any]]:
    """
    返回所有测试套件定义。
    每个套件包含采集器名称、类、测试参数列表。
    """
    # 延迟导入，避免启动时的 import 开销影响错误诊断
    from data_hub.collectors import (
        StockDailyCollector,
        StockSpotCollector,
        IndexComponentsCollector,
        IndexDailyCollector,
        IndexSpotCollector,
        OptionDataCollector,
        OptionIvHistoryCollector,
        FuturesBasisCollector,
        InterestRateCollector,
        EtfShareCollector,
        MarginTradingCollector,
        SectorFlowCollector,
        CentralBankOMOCollector,
        GlobalMacroCollector,
        RepoExchangeCollector,
        TreasuryFuturesCollector,
        SizeStyleCollector,
        NorthboundFlowCollector,
        EquityPledgeCollector,
    )

    recent_date = _recent_trade_date(5)
    start_date = _recent_trade_date(10)

    suites = [
        # --- A股日线 ---
        {
            "collector_name": "stock_daily",
            "collector_cls": StockDailyCollector,
            "description": "A股日线行情 - 单只",
            "test_cases": [
                {
                    "name": "single_stock_3days",
                    "kwargs": {
                        "ts_code": "000001.SZ",
                        "start_date": start_date,
                        "end_date": recent_date,
                        "adjust": "",
                    },
                    "min_expected": 1,
                },
            ],
        },
        # --- A股实时行情 ---
        {
            "collector_name": "stock_spot",
            "collector_cls": StockSpotCollector,
            "description": "A股实时行情",
            "test_cases": [
                {
                    "name": "spot_sh",
                    "kwargs": {"market": "sh"},
                    "min_expected": 1,
                },
            ],
        },
        # --- 指数成分股 ---
        {
            "collector_name": "index_components",
            "collector_cls": IndexComponentsCollector,
            "description": "指数成分股",
            "test_cases": [
                {
                    "name": "hs300",
                    "kwargs": {"index_code": "000300.SH"},
                    "min_expected": 30,
                },
            ],
        },
        # --- 指数日线 ---
        {
            "collector_name": "index_daily",
            "collector_cls": IndexDailyCollector,
            "description": "指数日线",
            "test_cases": [
                {
                    "name": "hs300_3days",
                    "kwargs": {
                        "ts_code": "000300.SH",
                        "start_date": start_date,
                        "end_date": recent_date,
                    },
                    "min_expected": 1,
                },
            ],
        },
        # --- 指数实时 ---
        {
            "collector_name": "index_spot",
            "collector_cls": IndexSpotCollector,
            "description": "指数实时行情",
            "test_cases": [
                {
                    "name": "major_indexes",
                    "kwargs": {"index_codes": ["000001.SH", "399001.SZ"]},
                    "min_expected": 1,
                },
            ],
        },
        # --- 期权数据 ---
        {
            "collector_name": "option_data",
            "collector_cls": OptionDataCollector,
            "description": "期权数据",
            "test_cases": [
                {
                    "name": "sse_510300",
                    "kwargs": {"exchange": "SSE", "underlying": "510300"},
                    "min_expected": 5,
                },
            ],
        },
        # --- 期权IV历史 ---
        {
            "collector_name": "option_iv_history",
            "collector_cls": OptionIvHistoryCollector,
            "description": "期权IV历史",
            "test_cases": [
                {
                    "name": "510300_iv_10days",
                    "kwargs": {
                        "underlying_code": "510300",
                        "start_date": start_date,
                        "end_date": recent_date,
                    },
                    "min_expected": 1,
                },
            ],
        },
        # --- 股指期货基差 ---
        {
            "collector_name": "futures_basis",
            "collector_cls": FuturesBasisCollector,
            "description": "股指期货基差",
            "test_cases": [
                {
                    "name": "if_basis",
                    "kwargs": {"products": ["IF"]},
                    "min_expected": 1,
                },
            ],
        },
        # --- 利率数据 ---
        {
            "collector_name": "interest_rate",
            "collector_cls": InterestRateCollector,
            "description": "利率数据",
            "test_cases": [
                {
                    "name": "shibor",
                    "kwargs": {"rate_type": "shibor"},
                    "min_expected": 1,
                },
            ],
        },
        # --- ETF份额 ---
        {
            "collector_name": "etf_share",
            "collector_cls": EtfShareCollector,
            "description": "ETF份额",
            "test_cases": [
                {
                    "name": "510300_share",
                    "kwargs": {
                        "ts_code": "510300.SH",
                        "lookback_days": 3,
                    },
                    "min_expected": 1,
                },
            ],
        },
        # --- 两融数据 ---
        {
            "collector_name": "margin_trading",
            "collector_cls": MarginTradingCollector,
            "description": "两融数据",
            "test_cases": [
                {
                    "name": "sh_margin_3days",
                    "kwargs": {
                        "market": "sh",
                        "lookback_days": 3,
                    },
                    "min_expected": 1,
                },
            ],
        },
        # --- 板块资金流 ---
        {
            "collector_name": "sector_flow",
            "collector_cls": SectorFlowCollector,
            "description": "板块资金流",
            "test_cases": [
                {
                    "name": "industry_flow",
                    "kwargs": {"sector_type": "industry"},
                    "min_expected": 1,
                },
            ],
        },
        # --- 央行OMO ---
        {
            "collector_name": "central_bank_omo",
            "collector_cls": CentralBankOMOCollector,
            "description": "央行OMO操作",
            "test_cases": [
                {
                    "name": "omo_7days",
                    "kwargs": {"lookback_days": 7},
                    "min_expected": 1,
                },
            ],
        },
        # --- 海外宏观 ---
        {
            "collector_name": "global_macro",
            "collector_cls": GlobalMacroCollector,
            "description": "海外宏观",
            "test_cases": [
                {
                    "name": "us_treasury_7days",
                    "kwargs": {"us_treasury_lookback_days": 7},
                    "min_expected": 1,
                },
            ],
        },
        # --- 交易所回购 ---
        {
            "collector_name": "repo_exchange",
            "collector_cls": RepoExchangeCollector,
            "description": "交易所回购利率",
            "test_cases": [
                {
                    "name": "repo_7days",
                    "kwargs": {"lookback_days": 7},
                    "min_expected": 1,
                },
            ],
        },
        # --- 国债期货 ---
        {
            "collector_name": "treasury_futures",
            "collector_cls": TreasuryFuturesCollector,
            "description": "国债期货历史",
            "test_cases": [
                {
                    "name": "tl_7days",
                    "kwargs": {"symbol": "TL", "lookback_days": 7},
                    "min_expected": 1,
                },
            ],
        },
        # --- 大小盘风格 ---
        {
            "collector_name": "size_style",
            "collector_cls": SizeStyleCollector,
            "description": "大小盘对比",
            "test_cases": [
                {
                    "name": "size_10days",
                    "kwargs": {"lookback_days": 10},
                    "min_expected": 1,
                },
            ],
        },
        # --- 北向资金 ---
        {
            "collector_name": "northbound_flow",
            "collector_cls": NorthboundFlowCollector,
            "description": "北向资金",
            "test_cases": [
                {
                    "name": "northbound_7days",
                    "kwargs": {"lookback_days": 7},
                    "min_expected": 1,
                },
            ],
        },
        # --- 股权质押 ---
        {
            "collector_name": "equity_pledge",
            "collector_cls": EquityPledgeCollector,
            "description": "股权质押",
            "test_cases": [
                {
                    "name": "pledge_recent",
                    "kwargs": {},
                    "min_expected": 1,
                },
            ],
        },
    ]
    return suites


# ============================================================
# 失败类型诊断
# ============================================================

# 未实现/不支持的关键词（该数据源明确不提供此能力，属于设计预期）
_NOT_IMPLEMENTED_PATTERNS = [
    "暂未实现",          # Web层常见
    "降级到Web层",        # coze_gateway 设计上不提供，需降级
    "暂无.*技能",        # coze_gateway 无对应技能
    "暂无.*技能CLI",     # coze_gateway 无对应技能CLI
    "无.*接口",          # 明确无接口
    "不支持",            # 明确不支持
    "无需重复爬取",       # Web层明确由上层覆盖，无需实现
    "请使用AKShare",     # Web层提示用上层
    "请使用Tushare",     # Web层提示用上层
    "已降级",            # 明确降级（该层不提供）
    "不支持全量拉取",     # 明确不支持此模式
]

# 代码 Bug 关键词（明显是代码问题）
_CODE_BUG_PATTERNS = [
    "AttributeError",    # 属性不存在（代码调用错误）
    "IndexError",        # 索引越界
    "KeyError",          # 键不存在
    "TypeError",         # 类型错误
    "NameError",         # 变量未定义
    "SyntaxError",       # 语法错误
    "ValueError",        # 值错误
    "tuple index out of range",
    "object has no attribute",
]

# 数据不足关键词
_DATA_INSUFFICIENT_PATTERNS = [
    "数据不足",
    "数据为空",
    "数据返回为空",
    "为空",  # 结合上下文判断，放后面
]


def _matches_patterns(text: str, patterns: List[str]) -> bool:
    """检查文本是否匹配任一模式（支持简单通配）。"""
    import re
    text_lower = text.lower()
    for pat in patterns:
        # 转换 * 为 .* 做正则匹配
        regex = pat.replace("*", ".*").lower()
        if re.search(regex, text_lower):
            return True
    return False


def classify_failure(exc: Exception) -> tuple[str, str, str]:
    """
    根据异常类型和错误信息判断失败原因。

    优先级：代码 Bug > 数据不足 > 未实现 > 数据源挂了

    Returns:
        (failure_type, short_msg, full_traceback)
    """
    tb = traceback.format_exc()
    msg = f"{type(exc).__name__}: {exc}"

    # 明确的数据不足
    if isinstance(exc, DataInsufficientError):
        return FAILURE_DATA_INSUFFICIENT, msg, tb

    # 明确的数据源不可用
    if isinstance(exc, SourceUnavailableError):
        return FAILURE_SOURCE_DOWN, msg, tb

    # 所有源都失败 — 拆开看第一个原因做精细判断
    if isinstance(exc, AllSourcesFailedError):
        if exc.failures:
            first_err = exc.failures[0]["error"]
            return _classify_by_error_text(first_err, msg, tb)
        return FAILURE_SOURCE_DOWN, msg, tb

    # NotImplementedError / 方法未实现
    if isinstance(exc, NotImplementedError):
        return FAILURE_NOT_IMPLEMENTED, msg, tb

    # 方法不存在（调用了未实现的 fetch_）
    if isinstance(exc, AttributeError) and "fetch_" in str(exc):
        return FAILURE_NOT_IMPLEMENTED, msg, tb

    # 根据错误文本精细分类
    return _classify_by_error_text(str(exc), msg, tb)


def _classify_by_error_text(
    error_text: str,
    original_msg: str,
    tb: str,
) -> tuple[str, str, str]:
    """根据错误文本内容精细判断失败类型。"""

    # 1. 代码 Bug 优先（最需要关注的问题）
    if _matches_patterns(error_text, _CODE_BUG_PATTERNS):
        return FAILURE_CODE_BUG, original_msg, tb

    # 2. 数据不足
    if "数据不足" in error_text or "insufficient" in error_text.lower():
        return FAILURE_DATA_INSUFFICIENT, original_msg, tb

    # 3. 未实现/不支持（设计上就不提供此能力）
    if _matches_patterns(error_text, _NOT_IMPLEMENTED_PATTERNS):
        return FAILURE_NOT_IMPLEMENTED, original_msg, tb

    # 4. 网络/连接问题 → 数据源挂了
    network_keywords = [
        "ConnectionError", "TimeoutError", "ReadTimeout",
        "ConnectTimeout", "HTTPError", "ProxyError",
        "SSLError", "socket.", "ConnectionReset",
        "ConnectionRefused", "gaierror", "RemoteDisconnected",
        "无法访问", "连接", "超时",
        "IP数量超限",  # Tushare IP 超限也算服务不可用
    ]
    for kw in network_keywords:
        if kw.lower() in error_text.lower():
            return FAILURE_SOURCE_DOWN, original_msg, tb

    # 5. 其他无法归类的 → 按数据源挂了处理（保守策略）
    return FAILURE_SOURCE_DOWN, original_msg, tb


# ============================================================
# 单用例执行
# ============================================================

def run_single_test(
    collector_cls: type,
    collector_name: str,
    source_name: str,
    test_kwargs: Dict[str, Any],
    min_expected: int,
) -> TestCaseResult:
    """
    对指定采集器 + 指定数据源运行一次测试。

    关键：通过设置 source_priority=[source_name] 强制只使用该数据源，
    避免降级掩盖问题。
    """
    t0 = time.time()
    result = TestCaseResult(
        collector=collector_name,
        source=source_name,
        source_tier=0,
        success=False,
        duration_ms=0,
    )

    try:
        # 强制只使用该数据源
        collector = collector_cls(source_priority=[source_name])

        # 获取 tier 信息
        src = collector._sources.get(source_name)
        if src:
            result.source_tier = src.tier

        # 检查是否注册了该数据源
        if not src:
            result.failure_type = FAILURE_NOT_IMPLEMENTED
            result.error_msg = f"数据源 {source_name} 未在采集器中注册"
            result.duration_ms = int((time.time() - t0) * 1000)
            return result

        # 执行采集
        data_result: DataResult = collector.collect(**test_kwargs)

        # 成功判断
        if data_result.success and data_result.data_count >= min_expected:
            result.success = True
            result.data_count = data_result.data_count
        elif data_result.success and data_result.data_count < min_expected:
            result.success = False
            result.failure_type = FAILURE_DATA_INSUFFICIENT
            result.data_count = data_result.data_count
            result.error_msg = (
                f"数据不足: 预期>={min_expected}, 实际={data_result.data_count}"
            )
        else:
            result.success = False
            result.failure_type = FAILURE_SOURCE_DOWN
            result.error_msg = data_result.error or "未知错误"
            result.data_count = data_result.data_count

    except Exception as e:
        failure_type, err_msg, tb_str = classify_failure(e)
        result.failure_type = failure_type
        result.error_msg = err_msg
        # 只对代码bug保留完整栈，减少输出体积
        if failure_type == FAILURE_CODE_BUG:
            result.error_traceback = tb_str

    result.duration_ms = int((time.time() - t0) * 1000)
    return result


# ============================================================
# 主测试引擎
# ============================================================

def _get_source_tier_map() -> Dict[str, int]:
    """数据源名 → 层级。"""
    return {
        "akshare": 1,
        "tushare": 2,
        "coze_gateway": 3,
        "web": 4,
    }


def run_smoke_test(
    collector_filter: Optional[str] = None,
    source_filter: Optional[str] = None,
    timeout_per_case: int = 30,
) -> SmokeTestSummary:
    """
    运行全部冒烟测试。

    Args:
        collector_filter: 只测指定采集器（None=全部）
        source_filter: 只测指定数据源（None=全部）
        timeout_per_case: 每个用例超时时间（秒）

    Returns:
        SmokeTestSummary
    """
    suites = _get_test_suites()
    source_tier_map = _get_source_tier_map()

    # 筛选采集器
    if collector_filter:
        suites = [s for s in suites if s["collector_name"] == collector_filter]

    all_results: List[TestCaseResult] = []
    total_start = time.time()

    print("=" * 70)
    print(f"  data_hub 全链路冒烟测试")
    print(f"  开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  采集器数量: {len(suites)}")
    print("=" * 70)

    for suite in suites:
        collector_name = suite["collector_name"]
        collector_cls = suite["collector_cls"]
        test_cases = suite["test_cases"]

        # 确定该采集器注册的数据源列表
        # 先实例化一次看看注册了哪些源
        try:
            tmp_collector = collector_cls()
            registered_sources = list(tmp_collector._sources.keys())
        except Exception as e:
            print(f"\n  ⚠️  {collector_name} 初始化失败: {e}")
            # 用全部4个源兜底
            registered_sources = ["akshare", "tushare", "coze_gateway", "web"]

        # 筛选数据源
        if source_filter:
            if source_filter in registered_sources:
                registered_sources = [source_filter]
            else:
                print(f"\n  ⚠️  {collector_name} 没有数据源 {source_filter}，跳过")
                continue

        print(f"\n📦 采集器: {collector_name} ({len(registered_sources)} 个数据源)")
        print(f"   数据源: {', '.join(registered_sources)}")

        for src in registered_sources:
            for tc in test_cases:
                tc_name = tc["name"]
                tc_kwargs = tc["kwargs"]
                min_exp = tc["min_expected"]

                test_id = f"{collector_name}::{src}::{tc_name}"
                print(f"   🧪 {test_id} ... ", end="", flush=True)

                result = run_single_test(
                    collector_cls=collector_cls,
                    collector_name=collector_name,
                    source_name=src,
                    test_kwargs=tc_kwargs,
                    min_expected=min_exp,
                )

                # 补全 tier（如果没拿到）
                if result.source_tier == 0:
                    result.source_tier = source_tier_map.get(src, 0)

                all_results.append(result)

                if result.success:
                    status_icon = "✅"
                    detail = f"{result.data_count} 条, {result.duration_ms}ms"
                else:
                    status_icon = "❌"
                    detail = f"[{result.failure_type}] {result.error_msg[:60]}"

                print(f"{status_icon} {detail}")

    total_duration = int((time.time() - total_start) * 1000)

    # 汇总
    total = len(all_results)
    passed = sum(1 for r in all_results if r.success)
    failed = total - passed
    pass_rate = (passed / total * 100) if total > 0 else 0.0

    failed_cases = []
    for r in all_results:
        if r.success:
            continue
        item = {
            "collector": r.collector,
            "source": r.source,
            "source_tier": r.source_tier,
            "failure_type": r.failure_type,
            "error_msg": r.error_msg,
            "duration_ms": r.duration_ms,
        }
        # 代码 bug 额外附完整栈，便于排查
        if r.failure_type == FAILURE_CODE_BUG and r.error_traceback:
            item["error_traceback"] = r.error_traceback
        failed_cases.append(item)

    all_cases_dicts = []
    for r in all_results:
        item = {
            "collector": r.collector,
            "source": r.source,
            "source_tier": r.source_tier,
            "success": r.success,
            "duration_ms": r.duration_ms,
            "data_count": r.data_count,
            "failure_type": r.failure_type,
            "error_msg": r.error_msg,
        }
        if r.failure_type == FAILURE_CODE_BUG and r.error_traceback:
            item["error_traceback"] = r.error_traceback
        all_cases_dicts.append(item)

    # 按数据源层级统计
    by_tier: Dict[str, Dict[str, int]] = {}
    for r in all_results:
        src = r.source
        if src not in by_tier:
            by_tier[src] = {"total": 0, "passed": 0, "failed": 0, "tier": r.source_tier}
        by_tier[src]["total"] += 1
        if r.success:
            by_tier[src]["passed"] += 1
        else:
            by_tier[src]["failed"] += 1

    # 按采集器统计
    by_collector: Dict[str, Dict[str, int]] = {}
    for r in all_results:
        c = r.collector
        if c not in by_collector:
            by_collector[c] = {"total": 0, "passed": 0, "failed": 0}
        by_collector[c]["total"] += 1
        if r.success:
            by_collector[c]["passed"] += 1
        else:
            by_collector[c]["failed"] += 1

    summary = SmokeTestSummary(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        total_cases=total,
        passed=passed,
        failed=failed,
        pass_rate=round(pass_rate, 2),
        total_duration_ms=total_duration,
        by_tier=by_tier,
        by_collector=by_collector,
        failed_cases=failed_cases,
        all_cases=all_cases_dicts,
    )

    return summary


# ============================================================
# 输出与 CLI
# ============================================================

def print_summary(summary: SmokeTestSummary) -> None:
    """打印测试汇总表。"""
    print("\n" + "=" * 70)
    print("  📊 冒烟测试汇总")
    print("=" * 70)
    print(f"  测试时间:     {summary.timestamp}")
    print(f"  总用例数:     {summary.total_cases}")
    print(f"  通过数:       {summary.passed}  ✅")
    print(f"  失败数:       {summary.failed}  ❌")
    print(f"  通过率:       {summary.pass_rate}%")
    print(f"  总耗时:       {summary.total_duration_ms / 1000:.1f}s")
    print("=" * 70)

    if summary.failed_cases:
        print("\n  ❌ 失败明细:")
        print("-" * 70)
        # 按失败类型分组
        by_type: Dict[str, List[Dict]] = {}
        for fc in summary.failed_cases:
            ft = fc["failure_type"]
            by_type.setdefault(ft, []).append(fc)

        type_labels = {
            FAILURE_SOURCE_DOWN: "🔌 数据源挂了",
            FAILURE_CODE_BUG: "🐛 代码 Bug",
            FAILURE_DATA_INSUFFICIENT: "📭 数据不足",
            FAILURE_NOT_IMPLEMENTED: "🚧 未实现（设计预期）",
        }

        # 按严重程度排序：code_bug > source_down > data_insufficient > not_implemented
        severity_order = [
            FAILURE_CODE_BUG,
            FAILURE_SOURCE_DOWN,
            FAILURE_DATA_INSUFFICIENT,
            FAILURE_NOT_IMPLEMENTED,
        ]

        for ft in severity_order:
            if ft not in by_type:
                continue
            cases = by_type[ft]
            label = type_labels.get(ft, ft)
            print(f"\n  {label} ({len(cases)} 个):")
            for c in cases:
                print(f"    • {c['collector']} :: {c['source']} (tier{c['source_tier']})")
                # 错误信息截断到 120 字
                err_short = c["error_msg"][:120]
                if len(c["error_msg"]) > 120:
                    err_short += "..."
                print(f"      {err_short}")
    else:
        print("\n  🎉 全部通过！所有数据源工作正常。")

    print("=" * 70)


def save_result(summary: SmokeTestSummary, output_path: str) -> None:
    """保存 JSON 结果文件。"""
    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    # 用手写序列化，确保所有字段都在
    result_dict = {
        "timestamp": summary.timestamp,
        "total_cases": summary.total_cases,
        "passed": summary.passed,
        "failed": summary.failed,
        "pass_rate": summary.pass_rate,
        "total_duration_ms": summary.total_duration_ms,
        "by_tier": summary.by_tier,
        "by_collector": summary.by_collector,
        "failed_cases": summary.failed_cases,
        "all_cases": summary.all_cases,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result_dict, f, ensure_ascii=False, indent=2)

    print(f"\n💾 结果已保存到: {output_path}")


def main():
    """CLI 入口。"""
    import argparse

    parser = argparse.ArgumentParser(description="data_hub 全链路冒烟测试")
    parser.add_argument(
        "--collector", "-c",
        default=None,
        help="只测试指定采集器（如 stock_daily）",
    )
    parser.add_argument(
        "--source", "-s",
        default=None,
        help="只测试指定数据源（akshare/tushare/coze_gateway/web）",
    )
    parser.add_argument(
        "--output", "-o",
        default=os.path.join(_script_dir, "smoke_test_result.json"),
        help="JSON 结果输出路径",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="单用例超时时间（秒），默认 30",
    )
    args = parser.parse_args()

    summary = run_smoke_test(
        collector_filter=args.collector,
        source_filter=args.source,
        timeout_per_case=args.timeout,
    )

    print_summary(summary)
    save_result(summary, args.output)

    # 退出码：有失败返回 1，全部通过返回 0
    sys.exit(1 if summary.failed > 0 else 0)


if __name__ == "__main__":
    main()
