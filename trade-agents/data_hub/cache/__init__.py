"""
DataHub 缓存层
===============

一个给**所有接口共用**的缓存域：SQLite 单文件存储 + TTL 过期 + LRU 淘汰 + 预取接口。

为什么单独做一层
----------------
原来的 ``daily_cache.py`` 是"按日期建目录 + pickle 文件"，只服务 data_hub 自己的
每日预拉取，且没有过期和清理逻辑，跑久了会无限膨胀。本模块是通用替代：

- 任何 API / 业务脚本都能直接用，不依赖 data_hub 内部结构
- 命名空间隔离，互不干扰
- 到期自动失效，过期条目惰性删除 + 定时物理清理
- 容量上限 + LRU 淘汰，永不无限膨胀
- 预取任务注册表，盘前/盘后一次预热，业务直接吃缓存

四层结构
--------
============================ ============================================
模块                          职责
============================ ============================================
``store.py``                  SQLite 引擎：表结构、编解码、清理、统计
``policy.py``                 过期策略：隔夜/交易日/盘中/固定/永久
``api.py``                    对外 API：get/set/cache_or_fetch/@cached/运维
``prefetch.py``               预取注册表 + 并发执行 + 报告
============================ ============================================

一行上手
--------
::

    from data_hub.cache import cache_or_fetch, cached, prefetch, run_prefetch

    # 读缓存 → 未命中才拉
    df = cache_or_fetch("option", "board:510300", lambda: fetch_board("510300"),
                        ttl="session")

    # 装饰器
    @cached(namespace="index", ttl="trade_day")
    def components(code):
        return get_data("index_components", index_code=code)

    # 预取
    @prefetch(namespace="macro", group="premarket", ttl="trade_day")
    def shibor():
        return get_data("interest_rate", rate_type="shibor")

    run_prefetch(group="premarket")

命令行
------
::

    python -m data_hub.cache stats            # 看缓存占用/命中/命名空间分布
    python -m data_hub.cache purge            # 清过期
    python -m data_hub.cache maintain         # 清过期 + LRU 淘汰 + VACUUM
    python -m data_hub.cache clear -n option  # 清某个命名空间
    python -m data_hub.cache prefetch -g daily --workers 6
"""

from __future__ import annotations

from .api import (
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
    get_counters,
    get_store,
    make_key,
    reset_counters,
    set_store,
)
from .policy import (
    POLICIES,
    is_trading_now,
    next_trade_day,
    resolve_ttl,
    ttl_minutes,
    ttl_never,
    ttl_overnight,
    ttl_seconds,
    ttl_session,
    ttl_trade_day,
    ttl_until,
)
from .prefetcher import (
    NEGATIVE_NAMESPACE,
    PrefetchReport,
    PrefetchTask,
    clear_failure_backoff,
    list_groups,
    list_tasks,
    prefetch,
    register,
    register_builtin_tasks,
    run_prefetch,
    unregister,
)
from .store import (
    DEFAULT_CACHE_DIR,
    DEFAULT_DB_NAME,
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_ENTRIES,
    CacheStore,
)

__all__ = [
    # 存储
    "CacheStore",
    "DEFAULT_CACHE_DIR",
    "DEFAULT_DB_NAME",
    "DEFAULT_MAX_ENTRIES",
    "DEFAULT_MAX_BYTES",
    "get_store",
    "set_store",
    # 基本 API
    "cache_get",
    "cache_set",
    "cache_delete",
    "cache_exists",
    "cache_keys",
    "cache_or_fetch",
    "cached",
    "make_key",
    # 运维
    "cache_stats",
    "cache_purge",
    "cache_evict",
    "cache_clear",
    "cache_maintain",
    # 统计
    "get_counters",
    "reset_counters",
    # 过期策略
    "POLICIES",
    "resolve_ttl",
    "ttl_overnight",
    "ttl_trade_day",
    "ttl_session",
    "ttl_seconds",
    "ttl_minutes",
    "ttl_never",
    "ttl_until",
    "next_trade_day",
    "is_trading_now",
    # 预取
    "prefetch",
    "register",
    "unregister",
    "list_tasks",
    "list_groups",
    "run_prefetch",
    "register_builtin_tasks",
    "clear_failure_backoff",
    "NEGATIVE_NAMESPACE",
    "PrefetchTask",
    "PrefetchReport",
]
