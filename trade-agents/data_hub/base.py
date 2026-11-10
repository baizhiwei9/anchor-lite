"""
data_hub 基类模块
================
提供三级数据源降级的核心逻辑、数据结果封装和异常定义。
所有采集器和数据源适配器都基于这些基类构建。
"""

from __future__ import annotations

import threading
import time
import traceback
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, TypeVar


class CallTimeoutError(TimeoutError):
    """数据源调用超时（用 daemon 线程实现，避免残留线程阻塞进程退出）。"""


def _call_with_timeout(fn: Callable, timeout: float, kwargs: Dict) -> Any:
    """在 daemon 线程中执行 fn(**kwargs)，超时抛 CallTimeoutError。

    为什么不用 ThreadPoolExecutor：
        ThreadPoolExecutor 的工作线程是非 daemon，超时后 `shutdown(wait=False)`
        无法终止在途调用，进程退出时 atexit 会 join 这些线程，导致「逻辑已结束
        但进程迟迟不退出」。改用 daemon 线程后，超时线程会被直接遗弃、
        不阻塞解释器退出，从而保证采集任务「有明确耗时上限」。
    """
    box: Dict[str, Any] = {}

    def _target() -> None:
        try:
            box["result"] = fn(**kwargs)
        except BaseException as e:  # noqa: BLE001
            box["error"] = e

    t = threading.Thread(target=_target, daemon=True, name="dh-call")
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        raise CallTimeoutError(f"调用超时 (> {timeout}s)")
    if "error" in box:
        raise box["error"]
    return box.get("result")

from .config import (
    DEFAULT_SOURCE_PRIORITY,
    TIMEOUT_CONFIG,
    logger,
)


# ============================================================
# 异常定义
# ============================================================

class DataHubError(Exception):
    """data_hub 基础异常类。"""
    pass


class SourceUnavailableError(DataHubError):
    """数据源不可用异常。"""
    def __init__(self, source_name: str, reason: str = ""):
        self.source_name = source_name
        self.reason = reason
        super().__init__(f"数据源 {source_name} 不可用: {reason}")


class DataInsufficientError(DataHubError):
    """数据量不足异常（触发降级的条件之一）。"""
    def __init__(self, source_name: str, data_type: str, expected: int, actual: int):
        self.source_name = source_name
        self.data_type = data_type
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"数据源 {source_name} 返回的 {data_type} 数据不足: "
            f"预期≥{expected}, 实际={actual}"
        )


class AllSourcesFailedError(DataHubError):
    """所有数据源均失败异常。"""
    def __init__(self, data_type: str, failures: List[Dict[str, str]]):
        self.data_type = data_type
        self.failures = failures
        failure_desc = "; ".join(
            f"[{f['source']}] {f['error']}" for f in failures
        )
        super().__init__(
            f"所有数据源获取 {data_type} 均失败: {failure_desc}"
        )


# ============================================================
# 数据结果封装
# ============================================================

T = TypeVar("T")


@dataclass
class DataResult:
    """
    统一数据结果封装。

    Attributes:
        data: 实际数据（DataFrame / dict / list 等）
        data_type: 数据类型标识
        source: 实际使用的数据源名称
        source_tier: 数据源级别（1/2/3）
        timestamp: 数据采集时间戳
        success: 是否成功获取
        error: 错误信息（失败时）
        meta: 附加元数据
    """
    data: Any = None
    data_type: str = ""
    source: str = ""
    source_tier: int = 0
    timestamp: float = field(default_factory=time.time)
    success: bool = False
    error: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_fresh(self, max_age_seconds: Optional[float] = None) -> bool:
        """检查数据是否在有效期内。"""
        if max_age_seconds is None:
            return True
        return (time.time() - self.timestamp) <= max_age_seconds

    @property
    def data_count(self) -> int:
        """估算数据量，用于质量判断。
        
        - DataFrame: 行数
        - list: 元素数
        - dict: 优先用各 DataFrame 值的行数之和（如期权 {contracts, daily}），
                否则回退为 key 数
        - 其他非空: 1
        """
        if self.data is None:
            return 0
        # pandas DataFrame
        if hasattr(self.data, "shape"):
            return int(self.data.shape[0])
        # list
        if isinstance(self.data, list):
            return len(self.data)
        # dict：累加其中 DataFrame/Series/ndarray 的长度
        if isinstance(self.data, dict):
            total = 0
            has_series = False
            for v in self.data.values():
                if hasattr(v, "shape"):
                    # 兼容标量 (shape=()) 和一维以上数组
                    shape = v.shape
                    if len(shape) >= 1:
                        total += int(shape[0])
                    else:
                        total += 1  # 标量按 1 计
                    has_series = True
                elif isinstance(v, (list,)):
                    total += len(v)
                    has_series = True
            if has_series:
                return total
            return len(self.data)
        # 其他类型按 1 计
        return 1 if self.data else 0


# ============================================================
# 数据源基类
# ============================================================

class BaseDataSource:
    """
    数据源基类。

    每个子类代表一个具体的数据源（如 AKShare、Tushare、Web爬取），
    实现具体的数据获取方法。
    """

    name: str = "base"
    tier: int = 0  # 数据源级别（1/2/3）

    def __init__(self):
        self.logger = logger

    def is_available(self) -> bool:
        """检查数据源是否可用。"""
        return True

    def _log_success(self, data_type: str, count: int):
        """记录成功日志。"""
        self.logger.info(
            f"[{self.name}] 获取 {data_type} 成功, 数据量={count}"
        )

    def _log_failure(self, data_type: str, error: str):
        """记录失败日志。"""
        self.logger.warning(
            f"[{self.name}] 获取 {data_type} 失败: {error}"
        )

    def _log_insufficient(self, data_type: str, expected: int, actual: int):
        """记录数据不足日志。"""
        self.logger.warning(
            f"[{self.name}] {data_type} 数据不足: 预期>={expected}, 实际={actual}"
        )

    # 各数据类型的获取方法，子类按需实现
    # 命名规范：fetch_<data_type>(**kwargs) -> DataResult

    def fetch_index_components(self, **kwargs) -> DataResult:
        """获取指数成分股。"""
        raise NotImplementedError

    def fetch_stock_daily(self, **kwargs) -> DataResult:
        """获取A股日线行情。"""
        raise NotImplementedError

    def fetch_option_data(self, **kwargs) -> DataResult:
        """获取期权数据。"""
        raise NotImplementedError

    def fetch_futures_data(self, **kwargs) -> DataResult:
        """获取股指期货数据。"""
        raise NotImplementedError

    def fetch_etf_data(self, **kwargs) -> DataResult:
        """获取ETF行情数据。"""
        raise NotImplementedError

    def fetch_money_flow(self, **kwargs) -> DataResult:
        """获取资金流向数据。"""
        raise NotImplementedError


# ============================================================
# 三级降级采集器基类
# ============================================================

class BaseCollector:
    """
    采集器基类，实现三级数据源降级逻辑。

    每个数据类型对应一个 Collector 子类，定义：
    - 数据类型标识 (data_type)
    - 数据质量判断 (is_data_sufficient)
    - 调用各数据源的方法映射
    """

    data_type: str = "base"
    min_data_count: int = 1  # 最少数据量阈值
    # 单源调用超时 / 重试次数的「局部覆盖」（None = 沿用全局 TIMEOUT_CONFIG）。
    # 2026-10-23 新增：仅用于少数单源本身耗时较长的数据类型，
    # 避免为此改动全局 TIMEOUT_CONFIG 而影响其他数据源/采集器。
    per_call_timeout_override: Optional[float] = None  # 秒
    max_retries_override: Optional[int] = None

    def __init__(
        self,
        sources: Optional[List[BaseDataSource]] = None,
        source_priority: Optional[List[str]] = None,
    ):
        """
        初始化采集器。

        Args:
            sources: 可用数据源列表
            source_priority: 数据源优先级顺序（名称列表）
        """
        self.logger = logger
        self._sources: Dict[str, BaseDataSource] = {}
        self._source_priority = source_priority or list(DEFAULT_SOURCE_PRIORITY)

        if sources:
            for src in sources:
                self._sources[src.name] = src

    def register_source(self, source: BaseDataSource) -> None:
        """注册一个数据源。"""
        self._sources[source.name] = source

    def get_available_sources(self) -> List[BaseDataSource]:
        """按优先级返回可用的数据源列表。"""
        available = []
        for name in self._source_priority:
            src = self._sources.get(name)
            if src and src.is_available():
                available.append(src)
        return available

    def is_data_sufficient(self, result: DataResult) -> bool:
        """
        判断数据是否充足（子类可覆盖）。
        默认：数据量 >= min_data_count 即视为充足。
        """
        return result.data_count >= self.min_data_count

    def _call_source_method(
        self,
        source: BaseDataSource,
        method_name: str,
        **kwargs,
    ) -> DataResult:
        """
        调用指定数据源的方法，带重试机制。

        Args:
            source: 数据源实例
            method_name: 方法名（如 "fetch_index_components"）
            **kwargs: 传递给方法的参数

        Returns:
            DataResult
        """
        method = getattr(source, method_name, None)
        if method is None:
            return DataResult(
                data_type=self.data_type,
                source=source.name,
                source_tier=source.tier,
                success=False,
                error=f"方法 {method_name} 未实现",
            )

        last_error = ""
        # 单次调用超时（秒），防止数据源永久挂住
        # 支持采集器子类局部覆盖（per_call_timeout_override / max_retries_override），
        # 不改动全局 TIMEOUT_CONFIG，避免影响其他数据源。
        if self.per_call_timeout_override is not None:
            per_call_timeout = self.per_call_timeout_override
        else:
            per_call_timeout = TIMEOUT_CONFIG.http_timeout * 4  # 默认60s
        if self.max_retries_override is not None:
            max_retries = self.max_retries_override
        else:
            max_retries = TIMEOUT_CONFIG.max_retries
        for attempt in range(max_retries + 1):
            try:
                if attempt > 0:
                    sleep_time = TIMEOUT_CONFIG.retry_backoff * (2 ** (attempt - 1))
                    self.logger.debug(
                        f"[{source.name}] 第 {attempt + 1} 次重试 {method_name}, "
                        f"等待 {sleep_time:.1f}s"
                    )
                    time.sleep(sleep_time)

                # 用 daemon 线程 + 超时兜底，防止数据源调用永久挂住且不阻塞进程退出
                result = _call_with_timeout(method, per_call_timeout, kwargs)

                # 统一填充字段
                if not result.data_type:
                    result.data_type = self.data_type
                if not result.source:
                    result.source = source.name
                if not result.source_tier:
                    result.source_tier = source.tier

                if result.success:
                    source._log_success(self.data_type, result.data_count)
                    return result
                else:
                    last_error = result.error or "未知错误"
                    if attempt < max_retries:
                        continue
                    # 最后一次仍失败
                    source._log_failure(self.data_type, last_error)
                    return result

            except (CallTimeoutError, FuturesTimeoutError):
                last_error = f"调用超时 (> {per_call_timeout}s)"
                self.logger.warning(
                    f"[{source.name}] {method_name} 超时 (attempt {attempt + 1}): {last_error}"
                )
                if attempt < max_retries:
                    continue
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                self.logger.debug(
                    f"[{source.name}] {method_name} 异常 (attempt {attempt + 1}): {last_error}"
                )
                self.logger.debug(traceback.format_exc())

        # 所有重试都失败
        source._log_failure(self.data_type, last_error)
        return DataResult(
            data_type=self.data_type,
            source=source.name,
            source_tier=source.tier,
            success=False,
            error=last_error,
        )

    def collect(self, **kwargs) -> DataResult:
        """
        执行三级降级采集。

        按优先级依次尝试各数据源，直到获取到充足数据或全部失败。
        每级失败都记录日志，绝不造假数据。

        Args:
            **kwargs: 采集参数

        Returns:
            DataResult —— 最终结果（成功或全部失败）

        Raises:
            AllSourcesFailedError: 所有数据源均失败且数据为空时
        """
        method_name = f"fetch_{self.data_type}"
        available_sources = self.get_available_sources()

        if not available_sources:
            self.logger.error(f"[{self.data_type}] 没有可用的数据源")
            raise AllSourcesFailedError(self.data_type, [
                {"source": name, "error": "数据源未注册或不可用"}
                for name in self._source_priority
            ])

        failures: List[Dict[str, str]] = []

        for source in available_sources:
            self.logger.info(
                f"[{self.data_type}] 尝试数据源: {source.name} (tier {source.tier})"
            )

            result = self._call_source_method(source, method_name, **kwargs)

            if result.success:
                # 检查数据质量
                if self.is_data_sufficient(result):
                    self.logger.info(
                        f"[{self.data_type}] 使用 {source.name} 数据 "
                        f"(tier {source.tier}), 数据量={result.data_count}"
                    )
                    return result
                else:
                    # 数据不足，降级
                    insufficient_msg = (
                        f"数据不足 (预期>={self.min_data_count}, "
                        f"实际={result.data_count})"
                    )
                    source._log_insufficient(
                        self.data_type, self.min_data_count, result.data_count
                    )
                    failures.append({
                        "source": source.name,
                        "error": insufficient_msg,
                    })
                    continue
            else:
                failures.append({
                    "source": source.name,
                    "error": result.error,
                })
                continue

        # 所有数据源均失败或数据不足
        self.logger.error(f"[{self.data_type}] 所有数据源均失败")
        for f in failures:
            self.logger.error(f"  - [{f['source']}] {f['error']}")

        raise AllSourcesFailedError(self.data_type, failures)
