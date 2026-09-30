"""v2/migrations.py 幂等迁移机制单测（模块 C，2026-09-28）。

纪律：全部用临时库（tmp_path），绝不触碰仓库 mathforge.db。
运行：cd D:/腾讯冲刺/作品A/mathforge && python -m pytest tests/test_migrations.py -q

「旧库」模拟说明：迁移机制上线前的真实库形态 = v1 三表 + v2 两表（含索引）已存在、
无 schema_migrations。接线后 mem2.init_schema 会登记迁移，故旧库不能再用它模拟——
改用 db.connect（v2/db.py，v1 退役后迁入）建 v1 三表 + 直接调用迁移 0001 的 apply（不经登记）复现 v2 两表，
保证与旧 init_schema 产物逐字一致（同一份 DDL 文本）。
"""

import sqlite3
import time
import uuid

import pytest

import mem2
import migrations
from migrations import MigrationError, MIGRATIONS, ensure_migrated, pending

ALL_IDS = [m["id"] for m in MIGRATIONS]

# 旧 mem2.init_schema 的 4 条 DDL 原文（2026-09-28 迁移改造前），独立写死作对比基准。
LEGACY_V2_DDL = [
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
    """,
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
    """,
    "CREATE INDEX IF NOT EXISTS idx_attempt_events_kp_ts ON attempt_events (kp, ts)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uniq_patterns_pack_kp ON patterns (pack, kp)",
]

ATTEMPT_COLS = [
    "id", "ts", "day", "sim", "pack", "kp", "qtype", "mode", "result",
    "attribution", "user_override", "policy_snapshot", "meta",
]
PATTERN_COLS = ["id", "pack", "kp", "pattern_md", "source", "confirmed", "created_ts"]


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _db_path(tmp_path) -> str:
    return str(tmp_path / f"{uuid.uuid4().hex}.db")


def _conn(path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _tables(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _indexes(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA index_list({table})")}


def _cols(conn, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _registry_rows(conn) -> list[tuple]:
    """登记表内容（id, applied_ts, description），按 id 序稳定输出。"""
    return sorted(
        (r[0], r[1], r[2])
        for r in conn.execute("SELECT id, applied_ts, description FROM schema_migrations")
    )


def _object_snapshot(conn) -> list[tuple]:
    """全部表/索引的 (type, name, sql) 快照——零 DDL 副作用的判定依据。"""
    return sorted(
        (r[0], r[1], r[2])
        for r in conn.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        )
    )


def _legacy_conn(tmp_path) -> sqlite3.Connection:
    """构造「迁移机制上线前的旧库」：v1 三表 + v2 两表(+索引)已存在，无登记表。

    v2 DDL 通过直接执行迁移 0001 的 apply 复现（不经 ensure_migrated、不登记），
    与旧 mem2.init_schema 的产物逐字一致。
    """
    path = _db_path(tmp_path)
    conn = _conn(path)
    from db import connect as v1_connect  # v1 三表（只读参考 v2/db.py，不改动它）
    v1_connect(path).close()
    apply_v2 = next(m for m in MIGRATIONS if m["id"] == "0001_v2_tables")
    apply_v2["apply"](conn)
    conn.commit()
    return conn


# --------------------------------------------------------------------------- #
# 1) 全新空库：建表 + 登记两条
# --------------------------------------------------------------------------- #
def test_fresh_db_creates_tables_and_registers_two(tmp_path):
    conn = _conn(_db_path(tmp_path))
    applied = ensure_migrated(conn)
    assert applied == ["0000_baseline", "0001_v2_tables"]

    assert {"attempt_events", "patterns", "schema_migrations"} <= _tables(conn)
    assert "idx_attempt_events_kp_ts" in _indexes(conn, "attempt_events")
    assert "uniq_patterns_pack_kp" in _indexes(conn, "patterns")

    # 表结构与 mem2.init_schema 产物一致（抽查列 + sqlite_master sql）
    assert _cols(conn, "attempt_events") == ATTEMPT_COLS
    assert _cols(conn, "patterns") == PATTERN_COLS
    sql_ae = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='attempt_events'"
    ).fetchone()[0]
    assert sql_ae.startswith("CREATE TABLE attempt_events")  # sqlite 存储 IF NOT EXISTS 已剥离
    for col in ATTEMPT_COLS:
        assert col in sql_ae

    rows = _registry_rows(conn)
    assert [r[0] for r in rows] == ["0000_baseline", "0001_v2_tables"]
    assert all(isinstance(r[1], float) for r in rows)      # applied_ts
    assert all(r[2] for r in rows)                          # description 非空
    conn.close()


def test_product_matches_independent_legacy_ddl(tmp_path):
    """迁移产物必须与「独立维护的旧 DDL 文本」建库产物完全一致（防 schema 悄悄漂移）。

    下方 4 条 DDL 是 2026-09-28 迁移改造前 mem2.init_schema 的原文，在测试内独立
    写死作基准——即使未来有人误改迁移 0001 的 DDL 文本，此处也会红。
    """
    legacy = _conn(_db_path(tmp_path))
    for ddl in LEGACY_V2_DDL:
        legacy.execute(ddl)
    legacy.commit()

    migrated = _conn(_db_path(tmp_path))
    ensure_migrated(migrated)

    # sqlite_master.sql 保留原始排版（仅剥离 IF NOT EXISTS），比较前压缩空白；
    # 语义差异（列/类型/约束/索引定义）任何一处漂移都会导致快照不一致。
    def norm(snap):
        return [(t, n, " ".join(sql.split()) if sql else sql) for t, n, sql in snap]

    snap_legacy = [r for r in _object_snapshot(legacy) if r[1] != "schema_migrations"]
    snap_migrated = [r for r in _object_snapshot(migrated) if r[1] != "schema_migrations"]
    assert norm(snap_migrated) == norm(snap_legacy)
    legacy.close()
    migrated.close()


# --------------------------------------------------------------------------- #
# 2) 已有五表的旧库：首次迁移零 DDL 副作用、正常登记、数据原样
# --------------------------------------------------------------------------- #
def test_legacy_db_first_run_no_ddl_side_effects_and_data_intact(tmp_path):
    conn = _legacy_conn(tmp_path)

    # 预置数据：v1 mastery + v2 事件 + 模式卡各一条
    conn.execute(
        "INSERT INTO mastery (kp, mastery, correct_count, wrong_count, streak_correct, updated_at) "
        "VALUES ('kp.legacy', 0.8, 4, 1, 2, 1000.0)"
    )
    eid = mem2.append_event(conn, pack="packA", kp="kp.legacy", qtype="fill",
                            mode="fill", result="wrong", ts=1000.0)
    conn.execute(
        "INSERT INTO patterns (pack, kp, pattern_md, source, confirmed, created_ts) "
        "VALUES ('packA', 'kp.legacy', '符号漏写', 'distill', 0, 1000.0)"
    )
    conn.commit()

    before_snapshot = _object_snapshot(conn)
    n_events = conn.execute("SELECT COUNT(*) FROM attempt_events").fetchone()[0]
    n_mastery = conn.execute("SELECT COUNT(*) FROM mastery").fetchone()[0]
    assert n_events == 1 and n_mastery == 1

    applied = ensure_migrated(conn)  # 不得有 DDL 错误
    assert applied == ALL_IDS

    # 五表 + 两索引的 schema 逐字节不变（零 DDL 副作用；登记表本身不在对比范围），数据原样
    after_snapshot = _object_snapshot(conn)
    kept = lambda snap: [r for r in snap if r[1] != "schema_migrations"]  # noqa: E731
    assert kept(before_snapshot) == kept(after_snapshot)
    assert conn.execute("SELECT COUNT(*) FROM attempt_events").fetchone()[0] == 1
    assert conn.execute("SELECT mastery FROM mastery WHERE kp='kp.legacy'").fetchone()[0] == 0.8
    assert conn.execute("SELECT id FROM attempt_events").fetchone()[0] == eid
    assert conn.execute("SELECT pattern_md FROM patterns").fetchone()[0] == "符号漏写"

    # 正常登记两条
    assert [r[0] for r in _registry_rows(conn)] == ALL_IDS
    # 迁移后投影功能正常
    assert mem2.project_mastery(conn, "kp.legacy", at=2000.0) is not None
    conn.close()


# --------------------------------------------------------------------------- #
# 3) 幂等：连跑两次，第二次 no-op，applied_ts 不变
# --------------------------------------------------------------------------- #
def test_idempotent_second_call_noop(tmp_path):
    conn = _conn(_db_path(tmp_path))
    assert ensure_migrated(conn) == ALL_IDS
    rows1 = _registry_rows(conn)
    time.sleep(0.02)  # 若误重跑，applied_ts 必然变化

    assert ensure_migrated(conn) == []  # no-op
    assert _registry_rows(conn) == rows1  # applied_ts / description 均未变
    assert pending(conn) == []
    conn.close()


# --------------------------------------------------------------------------- #
# 4) 失败回滚：假迁移抛错 → 异常含 id、登记不留该 id、DDL 撤销
# --------------------------------------------------------------------------- #
def test_failure_rolls_back_and_raises_with_id(monkeypatch, tmp_path):
    conn = _conn(_db_path(tmp_path))
    ensure_migrated(conn)  # 先落两条正常迁移

    def bad_apply(c):
        c.execute("CREATE TABLE t_bad (x INTEGER)")  # 半途 DDL，必须被回滚
        c.execute("INSERT INTO t_bad VALUES (1)")
        raise RuntimeError("boom-故意失败")

    fake = {"id": "0002_bad", "description": "故意失败的迁移", "apply": bad_apply}
    monkeypatch.setattr(migrations, "MIGRATIONS", MIGRATIONS + [fake])

    with pytest.raises(MigrationError) as ei:
        ensure_migrated(conn)
    msg = str(ei.value)
    assert "0002_bad" in msg and "boom-故意失败" in msg

    # 回滚生效：坏迁移的 DDL 不存在、登记不留该 id
    assert "t_bad" not in _tables(conn)
    ids = {r[0] for r in _registry_rows(conn)}
    assert "0002_bad" not in ids
    assert ids == set(ALL_IDS)
    conn.close()


def test_failure_on_fresh_db_leaves_nothing_registered(monkeypatch, tmp_path):
    """全新库 + 首个迁移即失败：登记表存在但零登记，后续迁移不执行。"""
    conn = _conn(_db_path(tmp_path))

    def bad_apply(c):
        raise ValueError("首迁移即炸")

    fake = {"id": "9999_bad", "description": "坏迁移", "apply": bad_apply}
    monkeypatch.setattr(migrations, "MIGRATIONS", [fake] + list(MIGRATIONS))

    with pytest.raises(MigrationError) as ei:
        ensure_migrated(conn)
    assert "9999_bad" in str(ei.value)

    assert "schema_migrations" in _tables(conn)  # 登记表本身建了（幂等基础设施）
    assert _registry_rows(conn) == []            # 但零登记（无半登记状态）
    assert "attempt_events" not in _tables(conn)  # 后续迁移未执行
    conn.close()


# --------------------------------------------------------------------------- #
# 5) pending()：新库返回全部；迁移后返回空
# --------------------------------------------------------------------------- #
def test_pending_fresh_vs_migrated(tmp_path):
    # 全新空库（登记表尚不存在）→ 全部未应用，且 pending 只读、不建表
    conn = _conn(_db_path(tmp_path))
    assert pending(conn) == ALL_IDS
    assert "schema_migrations" not in _tables(conn)

    ensure_migrated(conn)
    assert pending(conn) == []
    conn.close()

    # 迁移机制上线前的旧库（五表、无登记）→ 全部未应用（正是 doctor 触发
    # ensure_migrated 做零副作用登记的依据）；登记后即转为空。
    legacy = _legacy_conn(tmp_path)
    assert pending(legacy) == ALL_IDS
    ensure_migrated(legacy)
    assert pending(legacy) == []
    legacy.close()


# --------------------------------------------------------------------------- #
# 6) 与 mem2.connect() 集成
# --------------------------------------------------------------------------- #
def test_mem2_connect_integration(tmp_path):
    conn = mem2.connect(_db_path(tmp_path))
    assert conn.row_factory is sqlite3.Row
    eid = mem2.append_event(conn, pack="packB", kp="kp.x", qtype="fill",
                            mode="fill", result="correct", ts=1000.0)
    assert eid == 1
    m = mem2.project_mastery(conn, "kp.x", at=1000.0)
    assert m is not None and m["n"] == 1
    # connect 链路已顺带完成迁移登记
    assert [r[0] for r in _registry_rows(conn)] == ALL_IDS
    conn.close()


# --------------------------------------------------------------------------- #
# 补充：迁移定义形状 / 兼容性边界
# --------------------------------------------------------------------------- #
def test_migrations_definitions_contract():
    """MIGRATIONS 有序且 id 唯一、每项带 description 与可调用 apply（doctor 依赖的稳定形状）。"""
    ids = [m["id"] for m in MIGRATIONS]
    assert ids == sorted(ids)                       # 有序
    assert len(ids) == len(set(ids))                # 唯一
    for m in MIGRATIONS:
        assert set(m) == {"id", "description", "apply"}
        assert isinstance(m["description"], str) and m["description"]
        assert callable(m["apply"])


def test_ensure_migrated_on_conn_without_row_factory(tmp_path):
    """调用方未设 row_factory（默认 tuple）也应正常工作。"""
    conn = sqlite3.connect(_db_path(tmp_path))
    assert ensure_migrated(conn) == ALL_IDS
    assert ensure_migrated(conn) == []
    conn.close()


def test_ensure_migrated_with_caller_pending_transaction(tmp_path):
    """连接已有未提交事务时仍可安全迁移（SAVEPOINT 嵌套，不炸「事务内」错）。"""
    conn = _conn(_db_path(tmp_path))
    conn.execute("CREATE TABLE caller_t (x TEXT)")
    conn.execute("INSERT INTO caller_t VALUES ('pending')")  # legacy 模式隐式开启事务
    assert ensure_migrated(conn) == ALL_IDS  # 旧 init_schema 本就以此方式收尾 commit
    assert conn.execute("SELECT x FROM caller_t").fetchone()[0] == "pending"
    assert [r[0] for r in _registry_rows(conn)] == ALL_IDS
    conn.close()


# --------------------------------------------------------------------------- #
# 补充：审查裁定 5 条 minor 的场景测试（2026-09-28 第二轮）
# --------------------------------------------------------------------------- #
def test_migration_id_charset_rejected_at_load(monkeypatch):
    """迁移 id 必须匹配 ^[0-9A-Za-z_-]+$（id 会拼进 SAVEPOINT 名，防注入特殊字符）。"""
    monkeypatch.setattr(
        migrations, "MIGRATIONS",
        [{"id": "0003_bad; DROP TABLE x", "description": "非法 id", "apply": lambda c: None}],
    )
    with pytest.raises(MigrationError) as ei:
        migrations._validate_definitions()
    assert "非法字符" in str(ei.value)


def test_failure_with_caller_pending_dml_preserves_caller_transaction(monkeypatch, tmp_path):
    """(a) 调用方持有未提交 DML 时迁移失败：调用方 DML 原样保留、事务仍开、可自行回滚。"""
    conn = _conn(_db_path(tmp_path))
    conn.execute("CREATE TABLE caller_t (x TEXT)")
    conn.execute("INSERT INTO caller_t VALUES ('keep-me')")  # 未提交 DML
    assert conn.in_transaction

    def bad_apply(c):
        c.execute("CREATE TABLE t_bad (x)")
        raise RuntimeError("迁移中途炸")

    fake = {"id": "0002_bad", "description": "坏迁移", "apply": bad_apply}
    monkeypatch.setattr(migrations, "MIGRATIONS", MIGRATIONS + [fake])
    with pytest.raises(MigrationError):
        ensure_migrated(conn)  # 失败路径绝不能动调用方事务（不提交、不炸毁）

    assert conn.in_transaction                                   # 事务仍开
    assert conn.execute("SELECT x FROM caller_t").fetchone()[0] == "keep-me"  # DML 原样
    assert "t_bad" not in _tables(conn)                          # 坏迁移 DDL 已回滚
    assert {r[0] for r in _registry_rows(conn)} == set(ALL_IDS)  # 好迁移事务内可见

    # 调用方自行回滚：一切归零（证明失败未劫持/提交其事务）
    conn.rollback()
    assert not conn.in_transaction
    assert "t_bad" not in _tables(conn)
    # 归零后重新迁移：干净成功
    monkeypatch.setattr(migrations, "MIGRATIONS", list(MIGRATIONS))
    assert ensure_migrated(conn) == ALL_IDS
    conn.close()


def test_registry_insert_failure_rolls_back_completely(tmp_path):
    """(b) 登记行插入失败（触发器 RAISE(ABORT) 模拟）→ 回滚完整、零半登记。"""
    conn = _conn(_db_path(tmp_path))
    # 触发器建表时机要求：登记表须先存在，故先手工建（与 ensure_migrated 幂等前奏一致）
    conn.execute(migrations.REGISTRY_DDL)
    conn.execute(
        "CREATE TRIGGER trg_block_registry BEFORE INSERT ON schema_migrations "
        "BEGIN SELECT RAISE(ABORT, 'registry blocked'); END"
    )

    with pytest.raises(MigrationError) as ei:
        ensure_migrated(conn)
    msg = str(ei.value)
    assert "0000_baseline" in msg and "registry blocked" in msg

    # 零半登记：登记行 0 条；后续迁移未执行
    assert _registry_rows(conn) == []
    assert "attempt_events" not in _tables(conn)

    # 解除封锁后重跑：正常登记
    conn.execute("DROP TRIGGER trg_block_registry")
    assert ensure_migrated(conn) == ALL_IDS
    conn.close()


def test_half_built_db_self_heals(tmp_path):
    """(c) 半旧库（有 attempt_events 缺 patterns）→ ensure_migrated 自愈补齐、数据完好。"""
    conn = _conn(_db_path(tmp_path))
    conn.execute(LEGACY_V2_DDL[0])  # 只建 attempt_events（模拟旧版半途崩溃残库）
    conn.execute(
        "INSERT INTO attempt_events (ts, day, sim, pack, kp, qtype, mode, result) "
        "VALUES (1000.0, '1970-01-01', 0, 'packA', 'kp.half', 'fill', 'fill', 'wrong')"
    )
    conn.commit()
    assert "patterns" not in _tables(conn)

    applied = ensure_migrated(conn)
    assert applied == ALL_IDS
    # 补齐缺表与两索引，已有表未被重建
    assert "patterns" in _tables(conn) and "schema_migrations" in _tables(conn)
    assert _cols(conn, "attempt_events") == ATTEMPT_COLS
    assert "idx_attempt_events_kp_ts" in _indexes(conn, "attempt_events")
    assert "uniq_patterns_pack_kp" in _indexes(conn, "patterns")
    # 原数据完好，且迁移后链路可用（预置 wrong + 新追加 correct → 滑窗 n=2）
    assert conn.execute("SELECT kp FROM attempt_events").fetchone()[0] == "kp.half"
    mem2.append_event(conn, pack="packA", kp="kp.half", qtype="fill",
                      mode="fill", result="correct", ts=2000.0)
    m = mem2.project_mastery(conn, "kp.half", at=2000.0)
    assert m["n"] == 2 and 0.0 < m["value"] < 0.7  # 首答封顶只作用于 n=1
    conn.close()
