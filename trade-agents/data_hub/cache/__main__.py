"""
DataHub 缓存维护命令行
=======================

::

    python -m data_hub.cache stats                        # 概况
    python -m data_hub.cache stats -n option              # 看单个命名空间
    python -m data_hub.cache keys -n option               # 列出有效键
    python -m data_hub.cache purge                        # 清过期
    python -m data_hub.cache evict --max-entries 5000     # 按 LRU 淘汰
    python -m data_hub.cache maintain                     # 清过期+淘汰+VACUUM
    python -m data_hub.cache clear -n option              # 清命名空间
    python -m data_hub.cache clear -t daily               # 按标签清
    python -m data_hub.cache clear --all                  # 清全库
    python -m data_hub.cache reset-backoff                # 清除失败退避
    python -m data_hub.cache prefetch -g daily            # 跑预取
    python -m data_hub.cache prefetch -g daily --force    # 强制重拉
    python -m data_hub.cache tasks                        # 列出已注册预取任务
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from typing import List, Optional

from .api import (
    cache_clear,
    cache_evict,
    cache_keys,
    cache_maintain,
    cache_purge,
    cache_stats,
    get_store,
)
from .prefetcher import (
    clear_failure_backoff,
    list_groups,
    list_tasks,
    register_builtin_tasks,
    run_prefetch,
)
from .store import DEFAULT_MAX_BYTES, DEFAULT_MAX_ENTRIES


def _human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}" if unit != "B" else f"{n:.0f}B"
        n /= 1024
    return f"{n:.1f}TB"


def _ts(t: Optional[float]) -> str:
    if not t:
        return "-"
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- 子命令

def cmd_stats(args) -> int:
    st = cache_stats(args.namespace)
    print(f"缓存库: {st['db_path']}")
    print(f"磁盘占用: {_human_bytes(st['db_bytes'])}")
    print(f"有效条目: {st['entries']}   已过期待清: {st['expired']}")
    print(f"数据体积: {_human_bytes(st['bytes'])}")
    print(f"累计命中: {st['total_hits']}")
    print(f"最旧写入: {_ts(st['oldest'])}")
    print(f"最新写入: {_ts(st['newest'])}")
    if st["by_namespace"]:
        print("\n按命名空间:")
        print(f"  {'命名空间':<20} {'条目':>8} {'体积':>10}")
        for row in st["by_namespace"]:
            print(f"  {row['namespace']:<20} {row['entries']:>8} {_human_bytes(row['bytes']):>10}")
    return 0


def cmd_keys(args) -> int:
    ks = cache_keys(args.namespace, args.tag)
    if not ks:
        print("（无有效缓存键）")
        return 0
    for k in sorted(ks)[: args.limit]:
        print(k)
    if len(ks) > args.limit:
        print(f"... 共 {len(ks)} 条，已截断")
    return 0


def cmd_purge(args) -> int:
    n = cache_purge()
    print(f"清理过期条目: {n}")
    if args.vacuum and n:
        get_store().vacuum()
        print("已回收空间 (VACUUM)")
    return 0


def cmd_evict(args) -> int:
    n = cache_evict(max_entries=args.max_entries, max_bytes=args.max_bytes)
    print(f"LRU 淘汰: {n} 条")
    return 0


def cmd_maintain(args) -> int:
    res = cache_maintain(
        max_entries=args.max_entries, max_bytes=args.max_bytes, vacuum=not args.no_vacuum
    )
    print(f"清理过期: {res['expired']}    LRU 淘汰: {res['evicted']}")
    st = cache_stats()
    print(f"当前条目: {st['entries']}    磁盘: {_human_bytes(st['db_bytes'])}")
    return 0


def cmd_clear(args) -> int:
    if not args.all and not args.namespace and not args.tag:
        print("必须指定 --namespace / --tag / --all 之一，拒绝清空全库", file=sys.stderr)
        return 2
    if args.all:
        ans = input("确认清空整个缓存库？(yes/N) ").strip().lower()
        if ans != "yes":
            print("已取消")
            return 1
    n = cache_clear(None if args.all else args.namespace, args.tag)
    print(f"已删除 {n} 条")
    return 0


def cmd_reset_backoff(args) -> int:
    """清空失败退避标记——外部源恢复后想立刻重试时用。"""
    n = clear_failure_backoff()
    print(f"已清除失败退避标记: {n} 条")
    return 0


def cmd_tasks(args) -> int:
    register_builtin_tasks()
    tasks = list_tasks(args.group)
    if not tasks:
        print("（无已注册任务）")
        return 0
    print(f"{'任务名':<32} {'分组':<12} {'命名空间':<12} {'TTL':<12} {'键'}")
    print("-" * 100)
    for t in tasks:
        ttl = t.ttl if isinstance(t.ttl, str) else f"{t.ttl}s"
        print(f"{t.name:<32} {t.group:<12} {t.namespace:<12} {str(ttl):<12} {t.key}")
    print(f"\n分组: {', '.join(list_groups())}")
    return 0


def cmd_prefetch(args) -> int:
    n_new = register_builtin_tasks()
    if n_new:
        print(f"已加载内置预取任务 {n_new} 项")

    rep = run_prefetch(
        group=args.group,
        names=args.names,
        force=args.force,
        workers=args.workers,
        timeout=args.timeout,
    )

    if args.json:
        print(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2))
    elif args.markdown:
        print(rep.to_markdown())
    else:
        for t in rep.tasks:
            flag = {"hit": "♻️ ", "fetched": "🆕", "failed": "❌", "skipped": "⏭️ "}.get(t.status, "  ")
            extra = f"  <- {t.error}" if t.error else ""
            print(f"{flag}{t.name:<32} {t.status:<9} {t.elapsed_ms:>6}ms  {t.full_key}{extra}")
        print()
        print(rep.summary())

    return 0 if rep.ok else 1


# ---------------------------------------------------------------- 入口

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m data_hub.cache",
        description="DataHub 缓存维护工具",
    )
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("stats", help="查看缓存概况")
    s.add_argument("-n", "--namespace", help="只看某个命名空间")
    s.set_defaults(func=cmd_stats)

    s = sub.add_parser("keys", help="列出有效缓存键")
    s.add_argument("-n", "--namespace")
    s.add_argument("-t", "--tag")
    s.add_argument("--limit", type=int, default=100)
    s.set_defaults(func=cmd_keys)

    s = sub.add_parser("purge", help="清理过期条目")
    s.add_argument("--vacuum", action="store_true", help="清理后回收空间")
    s.set_defaults(func=cmd_purge)

    s = sub.add_parser("evict", help="按 LRU 淘汰")
    s.add_argument("--max-entries", type=int, default=DEFAULT_MAX_ENTRIES)
    s.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    s.set_defaults(func=cmd_evict)

    s = sub.add_parser("maintain", help="清过期 + LRU 淘汰 + 回收空间")
    s.add_argument("--max-entries", type=int, default=DEFAULT_MAX_ENTRIES)
    s.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    s.add_argument("--no-vacuum", action="store_true")
    s.set_defaults(func=cmd_maintain)

    s = sub.add_parser("clear", help="清空缓存")
    s.add_argument("-n", "--namespace")
    s.add_argument("-t", "--tag")
    s.add_argument("--all", action="store_true", help="清空全库（需二次确认）")
    s.set_defaults(func=cmd_clear)

    s = sub.add_parser("reset-backoff", help="清除失败退避标记，让失败任务立刻可重试")
    s.set_defaults(func=cmd_reset_backoff)

    s = sub.add_parser("tasks", help="列出已注册的预取任务")
    s.add_argument("-g", "--group")
    s.set_defaults(func=cmd_tasks)

    s = sub.add_parser("prefetch", help="执行预取")
    s.add_argument("-g", "--group", help="分组: daily / premarket / weekly / on_demand")
    s.add_argument("--names", nargs="+", help="指定任务名")
    s.add_argument("--force", action="store_true", help="忽略缓存强制重拉")
    s.add_argument("--workers", type=int, default=4)
    s.add_argument("--timeout", type=float, default=None, help="整批总超时（秒）")
    s.add_argument("--json", action="store_true", help="JSON 输出")
    s.add_argument("--markdown", action="store_true", help="Markdown 表格输出")
    s.set_defaults(func=cmd_prefetch)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
