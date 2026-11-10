"""
DataHub 缓存层 — 过期策略
==========================

把"什么时候该失效"这件事从业务代码里抽出来，统一成几种可复用的策略。

内置策略
--------
============== ================================================================
策略            语义
============== ================================================================
``overnight``   过夜即失效：到次日 00:00 自动过期。适合当日快照类数据。
``trade_day``   跨交易日失效：到下一个交易日 09:15 过期，周末/节假日自动顺延。
``session``     盘中会话失效：当日 15:30（收盘）过期，收盘后拉到的不受影响。
``seconds``     固定存活秒数。适合有明确刷新周期的行情/榜单。
``never``       永不过期。适合静态映射表（合约映射、指数成分名单等）。
============== ================================================================

用法
----
::

    from data_hub.cache.policy import ttl_overnight, ttl_trade_day, ttl_seconds

    cache_set("option", "510300", df, ttl=ttl_overnight())
    cache_set("index", "000300", df, ttl=ttl_trade_day())
    cache_set("spot", "510300", df, ttl=ttl_seconds(60))
"""

from __future__ import annotations

import logging
from datetime import datetime, time as dtime, timedelta
from typing import Optional

logger = logging.getLogger("data_hub.cache.policy")

# 收盘时刻（A股 15:00 收盘，留 30 分钟给数据落地）
MARKET_CLOSE = dtime(15, 30)
# 盘前时刻（集合竞价前，缓存应在此时前失效）
MARKET_OPEN = dtime(9, 15)


# ---------------------------------------------------------------- 交易日判断

# 进程内记忆：YYYYMMDD -> 是否交易日
_trade_date_cache: dict = {}

# 交易日历持久化缓存（放 SQLite 里，跨进程复用，避免每次都请求日历接口）
_CAL_NAMESPACE = "__calendar__"
_CAL_TTL = 30 * 24 * 3600          # 日历结果缓存 30 天

# 熔断：日历接口连续失败 N 次后，本进程内不再尝试，直接走周末降级。
# 目的：Tushare 挂掉时不要让每次缓存写入都白等 1~2 秒。
_CAL_FAIL_THRESHOLD = 2
_cal_fail_count = 0
_cal_disabled = False


def _cal_store():
    """取存储层（不走 api 层，避免与 ttl 解析形成递归）。"""
    from .store import CacheStore  # 局部导入，避免循环

    from .api import get_store

    return get_store()


def _load_calendar_flag(key: str) -> Optional[bool]:
    """从持久化缓存读某个日期的交易日标记。"""
    if _cal_disabled:
        return None
    try:
        ok, val = _cal_store().get(_CAL_NAMESPACE, f"trade_day:{key}", touch=False)
        if ok and isinstance(val, bool):
            return val
    except Exception:
        pass
    return None


def _save_calendar_flag(key: str, is_trade: bool) -> None:
    """把交易日标记落盘（直接走 store，避免递归调用 ttl 解析）。"""
    try:
        _cal_store().put(
            _CAL_NAMESPACE, f"trade_day:{key}", is_trade,
            ttl=_CAL_TTL, codec="json", tag="calendar",
        )
    except Exception:
        pass


def _is_trade_day(d: datetime) -> bool:
    """
    判断是否交易日。

    三级取值：
    1. 进程内记忆
    2. SQLite 持久化缓存（30 天，跨进程复用）
    3. data_hub 交易日历接口（失败则降级为"周一至周五"）

    日历接口连续失败达到阈值后熔断，本进程内直接走周末降级，
    不再为每次缓存写入白等接口超时。
    """
    global _cal_fail_count, _cal_disabled

    key = d.strftime("%Y%m%d")
    if key in _trade_date_cache:
        return _trade_date_cache[key]

    # 第二级：持久化缓存
    persisted = _load_calendar_flag(key)
    if persisted is not None:
        _trade_date_cache[key] = persisted
        return persisted

    result: Optional[bool] = None
    if not _cal_disabled:
        try:
            # 延迟导入，避免 cache 包初始化时反向依赖 data_hub 主包
            from .. import get_trade_dates  # type: ignore

            dates = get_trade_dates(key, key)
            if dates:
                result = key in set(str(x).replace("-", "") for x in dates)
            if result is None:
                _cal_fail_count += 1
            else:
                _cal_fail_count = 0
        except Exception:
            _cal_fail_count += 1

        if _cal_fail_count >= _CAL_FAIL_THRESHOLD:
            _cal_disabled = True
            logger.warning(
                "[cache] 交易日历接口连续失败，本进程改用周末降级判断"
            )

    if result is None:
        result = d.weekday() < 5          # 降级：周末非交易日
    else:
        _save_calendar_flag(key, result)  # 只有真正查到才落盘

    _trade_date_cache[key] = result
    return result


def next_trade_day(from_dt: Optional[datetime] = None) -> datetime:
    """返回严格晚于 ``from_dt`` 的下一个交易日 00:00。"""
    d = (from_dt or datetime.now()).replace(hour=0, minute=0, second=0, microsecond=0)
    for _ in range(30):                   # 最长跨 30 天，覆盖春节长假
        d += timedelta(days=1)
        if _is_trade_day(d):
            return d
    return d + timedelta(days=1)          # 兜底：日历彻底不可用时按自然日


def is_trading_now(now: Optional[datetime] = None) -> bool:
    """当前是否处于交易时段（交易日 09:15–15:30）。"""
    n = now or datetime.now()
    if not _is_trade_day(n):
        return False
    return MARKET_OPEN <= n.time() <= MARKET_CLOSE


# ---------------------------------------------------------------- TTL 策略

def ttl_overnight(now: Optional[datetime] = None) -> float:
    """
    **过夜失效**：到次日 00:00 过期。

    这是"过了夜就删"的直接实现——今天拿的当日快照，明天一律重新拉。
    返回剩余秒数（最小 60 秒，避免刚好卡在 23:59:59 时 ttl≈0）。
    """
    n = now or datetime.now()
    tomorrow = (n + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60.0, (tomorrow - n).total_seconds())


def ttl_trade_day(now: Optional[datetime] = None) -> float:
    """
    **跨交易日失效**：到下一个交易日 09:15 过期。

    比 overnight 更宽容——周五收盘拉的数据能活到周一开盘前，
    省掉周末的无谓重拉；但开盘前一定失效，保证盘前拿到的是新数据。
    """
    n = now or datetime.now()
    ntd = next_trade_day(n)
    expire_at = datetime.combine(ntd.date(), MARKET_OPEN)
    if expire_at <= n:                    # 今天就是交易日且还没到 9:15
        expire_at = datetime.combine(n.date(), MARKET_OPEN)
    return max(60.0, (expire_at - n).total_seconds())


def ttl_session(now: Optional[datetime] = None) -> float:
    """
    **盘中会话失效**：当日 15:30 过期；若已过 15:30，则顺延到下一交易日 15:30。
    """
    n = now or datetime.now()
    today_close = datetime.combine(n.date(), MARKET_CLOSE)
    if n < today_close:
        return max(60.0, (today_close - n).total_seconds())
    ntd = next_trade_day(n)
    return max(60.0, (datetime.combine(ntd.date(), MARKET_CLOSE) - n).total_seconds())


def ttl_seconds(seconds: float) -> float:
    """固定存活秒数。"""
    return max(1.0, float(seconds))


def ttl_minutes(minutes: float) -> float:
    """固定存活分钟数。"""
    return ttl_seconds(minutes * 60)


def ttl_never() -> None:
    """永不过期（返回 None，存储层据此写 NULL）。"""
    return None


def ttl_until(dt: datetime) -> float:
    """指定绝对过期时刻，返回剩余秒数。"""
    return max(1.0, (dt - datetime.now()).total_seconds())


# ---------------------------------------------------------------- 策略注册表

POLICIES = {
    "overnight": ttl_overnight,
    "trade_day": ttl_trade_day,
    "session": ttl_session,
    "never": ttl_never,
}


def resolve_ttl(spec) -> Optional[float]:
    """
    把策略名 / 数值 / None 统一解析成 TTL 秒数。

    支持：
    - ``"overnight"`` / ``"trade_day"`` / ``"session"`` / ``"never"``
    - 数字（秒）
    - ``timedelta``
    - ``None`` → 永不过期
    """
    if spec is None:
        return None
    if isinstance(spec, (int, float)):
        return float(spec)
    if isinstance(spec, timedelta):
        return max(1.0, spec.total_seconds())
    if isinstance(spec, str):
        name = spec.strip().lower()
        if name in POLICIES:
            return POLICIES[name]()
        raise ValueError(
            f"未知缓存策略 '{spec}'，可选: {', '.join(POLICIES)} 或直接传秒数"
        )
    raise TypeError(f"不支持的 ttl 类型: {type(spec)}")
