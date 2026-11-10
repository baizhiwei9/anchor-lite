"""
DataHub 缓存层 — 对外 API
==========================

给所有接口/业务脚本用的一套统一缓存入口。不绑定 data_hub 内部结构，
任何 API 想缓存都能直接用。

核心函数
--------
- :func:`cache_get` / :func:`cache_set` / :func:`cache_delete` —— 基本读写
- :func:`cache_or_fetch` —— **最常用**：命中就返回，未命中就拉取并回填
- :func:`cached` —— 装饰器，一行接入
- :func:`cache_stats` / :func:`cache_purge` / :func:`cache_evict` / :func:`cache_maintain` —— 运维
- :func:`cache_clear` —— 按命名空间/标签清空

命名空间约定
------------
按"数据源或业务域"划分，便于独立清理和观察：

============================ ==========================================
namespace                    用途
============================ ==========================================
``akshare``                   AKShare 原始接口
``tushare``                   Tushare 原始接口
``web``                       网页爬取兜底
``option``                    期权链/看板/标的行情
``index``                     指数成分/日线/快照
``stock``                     个股行情
``margin``                    两融
``macro``                     宏观（利率/汇率/央行）
``futures``                   期货/国债期货
``collector``                 采集器层最终结果
============================ ==========================================

快速上手
--------
::

    from data_hub.cache import cache_or_fetch, cached

    # 方式一：显式调用
    df = cache_or_fetch(
        namespace="option", key="board:510300:2611",
        fetch=lambda: ak.option_finance_board(symbol="华夏上证50ETF期权", end_month="2611"),
        ttl="overnight",
    )

    # 方式二：装饰器
    @cached(namespace="index", ttl="trade_day")
    def get_components(index_code: str):
        return ak.index_stock_cons(symbol=index_code)
    # key 自动由函数名 + 参数生成：index:get_components:index_code=000300
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import time
from typing import Any, Callable, Dict, Iterable, List, Optional

from .policy import resolve_ttl
from .store import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_ENTRIES,
    CacheStore,
)

logger = logging.getLogger("data_hub.cache")

# ---------------------------------------------------------------- 全局单例

_store: Optional[CacheStore] = None


def get_store(db_path: Optional[str] = None) -> CacheStore:
    """获取全局缓存实例（懒加载单例）。"""
    global _store
    if _store is None or (db_path and db_path != _store.db_path):
        _store = CacheStore(db_path)
    return _store


def set_store(store: CacheStore) -> None:
    """替换全局实例（测试用，也可用于接入自定义 db 路径）。"""
    global _store
    _store = store


# ---------------------------------------------------------------- 命中统计

_counters = {"hit": 0, "miss": 0, "set": 0, "error": 0}


def reset_counters() -> None:
    for k in _counters:
        _counters[k] = 0


def get_counters() -> Dict[str, int]:
    """进程内命中统计（用于单次任务结束时报告缓存效果）。"""
    total = _counters["hit"] + _counters["miss"]
    return {
        **_counters,
        "hit_rate": round(_counters["hit"] / total, 4) if total else 0.0,
    }


# ---------------------------------------------------------------- 基本读写

def cache_get(
    namespace: str,
    key: str,
    default: Any = None,
    store: Optional[CacheStore] = None,
) -> Any:
    """读缓存。未命中/已过期返回 ``default``。"""
    try:
        ok, value = (store or get_store()).get(namespace, key)
    except Exception as e:                # 缓存故障绝不能拖垮业务
        _counters["error"] += 1
        logger.warning(f"[cache] 读取失败 {namespace}:{key} -> {e}")
        return default
    if ok:
        _counters["hit"] += 1
        return value
    _counters["miss"] += 1
    return default


def cache_set(
    namespace: str,
    key: str,
    value: Any,
    ttl: Any = "overnight",
    tag: Optional[str] = None,
    codec: str = "auto",
    meta: Optional[Dict[str, Any]] = None,
    store: Optional[CacheStore] = None,
) -> bool:
    """
    写缓存。

    Args:
        ttl: 策略名（``overnight`` / ``trade_day`` / ``session`` / ``never``）、
             秒数、``timedelta``，或 ``None`` 表示永不过期。
        tag: 分组标签，便于 ``cache_clear(tag=...)`` 批量清理和按组预取。
    """
    try:
        seconds = resolve_ttl(ttl)
        (store or get_store()).put(
            namespace, key, value, ttl=seconds, tag=tag, codec=codec, meta=meta
        )
        _counters["set"] += 1
        return True
    except Exception as e:
        _counters["error"] += 1
        logger.warning(f"[cache] 写入失败 {namespace}:{key} -> {e}")
        return False


def cache_delete(namespace: str, key: str, store: Optional[CacheStore] = None) -> bool:
    """删除单条。"""
    try:
        return (store or get_store()).delete(namespace, key)
    except Exception as e:
        logger.warning(f"[cache] 删除失败 {namespace}:{key} -> {e}")
        return False


def cache_exists(namespace: str, key: str, store: Optional[CacheStore] = None) -> bool:
    """判断是否命中且未过期。"""
    try:
        return (store or get_store()).exists(namespace, key)
    except Exception:
        return False


def cache_or_fetch(
    namespace: str,
    key: str,
    fetch: Callable[[], Any],
    ttl: Any = "overnight",
    tag: Optional[str] = None,
    force: bool = False,
    store: Optional[CacheStore] = None,
    retries: int = 0,
    retry_wait: float = 1.0,
) -> Any:
    """
    **命中即返回，未命中则调用 fetch 并回填**。业务最常用的一行封装。

    约定：
    - ``fetch()`` 返回 ``None`` 或空 DataFrame/list 时**不写缓存**，
      避免把一次失败固化成缓存，让下次有机会重试。
    - 缓存层任何异常都只记日志，不影响业务拿到数据。

    Args:
        force: 跳过读缓存，强制重新拉取。
        retries: fetch 抛异常/返回空时的重试次数。
    """
    st = store or get_store()

    if not force:
        cached_value = cache_get(namespace, key, default=None, store=st)
        if cached_value is not None:
            return cached_value

    last_err: Optional[Exception] = None
    for attempt in range(retries + 1):
        t0 = time.time()
        try:
            value = fetch()
        except Exception as e:
            last_err = e
            logger.warning(
                f"[cache] fetch 异常 {namespace}:{key} "
                f"(第{attempt + 1}/{retries + 1}次) -> {e}"
            )
            if attempt < retries:
                time.sleep(retry_wait)
                continue
            return None

        if _is_empty(value):
            logger.debug(f"[cache] fetch 返回空，不缓存 {namespace}:{key}")
            if attempt < retries:
                time.sleep(retry_wait)
                continue
            return value

        elapsed_ms = int((time.time() - t0) * 1000)
        cache_set(
            namespace, key, value,
            ttl=ttl, tag=tag, store=st,
            meta={"fetch_ms": elapsed_ms, "fetched_at": time.time()},
        )
        return value

    if last_err:
        logger.warning(f"[cache] fetch 最终失败 {namespace}:{key}")
    return None


def _is_empty(value: Any) -> bool:
    """判断返回值是否算"空"——空结果不入缓存。"""
    if value is None:
        return True
    try:
        import pandas as pd

        if isinstance(value, pd.DataFrame):
            return value.empty
        if isinstance(value, pd.Series):
            return value.empty
    except Exception:
        pass
    if isinstance(value, (list, tuple, dict, set, str)):
        return len(value) == 0
    return False


# ---------------------------------------------------------------- 装饰器

def make_key(prefix: str, args: tuple, kwargs: dict, max_len: int = 200) -> str:
    """由函数名 + 参数稳定生成缓存键（超长则哈希截断）。"""
    parts: List[str] = []
    for a in args:
        parts.append(_short_repr(a))
    for k in sorted(kwargs):
        parts.append(f"{k}={_short_repr(kwargs[k])}")
    raw = prefix + (":" + ":".join(parts) if parts else "")
    if len(raw) > max_len:
        digest = hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]
        raw = f"{prefix}:{digest}"
    return raw


def _short_repr(v: Any) -> str:
    if isinstance(v, (str, int, float, bool)) or v is None:
        return str(v)
    if isinstance(v, (list, tuple)) and len(v) <= 5:
        return ",".join(_short_repr(x) for x in v)
    return type(v).__name__


def cached(
    namespace: str,
    ttl: Any = "overnight",
    tag: Optional[str] = None,
    key_fn: Optional[Callable[..., str]] = None,
    skip_empty: bool = True,
):
    """
    装饰器：给任意函数加缓存。

    ::

        @cached(namespace="macro", ttl="trade_day")
        def get_shibor(start, end):
            return ak.rate_interbank(...)

        # 自定义 key
        @cached(namespace="option", ttl="session",
                key_fn=lambda code, month: f"board:{code}:{month}")
        def get_board(code, month): ...
    """

    def decorator(func: Callable) -> Callable:
        prefix = f"{func.__module__.split('.')[-1]}.{func.__name__}"

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if kwargs.pop("_no_cache", False):
                return func(*args, **kwargs)
            force = kwargs.pop("_force_refresh", False)
            key = key_fn(*args, **kwargs) if key_fn else make_key(prefix, args, kwargs)
            return cache_or_fetch(
                namespace=namespace,
                key=key,
                fetch=lambda: func(*args, **kwargs),
                ttl=ttl,
                tag=tag or func.__name__,
                force=force,
            )

        wrapper.cache_namespace = namespace          # type: ignore[attr-defined]
        wrapper.cache_ttl = ttl                      # type: ignore[attr-defined]
        wrapper.cache_clear = lambda: cache_clear(namespace, tag=tag or func.__name__)  # type: ignore[attr-defined]
        return wrapper

    return decorator


# ---------------------------------------------------------------- 运维操作

def cache_stats(namespace: Optional[str] = None, store: Optional[CacheStore] = None) -> Dict[str, Any]:
    """缓存概况。"""
    return (store or get_store()).stats(namespace)


def cache_purge(store: Optional[CacheStore] = None) -> int:
    """清理所有已过期条目（物理删除）。"""
    return (store or get_store()).purge_expired()


def cache_evict(
    max_entries: int = DEFAULT_MAX_ENTRIES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    store: Optional[CacheStore] = None,
) -> int:
    """按 LRU 淘汰到容量上限内。"""
    return (store or get_store()).evict_lru(max_entries, max_bytes)


def cache_clear(
    namespace: Optional[str] = None,
    tag: Optional[str] = None,
    store: Optional[CacheStore] = None,
) -> int:
    """清空（可按 namespace / tag 缩小范围）。"""
    return (store or get_store()).clear(namespace, tag)


def cache_maintain(
    max_entries: int = DEFAULT_MAX_ENTRIES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    vacuum: bool = True,
    store: Optional[CacheStore] = None,
) -> Dict[str, int]:
    """
    一站式维护：**清过期 → LRU 淘汰 → 回收空间**。

    建议挂在每日盘后或凌晨跑一次，就是"过夜就删"的落地动作。
    """
    st = store or get_store()
    expired = st.purge_expired()
    evicted = st.evict_lru(max_entries, max_bytes)
    if vacuum and (expired or evicted):
        st.vacuum()
    return {"expired": expired, "evicted": evicted}


def cache_keys(
    namespace: Optional[str] = None,
    tag: Optional[str] = None,
    store: Optional[CacheStore] = None,
) -> List[str]:
    """列出当前有效的缓存键。"""
    return (store or get_store()).keys(namespace, tag)
