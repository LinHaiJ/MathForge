"""player 端到端复演检查：在 MATHFORGE_DEMO=1 + 全新 mathforge.db 的服务上，
按 ui/player.html 的确切 fetch 序列走一遍，验证零 API 与策略叙事链。

录制前程序化预检（对应 demo_checklist.md）。用法：
  1) MATHFORGE_DEMO=1 rm -f mathforge.db
  2) MATHFORGE_DEMO=1 python -m uvicorn app:app --port 8127 &
  3) MATHFORGE_DEMO=1 python scripts/player_replay_check.py
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8127"


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read())


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def answer_body(q: dict, student: str, override: str | None = None) -> dict:
    b = {
        "kp": q["kp"], "statement_md": q["statement_md"], "student_answer": student,
        "standard_answer": q.get("answer_sympy") or q.get("answer_md") or q.get("correct") or "",
        "verify_level": q.get("verify_level") or "green",
        "options": q.get("options"), "correct": q.get("correct"),
        "current_difficulty": q.get("difficulty") or "基础", "analysis": q.get("analysis"),
    }
    if override:
        b["attribution_override"] = override
    return b


def main() -> int:
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
        ok = ok and cond

    # 1 摄取
    md = (Path(__file__).resolve().parent.parent / "ui" / "demo_assets" / "高数-微分中值定理.md").read_text(encoding="utf-8")
    ing = post("/ingest", {"md": md, "source": "高数-微分中值定理.md"})
    kps = [k["kp"] for k in ing["data"]["kp_list"]]
    check("ingest", len(kps) >= 3, f"{kps}")

    # 2 P2 探测（罗尔 实例#1）
    d = get("/generate?kp=%E7%BD%97%E5%B0%94%E5%AE%9A%E7%90%86&difficulty=%E5%9F%BA%E7%A1%80&qtype=calculation")
    check("P2 首触", d["decision"]["trigger_rule"] == "P2", str(d["decision"]["trigger_rule"]))
    q1 = d["question"]
    check("绿标题干", bool(q1.get("statement_md")) and q1["verify_level"] == "green")

    # 3 答错 → LLM 归因（预热缓存：审题错误 0.9）
    a = post("/answer", answer_body(q1, "999"))
    check("归因=审题错误", a["attribution"] == "审题错误", str(a["attribution"]))
    # 4 修正归因 → 计算失误 → next P4
    a2 = post("/answer", answer_body(q1, "999", override="计算失误"))
    check("修正后 next=P4", a2["next"]["trigger_rule"] == "P4", str(a2["next"]["trigger_rule"]))

    # 5 P4 变式 ×1 自动答对
    d2 = get("/generate?kp=%E7%BD%97%E5%B0%94%E5%AE%9A%E7%90%86&difficulty=%E5%9F%BA%E7%A1%80&qtype=calculation")
    check("P4 后出题", bool(d2["question"].get("statement_md")))
    a3 = post("/answer", answer_body(d2["question"], d2["question"]["answer_sympy"]))
    check("变式答对", a3["correct"] is True)

    # 6 拉格朗日：P2 → ×2 连对 → P5
    d3 = get("/generate?kp=%E6%8B%89%E6%A0%BC%E6%9C%97%E6%97%A5%E4%B8%AD%E5%80%BC%E5%AE%9A%E7%90%86&difficulty=%E5%9F%BA%E7%A1%80&qtype=calculation")
    check("拉格朗日 P2", d3["decision"]["trigger_rule"] == "P2")
    a4 = post("/answer", answer_body(d3["question"], d3["question"]["answer_sympy"]))
    check("拉1 答对", a4["correct"] is True)
    d4 = get("/generate?kp=%E6%8B%89%E6%A0%BC%E6%9C%97%E6%97%A5%E4%B8%AD%E5%80%BC%E5%AE%9A%E7%90%86&difficulty=%E8%BF%9B%E9%98%B6&qtype=calculation")
    a5 = post("/answer", answer_body(d4["question"], d4["question"]["answer_sympy"]))
    check("拉2 答对", a5["correct"] is True)
    d5 = get("/generate?kp=%E6%8B%89%E6%A0%BC%E6%9C%97%E6%97%A5%E4%B8%AD%E5%80%BC%E5%AE%9A%E7%90%86&difficulty=%E8%BF%9B%E9%98%B6&qtype=calculation")
    check("P5 升档", d5["decision"]["trigger_rule"] == "P5", str(d5["decision"]["trigger_rule"]))

    # 7 黄标概念题答错 → 概念混淆（预热缓存 0.9）
    d6 = get("/generate?kp=%E7%BD%97%E5%B0%94%E5%AE%9A%E7%90%86&difficulty=%E5%9F%BA%E7%A1%80&qtype=concept")
    q6 = d6["question"]
    check("黄标概念题", q6["verify_level"] == "yellow" and bool(q6.get("statement_md")))
    a6 = post("/answer", answer_body(q6, "错误"))
    check("归因=概念混淆", a6["attribution"] == "概念混淆", str(a6["attribution"]))
    check("next=P3", a6["next"]["trigger_rule"] == "P3", str(a6["next"]["trigger_rule"]))

    # 8 mastery 面板数据
    m = get("/mastery")
    check("mastery 两 kp", len(m["mastery"]) >= 2, str([r["kp"] for r in m["mastery"]]))

    print("\n== 结论 ==")
    print("全部通过：player 叙事链在 DEMO=1 下零 API 可复演 ✅" if ok else "存在 FAIL —— 录制前必须先修复/预热")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
