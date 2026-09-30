"""MathForge /v2/doctor 自检端点（模块 D，2026-09-28）。

借鉴 Claude Code /doctor 诊断屏：配置来源、环境路径、警告一键汇总——一屏看清
「这台机器上的 MathForge 处于什么状态、哪里需要人动手」。

三条铁律（红线）：
- 零 LLM 调用、零网络、只读：任何情况下不调 llm.chat / chat_json，不写任何
  db / cache（数据库以只读 URI 模式打开，连 -journal 都不会产生）；
  MATHFORGE_DEMO=1 与无 key 环境都必须能工作。
- 绝不泄露 DEEPSEEK_API_KEY 的值（key 只报「已配置/未配置」布尔），不泄露
  学生作答内容（表只报行数，绝不 SELECT 内容列）。
- 只诊断不动手：发现未应用迁移只上报，绝不代替用户执行迁移（ensure_migrated
  的调用权在用户/调用链，不在 doctor）。

对外接口：
- run_doctor() -> dict  纯函数，不依赖 FastAPI（方便测试与脚本直调）：
    {
      ok: bool,                # 所有节 ok 且无 warnings
      generated_at: str,       # ISO 时间戳（本地时区，含 UTC 偏移，如 2026-09-28T23:13:36+08:00）
      checks: {                # 每节 {ok: bool, detail: {...}}
        env,                   # MATHFORGE_DEMO / DEEPSEEK_API_KEY(布尔) / BASE_URL
        cache,                 # 生效缓存目录（尊重 MATHFORGE_CACHE_DIR 覆写）+ 条目数
        database,              # 库路径 / 只读可开 / 五表存在性+行数（每表带 level：存在=ok、
                               #   v2 缺失=warning、v1 缺失=info）/ integrity / 迁移状态
        packs,                 # 逐包 load：kp 数 / families 数 / maturity
        llm_entry,             # llm 模块可导入 + 错误类 + _get_client 存在（不真调）
        intents,               # v2intent._ALIASES 词表条数（意图层装配证明）
      },
      warnings: [str, ...],    # 全部非致命异常的人话汇总
      summary: str,            # 一行人话（如「全部就绪（离线演示态）」/「2 项警告」）
    }
- HTTP 层在 app.py：GET /v2/doctor 恒 200，健康状态放 JSON 字段，便于监控脚本
  消费；run_doctor 意外异常由端点兜底为 {ok:false, error}，绝不 500。
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

# 关注的五张表：v1 三表（v1/db.py 管）+ v2 两表（mem2/迁移 0001 管）。
# 名单写死白名单：拼 COUNT(*) 查询无注入面；行数只数数，绝不取内容列。
# 缺表分级（见 _check_database）：v2 缺失=warning（归迁移管，缺失才是真问题）；
# v1 缺失=info（v1 端点首访时才建，纯 /v2 新库没有属正常，不计 warnings）。
V1_TABLES = ("mastery", "mistakes", "policy_log")
V2_TABLES = ("attempt_events", "patterns")

_V1_TABLE_MISSING_NOTE = "v1 端点首次访问时自动创建，不影响 /v2 链路"


# --------------------------------------------------------------------------- #
# 各体检节（每节函数：往 warn 追加人话警告，返回 {"ok": bool, "detail": {...}}；
# 模块导入放在函数体内——doctor 要在某个部件本身损坏时仍能对其余部件出报告）
# --------------------------------------------------------------------------- #
def _check_env(warn: list[str]) -> dict:
    """env：演示开关 + key 配置布尔（绝不带值）+ 自定义 BASE_URL。"""
    demo = os.environ.get("MATHFORGE_DEMO")
    demo_on = demo == "1"
    key_configured = bool(os.environ.get("DEEPSEEK_API_KEY"))  # 只取布尔，值永不进报告
    detail = {
        "MATHFORGE_DEMO": demo,
        "demo_mode": demo_on,
        # key 单独成对象并注释封口：将来若加别的 key 元信息，也不至于顺手把值塞进来
        "DEEPSEEK_API_KEY": {"configured": key_configured},
        "DEEPSEEK_BASE_URL": os.environ.get("DEEPSEEK_BASE_URL"),
    }
    ok = True
    if not demo_on and not key_configured:
        ok = False
        warn.append("env: 非 demo 模式但未配置 DEEPSEEK_API_KEY（在线出题/归因不可用，"
                    "演示请设 MATHFORGE_DEMO=1）")
    return {"ok": ok, "detail": detail}


def _check_cache(warn: list[str]) -> dict:
    """cache：当前生效缓存目录 + *.json 条目数（只数数）。

    取值逻辑复刻 llm._cache_path 的 MATHFORGE_CACHE_DIR 覆写语义（每次读环境变量、
    未设置退回模块默认），但不调用它——_cache_path 会 mkdir 建目录，doctor 只读不写。
    """
    import llm

    env_dir = os.environ.get("MATHFORGE_CACHE_DIR")
    if env_dir:
        base = Path(env_dir)
        source = "env:MATHFORGE_CACHE_DIR"
    else:
        base = Path(llm.CACHE_DIR)
        source = "default(llm.CACHE_DIR)"
    detail = {"dir": str(base), "source": source, "exists": base.is_dir(),
              "json_entries": 0}
    if detail["exists"]:
        try:
            detail["json_entries"] = sum(1 for _ in base.glob("*.json"))
        except OSError as e:
            warn.append(f"cache: 缓存目录无法读取：{e}")
            return {"ok": False, "detail": detail}
    return {"ok": True, "detail": detail}


def _table_probe(conn: sqlite3.Connection, name: str) -> dict:
    """单表探测：存在性 + 行数（白名单表名，COUNT(*) 只数数）。"""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    if not row:
        return {"exists": False, "rows": 0}
    rows = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
    return {"exists": True, "rows": rows}


def _check_database(warn: list[str]) -> dict:
    """database：默认库路径 / 只读可开 / 五表存在性与行数 / integrity / 迁移状态。

    - 路径尊重 MATHFORGE_V2_DB 覆写（与 v2api 同一环境变量，测试隔离口径一致），
      未设置时用 mem2.default_db_path()（与 v1 共用的 mathforge.db）；
    - 只读 URI 模式打开（mode=ro）：库文件存在性不够时连写句柄都拿不到，
      从机制上保证 doctor 零写库；
    - 迁移状态调 migrations.pending()（只读不建表），只上报不执行。
    """
    import mem2
    import migrations

    env_db = os.environ.get("MATHFORGE_V2_DB")
    if env_db:
        db_path = Path(env_db)
        source = "env:MATHFORGE_V2_DB"
    else:
        db_path = Path(mem2.default_db_path())
        source = "default(mem2.default_db_path，与 v1 共用)"
    detail: dict = {"path": str(db_path), "path_source": source,
                    "exists": db_path.exists()}
    ok = True
    if not detail["exists"]:
        ok = False
        warn.append(f"database: 库文件不存在：{db_path}（首次运行会被正常创建，"
                    f"或手动跑一次任意答题链路初始化）")
        return {"ok": ok, "detail": detail}

    conn = None
    try:
        # as_uri 处理 Windows 盘符/中文/空格的百分号编码；?mode=ro 只读
        uri = db_path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        detail["integrity_check"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
        detail["v1_tables"] = {t: _table_probe(conn, t) for t in V1_TABLES}
        detail["v2_tables"] = {t: _table_probe(conn, t) for t in V2_TABLES}
        try:
            pending_ids = migrations.pending(conn)  # 只读：不建登记表、不写库
            detail["pending_migrations"] = pending_ids
        except Exception as e:  # noqa: BLE001
            detail["pending_migrations"] = None
            detail["pending_error"] = f"{type(e).__name__}: {e}"
            ok = False
            warn.append(f"database: 迁移状态读取失败：{e}")
    except sqlite3.Error as e:
        ok = False
        if "database is locked" in str(e).lower():
            warn.append("database: 默认库被另一连接占用（写锁），稍后重试")
        else:
            warn.append(f"database: 库无法以只读方式打开：{e}")
        detail["open_error"] = f"{type(e).__name__}: {e}"
        return {"ok": ok, "detail": detail}
    finally:
        if conn is not None:
            conn.close()

    # 缺表分级（修复「全新库上 doctor 恒黄」，2026-09-29）：v2 两表归迁移管，缺失
    # 才是真问题 → warning（计入 warnings、拉低 ok）；v1 三表由 v1/db.py 在 v1 端点
    # 首访时才创建，纯 /v2 链路的新库没有它们属正常现象 → info 级提示（不计 warnings、
    # 不影响 ok），防「新机器冷启动」口径 doctor 恒黄误导。每表探测结果带 level 字段
    # （ok / warning / info），既有键不删、仅新增，向后兼容。
    for gen, names in (("v1", detail["v1_tables"]), ("v2", detail["v2_tables"])):
        for t, info in names.items():
            if info["exists"]:
                info["level"] = "ok"
                continue
            if gen == "v2":
                info["level"] = "warning"
                ok = False
                warn.append(f"database: v2 表 {t} 缺失（跑一次答题链路或迁移即建）")
            else:
                info["level"] = "info"
                info["note"] = _V1_TABLE_MISSING_NOTE
    if detail["integrity_check"] != "ok":
        ok = False
        warn.append(f"database: integrity_check={detail['integrity_check']}（库可能损坏，"
                    f"请从备份恢复）")
    if detail.get("pending_migrations"):
        ok = False
        warn.append(
            f"database: 有 {len(detail['pending_migrations'])} 个未应用迁移："
            f"{', '.join(detail['pending_migrations'])}"
            f"（仅上报，doctor 不执行迁移——跑一次 v2 答题链路即自动应用）"
        )
    return {"ok": ok, "detail": detail}


def _check_packs(warn: list[str]) -> dict:
    """packs：逐包 load_pack，报 kp 数 / families 模块数 / maturity；失败不抛。"""
    import pack_loader

    try:
        ids = pack_loader.list_packs()
    except Exception as e:  # noqa: BLE001
        warn.append(f"packs: 包目录扫描失败：{e}")
        return {"ok": False,
                "detail": {"packs": [], "count": 0, "error": f"{type(e).__name__}: {e}"}}
    packs = []
    for pid in ids:
        entry: dict = {"id": pid, "ok": True}
        try:
            # 运行期属性访问（非 from-import 绑定）：测试 monkeypatch 才能生效
            p = pack_loader.load_pack(pid)
            entry["kp_count"] = len(p.get("kp_ids", []))
            entry["families"] = len(p.get("families", {}))
            entry["maturity"] = p.get("maturity")
        except Exception as e:  # noqa: BLE001 —— 单包失败只降级该包，人话原因进 warnings
            entry["ok"] = False
            entry["error"] = f"{type(e).__name__}: {e}"
            warn.append(f"packs: 包 {pid} 加载失败：{e}")
        packs.append(entry)
    if not packs:
        warn.append("packs: 未发现任何科目包（packs/ 下没有含 pack.json 的目录）")
        return {"ok": False, "detail": {"packs": packs, "count": 0}}
    return {"ok": all(p["ok"] for p in packs),
            "detail": {"packs": packs, "count": len(packs)}}


def _check_llm_entry(warn: list[str]) -> dict:
    """llm_entry：模块可导入、两个错误类在、_get_client 在（getattr 探测，不真调）。"""
    import llm

    detail: dict = {"importable": True}
    ok = True
    for attr, why in (("LLMFatalError", "致命错误类（模块 A 错误分类链不完整）"),
                      ("LLMRetryableError", "可重试错误类（模块 A 错误分类链不完整）")):
        present = hasattr(llm, attr)
        detail[attr] = present
        if not present:
            ok = False
            warn.append(f"llm_entry: llm.{attr} 缺失（{why}）")
    # 仅 getattr 探测 _get_client 存在且可调用；绝不调用（调用会构造 OpenAI 客户端，
    # 与「零 LLM 调用、零网络」红线冲突）。无 key 会抛 LLMFatalError 这一行为由
    # test_llm_errors.py 的既有用例守护，doctor 不重复验证。
    has_client = callable(getattr(llm, "_get_client", None))
    detail["_get_client"] = has_client
    if not has_client:
        ok = False
        warn.append("llm_entry: llm._get_client 缺失（LLM 唯一入口异常）")
    return {"ok": ok, "detail": detail}


def _check_intents(warn: list[str]) -> dict:
    """intents：v2intent._ALIASES 词表条数（意图层已装配的最低证明）。"""
    import v2intent

    n = len(v2intent._ALIASES)
    detail = {"module": "v2intent", "aliases": n}
    if n <= 0:
        warn.append("intents: v2intent._ALIASES 为空（意图层词表未装配）")
        return {"ok": False, "detail": detail}
    return {"ok": True, "detail": detail}


# --------------------------------------------------------------------------- #
# 汇总
# --------------------------------------------------------------------------- #
def run_doctor() -> dict:
    """跑全部体检节并汇总（纯函数、只读；单节意外异常降级为该节 ok:false，不外抛）。"""
    warn: list[str] = []
    checks: dict = {}
    # 预导入 llm：真实 llm.py 在 import 期执行 load_dotenv（读 llm.py 同目录 .env）。
    # env 节排节循环第一位，若不在此先完成该装载，全新进程脚本直调 run_doctor() 的
    # 首调会把「.env 已配置 key」误报成未配置 + 假警告（对抗审查 major：首调 2 警告、
    # 二调 1 警告）。llm 本就是 cache/llm_entry 两节的依赖，预导入不改变降级语义：
    # 导入失败在此静默，交由相应节照常以自检节异常/缺失降级，doctor 整体照常出报告。
    try:
        import llm  # noqa: F401
    except Exception:  # noqa: BLE001
        pass
    for name, fn in (
        ("env", _check_env),
        ("cache", _check_cache),
        ("database", _check_database),
        ("packs", _check_packs),
        ("llm_entry", _check_llm_entry),
        ("intents", _check_intents),
    ):
        try:
            checks[name] = fn(warn)
        except Exception as e:  # noqa: BLE001 —— 任何意外都不许让 doctor 本身崩掉
            warn.append(f"{name}: 自检节异常：{type(e).__name__}: {e}")
            checks[name] = {"ok": False, "detail": {"error": f"{type(e).__name__}: {e}"}}

    ok = all(bool(c.get("ok")) for c in checks.values()) and not warn
    demo = os.environ.get("MATHFORGE_DEMO") == "1"
    summary = f"全部就绪{'（离线演示态）' if demo else ''}" if ok else f"{len(warn)} 项警告"
    return {
        "ok": ok,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "checks": checks,
        "warnings": warn,
        "summary": summary,
    }
