"""Day6 任务 4：变式溯源单测。

- 家族 kp：确定性族内换参出变式（零 API、构造即正确），溯源块字段完整
- 题库斜杠路径 kp（无 params）：子串匹配家族，标注题型转换与新参数格
- 非家族 kp：演示模式缓存未命中 → 走既有拦截链优雅降级（不崩溃、不出网）
- /variant 端点闭环
"""

import os
import sys
from pathlib import Path

os.environ["MATHFORGE_DEMO"] = "1"  # 零 API：LLM 路径缓存未命中按拦截降级，绝不出网

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
from families import enumerate_family  # noqa: E402
from generate import generate_variant  # noqa: E402


def _family_source():
    spec = enumerate_family("rolle_xi", limit=1)[0]
    return {"kp": spec["kp"], "statement_md": spec["statement_md"],
            "params": spec["params"], "difficulty": spec["difficulty"],
            "qtype": "calculation"}


def test_family_variant_provenance():
    src = _family_source()
    q = generate_variant("罗尔定理", src)
    assert q.get("statement_md"), q.get("error")
    v = q["variant"]
    assert v["source_kp"] == "罗尔定理"
    assert v["source_statement_md"] == src["statement_md"]
    assert "参数扰动" in v["dimensions"]
    assert v["changed_params"], "族内换参必须留参数对照（用户能看出哪里变了）"
    assert v["kp_consistent"] is True


def test_family_variant_differs_from_source():
    src = _family_source()
    q = generate_variant("罗尔定理", src)
    assert q["statement_md"] != src["statement_md"]  # 确定性枚举跳过同参数格


def test_variant_from_bank_style_kp():
    kp = "行列式 / 具体行列式计算 / X型"
    q = generate_variant(kp, {"kp": kp, "statement_md": "计算三阶行列式。",
                              "params": {}, "qtype": "solution", "difficulty": "基础"})
    assert q.get("statement_md"), q.get("error")
    v = q["variant"]
    assert v["kp_consistent"] is True            # 子串匹配 → 知识点一致
    assert "题型转换" in v["dimensions"]          # solution → calculation
    assert v["introduced_params"], "源题无参数格时须标注新引入参数"
    assert "难度上移" in v["dimensions"]          # 基础 → 综合（det_3x3 家族难度）


def test_nonfamily_variant_demo_graceful_block():
    # 演示模式零 API：非家族 kp 的 LLM 扰动路径缓存未命中 → 拦截卡，不崩溃不出网
    q = generate_variant("函数极限概念", {"kp": "函数极限概念",
                                          "statement_md": "求极限。", "params": {},
                                          "qtype": "solution"})
    assert q.get("status") == "blocked_pending_human"
    assert q["variant"]["source_kp"] == "函数极限概念"  # 拦截卡仍带溯源块


def test_variant_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "v.db")
    from app import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    src = _family_source()
    r = client.post("/variant", json={
        "kp": src["kp"], "source_statement_md": src["statement_md"],
        "source_params": src["params"], "source_difficulty": "基础",
        "source_qtype": "calculation"})
    assert r.status_code == 200
    d = r.json()["question"]
    assert d.get("statement_md"), d.get("error")
    assert d["variant"]["changed_params"]
    assert d["variant"]["kp_consistent"] is True
