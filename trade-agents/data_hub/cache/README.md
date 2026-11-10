# data_hub.cache —— 通用缓存域

给 data_hub 和**其他任何业务**共用的缓存层。一句话：**拉过的数据别再拉，过期自动清，空间满了按 LRU 淘汰，还能提前预热。**

```
┌──────────────────────────────────────────────────────────┐
│  prefetcher.py   预取接口：注册任务 → 并发预热 → 出报告      │
├──────────────────────────────────────────────────────────┤
│  api.py          对外 API：get/set/or_fetch/装饰器/stats     │
├──────────────────────────────────────────────────────────┤
│  policy.py       过期策略：overnight / trade_day / session  │
├──────────────────────────────────────────────────────────┤
│  store.py        SQLite 引擎：WAL、TTL 字段、LRU 字段        │
└──────────────────────────────────────────────────────────┘
```

## 为什么是 SQLite

| 方案 | 结论 |
|---|---|
| 一堆 pickle/CSV 小文件 | 现有 `daily_cache.py` 的做法。海量小文件、没法按 TTL 查、没法排序淘汰、跨脚本共享难 |
| 内存 dict | 进程一退就没了，跨 cron 任务完全失效 |
| Redis | 要额外起服务，云电脑上不划算 |
| **SQLite（选它）** | 单文件、零运维、WAL 支持多进程并发读、TTL 和 LRU 都是 SQL 一行、能原子淘汰、能 VACUUM 回收 |

默认落在 **本地盘** `~/.cache/data_hub/cache.db`，不放云盘：
- 本地盘快，且不会有 FUSE 文件锁的坑
- 缓存本来就是易失的，重启丢了无所谓
- 换位置：`export DATA_HUB_CACHE_DIR=/your/path`

## 三种过期策略（"过夜就删"怎么落地）

| 策略 | 什么时候失效 | 适合 |
|---|---|---|
| `"overnight"` | 次日 00:00 | 当天算一次就够的（指数成分、行业分类） |
| `"trade_day"` | 下一个交易日 09:15 | 一个交易日内的行情类数据 |
| `"session"` | 当日 15:05 收盘后 | 盘中要刷新的（期权链、分时） |
| `"never"` | 永不 | 静态字典（交易日历、代码表） |
| 数字 | N 秒后 | 自定义 |
| `ttl_seconds(n)` / `ttl_minutes(n)` / `ttl_until(dt)` | 灵活 | 自定义 |

**过期不是靠定时器**：读取时就会判 `expires_at`，过期直接当 miss。
`purge` 只是把死行物理删掉、回收磁盘，属于后台打扫，不影响正确性。

## 快速上手

```python
from data_hub.cache import cache_get, cache_set, cache_or_fetch, cached

# 1) 最常用：命中就返回，没命中才拉，拉完自动回填
df = cache_or_fetch(
    namespace="option",          # 命名空间 = 业务域，天然隔离
    key="board:510300",
    fetch=lambda: get_data("option_data", exchange="SSE", underlying="510300"),
    ttl="session",               # 收盘后失效
    tag="daily",                 # 打标签，方便批量清
)

# 2) 手动控制
cache_set("macro", "shibor", value, ttl="trade_day")
v = cache_get("macro", "shibor", default=None)

# 3) 装饰器：函数级缓存，key 由参数自动生成
@cached(namespace="index", ttl="trade_day")
def get_components(index_code: str):
    return get_data("index_components", index_code=index_code)
```

**空结果不缓存**：`None` / `[]` / `{}` / 空 DataFrame 一律不写缓存，
避免把一次失败固化成"永远拿不到数据"。

## 预取接口（提前准备）

其他接口也能注册自己的预取任务：

```python
from data_hub.cache import prefetch, run_prefetch, register_builtin_tasks

@prefetch(namespace="macro", key="cpi", ttl="trade_day", group="premarket")
def warm_cpi():
    return get_data("macro_data", indicator="cpi")

# 开盘前把 premarket 组一次性预热
register_builtin_tasks()          # 挂上 data_hub 自带的 12 个高频任务
report = run_prefetch(group="premarket", workers=4)
print(report.summary())
```

`run_prefetch` 返回报告，支持 `.summary()`（纯文本）和 `.to_markdown()`（卡片/邮件）：

```
预取 共11项 / 命中10 / 新拉1 / ⏭️跳过0 / ❌失败0 / 耗时 2.3s
```

**失败退避**：某个任务拉取失败后，`retry_after`（默认 600 秒）内不再重试。
外部源整体挂掉时，不会让每轮预取都白等十几秒。
源恢复了想立刻重试：`python -m data_hub.cache reset-backoff`

## 维护命令

```bash
python -m data_hub.cache stats                    # 看用量、命中率、按命名空间分布
python -m data_hub.cache maintain                 # 清过期 → LRU 淘汰 → VACUUM（每日跑这个）
python -m data_hub.cache purge                    # 只清过期
python -m data_hub.cache evict --max-entries 5000 # 只按 LRU 淘汰
python -m data_hub.cache keys -n option           # 列某个命名空间的键
python -m data_hub.cache clear -n option          # 清某个命名空间
python -m data_hub.cache clear -t daily           # 按 tag 清
python -m data_hub.cache reset-backoff            # 清失败退避
python -m data_hub.cache tasks                    # 列已注册的预取任务
python -m data_hub.cache prefetch -g daily        # 跑预取
```

**每日过夜清理**（建议挂 crontab）：
```cron
10 0 * * * cd <repo> && PYTHONPATH=. python3 -m data_hub.cache maintain >> /tmp/data_hub_cache.log 2>&1
```

## 容量上限

默认 `max_entries=20000` / `max_bytes=2GB`，超了按 `accessed_at`（最近访问时间）淘汰最久没用的。
调参：`cache_maintain(max_entries=..., max_bytes=...)` 或改 `DATA_HUB_CACHE_MAX_ENTRIES` / `DATA_HUB_CACHE_MAX_BYTES`。

## 统计

```python
from data_hub.cache import cache_stats, get_counters

cache_stats()            # 全局：条目数、磁盘占用、数据体积、过期待清、最旧/最新、按命名空间分布
cache_stats("option")    # 只看某个域
get_counters()           # 本进程命中/未命中计数 → {'hit': 42, 'miss': 7, ...}
```

## 设计要点

- **命名空间隔离**：不同业务/API 用不同 namespace，互不覆盖，可以单独清。
- **tag 分组**：同一批数据打同一个 tag，能跨命名空间批量清（比如"今天这批 daily 全清"）。
- **序列化自适应**：DataFrame 走 pickle、dict/list 走 json、str 走 text，自动选最省的。
- **并发安全**：WAL + `busy_timeout=5000` + 写操作 `BEGIN IMMEDIATE`，多个 cron 脚本同时写不会互相踩。
- **异常不影响业务**：缓存层任何异常只记日志，业务拿不到缓存就走原路径，不会因为缓存挂了而挂。
- **不缓存空结果**：见上。

## 和 `daily_cache.py` 的关系

`daily_cache.py` 是老的按日期建目录的文件缓存，**保留不动**（线上脚本还在用）。
新代码建议直接用 `data_hub.cache`；老脚本要迁移的话，把 `load_daily_cache/save_daily_cache`
换成 `cache_get/cache_set(namespace="daily", key=f"{trade_date}:{data_type}", ttl="never")` 即可。
