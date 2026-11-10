"""
DataHub 缓存层 — 预取接口
==========================

"提前准备"的落地：把"哪些数据要预热、什么时候预热、预热失败怎么办"统一注册到这里，
盘前/盘后跑一次，业务脚本就直接吃缓存，不再各自现场拉数。

三个层次
--------
1. **注册**：``@prefetch(...)`` 装饰器或 :func:`register`，声明一个预取任务。
2. **执行**：:func:`run_prefetch` 按组/按名并发跑，返回结构化报告。
3. **调度**：命令行 ``python -m data_hub.cache prefetch --group daily``，
   或挂 crontab / CodeAct 定时触发。

任务分组（group）
-----------------
============================ ============================================
group                        典型触发时机
============================ ============================================
``daily``                     每交易日盘后（收盘后全量预热）
``premarket``                 每交易日盘前（隔夜外围/宏观/利率）
``weekly``                    每周一次（成分股名单等低频数据）
``on_demand``                 不参与批量预取，仅手动触发
============================ ============================================

快速上手
--------
::

    from data_hub.cache import prefetch, run_prefetch

    @prefetch(namespace="index", group="daily", ttl="trade_day",
              key_fn=lambda code: f"components:{code}")
    def components(code: str):
        return ak.index_stock_cons(symbol=code)

    # 盘后跑全量
    report = run_prefetch(group="daily")
    print(report.summary())

    # 只跑某几个
    report = run_prefetch(names=["components"])

    # 强制重拉
    report = run_prefetch(group="daily", force=True)
"""

from __future__ import annotations

import logging
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional

from .api import _is_empty, cache_set, get_store
from .policy import resolve_ttl

logger = logging.getLogger("data_hub.cache.prefetch")

# 分组优先级（数字越小越先跑）——慢/关键的先发车
GROUP_PRIORITY = {"premarket": 1, "daily": 2, "weekly": 3, "on_demand": 9}


# ---------------------------------------------------------------- 任务定义

@dataclass
class PrefetchTask:
    """一个预取任务的声明。"""

    name: str
    namespace: str
    fetch: Callable[[], Any]
    key: str = "default"
    ttl: Any = "overnight"
    tag: Optional[str] = None
    group: str = "daily"
    enabled: bool = True
    timeout: float = 300.0
    desc: str = ""
    # 失败退避：某项拉取失败后，这段时间内不再重试（秒）。
    # 目的：外部源整体挂掉时，不要让每轮预取都白等十几秒。
    retry_after: float = 600.0
    # 运行时填充
    status: str = "pending"           # pending/hit/fetched/failed/skipped
    elapsed_ms: int = 0
    size: int = 0
    error: str = ""

    @property
    def full_key(self) -> str:
        return f"{self.namespace}:{self.key}"


# 失败退避标记存放的命名空间（与业务缓存隔离，便于单独清理）
NEGATIVE_NAMESPACE = "__negative__"


_registry: Dict[str, PrefetchTask] = {}


# ---------------------------------------------------------------- 注册

def register(
    name: str,
    fetch: Callable[[], Any],
    namespace: str,
    key: str = "default",
    ttl: Any = "overnight",
    tag: Optional[str] = None,
    group: str = "daily",
    enabled: bool = True,
    timeout: float = 300.0,
    desc: str = "",
    retry_after: float = 600.0,
    override: bool = True,
) -> PrefetchTask:
    """注册一个预取任务（同名已存在时默认覆盖）。"""
    if name in _registry and not override:
        raise ValueError(f"预取任务已存在: {name}")
    task = PrefetchTask(
        name=name, namespace=namespace, fetch=fetch, key=key, ttl=ttl,
        tag=tag, group=group, enabled=enabled, timeout=timeout, desc=desc,
        retry_after=retry_after,
    )
    _registry[name] = task
    return task


def prefetch(
    namespace: str,
    ttl: Any = "overnight",
    group: str = "daily",
    key: Optional[str] = None,
    key_fn: Optional[Callable[..., str]] = None,
    tag: Optional[str] = None,
    enabled: bool = True,
    timeout: float = 300.0,
    desc: str = "",
    retry_after: float = 600.0,
):
    """
    装饰器：把一个无参（或参数固定）的函数注册成预取任务。

    ::

        @prefetch(namespace="margin", group="daily", ttl="trade_day")
        def margin_all():
            return get_data("margin_trading", market="all")

        @prefetch(namespace="index", group="daily", ttl="trade_day",
                  key_fn=lambda code="000300.SH": f"components:{code}")
        def components(code: str = "000300.SH"):
            return get_data("index_components", index_code=code)
    """

    def decorator(func: Callable) -> Callable:
        task_name = func.__name__
        task_key = key if key is not None else (
            key_fn() if (key_fn and _callable_without_required_args(key_fn)) else task_name
        )
        register(
            name=task_name, fetch=func, namespace=namespace, key=task_key,
            ttl=ttl, tag=tag or task_name, group=group,
            enabled=enabled, timeout=timeout, desc=desc or (func.__doc__ or "").strip(),
            retry_after=retry_after,
        )
        func.prefetch_task = task_name       # type: ignore[attr-defined]
        return func

    return decorator


def _callable_without_required_args(fn: Callable) -> bool:
    """判断函数能否无参调用。"""
    import inspect

    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    for p in sig.parameters.values():
        if p.default is inspect.Parameter.empty and p.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            return False
    return True


def unregister(name: str) -> bool:
    """注销任务。"""
    return _registry.pop(name, None) is not None


def clear_registry() -> int:
    """清空任务注册表（主要给测试用）。"""
    n = len(_registry)
    _registry.clear()
    return n


def list_tasks(group: Optional[str] = None) -> List[PrefetchTask]:
    """列出已注册任务（按分组优先级 + 名称排序）。"""
    tasks = [t for t in _registry.values() if group is None or t.group == group]
    return sorted(tasks, key=lambda t: (GROUP_PRIORITY.get(t.group, 9), t.name))


def list_groups() -> List[str]:
    """列出所有已注册的分组。"""
    return sorted({t.group for t in _registry.values()},
                  key=lambda g: GROUP_PRIORITY.get(g, 9))


# ---------------------------------------------------------------- 执行

@dataclass
class PrefetchReport:
    """预取执行报告。"""

    started_at: float = field(default_factory=time.time)
    elapsed_ms: int = 0
    tasks: List[PrefetchTask] = field(default_factory=list)

    # -- 统计 ------------------------------------------------------

    @property
    def total(self) -> int:
        return len(self.tasks)

    def _count(self, status: str) -> int:
        return sum(1 for t in self.tasks if t.status == status)

    @property
    def hit(self) -> int:
        return self._count("hit")

    @property
    def fetched(self) -> int:
        return self._count("fetched")

    @property
    def failed(self) -> int:
        return self._count("failed")

    @property
    def skipped(self) -> int:
        return self._count("skipped")

    @property
    def ok(self) -> bool:
        """只要没有 failed 就算成功（命中或新拉都算）。"""
        return self.failed == 0

    def failures(self) -> List[PrefetchTask]:
        return [t for t in self.tasks if t.status == "failed"]

    # -- 输出 ------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "started_at": self.started_at,
            "elapsed_ms": self.elapsed_ms,
            "total": self.total,
            "hit": self.hit,
            "fetched": self.fetched,
            "failed": self.failed,
            "skipped": self.skipped,
            "ok": self.ok,
            "tasks": [
                {
                    "name": t.name, "group": t.group, "key": t.full_key,
                    "status": t.status, "elapsed_ms": t.elapsed_ms,
                    "size": t.size, "error": t.error,
                }
                for t in self.tasks
            ],
        }

    def summary(self) -> str:
        """一行摘要，适合塞进日报/告警。"""
        parts = [f"共{self.total}项", f"命中{self.hit}", f"新拉{self.fetched}"]
        if self.failed:
            parts.append(f"❌失败{self.failed}")
        if self.skipped:
            parts.append(f"跳过{self.skipped}")
        parts.append(f"耗时{self.elapsed_ms}ms")
        line = "预取 " + " / ".join(parts)
        if self.failed:
            names = ", ".join(t.name for t in self.failures()[:5])
            line += f"\n失败项: {names}"
        return line

    def to_markdown(self) -> str:
        """Markdown 表格，适合飞书卡片/邮件正文。"""
        icon = {"hit": "♻️", "fetched": "🆕", "failed": "❌", "skipped": "⏭️"}
        rows = [
            "| 任务 | 分组 | 缓存键 | 状态 | 耗时 | 大小 |",
            "|---|---|---|---|---|---|",
        ]
        for t in self.tasks:
            rows.append(
                f"| {t.name} | {t.group} | `{t.full_key}` | "
                f"{icon.get(t.status, '')} {t.status} | {t.elapsed_ms}ms | "
                f"{_human_bytes(t.size)} |"
            )
        return "\n".join(rows) + "\n\n" + self.summary()


def _human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def _run_one(task: PrefetchTask, force: bool) -> PrefetchTask:
    """执行单个任务，结果回写到 task 对象上。"""
    if not task.enabled:
        task.status = "skipped"
        task.error = "任务已禁用"
        return task

    t0 = time.time()
    st = get_store()
    neg_key = task.full_key

    try:
        # 0) 失败退避：上次失败后 retry_after 秒内不重试，避免反复白等
        if not force and task.retry_after > 0:
            ok_neg, neg_info = st.get(NEGATIVE_NAMESPACE, neg_key, touch=False)
            if ok_neg and neg_info:
                task.status = "skipped"
                task.error = f"失败退避中（{neg_info.get('reason', '')}）"
                task.elapsed_ms = int((time.time() - t0) * 1000)
                logger.info(f"[prefetch] {task.name} 处于失败退避期，跳过")
                return task

        # 1) 已命中缓存 -> 直接算 hit，不做任何拉取
        if not force and st.exists(task.namespace, task.key):
            task.status = "hit"
            task.elapsed_ms = int((time.time() - t0) * 1000)
            logger.debug(f"[prefetch] {task.name} 命中缓存，跳过")
            return task

        # 2) 真正拉取
        #    这里不走 cache_or_fetch：它会吞掉异常返回 None，
        #    预取需要把真实失败原因报出来，方便定位是哪个数据源挂了。
        fetch_t0 = time.time()
        value = task.fetch()
        fetch_ms = int((time.time() - fetch_t0) * 1000)
        task.elapsed_ms = int((time.time() - t0) * 1000)

        if not _is_empty(value):
            cache_set(
                task.namespace, task.key, value,
                ttl=task.ttl, tag=task.tag or task.name, store=st,
                meta={"fetch_ms": fetch_ms, "task": task.name},
            )

        if _is_empty(value):
            task.status = "failed"
            task.error = "返回空数据"
            _mark_failure(st, neg_key, task, "返回空数据")
            logger.warning(f"[prefetch] {task.name} 返回空数据")
            return task

        task.status = "fetched"
        try:
            task.size = len(value) if hasattr(value, "__len__") else 0
        except Exception:
            task.size = 0
        _clear_failure(st, neg_key)          # 成功即解除退避
        logger.info(f"[prefetch] {task.name} 完成 ({task.elapsed_ms}ms)")
        return task

    except Exception as e:
        task.status = "failed"
        task.error = f"{type(e).__name__}: {e}"
        task.elapsed_ms = int((time.time() - t0) * 1000)
        _mark_failure(st, neg_key, task, task.error)
        logger.warning(f"[prefetch] {task.name} 失败 -> {task.error}")
        logger.debug(traceback.format_exc())
        return task


def _mark_failure(store, neg_key: str, task: PrefetchTask, reason: str) -> None:
    """写入失败退避标记（不污染业务缓存命名空间）。"""
    if task.retry_after <= 0:
        return
    try:
        store.put(
            NEGATIVE_NAMESPACE, neg_key,
            {"reason": reason, "task": task.name, "at": time.time()},
            ttl=task.retry_after, codec="json", tag="negative",
        )
    except Exception:
        pass


def _clear_failure(store, neg_key: str) -> None:
    """解除失败退避。"""
    try:
        store.delete(NEGATIVE_NAMESPACE, neg_key)
    except Exception:
        pass


def clear_failure_backoff() -> int:
    """清空所有失败退避标记（外部源恢复后想立刻重试时用）。"""
    return get_store().clear(NEGATIVE_NAMESPACE)


def run_prefetch(
    group: Optional[str] = None,
    groups: Optional[Iterable[str]] = None,
    names: Optional[Iterable[str]] = None,
    force: bool = False,
    workers: int = 4,
    timeout: Optional[float] = None,
    stop_on_all_failed: bool = False,
) -> PrefetchReport:
    """
    执行预取。

    Args:
        group: 只跑某个分组（如 ``daily``）。
        groups: 跑多个分组。
        names: 只跑指定任务名（优先级高于 group）。
        force: 忽略已有缓存，强制重拉。
        workers: 并发线程数。数据拉取是 IO 密集，4~8 比较合适。
        timeout: 整批总超时（秒），超时后未完成的任务标记 failed。
        stop_on_all_failed: 整批全失败时提前结束（外部源整体挂掉时省时间）。

    Returns:
        :class:`PrefetchReport`
    """
    report = PrefetchReport()

    if names:
        tasks = [t for n in names if (t := _registry.get(n)) is not None]
        missing = [n for n in names if n not in _registry]
        if missing:
            logger.warning(f"[prefetch] 未注册的任务: {', '.join(missing)}")
    else:
        wanted = set(groups) if groups else ({group} if group else None)
        tasks = [
            t for t in list_tasks()
            if wanted is None or t.group in wanted
        ]

    if not tasks:
        logger.warning("[prefetch] 没有匹配的任务")
        report.tasks = []
        return report

    logger.info(f"[prefetch] 开始，共 {len(tasks)} 项，并发 {workers}")
    t_start = time.time()

    # 按分组优先级排序后提交，让慢/关键任务先发车
    tasks = sorted(tasks, key=lambda t: (GROUP_PRIORITY.get(t.group, 9), t.name))

    if workers <= 1:
        for t in tasks:
            _run_one(t, force)
            if stop_on_all_failed and t.status == "failed" and report._count("failed") == len(tasks):
                break
    else:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(_run_one, t, force): t for t in tasks}
            for fut in as_completed(futures):
                if timeout and (time.time() - t_start) > timeout:
                    pending = [f for f in futures if not f.done()]
                    for f in pending:
                        futures[f].status = "failed"
                        futures[f].error = "批次总超时"
                        f.cancel()
                    break
                try:
                    fut.result()
                except Exception as e:      # _run_one 内部已兜住，这里是双保险
                    tk = futures[fut]
                    tk.status = "failed"
                    tk.error = str(e)

    report.tasks = tasks
    report.elapsed_ms = int((time.time() - t_start) * 1000)
    logger.info(f"[prefetch] 结束 -> {report.summary().splitlines()[0]}")
    return report


# ---------------------------------------------------------------- 内置预取任务
#
# 这里只注册"几乎所有业务都会用到"的高频数据。
# 业务脚本要预热自己的数据，在自己的模块里用 @prefetch 注册即可，
# 不需要改本文件。

def register_builtin_tasks() -> int:
    """
    注册 data_hub 内置的高频预取任务（幂等，可重复调用）。

    Returns:
        本次新注册的任务数。
    """
    import os
    from datetime import datetime, timedelta

    before = len(_registry)

    def _get(*args, **kwargs):
        from .. import get_data       # 延迟导入，避免循环依赖
        return get_data(*args, **kwargs)

    # 指数日线拉全历史很慢（akshare 6000+ 行要 60s+），
    # 默认只预热近 N 年，业务要更长历史就自己 _force_refresh 拉一次。
    lookback_years = int(os.environ.get("DATA_HUB_PREFETCH_LOOKBACK_YEARS", "3"))
    start_date = (
        datetime.now() - timedelta(days=365 * lookback_years)
    ).strftime("%Y%m%d")

    builtin = [
        # (name, namespace, key, group, ttl, fetch)
        ("index_components_000300", "index", "components:000300.SH", "daily", "trade_day",
         lambda: _get("index_components", index_code="000300.SH")),
        ("index_components_000905", "index", "components:000905.SH", "daily", "trade_day",
         lambda: _get("index_components", index_code="000905.SH")),
        ("index_components_000852", "index", "components:000852.SH", "daily", "trade_day",
         lambda: _get("index_components", index_code="000852.SH")),
        ("index_daily_000300", "index", "daily:000300.SH", "daily", "trade_day",
         lambda: _get("stock_daily", ts_code="000300.SH", start_date=start_date)),
        ("index_daily_000852", "index", "daily:000852.SH", "daily", "trade_day",
         lambda: _get("stock_daily", ts_code="000852.SH", start_date=start_date)),
        ("option_board_510300", "option", "board:510300", "daily", "session",
         lambda: _get("option_data", exchange="SSE", underlying="510300")),
        ("option_board_510050", "option", "board:510050", "daily", "session",
         lambda: _get("option_data", exchange="SSE", underlying="510050")),
        ("option_board_510500", "option", "board:510500", "daily", "session",
         lambda: _get("option_data", exchange="SSE", underlying="510500")),
        ("option_board_MO", "option", "board:MO", "daily", "session",
         lambda: _get("option_data", exchange="CFFEX", underlying="MO")),
        ("margin_all", "margin", "all", "daily", "trade_day",
         lambda: _get("margin_trading", market="all", lookback_days=30)),
        ("northbound_flow", "margin", "northbound", "daily", "trade_day",
         lambda: _get("northbound_flow")),
        ("interest_rate_shibor", "macro", "shibor", "premarket", "trade_day",
         lambda: _get("interest_rate", rate_type="shibor")),
    ]

    for name, ns, key, group, ttl, fn in builtin:
        register(name=name, fetch=fn, namespace=ns, key=key,
                 ttl=ttl, group=group, tag=group, override=False)

    return len(_registry) - before
