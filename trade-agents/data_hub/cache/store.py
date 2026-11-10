"""
DataHub 缓存层 — SQLite 存储引擎
=================================

设计要点
--------
1. **单文件 SQLite**：一个 .db 管所有缓存，避免海量小文件导致 inode 爆炸。
2. **WAL 模式**：多进程可并发读，写锁粒度小，适合 crontab 多脚本同时跑。
3. **命名空间隔离**：不同数据源/业务域互不干扰（akshare / tushare / option / macro ...）。
4. **TTL 内建**：每行带 ``expires_at``，读时惰性判定 + 定时物理清理双保险。
5. **LRU 内建**：每行带 ``accessed_at`` / ``hits``，容量超限按最久未访问淘汰。
6. **落本地盘**：默认放 ``~/.cache/data_hub/``，不放云盘 ——
   云盘 FUSE 对文件锁支持差，SQLite 在上面容易锁死；且本地盘 IO 快一个量级。

表结构
------
::

    cache_entries(
        namespace, key,           -- 主键
        value, codec,             -- 序列化后的值 + 编解码方式
        size_bytes,               -- 占用的字节数，用于容量淘汰
        created_at, expires_at,   -- 创建时间 / 过期时间（NULL=永不过期）
        accessed_at, hits,        -- LRU 依据 + 命中统计
        tag,                      -- 分组标签，支持按标签批量清理/预取
        meta                      -- JSON 元信息（来源、耗时、参数指纹等）
    )
"""

from __future__ import annotations

import json
import logging
import os
import pickle
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("data_hub.cache.store")

# ---------------------------------------------------------------- 路径与默认值

DEFAULT_CACHE_DIR = os.environ.get(
    "DATA_HUB_CACHE_DIR",
    os.path.join(os.path.expanduser("~"), ".cache", "data_hub"),
)
DEFAULT_DB_NAME = "cache.db"

# 默认容量上限（可按需调）
DEFAULT_MAX_ENTRIES = int(os.environ.get("DATA_HUB_CACHE_MAX_ENTRIES", "20000"))
DEFAULT_MAX_BYTES = int(os.environ.get("DATA_HUB_CACHE_MAX_BYTES", str(512 * 1024 * 1024)))

# SQLite 忙等超时（秒）——多进程并发时给足重试窗口
_BUSY_TIMEOUT = float(os.environ.get("DATA_HUB_CACHE_BUSY_TIMEOUT", "15"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS cache_entries (
    namespace   TEXT    NOT NULL,
    key         TEXT    NOT NULL,
    value       BLOB    NOT NULL,
    codec       TEXT    NOT NULL DEFAULT 'pickle',
    size_bytes  INTEGER NOT NULL DEFAULT 0,
    created_at  REAL    NOT NULL,
    expires_at  REAL,
    accessed_at REAL    NOT NULL,
    hits        INTEGER NOT NULL DEFAULT 0,
    tag         TEXT,
    meta        TEXT,
    PRIMARY KEY (namespace, key)
);
CREATE INDEX IF NOT EXISTS idx_cache_expires  ON cache_entries(expires_at);
CREATE INDEX IF NOT EXISTS idx_cache_accessed ON cache_entries(accessed_at);
CREATE INDEX IF NOT EXISTS idx_cache_tag      ON cache_entries(tag);
CREATE INDEX IF NOT EXISTS idx_cache_ns       ON cache_entries(namespace);
"""

# 兼容旧库升级（v1 -> v2 若缺列则补齐）
_MIGRATIONS = {
    "codec": "ALTER TABLE cache_entries ADD COLUMN codec TEXT NOT NULL DEFAULT 'pickle'",
    "size_bytes": "ALTER TABLE cache_entries ADD COLUMN size_bytes INTEGER NOT NULL DEFAULT 0",
    "hits": "ALTER TABLE cache_entries ADD COLUMN hits INTEGER NOT NULL DEFAULT 0",
    "tag": "ALTER TABLE cache_entries ADD COLUMN tag TEXT",
    "meta": "ALTER TABLE cache_entries ADD COLUMN meta TEXT",
}


# ---------------------------------------------------------------- 连接管理

class CacheStore:
    """SQLite 缓存存储。线程内复用连接，跨线程各自新建（SQLite 连接非线程安全）。"""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            os.makedirs(DEFAULT_CACHE_DIR, exist_ok=True)
            db_path = os.path.join(DEFAULT_CACHE_DIR, DEFAULT_DB_NAME)
        self.db_path = db_path
        parent = os.path.dirname(os.path.abspath(db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._local = threading.local()
        self._init_lock = threading.Lock()
        self._initialized = False
        self._ensure_schema()

    # -- 连接 ----------------------------------------------------------

    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                self.db_path,
                timeout=_BUSY_TIMEOUT,
                isolation_level=None,          # 自动提交，显式用 BEGIN
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            # WAL：多进程并发读 + 写不阻塞读
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                pass  # 某些文件系统不支持 WAL，退回默认模式
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(f"PRAGMA busy_timeout={int(_BUSY_TIMEOUT * 1000)}")
            conn.execute("PRAGMA temp_store=MEMORY")
            self._local.conn = conn
        return conn

    @contextmanager
    def transaction(self):
        """显式写事务，异常自动回滚。"""
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    def _ensure_schema(self) -> None:
        with self._init_lock:
            if self._initialized:
                return
            conn = self.conn
            conn.executescript(SCHEMA)
            # 旧库补列
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(cache_entries)")}
            for col, ddl in _MIGRATIONS.items():
                if col not in cols:
                    try:
                        conn.execute(ddl)
                        logger.info(f"[cache] 升级表结构，补列 {col}")
                    except sqlite3.OperationalError:
                        pass
            self._initialized = True

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # -- 编解码 --------------------------------------------------------

    @staticmethod
    def _encode(value: Any, codec: str = "auto") -> Tuple[bytes, str]:
        """把任意 Python 对象编码成 bytes，返回 (blob, 实际 codec)。"""
        if codec == "auto":
            if isinstance(value, str):
                codec = "text"
            elif isinstance(value, (dict, list, tuple, int, float, bool)) or value is None:
                codec = "json"
            else:
                codec = "pickle"

        if codec == "text":
            return value.encode("utf-8"), "text"
        if codec == "json":
            return json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"), "json"
        if codec == "pickle":
            return pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL), "pickle"
        raise ValueError(f"未知 codec: {codec}")

    @staticmethod
    def _decode(blob: bytes, codec: str) -> Any:
        if codec == "text":
            return blob.decode("utf-8")
        if codec == "json":
            return json.loads(blob.decode("utf-8"))
        if codec == "pickle":
            return pickle.loads(blob)
        raise ValueError(f"未知 codec: {codec}")

    # -- 基本读写 ------------------------------------------------------

    def put(
        self,
        namespace: str,
        key: str,
        value: Any,
        ttl: Optional[float] = None,
        tag: Optional[str] = None,
        codec: str = "auto",
        meta: Optional[Dict[str, Any]] = None,
    ) -> int:
        """写入一条缓存，返回占用字节数。ttl=None 表示永不过期。"""
        blob, used_codec = self._encode(value, codec)
        now = time.time()
        expires_at = None if ttl is None else now + float(ttl)
        meta_json = json.dumps(meta, ensure_ascii=False, default=str) if meta else None
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO cache_entries
                    (namespace, key, value, codec, size_bytes,
                     created_at, expires_at, accessed_at, hits, tag, meta)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(namespace, key) DO UPDATE SET
                    value       = excluded.value,
                    codec       = excluded.codec,
                    size_bytes  = excluded.size_bytes,
                    created_at  = excluded.created_at,
                    expires_at  = excluded.expires_at,
                    accessed_at = excluded.accessed_at,
                    tag         = excluded.tag,
                    meta        = excluded.meta
                """,
                (namespace, key, blob, used_codec, len(blob),
                 now, expires_at, now, tag, meta_json),
            )
        return len(blob)

    def get(self, namespace: str, key: str, touch: bool = True) -> Tuple[bool, Any]:
        """
        读取一条缓存，返回 ``(命中?, 值)``。

        过期条目视为未命中（惰性删除，物理清理交给 ``purge_expired``）。
        ``touch=True`` 时更新 accessed_at / hits，供 LRU 使用。
        """
        row = self.conn.execute(
            "SELECT value, codec, expires_at, meta FROM cache_entries "
            "WHERE namespace=? AND key=?",
            (namespace, key),
        ).fetchone()
        if row is None:
            return False, None

        expires_at = row["expires_at"]
        if expires_at is not None and expires_at <= time.time():
            # 惰性删除：过期即清，不占空间也不占 LRU
            self.delete(namespace, key)
            return False, None

        if touch:
            self.conn.execute(
                "UPDATE cache_entries SET accessed_at=?, hits=hits+1 "
                "WHERE namespace=? AND key=?",
                (time.time(), namespace, key),
            )
        return True, self._decode(row["value"], row["codec"])

    def get_meta(self, namespace: str, key: str) -> Optional[Dict[str, Any]]:
        """
        取一条记录的完整元信息（不含 value 本体，省 IO）。

        返回字段：namespace / key / codec / size_bytes / created_at /
        expires_at / accessed_at / hits / tag / meta / ttl_left。
        条目不存在返回 None。``expires_at`` 为 None 表示永不过期。
        """
        row = self.conn.execute(
            "SELECT namespace, key, codec, size_bytes, created_at, expires_at, "
            "accessed_at, hits, tag, meta FROM cache_entries "
            "WHERE namespace=? AND key=?",
            (namespace, key),
        ).fetchone()
        if row is None:
            return None
        info = dict(row)
        try:
            info["meta"] = json.loads(info["meta"]) if info["meta"] else {}
        except (ValueError, TypeError):
            info["meta"] = {}
        exp = info.get("expires_at")
        info["ttl_left"] = None if exp is None else max(0.0, exp - time.time())
        info["expired"] = exp is not None and exp <= time.time()
        return info

    def exists(self, namespace: str, key: str) -> bool:
        """判断是否命中且未过期（不更新 LRU）。"""
        row = self.conn.execute(
            "SELECT expires_at FROM cache_entries WHERE namespace=? AND key=?",
            (namespace, key),
        ).fetchone()
        if row is None:
            return False
        exp = row["expires_at"]
        return exp is None or exp > time.time()

    def delete(self, namespace: str, key: str) -> bool:
        cur = self.conn.execute(
            "DELETE FROM cache_entries WHERE namespace=? AND key=?", (namespace, key)
        )
        return cur.rowcount > 0

    def keys(self, namespace: Optional[str] = None, tag: Optional[str] = None) -> List[str]:
        """列出有效（未过期）的 key，格式 ``namespace:key``。"""
        sql = "SELECT namespace, key FROM cache_entries WHERE (expires_at IS NULL OR expires_at > ?)"
        args: List[Any] = [time.time()]
        if namespace:
            sql += " AND namespace=?"
            args.append(namespace)
        if tag:
            sql += " AND tag=?"
            args.append(tag)
        return [f"{r['namespace']}:{r['key']}" for r in self.conn.execute(sql, args)]

    # -- 清理与淘汰 ----------------------------------------------------

    def purge_expired(self) -> int:
        """物理删除所有已过期条目，返回删除条数。"""
        cur = self.conn.execute(
            "DELETE FROM cache_entries WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (time.time(),),
        )
        n = cur.rowcount or 0
        if n:
            logger.info(f"[cache] 清理过期条目 {n} 条")
        return n

    def evict_lru(
        self,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> int:
        """
        LRU 淘汰：先按条数、再按总字节数收敛，始终从最久未访问的开始删。
        返回淘汰条数。
        """
        evicted = 0

        # 1) 条数超限
        total = self.conn.execute("SELECT COUNT(*) AS c FROM cache_entries").fetchone()["c"]
        if total > max_entries:
            need = total - max_entries
            cur = self.conn.execute(
                """
                DELETE FROM cache_entries WHERE rowid IN (
                    SELECT rowid FROM cache_entries
                    ORDER BY accessed_at ASC LIMIT ?
                )
                """,
                (need,),
            )
            evicted += cur.rowcount or 0

        # 2) 字节超限
        used = self.conn.execute(
            "SELECT COALESCE(SUM(size_bytes), 0) AS s FROM cache_entries"
        ).fetchone()["s"]
        if used > max_bytes:
            over = used - max_bytes
            rows = self.conn.execute(
                "SELECT rowid, size_bytes FROM cache_entries ORDER BY accessed_at ASC"
            ).fetchall()
            freed, victims = 0, []
            for r in rows:
                if freed >= over:
                    break
                victims.append(r["rowid"])
                freed += r["size_bytes"]
            if victims:
                placeholders = ",".join("?" * len(victims))
                cur = self.conn.execute(
                    f"DELETE FROM cache_entries WHERE rowid IN ({placeholders})", victims
                )
                evicted += cur.rowcount or 0

        if evicted:
            logger.info(f"[cache] LRU 淘汰 {evicted} 条")
        return evicted

    def clear(self, namespace: Optional[str] = None, tag: Optional[str] = None) -> int:
        """按命名空间/标签清空。两者都不传则清全库。"""
        if not namespace and not tag:
            cur = self.conn.execute("DELETE FROM cache_entries")
        elif namespace and tag:
            cur = self.conn.execute(
                "DELETE FROM cache_entries WHERE namespace=? AND tag=?", (namespace, tag)
            )
        elif namespace:
            cur = self.conn.execute(
                "DELETE FROM cache_entries WHERE namespace=?", (namespace,)
            )
        else:
            cur = self.conn.execute(
                "DELETE FROM cache_entries WHERE tag=?", (tag,)
            )
        return cur.rowcount or 0

    def vacuum(self) -> None:
        """回收空间。清空/大量删除后调用，注意会短暂锁库。"""
        try:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.conn.execute("VACUUM")
        except sqlite3.OperationalError as e:
            logger.warning(f"[cache] VACUUM 跳过: {e}")

    # -- 统计 ----------------------------------------------------------

    def stats(self, namespace: Optional[str] = None) -> Dict[str, Any]:
        """返回缓存概况：条数、字节、过期数、命中分布、最旧/最新。"""
        where, args = "", []
        if namespace:
            where, args = " WHERE namespace=?", [namespace]

        now = time.time()
        row = self.conn.execute(
            f"""
            SELECT
                COUNT(*)                                             AS entries,
                COALESCE(SUM(size_bytes), 0)                         AS bytes,
                COALESCE(SUM(CASE WHEN expires_at IS NOT NULL
                                  AND expires_at <= ? THEN 1 ELSE 0 END), 0) AS expired,
                COALESCE(SUM(hits), 0)                               AS total_hits,
                MIN(created_at)                                      AS oldest,
                MAX(created_at)                                      AS newest
            FROM cache_entries{where}
            """,
            [now] + args,
        ).fetchone()

        by_ns = [
            {"namespace": r["ns"], "entries": r["c"], "bytes": r["b"]}
            for r in self.conn.execute(
                f"SELECT namespace AS ns, COUNT(*) AS c, "
                f"COALESCE(SUM(size_bytes),0) AS b FROM cache_entries{where} "
                f"GROUP BY namespace ORDER BY c DESC",
                args,
            )
        ]

        db_bytes = 0
        for suffix in ("", "-wal", "-shm"):
            p = self.db_path + suffix
            if os.path.exists(p):
                db_bytes += os.path.getsize(p)

        return {
            "db_path": self.db_path,
            "db_bytes": db_bytes,
            "entries": row["entries"],
            "bytes": row["bytes"],
            "expired": row["expired"],
            "total_hits": row["total_hits"],
            "oldest": row["oldest"],
            "newest": row["newest"],
            "by_namespace": by_ns,
        }
