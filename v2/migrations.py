"""MathForge SQLite 幂等迁移机制（模块 C，2026-09-28）。

借鉴 Claude Code migrations/ 的一次性迁移纪律：
- 每个迁移幂等（IF NOT EXISTS 语义 / 无 DDL 副作用）、按 id 有序、只增不改
  （已发布的迁移定义与 DDL 文本永不变更，修正走新迁移）；
- 跑一遍即登记进 schema_migrations，重复执行 no-op（applied_ts 不变）；
- 单个迁移失败 → 整体抛 MigrationError（带迁移 id 与原因），该迁移的 DDL 与
  登记行在同一事务内原子回滚，绝不留「半登记」状态。

基线定案（0000_baseline）：
- v1 三表 mastery / mistakes / policy_log 在本机制上线前已在野存在，由 v1/db.py
  管理并继续由其管理——作为基线登记，不纳入本机制的 DDL（v1/db.py 永不改动）；
- v2 两表 attempt_events / patterns 原由 mem2.init_schema 用
  CREATE TABLE IF NOT EXISTS 管理，其 DDL 原样搬入迁移 0001_v2_tables；
  mem2.init_schema 现委托 ensure_migrated（对外签名与行为不变）。

对「已建好全部表的现有库」首次跑迁移：两个迁移的 apply 均为 IF NOT EXISTS 语义，
零 DDL 副作用（sqlite_master 逐字节不变），仅正常登记两条记录。

对外接口：
- MIGRATIONS：有序列表，每项 {id: str, description: str, apply: Callable[[sqlite3.Connection], None]}；
- ensure_migrated(conn) -> list[str]；
- pending(conn) -> list[str]（模块 D doctor 用）。
"""

from __future__ import annotations

import re
import sqlite3
import time
from typing import Callable

# 登记表（幂等迁移的唯一事实源）。
REGISTRY_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    id TEXT PRIMARY KEY,
    applied_ts REAL,
    description TEXT
)
"""


class MigrationError(RuntimeError):
    """迁移执行失败。异常信息含迁移 id 与底层原因；失败迁移所在事务已回滚。"""


# 迁移 id 字符集白名单：id 会被拼进 SAVEPOINT 名（f-string），必须杜绝特殊字符注入。
_MIGRATION_ID_RE = re.compile(r"^[0-9A-Za-z_-]+$")


# --------------------------------------------------------------------------- #
# 迁移定义（有序、只增不改）
# --------------------------------------------------------------------------- #
def _apply_0000_baseline(conn: sqlite3.Connection) -> None:
    """基线登记，无任何 DDL：v1 三表（mastery/mistakes/policy_log）在机制上线前
    已存在，仍由 v1/db.py 管理；本机制不重建、不改列、不建索引。"""
    return None


def _apply_0001_v2_tables(conn: sqlite3.Connection) -> None:
    """v2 两表 + 两索引（DDL 原样自 mem2.init_schema 搬家，逐字未改）。

    IF NOT EXISTS 幂等：对已建好两表的旧库重复执行为零副作用 no-op。
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS attempt_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            day TEXT,
            sim INTEGER DEFAULT 0,
            pack TEXT,
            kp TEXT,
            qtype TEXT,
            mode TEXT,
            result TEXT,
            attribution TEXT,          -- JSON: {"type":..., "conf":...}
            user_override TEXT,       -- JSON: {"type":..., "conf":...}（人类修正）
            policy_snapshot TEXT,     -- JSON
            meta TEXT                 -- JSON: {"origin_event_id":...}
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS patterns (
            id INTEGER PRIMARY KEY,
            pack TEXT NOT NULL,
            kp TEXT NOT NULL,
            pattern_md TEXT,
            source TEXT,
            confirmed INTEGER DEFAULT 0,
            created_ts REAL
        )
        """
    )
    # 索引：按 kp + ts 取最近事件
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_attempt_events_kp_ts ON attempt_events (kp, ts)"
    )
    # patterns 每张卡唯一对应 (pack, kp)，便于 upsert（v2 蒸馏器 TBD 占位）
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uniq_patterns_pack_kp ON patterns (pack, kp)"
    )


MIGRATIONS: list[dict] = [
    {
        "id": "0000_baseline",
        "description": "基线登记：v1 三表(mastery/mistakes/policy_log)在野已存在，"
                       "由 v1/db.py 管理，不纳入迁移 DDL",
        "apply": _apply_0000_baseline,
    },
    {
        "id": "0001_v2_tables",
        "description": "v2 两表 attempt_events/patterns + 两索引"
                       "（DDL 原样自 mem2.init_schema 迁入，IF NOT EXISTS 幂等）",
        "apply": _apply_0001_v2_tables,
    },
]


def _validate_definitions() -> None:
    """模块加载期校验迁移定义：id 非空、合法字符集（^[0-9A-Za-z_-]+$，防 SAVEPOINT
    名注入）且唯一、apply 可调用、description 非空（配置错误尽早炸）。"""
    seen: set[str] = set()
    for m in MIGRATIONS:
        mid = m.get("id")
        if not isinstance(mid, str) or not mid:
            raise MigrationError(f"迁移定义缺少合法 id: {m!r}")
        if not _MIGRATION_ID_RE.match(mid):
            raise MigrationError(
                f"迁移 id 含非法字符（仅限 0-9A-Za-z_-）: {mid!r}"
            )
        if not callable(m.get("apply")):
            raise MigrationError(f"迁移 {mid} 缺少可调用的 apply")
        if not isinstance(m.get("description"), str) or not m["description"]:
            raise MigrationError(f"迁移 {mid} 缺少 description")
        if mid in seen:
            raise MigrationError(f"迁移 id 重复: {mid}")
        seen.add(mid)


_validate_definitions()


# --------------------------------------------------------------------------- #
# 执行
# --------------------------------------------------------------------------- #
def ensure_migrated(conn: sqlite3.Connection) -> list[str]:
    """执行所有未应用的迁移并登记（幂等）。

    契约（定案）：
    - 返回值 = **本次调用新应用**的迁移 id 列表（按执行序）；全部已应用时返回
      []（重复调用为 no-op，applied_ts 与 description 不变）。
    - 登记表 schema_migrations(id TEXT PRIMARY KEY, applied_ts REAL, description TEXT)
      不存在则先创建（幂等，IF NOT EXISTS）。
    - 事务边界：每个迁移的 apply + 登记行在各自 SAVEPOINT 内原子提交；某迁移失败
      → 回滚该 SAVEPOINT（DDL 与登记一并撤销，不留半登记状态），并整体抛
      MigrationError（信息含迁移 id 与原因，链条保留原异常）；其前面的迁移保持
      已提交（若在调用方事务内，则随调用方事务提交/回滚——与旧 mem2.init_schema
      的事务语义一致）、后面的迁移不再执行。
    - 与调用方事务兼容：经 SAVEPOINT 实现，无论连接当前是否已有未提交事务均可
      安全执行；末尾 conn.commit() 与旧 mem2.init_schema 行为一致（无未提交事务
      时为 no-op）。
    """
    try:
        conn.execute(REGISTRY_DDL)
        done = {row[0] for row in conn.execute("SELECT id FROM schema_migrations")}
        newly: list[str] = []
        for m in MIGRATIONS:
            mid = m["id"]
            if mid in done:
                continue
            sp = f"mathforge_migration_{mid}"
            try:
                conn.execute(f'SAVEPOINT "{sp}"')
                m["apply"](conn)
                conn.execute(
                    "INSERT INTO schema_migrations (id, applied_ts, description) "
                    "VALUES (?, ?, ?)",
                    (mid, time.time(), m["description"]),
                )
                conn.execute(f'RELEASE SAVEPOINT "{sp}"')
            except Exception as exc:
                try:
                    conn.execute(f'ROLLBACK TO SAVEPOINT "{sp}"')
                    conn.execute(f'RELEASE SAVEPOINT "{sp}"')
                except sqlite3.Error:
                    pass  # 回滚失败时以原始异常为准，不吞错
                raise MigrationError(
                    f"迁移 {mid} 应用失败（已回滚，未登记）: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            done.add(mid)
            newly.append(mid)
    except MigrationError:
        raise
    except sqlite3.Error as exc:
        raise MigrationError(f"迁移机制自身失败（登记表创建/读取）: {exc}") from exc

    # 提交阶段单独捕获：失败含义完全不同（迁移已应用但未持久化，而非登记表问题），
    # 文案必须可排障，不得混入上面的「登记表创建/读取」口径。
    try:
        conn.commit()
    except sqlite3.Error as exc:
        raise MigrationError(
            f"迁移已应用但提交失败（未持久化，请检查连接/磁盘状态后重试）: {exc}"
        ) from exc
    return newly


def pending(conn: sqlite3.Connection) -> list[str]:
    """返回未应用的迁移 id（按 MIGRATIONS 顺序；模块 D doctor 用）。

    只读：不建表、不写库。登记表尚不存在（全新空库 / 迁移机制上线前的旧库）时
    视为全部未应用——这正是驱动 doctor 对旧库执行 ensure_migrated（零 DDL 副作用、
    正常登记）的依据。
    """
    try:
        rows = conn.execute("SELECT id FROM schema_migrations").fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return [m["id"] for m in MIGRATIONS]
        raise
    done = {row[0] for row in rows}
    return [m["id"] for m in MIGRATIONS if m["id"] not in done]
