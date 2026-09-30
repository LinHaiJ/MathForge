"""模块 G（薄弱模式卡自动蒸馏器）测试。

覆盖：
- 阈值定案：5 事件/2 错 → 不蒸馏；6 事件 2 错 → 蒸馏（payload 含归因统计与摘录、
  namespace=distill:、卡写入 confirmed=False）；6 事件 1 错 → 不蒸馏
- 节流定案：蒸完后 +2 事件 → 不再蒸；+3 事件 → 再蒸
- demo 红线：MATHFORGE_DEMO=1 / 无 DEEPSEEK_API_KEY → 整体跳过（炸弹假件零调用）
- LLM 失败/坏输出 → distilled False 不抛、卡不写
- payload 口径：旧事件无 meta.q/ans 容错；占位串「未归因」同 None 不计；
  override 归因优先进 payload；窗口/错题口径与 recall_for_generation 一致
- sim 隔离口径：只认传入 conn（另一库满窗错题不影响本库判断）
- 对抗审查回归：rule 卡不重置节流时钟（单测 + 端点级 5 题→学情页→第 6 题触发）；
  并发双作答 per-(pack,kp) 在途去重（in_progress + finally 清理）；
  /v2/daily 查卡 pack 与 recall 同口径（事件 pack 列优先）；
  X-MF-Profile 档案库覆盖（卡落 profiles/<id>.db，默认库零写入）
- 模块 F 联动（只验证不改）：蒸馏卡自动进入出题记忆块
- 端点集成：/v2/answer、/v2/selfassess 落库后 SQL 预判 + BackgroundTasks 触发
  （TestClient 下随响应同步完成）；/v2/daily 透出 patterns 字段（有卡才出现）；
  demo 端点零蒸馏；/v2/patterns rule 物化不降级 auto 卡

隔离纪律（与 test_memory_inject 同口径）：
- mem2/distill 全走 tmp_path 临时库，distill 只认传入 conn；
- 假 LLM 直接注入（绝不触网）；demo 态传炸弹假件验证零 LLM；
- HTTP 用 MATHFORGE_V2_DB/MATHFORGE_PROFILES_DIR 指向临时目录，
  真实 mathforge.db / profiles/ 全程不写。
"""

import json
import os
import sqlite3
import sys
import threading
import time
import uuid
from datetime import date

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import distill  # noqa: E402
import mem2  # noqa: E402

PACK = "calculus"
KP = "calc.rolle"                # 真实包内 kp（端点集成用；kp_meta 可解析 pack_id）
CARD_MD = "该生在罗尔定理应用中常忽略端点函数值相等的验证，建议专项练习构造辅助函数。"


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _online_env(monkeypatch):
    """本模块默认在线态：conftest 全局密封为 MATHFORGE_DEMO=1 / 无 key（防出网），
    蒸馏主链用例需绕过 demo 门才能测阈值/节流/payload——这里统一翻到在线态。
    demo 专项用例在测试体内显式覆盖回 demo/无 key；假 LLM 全程注入，绝不触网。"""
    monkeypatch.setenv("MATHFORGE_DEMO", "0")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-not-real")


def _conn(tmp_path):
    p = tmp_path / f"{uuid.uuid4().hex}.db"
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    mem2.init_schema(conn)
    return conn


def _ev(conn, kp=KP, result="wrong", mode="answer", ts=1000.0, pack=PACK,
        attribution=None, user_override=None, meta=None):
    return mem2.append_event(
        conn, pack=pack, kp=kp, qtype="fill", mode=mode, result=result,
        attribution=attribution, user_override=user_override, meta=meta, ts=ts)


def _fake_llm(pattern_md=CARD_MD):
    """chat_json 兼容假件：记录 (messages, kw) 后返回固定 JSON 契约。"""
    calls = []

    def fake(messages, **kw):
        calls.append({"messages": messages, "kw": kw})
        return {"pattern_md": pattern_md}

    fake.calls = calls
    return fake


def _payload_of(user_content: str) -> dict:
    """从 user 消息里取回蒸馏 payload JSON（payload 为其中唯一的 {...} 主体）。"""
    start = user_content.index("{")
    end = user_content.rindex("}") + 1
    return json.loads(user_content[start:end])


def _seed_six(conn, wrong=6, with_meta=True):
    """6 条窗口事件（默认全错、带归因与摘录 meta）。"""
    ids = []
    for i in range(6):
        res = "wrong" if i >= 6 - wrong else "correct"
        ids.append(_ev(
            conn, result=res, ts=1000.0 + i,
            attribution=({"type": "计算失误", "conf": 0.9} if res == "wrong" else None),
            meta=({"q": f"错题题面{i}", "ans": f"错答{i}"} if res == "wrong" and with_meta else None),
        ))
    return ids


# --------------------------------------------------------------------------- #
# 阈值定案
# --------------------------------------------------------------------------- #
def test_threshold_5ev_2wrong_no_distill(tmp_path):
    conn = _conn(tmp_path)
    _seed_six(conn, wrong=2)
    conn.execute("DELETE FROM attempt_events WHERE ts=1000.0")  # 5 事件（去掉最早一条）
    conn.commit()
    fake = _fake_llm()
    r = distill.maybe_distill(conn, PACK, KP, llm=fake)
    assert r["distilled"] is False and r["reason"] == "threshold"
    assert fake.calls == []
    assert mem2.patterns_get(conn, PACK, KP) is None
    conn.close()


def test_six_events_two_wrong_distills(tmp_path):
    conn = _conn(tmp_path)
    _seed_six(conn, wrong=2)
    fake = _fake_llm()
    r = distill.maybe_distill(conn, PACK, KP, llm=fake)
    assert r["distilled"] is True
    assert r["source"] == "auto_distill" and r["confirmed"] is False
    assert r["pattern_md"] == CARD_MD
    # LLM 调用契约：namespace + temperature + payload 含归因统计与错题摘录
    assert len(fake.calls) == 1
    kw = fake.calls[0]["kw"]
    assert kw.get("namespace") == "distill:" and kw.get("temperature") == 0.2
    body = _payload_of(fake.calls[0]["messages"][-1]["content"])
    assert body["window_n"] == 6 and body["wrong_n"] == 2
    assert body["attribution_counts"] == {"计算失误": 2}
    # 摘录新→旧（ts 大的在前）；q/ans 来自 F1 落库键
    assert [w["q"] for w in body["wrong_excerpts"]] == ["错题题面5", "错题题面4"]
    assert body["wrong_excerpts"][0]["ans"] == "错答5"
    assert body["result_seq"] == ["correct"] * 4 + ["wrong", "wrong"]
    # 卡写入（upsert 后读取确认）
    card = mem2.patterns_get(conn, PACK, KP)
    assert card and card["source"] == "auto_distill" and card["confirmed"] is False
    assert card["pattern_md"] == CARD_MD
    conn.close()


def test_six_events_one_wrong_no_distill(tmp_path):
    conn = _conn(tmp_path)
    _seed_six(conn, wrong=1)
    fake = _fake_llm()
    r = distill.maybe_distill(conn, PACK, KP, llm=fake)
    assert r["distilled"] is False and r["reason"] == "threshold"
    assert fake.calls == []
    assert mem2.patterns_get(conn, PACK, KP) is None
    conn.close()


# --------------------------------------------------------------------------- #
# 节流定案：卡创建后新增有效事件 ≥3 才重蒸
# --------------------------------------------------------------------------- #
def test_throttle_two_new_events_skip_three_redistill(tmp_path):
    conn = _conn(tmp_path)
    _seed_six(conn, wrong=6)
    fake = _fake_llm("卡v1")
    assert distill.maybe_distill(conn, PACK, KP, llm=fake)["distilled"] is True
    created = mem2.patterns_get(conn, PACK, KP)["created_ts"]

    # +2 事件 → 节流：不再蒸（卡仍是 v1，假件零调用）
    for i in range(2):
        _ev(conn, ts=created + 1 + i, attribution={"type": "计算失误", "conf": 0.9})
    fake2 = _fake_llm("卡v2")
    r = distill.maybe_distill(conn, PACK, KP, llm=fake2)
    assert r["distilled"] is False and r["reason"] == "throttled"
    assert fake2.calls == []
    assert mem2.patterns_get(conn, PACK, KP)["pattern_md"] == "卡v1"

    # 第 3 条新事件 → 卡过期 → 再蒸（upsert 覆盖为 v2）
    _ev(conn, ts=created + 10, attribution={"type": "审题错误", "conf": 0.9})
    r2 = distill.maybe_distill(conn, PACK, KP, llm=fake2)
    assert r2["distilled"] is True
    assert len(fake2.calls) == 1
    assert mem2.patterns_get(conn, PACK, KP)["pattern_md"] == "卡v2"
    conn.close()


def test_rule_card_does_not_throttle_auto_distill(tmp_path):
    """对抗审查 major 回归（单测）：节流只对 source=auto_distill 的卡生效——
    既有 rule 卡（/v2/patterns 物化，每次刷新 created_ts）视为「无自动卡」，
    达阈值即升级覆盖，绝不被规则卡把节流时钟永远重置而饿死。"""
    conn = _conn(tmp_path)
    _seed_six(conn, wrong=6)
    mem2.patterns_set(conn, PACK, KP, "规则卡文案。建议回看典型坑位。",
                      source="rule", confirmed=False)
    st = distill.distill_status(conn, PACK, KP)
    assert st["has_card"] is True and st["card_source"] == "rule"
    assert st["has_auto_card"] is False
    assert st["events_after_card"] is None       # 节流时钟不看 rule 卡
    assert st["due"] is True
    fake = _fake_llm()
    r = distill.maybe_distill(conn, PACK, KP, llm=fake)
    assert r["distilled"] is True
    card = mem2.patterns_get(conn, PACK, KP)
    assert card["source"] == "auto_distill" and card["pattern_md"] == CARD_MD
    conn.close()


# --------------------------------------------------------------------------- #
# 并发去重：per-(pack,kp) 在途集合（对抗审查 minor）
# --------------------------------------------------------------------------- #
def test_inflight_dedup_and_cleanup(tmp_path):
    """并发双作答同时达阈值 → 只有第一次调 LLM，第二次 in_progress 直接返回；
    任务结束 finally 清理在途标记（此后同键调用正常走阈值/节流判断）。"""
    db = tmp_path / "inflight.db"
    conn_main = mem2.connect(str(db))
    _seed_six(conn_main, wrong=6)
    release = threading.Event()
    calls = []

    def slow_llm(messages, **kw):
        calls.append(1)
        release.wait(timeout=5)
        return {"pattern_md": CARD_MD}

    out = {}

    def worker():
        conn_w = mem2.connect(str(db))   # 线程自建连接（sqlite 不跨线程共享）
        try:
            out["r1"] = distill.maybe_distill(conn_w, PACK, KP, llm=slow_llm)
        finally:
            conn_w.close()

    t = threading.Thread(target=worker)
    t.start()
    for _ in range(300):                 # 等第一个调用进入在途（已到 LLM）
        if calls:
            break
        time.sleep(0.01)
    assert calls, "worker 未进入慢 LLM"

    r2 = distill.maybe_distill(conn_main, PACK, KP, llm=slow_llm)
    assert r2 == {"distilled": False, "reason": "in_progress"}
    assert len(calls) == 1               # 零 LLM 浪费

    release.set()
    t.join(timeout=5)
    assert out["r1"]["distilled"] is True

    # finally 已清理在途：再次调用不再 in_progress（卡已写 + 无新事件 → throttled）
    r3 = distill.maybe_distill(conn_main, PACK, KP, llm=slow_llm)
    assert r3["distilled"] is False and r3["reason"] == "throttled"
    conn_main.close()


# --------------------------------------------------------------------------- #
# demo 红线：零 LLM 整体跳过
# --------------------------------------------------------------------------- #
def test_demo_env_skip_never_calls_llm(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _seed_six(conn, wrong=6)
    calls = []

    def bomb(messages, **kw):
        calls.append(1)
        raise RuntimeError("demo 态绝不调 LLM")

    monkeypatch.setenv("MATHFORGE_DEMO", "1")
    r = distill.maybe_distill(conn, PACK, KP, llm=bomb)
    assert r == {"distilled": False, "reason": "demo"}
    assert calls == []
    assert mem2.patterns_get(conn, PACK, KP) is None
    conn.close()


def test_no_api_key_counts_as_demo(tmp_path, monkeypatch):
    """无 DEEPSEEK_API_KEY（口径同 router._demo_mode）→ demo 跳过。"""
    conn = _conn(tmp_path)
    _seed_six(conn, wrong=6)
    monkeypatch.setenv("MATHFORGE_DEMO", "0")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    r = distill.maybe_distill(conn, PACK, KP, llm=_fake_llm())
    assert r == {"distilled": False, "reason": "demo"}
    conn.close()


# --------------------------------------------------------------------------- #
# LLM 失败 / 坏输出：不抛出、卡不写
# --------------------------------------------------------------------------- #
def test_llm_failure_folded_not_raised(tmp_path):
    conn = _conn(tmp_path)
    _seed_six(conn, wrong=6)

    def boom(messages, **kw):
        raise RuntimeError("DeepSeek 调用失败（重试 3 次耗尽）：timeout")

    r = distill.maybe_distill(conn, PACK, KP, llm=boom)
    assert r["distilled"] is False and r["reason"] == "llm_error"
    assert "timeout" in r["error"]
    assert mem2.patterns_get(conn, PACK, KP) is None
    conn.close()


def test_llm_bad_output_folded_not_raised(tmp_path):
    """坏 JSON / 缺 pattern_md / 非 dict / 空串：一律 llm_error 且卡不写。"""
    conn = _conn(tmp_path)
    _seed_six(conn, wrong=6)
    cases = [
        ("坏 JSON", lambda m, **k: (_ for _ in ()).throw(
            ValueError("LLM 输出无法解析为 JSON：{pattern_md}"))),
        ("缺 pattern_md", lambda m, **k: {"other": "不是模式卡"}),
        ("非 dict", lambda m, **k: "纯文本不是 JSON 对象"),
        ("空串", lambda m, **k: {"pattern_md": "   "}),
    ]
    for name, fn in cases:
        r = distill.maybe_distill(conn, PACK, KP, llm=fn)
        assert r["distilled"] is False and r["reason"] == "llm_error", name
        assert r.get("error"), name
        assert mem2.patterns_get(conn, PACK, KP) is None, name
    conn.close()


# --------------------------------------------------------------------------- #
# payload 口径：旧事件容错 / 占位串 / override 优先
# --------------------------------------------------------------------------- #
def test_legacy_events_no_meta_and_placeholder_attr(tmp_path):
    """旧事件 meta 无 q/ans（甚至 meta=None）→ 摘录键缺省省略绝不崩；
    「未归因」占位与 None 同义：不进 attribution_counts，摘录标注「未归因」。"""
    conn = _conn(tmp_path)
    for i in range(4):
        _ev(conn, result="correct", ts=1000.0 + i)
    _ev(conn, ts=2000.0, attribution={"type": "未归因", "conf": 0.0}, meta=None)
    _ev(conn, ts=3000.0, attribution=None, meta={"stmt_summary": "旧事件只有摘要键"})
    fake = _fake_llm()
    r = distill.maybe_distill(conn, PACK, KP, llm=fake)
    assert r["distilled"] is True
    body = _payload_of(fake.calls[0]["messages"][-1]["content"])
    assert body["wrong_n"] == 2 and body["attribution_counts"] == {}
    for w in body["wrong_excerpts"]:
        assert w["attribution_type"] == "未归因"
        assert "q" not in w and "ans" not in w
    conn.close()


def test_override_attribution_wins_in_payload(tmp_path):
    """人工修正（独立 override 事件）优先进蒸馏 payload（与 recall 口径一致）。"""
    conn = _conn(tmp_path)
    ids = _seed_six(conn, wrong=6)
    mem2.override_attribution(conn, ids[-1], "概念混淆")  # 修正最近一条
    fake = _fake_llm()
    r = distill.maybe_distill(conn, PACK, KP, llm=fake)
    assert r["distilled"] is True
    body = _payload_of(fake.calls[0]["messages"][-1]["content"])
    assert body["attribution_counts"] == {"计算失误": 5, "概念混淆": 1}
    assert body["wrong_excerpts"][0]["attribution_type"] == "概念混淆"  # 最近一条在前
    # override 事件本身（mode='override'）不进窗口
    assert body["window_n"] == 6
    conn.close()


# --------------------------------------------------------------------------- #
# distill_status：只读诊断 + 预判字段
# --------------------------------------------------------------------------- #
def test_distill_status_readonly_and_fields(tmp_path):
    conn = _conn(tmp_path)
    _seed_six(conn, wrong=6)
    before = conn.total_changes
    st = distill.distill_status(conn, PACK, KP)
    assert conn.total_changes == before          # 纯只读
    assert st["n"] == 6 and st["wrong"] == 6
    assert st["has_card"] is False and st["threshold_met"] is True
    assert st["expired"] is False and st["due"] is True
    assert st["events_after_card"] is None

    assert distill.maybe_distill(conn, PACK, KP, llm=_fake_llm())["distilled"] is True
    st2 = distill.distill_status(conn, PACK, KP)
    assert st2["has_card"] is True and st2["card_source"] == "auto_distill"
    assert st2["card_confirmed"] is False
    assert st2["events_after_card"] == 0 and st2["due"] is False
    conn.close()


def test_distill_only_uses_passed_conn(tmp_path):
    """sim 隔离口径：只认传入 conn——另一库满窗错题绝不影响本库判断/写卡。"""
    conn_sim = _conn(tmp_path)    # 模拟器库（满窗错题）
    conn_real = _conn(tmp_path)   # 另一库（空）
    _seed_six(conn_sim, wrong=6)
    fake = _fake_llm()
    r = distill.maybe_distill(conn_real, PACK, KP, llm=fake)
    assert r["distilled"] is False and r["reason"] == "threshold"
    assert fake.calls == []
    assert mem2.patterns_get(conn_real, PACK, KP) is None
    conn_sim.close()
    conn_real.close()


# --------------------------------------------------------------------------- #
# 模块 F 联动（只验证不改）：蒸馏卡自然进入出题记忆块
# --------------------------------------------------------------------------- #
def test_distilled_card_flows_into_generation_memory_block(tmp_path):
    conn = _conn(tmp_path)
    _seed_six(conn, wrong=6)
    fake = _fake_llm("该生常在构造辅助函数时漏验端点相等条件。")
    assert distill.maybe_distill(conn, PACK, KP, llm=fake)["distilled"] is True
    recall = mem2.recall_for_generation(conn, KP)
    assert recall["pattern_card"] == "该生常在构造辅助函数时漏验端点相等条件。"
    block = mem2.format_memory_block(recall)
    assert "薄弱模式卡：该生常在构造辅助函数时漏验端点相等条件。" in block
    conn.close()


# --------------------------------------------------------------------------- #
# 端点集成（HTTP）：落库 → SQL 预判 → BackgroundTasks 蒸馏 → /v2/daily 透出
# --------------------------------------------------------------------------- #
def _ans_body(i: int) -> dict:
    return {
        "pack_id": PACK, "kp": KP, "qtype": "fill",
        "student_answer": "0", "standard_answer": "1",       # 判错（SymPy 等价）
        "statement_md": f"第{i}题：求函数在某闭区间上满足罗尔定理的 xi（变体{i}）",
        "analysis": "标准解析", "difficulty": "基础",
    }


def _card_from_db(db_path):
    conn = mem2.connect(db_path)
    try:
        return mem2.patterns_get(conn, PACK, KP)
    finally:
        conn.close()


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    """独立临时库 + 非 demo 态 + 假归因/假 LLM 的 TestClient（随测试即建即毁）。

    - MATHFORGE_DEMO=0 + 假 key：只为了过 maybe_distill 的 demo 门，真实 LLM
      调用全部被假件拦截（v2api 归因走 stub、蒸馏走 distill.chat_json stub）；
    - v2api 调 maybe_distill 不传 llm → 调用期解析模块级 chat_json，
      monkeypatch distill.chat_json 即可注入假件（真实路径零感知）。
    """
    db = tmp_path / "api_v2.db"
    monkeypatch.setenv("MATHFORGE_V2_DB", str(db))
    monkeypatch.setenv("MATHFORGE_PROFILES_DIR", str(tmp_path / "profiles"))
    monkeypatch.setenv("MATHFORGE_DEMO", "0")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-not-real")
    from fastapi.testclient import TestClient
    import app as app_mod
    import v2api
    monkeypatch.setattr(v2api, "attribute_error",
                        lambda *a, **k: {"attribution": "计算失误", "confidence": 0.9})
    fake = _fake_llm()
    monkeypatch.setattr(distill, "chat_json", fake)
    with TestClient(app_mod.app) as c:
        yield c, fake


def test_answer_endpoint_triggers_distill_and_daily_exposes(api_client):
    c, fake = api_client
    for i in range(6):
        r = c.post("/v2/answer", json=_ans_body(i))
        assert r.status_code == 200 and r.json()["event_recorded"] is True
    # 第 6 题响应路径（BackgroundTasks 随响应同步执行）触发恰好一次蒸馏
    assert len(fake.calls) == 1
    assert fake.calls[0]["kw"]["namespace"] == "distill:"
    card = _card_from_db(os.environ["MATHFORGE_V2_DB"])
    assert card and card["source"] == "auto_distill" and card["confirmed"] is False

    # /v2/daily（近况端点）透出卡片字段（有卡才出现），标注 source/confirmed
    d = c.get("/v2/daily").json()
    assert d["day"] == date.today().isoformat()
    pats = [p for p in d.get("patterns", []) if p["kp"] == KP]
    assert len(pats) == 1
    assert pats[0]["pack_id"] == PACK and pats[0]["kp_name"] == "罗尔定理"
    assert pats[0]["source"] == "auto_distill" and pats[0]["confirmed"] is False
    assert pats[0]["pattern_md"] == CARD_MD

    # 节流（端点级）：卡创建后再答 1 题 → SQL 预判不 due → 不再蒸馏
    r7 = c.post("/v2/answer", json=_ans_body(6))
    assert r7.status_code == 200 and r7.json()["event_recorded"] is True
    assert len(fake.calls) == 1


def test_selfassess_endpoint_triggers_distill(api_client):
    c, fake = api_client
    for i in range(6):
        r = c.post("/v2/selfassess", json={
            "pack_id": PACK, "kp": KP, "qtype": "solution",
            "student_steps": [f"第{i}题步骤"], "grade": "不会",
            "attribution": "方法选错", "statement_md": f"解答题{i}题面",
        })
        assert r.status_code == 200 and r.json()["ok"] is True
    assert len(fake.calls) == 1
    card = _card_from_db(os.environ["MATHFORGE_V2_DB"])
    assert card and card["source"] == "auto_distill"
    body = _payload_of(fake.calls[0]["messages"][-1]["content"])
    assert body["attribution_counts"] == {"方法选错": 6}
    assert body["wrong_n"] == 6          # selfassess+wrong 按错题口径计入


def test_demo_endpoint_never_distills(tmp_path, monkeypatch):
    """demo 红线（端点级）：答满阈值也零蒸馏零 LLM——炸弹假件不许被调。"""
    db = tmp_path / "demo_v2.db"
    monkeypatch.setenv("MATHFORGE_V2_DB", str(db))
    monkeypatch.setenv("MATHFORGE_PROFILES_DIR", str(tmp_path / "profiles"))
    monkeypatch.setenv("MATHFORGE_DEMO", "1")            # conftest 密封值，显式重申
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    from fastapi.testclient import TestClient
    import app as app_mod
    calls = []

    def bomb(messages, **kw):
        calls.append(1)
        raise RuntimeError("demo 态绝不调 LLM")

    monkeypatch.setattr(distill, "chat_json", bomb)
    with TestClient(app_mod.app) as c:
        for i in range(6):
            r = c.post("/v2/answer", json=_ans_body(i))
            assert r.status_code == 200 and r.json()["event_recorded"] is True
        d = c.get("/v2/daily").json()
    assert calls == []                        # 后台任务零 LLM（事件照常落库）
    assert _card_from_db(str(db)) is None
    assert "patterns" not in d                # 无卡 → 字段整体缺席（有卡才出现）


def test_patterns_endpoint_does_not_downgrade_auto_card(api_client):
    """/v2/patterns rule 物化只补空缺：LLM 自动卡不被规则聚合文案降级覆盖。"""
    c, fake = api_client
    for i in range(6):
        c.post("/v2/answer", json=_ans_body(i))
    assert _card_from_db(os.environ["MATHFORGE_V2_DB"])["source"] == "auto_distill"
    r = c.get("/v2/patterns")
    assert r.status_code == 200
    card = _card_from_db(os.environ["MATHFORGE_V2_DB"])
    assert card["source"] == "auto_distill"
    assert card["pattern_md"] == CARD_MD      # 未被 rule 文案覆盖


def test_rule_materialization_does_not_starve_auto_distill(api_client):
    """对抗审查 major 回归（端点级，裁定指定场景）：种 5 题 → 逛一次学情页
    （/v2/patterns 物化 rule 卡、刷新 created_ts）→ 第 6 题达阈值 → 蒸馏照常
    触发且升级覆盖为 auto 卡——学情页轮询不再饿死自动蒸馏。"""
    c, fake = api_client
    for i in range(5):
        c.post("/v2/answer", json=_ans_body(i))
    assert c.get("/v2/patterns").status_code == 200     # 物化 rule 卡
    card = _card_from_db(os.environ["MATHFORGE_V2_DB"])
    assert card and card["source"] == "rule"            # rule 卡已落库
    r6 = c.post("/v2/answer", json=_ans_body(5))
    assert r6.json()["event_recorded"] is True
    assert len(fake.calls) == 1                         # rule 卡不节流 → 触发蒸馏
    card2 = _card_from_db(os.environ["MATHFORGE_V2_DB"])
    assert card2["source"] == "auto_distill"
    assert card2["pattern_md"] == CARD_MD               # 升级覆盖


def test_daily_patterns_pack_follows_event_pack_like_recall(api_client):
    """对抗审查 minor 回归：/v2/daily 查卡 pack 与 recall_for_generation 同口径
    （该 kp 最近事件的 pack 列）。客户端 pack 口径漂移（错挂 wrongpack）时，
    两条读路径一致地指向同一张卡键——不再分裂。"""
    c, fake = api_client
    for i in range(6):
        c.post("/v2/answer", json=_ans_body(i))          # 卡写在 (calculus, KP)
    # 直接向事件流插一条错挂 pack 的当日事件（比 6 题更新）
    conn = mem2.connect(os.environ["MATHFORGE_V2_DB"])
    mem2.append_event(conn, pack="wrongpack", kp=KP, qtype="fill",
                      mode="answer", result="wrong", sim=0)
    conn.close()
    # daily：最近事件 pack=wrongpack → 查 (wrongpack, KP) → 无卡 → 字段缺席
    d = c.get("/v2/daily").json()
    assert "patterns" not in d
    # recall 同口径：同样按最近事件 pack 查不到 —— 两条路径一致
    conn = mem2.connect(os.environ["MATHFORGE_V2_DB"])
    try:
        assert mem2.recall_for_generation(conn, KP)["pattern_card"] is None
    finally:
        conn.close()


def test_profile_header_distill_lands_in_profile_db(tmp_path, monkeypatch):
    """对抗审查 minor 回归（档案隔离）：带 X-MF-Profile 答满阈值 → 后台蒸馏
    经 contextvar 传播落 profiles/<id>.db；默认（非档案）库零事件零卡。"""
    prof_root = tmp_path / "profiles"
    base_db = tmp_path / "base.db"
    monkeypatch.setenv("MATHFORGE_V2_DB", str(base_db))
    monkeypatch.setenv("MATHFORGE_PROFILES_DIR", str(prof_root))
    monkeypatch.setenv("MATHFORGE_DEMO", "0")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-not-real")
    from fastapi.testclient import TestClient
    import app as app_mod
    import v2api
    monkeypatch.setattr(v2api, "attribute_error",
                        lambda *a, **k: {"attribution": "计算失误", "confidence": 0.9})
    fake = _fake_llm()
    monkeypatch.setattr(distill, "chat_json", fake)
    with TestClient(app_mod.app) as c:
        for i in range(6):
            r = c.post("/v2/answer", json=_ans_body(i),
                       headers={"X-MF-Profile": "studentA"})
            assert r.status_code == 200 and r.json()["event_recorded"] is True
    assert len(fake.calls) == 1                 # 档案库视角下阈值命中、恰好蒸一次

    prof_db = prof_root / "mathforge_studentA.db"
    assert prof_db.exists()
    conn = mem2.connect(str(prof_db))
    try:
        card = mem2.patterns_get(conn, PACK, KP)
        assert card and card["source"] == "auto_distill" and card["confirmed"] is False
    finally:
        conn.close()
    # 默认库零写入（事件与卡都只在档案库）
    conn = mem2.connect(str(base_db))
    try:
        assert mem2.patterns_get(conn, PACK, KP) is None
        assert conn.execute("SELECT COUNT(*) FROM attempt_events").fetchone()[0] == 0
    finally:
        conn.close()
