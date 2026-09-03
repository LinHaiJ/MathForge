# -*- coding: utf-8 -*-
"""scripts/simulate_days.py 模拟卷模拟器单测（设计共识 §4）。

覆盖：①种子固定→输出确定性 ②默认 3 天总分落锚带 [95,105] ③薄弱 kp 错题占比显著更高
④全部事件 sim=1 且只落独立 sim 库 ⑤真实库 mathforge.db 零写入（含红线守卫）。
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import sqlite3
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "simulate_days.py")


def _load_mod():
    spec = importlib.util.spec_from_file_location("simulate_days", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sim = _load_mod()


def _cli(*args, cwd=ROOT):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run(
        [sys.executable, SCRIPT, *args],
        cwd=cwd, env=env, capture_output=True, encoding="utf-8", errors="replace",
    )
    assert p.returncode == 0, f"CLI 失败：{p.returncode}\n{p.stdout}\n{p.stderr}"
    return p.stdout


def _scores(stdout: str) -> list[int]:
    return [int(m) for m in re.findall(r"得分 (\d+)/150", stdout)]


# --------------------------------------------------------------------------- #
# ① 种子固定 → 输出确定性一致
# --------------------------------------------------------------------------- #
def test_same_seed_deterministic_output(tmp_path):
    db = str(tmp_path / "sim.db")
    args = ("--days", "3", "--seed", "7", "--start-day", "2026-01-05", "--db", db)
    out1 = _cli(*args)
    out2 = _cli(*args)
    assert out1 == out2
    assert "模拟" in out1  # §4.3 诚实口径：输出必带模拟标识


def test_different_seed_differs(tmp_path):
    db = str(tmp_path / "sim.db")
    a = _cli("--days", "2", "--seed", "1", "--start-day", "2026-01-05", "--db", db)
    b = _cli("--days", "2", "--seed", "2", "--start-day", "2026-01-05", "--db", db)
    assert a != b


def test_generate_paper_pure_and_structured():
    exam = sim.load_exam("math1")
    pool = sim.load_kp_pool(exam)
    p1 = sim.generate_paper(exam, pool, sim.DEFAULT_WEAK_KPS, "seed-x")
    p2 = sim.generate_paper(exam, pool, sim.DEFAULT_WEAK_KPS, "seed-x")
    assert p1 == p2  # 纯函数
    assert len(p1) == 22
    assert [q["qtype"] for q in p1].count("choice") == 10
    assert [q["qtype"] for q in p1].count("fill") == 6
    assert [q["qtype"] for q in p1].count("solution") == 6
    assert sum(q["points"] for q in p1) == 150
    kp_ids = {k["kp"] for k in pool}
    for q in p1:
        assert q["kp"] in kp_ids
        assert q["qtype"] in ("choice", "fill", "solution")
        assert q["difficulty"] in ("basic", "mid", "advanced")
        assert q["pack"] in ("calculus", "linear", "probability")
    assert [q["no"] for q in p1] == list(range(1, 23))


# --------------------------------------------------------------------------- #
# ② 默认 --days 3 总分落在锚带 [95,105]
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("seed", ["1", "42", "2026"])
def test_scores_hit_anchor_band(tmp_path, seed):
    out = _cli("--days", "3", "--seed", seed, "--start-day", "2026-02-01",
               "--db", str(tmp_path / f"sim{seed}.db"))
    scores = _scores(out)
    assert len(scores) == 3
    for s in scores:
        assert 95 <= s <= 105, f"seed={seed} 分数 {s} 偏离锚带：\n{out}"
    assert "落在锚带 100±5 内 3/3" in out


def test_custom_anchor(tmp_path):
    out = _cli("--days", "2", "--seed", "5", "--score", "130", "--start-day", "2026-02-01",
               "--db", str(tmp_path / "sim.db"))
    for s in _scores(out):
        assert 125 <= s <= 135, out


def test_calibration_recorded_in_meta(tmp_path):
    """校准动作落 meta.calibrated（可视察）；resample=1 时必然要靠校准把分数拉回锚带。"""
    db = str(tmp_path / "sim.db")
    buf = io.StringIO()
    res = sim.run(days=6, seed="9", start_day="2026-03-01", db=db, out=buf, resample=1)
    calibrated_days = [d for d in res["days"] if d["actions"]]
    assert calibrated_days, "6 天内应至少有一天需要校准"
    for s in res["scores"]:
        assert 95 <= s <= 105
    for d in calibrated_days:
        assert all(a["delta"] != 0 for a in d["actions"])
    conn = sqlite3.connect(db)
    metas = [json.loads(r[0]) for r in conn.execute(
        "SELECT meta FROM attempt_events WHERE mode='sim'")]
    n_cal = sum(1 for m in metas if m.get("calibrated"))
    assert n_cal == sum(len(d["actions"]) for d in res["days"])
    # 校准只动少数题（可信度要求）
    assert n_cal <= 0.2 * len(metas)
    conn.close()


def test_grade_paper_and_calibrate_pure():
    exam = sim.load_exam("math1")
    pool = sim.load_kp_pool(exam)
    paper = sim.generate_paper(exam, pool, sim.DEFAULT_WEAK_KPS, "g")
    all_right = [{"no": q["no"], "result": "correct", "earned": q["points"], "p": 1.0,
                  "calibrated": False} for q in paper]
    assert sim.grade_paper(paper, all_right) == 150
    fixed, actions = sim.calibrate_to_anchor(paper, all_right, 100, 5)
    assert actions, "满分应触发校准"
    assert abs(sim.grade_paper(paper, fixed) - 100) <= 5
    assert all(a["from"] != a["to"] for a in actions)


# --------------------------------------------------------------------------- #
# ③ 错误集中在薄弱 kp
# --------------------------------------------------------------------------- #
def test_weak_kps_dominate_mistakes(tmp_path):
    db = str(tmp_path / "sim.db")
    buf = io.StringIO()
    weak = ["calc.rolle", "calc.lagrange", "la.eigen", "prob.total_bayes"]
    res = sim.run(days=8, seed="11", start_day="2026-04-01", db=db, weak_kps=weak, out=buf)
    weak_n = weak_wrong = other_n = other_wrong = 0
    for d in res["days"]:
        st = sim.day_stats(d)
        weak_n += st["weak"][0]
        weak_wrong += st["weak"][1]
        other_n += st["nonweak"][0]
        other_wrong += st["nonweak"][1]
    assert weak_n >= 20 and other_n >= 20
    wr_weak = weak_wrong / weak_n
    wr_other = other_wrong / other_n
    assert wr_weak > wr_other * 1.5, f"薄弱错误率 {wr_weak:.2f} 未显著高于 {wr_other:.2f}"
    # 薄弱 kp 在错题里的占比 > 其在试卷里的占比（错误确实集中）
    share_in_paper = weak_n / (weak_n + other_n)
    share_in_wrong = weak_wrong / (weak_wrong + other_wrong)
    assert share_in_wrong > share_in_paper


def test_attribution_distribution_four_types(tmp_path):
    db = str(tmp_path / "sim.db")
    buf = io.StringIO()
    sim.run(days=10, seed="3", start_day="2026-05-01", db=db, out=buf)
    conn = sqlite3.connect(db)
    rows = [json.loads(r[0]) for r in conn.execute(
        "SELECT attribution FROM attempt_events WHERE attribution IS NOT NULL")]
    conn.close()
    types = [r["type"] for r in rows]
    assert set(types) <= set(sim.ATTRIBUTIONS)
    assert len(set(types)) == 4, "四类错因都应出现"
    # 默认比例 0.4/0.3/0.2/0.1 → 概念混淆最多，且不应垄断（§4.2 视察项 2）
    top = max(sim.ATTRIBUTIONS, key=lambda t: types.count(t))
    assert top == "概念混淆"
    assert types.count(top) / len(types) < 0.7
    assert all(0 < r["conf"] <= 1 for r in rows)


def test_attribution_weights_configurable(tmp_path):
    out = _cli("--days", "4", "--seed", "4", "--start-day", "2026-06-01",
               "--attribution-weights", "0,0,1,0", "--db", str(tmp_path / "sim.db"))
    assert "方法选错" in out
    assert "概念混淆" not in out.split("归因总分布")[-1]


# --------------------------------------------------------------------------- #
# ④ 全部事件 sim=1 且落在独立 db
# --------------------------------------------------------------------------- #
def test_events_all_sim_in_isolated_db(tmp_path):
    db = tmp_path / "sim_events.db"
    buf = io.StringIO()
    res = sim.run(days=3, seed="21", start_day="2026-07-01", db=str(db), out=buf)
    assert db.exists()
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM attempt_events ORDER BY id").fetchall()
    conn.close()
    assert len(rows) == sum(len(d["events"]) for d in res["days"])
    assert len(rows) >= 3 * 22
    assert {r["sim"] for r in rows} == {1}
    assert {r["mode"] for r in rows} <= {"sim", "selfassess"}
    assert {r["qtype"] for r in rows} <= {"choice", "fill", "solution"}
    assert {r["result"] for r in rows} <= {"correct", "wrong", "partial"}
    days = sorted({r["day"] for r in rows})
    assert days == ["2026-07-01", "2026-07-02", "2026-07-03"]  # 跨日历日连续推进
    ts = [r["ts"] for r in rows]
    assert ts == sorted(ts)
    for r in rows:
        m = json.loads(r["meta"])
        assert m.get("simulated") is True and m.get("label") == "模拟"
        if r["result"] == "wrong":
            assert r["attribution"], "错题必须带归因"
    # 复盘事件（当晚）指向当日错题
    review = [r for r in rows if r["mode"] == "selfassess"]
    assert review and all(r["result"] == "partial" for r in review)
    assert all(json.loads(r["meta"]).get("unresolved") for r in review)


def test_no_sim_events_leak_outside_db(tmp_path):
    """两次不同 --db 的运行互不干扰（sim 命名空间隔离）。"""
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    buf = io.StringIO()
    sim.run(days=2, seed="1", start_day="2026-08-01", db=str(a), out=buf)
    n_a = sqlite3.connect(str(a)).execute("SELECT count(*) FROM attempt_events").fetchone()[0]
    sim.run(days=2, seed="1", start_day="2026-08-01", db=str(b), out=buf)
    n_a2 = sqlite3.connect(str(a)).execute("SELECT count(*) FROM attempt_events").fetchone()[0]
    n_b = sqlite3.connect(str(b)).execute("SELECT count(*) FROM attempt_events").fetchone()[0]
    assert n_a == n_a2 == n_b


def test_builtin_backend_matches_mem2_backend(tmp_path):
    """mem2 缺失时的内建 append 降级：事件内容与 mem2 后端一致（低耦合保证）。"""
    cols = "ts, day, sim, pack, kp, qtype, mode, result, attribution, meta"
    outs = {}
    for tag, prefer in (("mem2", True), ("builtin", False)):
        db = tmp_path / f"{tag}.db"
        buf = io.StringIO()
        sim.run(days=2, seed="6", start_day="2026-12-01", db=str(db), out=buf,
                prefer_mem2=prefer)
        conn = sqlite3.connect(str(db))
        outs[tag] = conn.execute(f"SELECT {cols} FROM attempt_events ORDER BY id").fetchall()
        conn.close()
        assert f"写入后端 {tag}" in buf.getvalue()
    assert outs["mem2"] == outs["builtin"]


# --------------------------------------------------------------------------- #
# ⑤ 真实库零写入（红线）
# --------------------------------------------------------------------------- #
def test_real_db_untouched(tmp_path):
    """tmp 主库（mathforge.db）在模拟器运行前后行数不变。"""
    real = tmp_path / "mathforge.db"
    conn = sqlite3.connect(str(real))
    conn.execute("CREATE TABLE mastery (kp TEXT PRIMARY KEY, mastery REAL)")
    conn.executemany("INSERT INTO mastery VALUES (?,?)", [("calc.rolle", 0.5), ("la.eigen", 0.7)])
    conn.commit()
    before = conn.execute("SELECT count(*) FROM mastery").fetchone()[0]
    tables_before = sorted(r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"))
    conn.close()

    buf = io.StringIO()
    sim.run(days=3, seed="1", start_day="2026-09-01", db=str(tmp_path / "sim.db"), out=buf)

    conn = sqlite3.connect(str(real))
    assert conn.execute("SELECT count(*) FROM mastery").fetchone()[0] == before
    assert sorted(r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")) == tables_before
    conn.close()


def test_refuse_writing_real_db(tmp_path):
    """红线守卫：--db 指向 mathforge.db 直接拒绝。"""
    with pytest.raises(SystemExit) as e:
        sim.EventSink(str(tmp_path / "mathforge.db"))
    assert "mathforge.db" in str(e.value)
    assert not (tmp_path / "mathforge.db").exists()


def test_default_db_is_sim_db():
    assert os.path.basename(sim.load_exam("math1")["id"]) == "math1"
    src = open(SCRIPT, encoding="utf-8").read()
    assert "mathforge_sim.db" in src
    assert sim.REAL_DB_NAME == "mathforge.db"


# --------------------------------------------------------------------------- #
# 报告产物
# --------------------------------------------------------------------------- #
def test_markdown_report(tmp_path):
    rp = tmp_path / "rep.md"
    out = _cli("--days", "2", "--seed", "8", "--start-day", "2026-10-01",
               "--db", str(tmp_path / "sim.db"), "--report", str(rp))
    assert rp.exists()
    text = rp.read_text(encoding="utf-8")
    assert "模拟" in text and "得分锚" in text
    assert text.count("| 题号 | 题型 |") == 2
    assert f"报告已写出：{rp}" in out


def test_simulate_subcommand_form(tmp_path):
    """设计共识写法：simulate_days.py simulate --days N --score 100。"""
    out = _cli("simulate", "--days", "1", "--score", "100", "--seed", "1",
               "--start-day", "2026-11-01", "--db", str(tmp_path / "sim.db"))
    assert _scores(out) and 95 <= _scores(out)[0] <= 105
