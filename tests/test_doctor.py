"""v2/doctor.py 自检端点测试（模块 D，2026-09-28）。

纪律：
- 全部走临时库（MATHFORGE_V2_DB → tmp_path），真实 mathforge.db / cache/ 零写入；
- 密封环境由根 conftest 的 _sealed_test_env 提供（MATHFORGE_DEMO=1、无 key、
  MATHFORGE_CACHE_DIR → per-test 临时目录），doctor 的「无 key / demo 正常」在此口径下验证；
- 红线验证：响应全文（含嵌套）绝不出现 DEEPSEEK_API_KEY 的值；doctor 绝不代跑迁移。

运行：cd D:/腾讯冲刺/作品A/mathforge && python -m pytest tests/test_doctor.py -q
"""

import json
import os
import sqlite3
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

ALL_MIGRATION_IDS = ["0000_baseline", "0001_v2_tables"]


def _healthy_tmp_db(path) -> None:
    """造一个「全绿」临时库：v1 三表（db.connect）+ v2 两表 + 迁移登记（mem2.connect）。"""
    import db as v1db  # v2/db.py（v1 退役后 db.py 迁入 v2/；conftest 已把 v2/ 挂上 sys.path）
    import mem2

    v1db.connect(str(path)).close()
    mem2.connect(str(path)).close()


def _empty_tmp_db(path) -> None:
    """造一个 0 字节空库文件：能以只读打开，但一张表都没有（模拟迁移机制上线前的旧库雏形）。"""
    conn = sqlite3.connect(str(path))
    conn.close()


@pytest.fixture()
def tmp_db_env(monkeypatch, tmp_path):
    """把 MATHFORGE_V2_DB 指向临时库并返回路径（测完自动还原，不污染其他测试）。"""
    db_path = tmp_path / "doctor_tmp.db"
    monkeypatch.setenv("MATHFORGE_V2_DB", str(db_path))
    return db_path


# --------------------------------------------------------------------------- #
# run_doctor：结构与全绿路径
# --------------------------------------------------------------------------- #
def test_run_doctor_structure_all_green(tmp_db_env, monkeypatch):
    _healthy_tmp_db(tmp_db_env)
    import doctor
    # 先行导入 llm：让 load_dotenv 的 import 期 .env 装载先于 delenv 发生。
    # （对抗审查 major 修复后 run_doctor 会预导入 llm——若本进程的首次真实导入
    # 恰发生在 run_doctor 内，真实 .env 的 key 会在密封删除之后被重新灌进
    # os.environ，env 节报 configured:true。那是修复后的正确行为，却让「无 key」
    # 断言非确定；此处先行导入 + 显式删除，钉死无 key 前提，与密封语义一致。）
    import llm  # noqa: F401
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    report = doctor.run_doctor()

    assert set(report) == {"ok", "generated_at", "checks", "warnings", "summary"}
    assert isinstance(report["generated_at"], str) and report["generated_at"]
    assert set(report["checks"]) == {"env", "cache", "database", "packs",
                                     "llm_entry", "intents"}
    for name, section in report["checks"].items():
        assert isinstance(section["ok"], bool), f"节 {name} 缺 ok 布尔"
        assert isinstance(section["detail"], dict), f"节 {name} detail 非字典"

    # 密封环境：demo=1、无 key（conftest 清除）→ env 节绿
    env = report["checks"]["env"]["detail"]
    assert env["demo_mode"] is True
    assert env["DEEPSEEK_API_KEY"] == {"configured": False}  # 只有布尔，绝无值

    # cache：尊重 MATHFORGE_CACHE_DIR 覆写（conftest 已指向临时目录）
    cache = report["checks"]["cache"]["detail"]
    assert cache["source"] == "env:MATHFORGE_CACHE_DIR"
    assert cache["exists"] is False and cache["json_entries"] == 0

    # database：健康临时库 → 五表齐全、迁移全部已应用
    dbd = report["checks"]["database"]
    assert dbd["ok"] is True
    assert dbd["detail"]["path_source"] == "env:MATHFORGE_V2_DB"
    assert dbd["detail"]["integrity_check"] == "ok"
    for t in ("mastery", "mistakes", "policy_log", "attempt_events", "patterns"):
        info = (dbd["detail"]["v1_tables"].get(t)
                or dbd["detail"]["v2_tables"].get(t))
        assert info == {"exists": True, "rows": 0, "level": "ok"}, \
            f"表 {t} 探测异常：{info}"
    assert dbd["detail"]["pending_migrations"] == []

    # packs：三包全加载
    packs = report["checks"]["packs"]
    assert packs["ok"] is True
    assert packs["detail"]["count"] == 3
    for p in packs["detail"]["packs"]:
        assert p["ok"] is True and p["kp_count"] > 0
        assert "families" in p and "maturity" in p

    # llm_entry / intents
    assert report["checks"]["llm_entry"]["detail"] == {
        "importable": True, "LLMFatalError": True,
        "LLMRetryableError": True, "_get_client": True,
    }
    assert report["checks"]["intents"]["detail"]["aliases"] > 0

    # 全绿汇总
    assert report["ok"] is True
    assert report["warnings"] == []
    assert report["summary"].startswith("全部就绪")
    assert "离线演示态" in report["summary"]


# --------------------------------------------------------------------------- #
# database 节：空库 / 缺文件 / 只报不执行迁移
# --------------------------------------------------------------------------- #
def test_run_doctor_empty_db_missing_tables_are_warnings_not_crash(tmp_db_env):
    """空库（0 表）：v2 两表缺失 = 真问题（归迁移管）→ warning；v1 三表缺失 = info，
    不进 warnings（全新库冷启动口径，见 test_run_doctor_fresh_v2_only_db）。"""
    _empty_tmp_db(tmp_db_env)
    import doctor

    report = doctor.run_doctor()  # 不抛即通过第一关

    dbd = report["checks"]["database"]
    assert dbd["ok"] is False
    for t in ("attempt_events", "patterns"):
        assert any(f"表 {t} 缺失" in w for w in report["warnings"])
    for t in ("mastery", "mistakes", "policy_log"):
        assert not any(f"表 {t} 缺失" in w for w in report["warnings"])
    # pending() 透出：登记表不存在 → 全部迁移视为未应用
    assert dbd["detail"]["pending_migrations"] == ALL_MIGRATION_IDS
    assert any("未应用迁移" in w for w in report["warnings"])
    assert report["ok"] is False
    assert report["summary"] == f"{len(report['warnings'])} 项警告"


def test_run_doctor_fresh_v2_only_db_v1_missing_is_info_not_warning(tmp_db_env):
    """修复「全新库上 doctor 恒黄」：纯 /v2 链路的新库（迁移自动登记，只有 v2 两表，
    v1 三表要等 v1 端点首访才由 v1/db.py 建）——v1 缺表降级为 info 级提示（detail 注明
    原因），不计 warnings、不影响 ok；v2 表缺失依旧 warning。"""
    import mem2
    import doctor

    mem2.connect(str(tmp_db_env)).close()  # 只建 v2 表 + 迁移登记，不建 v1 三表

    report = doctor.run_doctor()

    dbd = report["checks"]["database"]
    assert dbd["ok"] is True
    for t in ("mastery", "mistakes", "policy_log"):
        info = dbd["detail"]["v1_tables"][t]
        assert info["exists"] is False
        assert info["level"] == "info"
        assert "v1 端点首次访问时自动创建" in info["note"]
        assert "不影响 /v2 链路" in info["note"]
        assert not any(f"表 {t} 缺失" in w for w in report["warnings"])
    for t in ("attempt_events", "patterns"):
        assert dbd["detail"]["v2_tables"][t] == {"exists": True, "rows": 0, "level": "ok"}
    assert dbd["detail"]["pending_migrations"] == []
    assert report["warnings"] == []
    assert report["ok"] is True
    assert report["summary"].startswith("全部就绪")


def test_run_doctor_v2_table_missing_still_warning(tmp_db_env):
    """v2 两表归迁移管：缺失才是真问题 → 仍 warning（level=warning、计入 ok）。"""
    import db as v1db  # v2/db.py（v1 退役后 db.py 迁入 v2/；conftest 已把 v2/ 挂上 sys.path）
    import doctor

    v1db.connect(str(tmp_db_env)).close()  # 只建 v1 三表，不建 v2 表

    report = doctor.run_doctor()

    dbd = report["checks"]["database"]
    assert dbd["ok"] is False
    for t in ("attempt_events", "patterns"):
        info = dbd["detail"]["v2_tables"][t]
        assert info["exists"] is False
        assert info["level"] == "warning"
        assert "note" not in info
        assert any(f"v2 表 {t} 缺失" in w for w in report["warnings"])
    for t in ("mastery", "mistakes", "policy_log"):
        assert dbd["detail"]["v1_tables"][t]["level"] == "ok"
    assert report["ok"] is False


def test_run_doctor_missing_db_file_no_crash(monkeypatch, tmp_path):
    monkeypatch.setenv("MATHFORGE_V2_DB", str(tmp_path / "no_such.db"))
    import doctor

    report = doctor.run_doctor()

    assert report["checks"]["database"]["ok"] is False
    assert any("库文件不存在" in w for w in report["warnings"])
    assert report["ok"] is False


def test_run_doctor_reports_pending_without_executing_migrations(tmp_db_env):
    """红线：doctor 只报迁移状态，绝不代跑——空库体检后仍无 schema_migrations。"""
    _empty_tmp_db(tmp_db_env)
    import doctor

    report = doctor.run_doctor()
    assert report["checks"]["database"]["detail"]["pending_migrations"] == ALL_MIGRATION_IDS

    conn = sqlite3.connect(str(tmp_db_env))
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert "schema_migrations" not in names  # 体检行为零写库


def test_run_doctor_locked_db_reports_human_message(tmp_db_env):
    """「database is locked」单列人话：默认库被另一连接占用（写锁），稍后重试。"""
    _healthy_tmp_db(tmp_db_env)
    import doctor

    holder = sqlite3.connect(str(tmp_db_env))
    try:
        holder.execute("BEGIN EXCLUSIVE")  # 另一连接持有写锁，只读探测必被拒
        report = doctor.run_doctor()
    finally:
        holder.rollback()
        holder.close()

    assert report["checks"]["database"]["ok"] is False
    assert any("默认库被另一连接占用" in w for w in report["warnings"])


# --------------------------------------------------------------------------- #
# packs 节：加载失败注入
# --------------------------------------------------------------------------- #
def test_run_doctor_pack_load_failure_degrades_gracefully(tmp_db_env, monkeypatch):
    _healthy_tmp_db(tmp_db_env)
    import pack_loader
    import doctor

    def _boom(pack_id):
        raise FileNotFoundError(f"pack not found: {pack_id}")

    monkeypatch.setattr(pack_loader, "load_pack", _boom)
    report = doctor.run_doctor()  # 整体不崩

    packs = report["checks"]["packs"]
    assert packs["ok"] is False
    assert packs["detail"]["count"] == 3
    assert all(p["ok"] is False and "error" in p for p in packs["detail"]["packs"])
    assert any(w.startswith("packs: 包 calculus 加载失败") for w in report["warnings"])
    assert report["warnings"] and report["ok"] is False


# --------------------------------------------------------------------------- #
# 泄漏防护：key 值绝不进响应
# --------------------------------------------------------------------------- #
def test_run_doctor_never_leaks_api_key_value(tmp_db_env, monkeypatch):
    _healthy_tmp_db(tmp_db_env)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test123")
    import doctor

    report = doctor.run_doctor()

    text = json.dumps(report, ensure_ascii=False, default=str)
    assert "sk-test123" not in text
    assert "sk-test123" not in str(report)
    # key 只以布尔口径出现
    assert report["checks"]["env"]["detail"]["DEEPSEEK_API_KEY"] == {"configured": True}


# --------------------------------------------------------------------------- #
# 回归（对抗审查 major）：全新进程首调 run_doctor，env 节不得假阴性
# --------------------------------------------------------------------------- #
def test_run_doctor_fresh_process_env_not_false_negative(tmp_path):
    """原 bug：load_dotenv 在 llm.py import 期执行（读 llm.py 同目录 .env），修复前
    env 节先于该 import 跑 → .env 已配置 key 的全新进程首调报 configured:false +
    假警告（实测首调 2 警告、二调 1 警告）。修复：run_doctor 节循环前预导入 llm。

    子进程模拟：临时目录放「伪装 llm.py」，模块级等价复刻真实 llm.py 的
    「import 期读同目录 .env 写入 os.environ」副作用（真实 load_dotenv 固定读
    llm.py 同目录，测试进程内已 import 过 llm 无法复现排序差分，故走子进程）。
    子进程删净 DEEPSEEK_API_KEY 后，该 key 的唯一来源 = import llm 的 .env 装载——
    首调 configured:true 当且仅当预导入修复在位（修复被移除即测试红）。
    """
    stub_dir = tmp_path / "llm_stub"
    stub_dir.mkdir()
    (stub_dir / "llm.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "_HERE = Path(__file__).resolve().parent\n"
        "for _line in (_HERE / '.env').read_text(encoding='utf-8').splitlines():\n"
        "    _line = _line.strip()\n"
        "    if _line and not _line.startswith('#') and '=' in _line:\n"
        "        _k, _, _v = _line.partition('=')\n"
        "        os.environ.setdefault(_k.strip(), _v.strip())\n",
        encoding="utf-8",
    )
    (stub_dir / ".env").write_text("DEEPSEEK_API_KEY=sk-dotenv-regression\n",
                                   encoding="utf-8")

    child = (
        "import sys, json\n"
        f"sys.path[0:0] = [{str(stub_dir)!r}, {ROOT!r}, {os.path.join(ROOT, 'v2')!r}]\n"
        "import doctor\n"
        "r1 = doctor.run_doctor()\n"
        "r2 = doctor.run_doctor()\n"
        "print('RESULT>' + json.dumps([r1, r2], ensure_ascii=False, default=str))\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "DEEPSEEK_API_KEY"}
    env["MATHFORGE_DEMO"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run([sys.executable, "-c", child], capture_output=True,
                          text=True, encoding="utf-8", env=env, cwd=ROOT, timeout=120)
    assert proc.returncode == 0, f"子进程失败：{proc.stderr[-2000:]}"

    line = next(l for l in proc.stdout.splitlines() if l.startswith("RESULT>"))
    r1, r2 = json.loads(line[len("RESULT>"):])

    for r in (r1, r2):  # 首调、二调一致：load_dotenv 已先于任何节完成
        sec = r["checks"]["env"]
        assert sec["detail"]["DEEPSEEK_API_KEY"] == {"configured": True}
        assert sec["ok"] is True
        assert not any("未配置 DEEPSEEK_API_KEY" in w for w in r["warnings"])
    # 泄漏防护顺带断言：.env 注入的测试值绝不进报告全文
    assert "sk-dotenv-regression" not in line


# --------------------------------------------------------------------------- #
# 端点级：GET /v2/doctor
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client(tmp_db_env):
    _healthy_tmp_db(tmp_db_env)
    from fastapi.testclient import TestClient

    import app as app_mod

    with TestClient(app_mod.app) as c:
        yield c


def test_endpoint_doctor_200_with_top_level_fields(client, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test123")
    r = client.get("/v2/doctor")

    assert r.status_code == 200
    data = r.json()
    for key in ("ok", "generated_at", "checks", "warnings", "summary"):
        assert key in data, f"顶层缺字段 {key}"
    assert set(data["checks"]) == {"env", "cache", "database", "packs",
                                   "llm_entry", "intents"}
    assert data["checks"]["env"]["detail"]["DEEPSEEK_API_KEY"] == {"configured": True}
    assert "sk-test123" not in r.text  # 泄漏防护：HTTP 响应全文


def test_endpoint_doctor_exception_falls_back_to_200_ok_false(client, monkeypatch):
    import doctor

    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(doctor, "run_doctor", _boom)
    r = client.get("/v2/doctor")

    assert r.status_code == 200  # 诊断端点绝不 500
    data = r.json()
    assert data["ok"] is False
    assert "boom" in data.get("error", "")
