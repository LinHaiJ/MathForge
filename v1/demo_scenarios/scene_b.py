"""演示场景 B（Day2 任务 4）：策略对比——五条规则各构造一个记忆状态，观察决策差异。

证明"决策由记忆状态驱动，非随机"：同一引擎，不同记忆 → 不同规则命中、不同动作。
全部纯规则计算，零 LLM 调用（P1-P5 均不触发 LLM；P6 兜底才调用解释器）。
"""

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # v1/（db/policy）
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 仓库根（llm 等）
import db  # noqa: E402
from policy import decide  # noqa: E402

EV_DIR = Path(__file__).resolve().parents[2] / "证据" / "demo"


def fresh_state(kp="拉格朗日中值定理", difficulty="进阶", prereq_healthy=False):
    conn = db.connect(tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)
    if prereq_healthy:
        db.record_answer(conn, "罗尔定理", correct=True)
        db.record_answer(conn, "罗尔定理", correct=True)
    return {"conn": conn, "current_kp": kp, "current_difficulty": difficulty,
            "watch_kps": [kp, "罗尔定理"], "prerequisites": {kp: ["罗尔定理"]}}


def main() -> int:
    rows = []

    # S1 首次接触 → P2
    st = fresh_state()
    d = decide(st)
    rows.append(("S1 知识点首次接触（无记忆）", d["trigger_rule"],
                 f"{d['kp']} · {d['difficulty']} ×{d['count']}", d["reason"]))
    st["conn"].close()

    # S2 连对 2 次 → P5
    st = fresh_state(prereq_healthy=True)
    db.record_answer(st["conn"], st["current_kp"], correct=True)
    db.record_answer(st["conn"], st["current_kp"], correct=True)
    d = decide(st)
    rows.append(("S2 连对 2 次", d["trigger_rule"],
                 f"{d['kp']} · {d['difficulty']} ×{d['count']}", d["reason"]))
    st["conn"].close()

    # S3 连错 2 次计算失误（前置健康）→ P4
    st = fresh_state(prereq_healthy=True)
    db.record_answer(st["conn"], st["current_kp"], correct=False, attribution="计算失误")
    db.record_answer(st["conn"], st["current_kp"], correct=False, attribution="计算失误")
    d = decide(st)
    rows.append(("S3 连错 2 次·计算失误", d["trigger_rule"],
                 f"{d['kp']} · {d['difficulty']} ×{d['count']}", d["reason"]))
    st["conn"].close()

    # S4 最近错因=概念混淆 → P3
    st = fresh_state(prereq_healthy=True)
    db.record_answer(st["conn"], st["current_kp"], correct=False, attribution="概念混淆")
    d = decide(st)
    rows.append(("S4 错因=概念混淆", d["trigger_rule"],
                 f"{d['kp']} · {d['difficulty']} ×{d['count']}" + (f" +变式{d.get('plus_variant', 0)}" if d.get("plus_variant") else ""),
                 d["reason"]))
    st["conn"].close()

    # S5 掌握度低且前置薄弱 → P1（P1 优先级最高，压制 P4）
    st = fresh_state(prereq_healthy=False)
    db.record_answer(st["conn"], st["current_kp"], correct=False, attribution="计算失误")
    db.record_answer(st["conn"], st["current_kp"], correct=False, attribution="计算失误")
    d = decide(st)
    rows.append(("S5 掌握度<40% 且前置薄弱", d["trigger_rule"],
                 f"{d['kp']} · {d['difficulty']} ×{d['count']}", d["reason"]))
    st["conn"].close()

    lines = ["# 演示场景 B · 策略对比（同一引擎，五种记忆状态 → 五种决策）", "",
             "> 规则表 P1-P6 按优先级命中即执行（PRD §4）。决策差异仅由记忆状态驱动。", "",
             "| 状态 | 命中规则 | 动作 | 理由 |", "|---|---|---|---|"]
    for name, rule, action, reason in rows:
        lines.append(f"| {name} | **{rule}** | {action} | {reason} |")
    lines += ["", "P6（兜底+LLM 解释）在所有规则都不命中时触发，含一次 LLM 调用，",
              "见 证据/api_live_r1.txt 的 answer#1 实况。"]

    out = "\n".join(lines)
    print(out)
    EV_DIR.mkdir(parents=True, exist_ok=True)
    (EV_DIR / "scene_b.md").write_text(out, encoding="utf-8")
    print(f"\n证据已存：证据/demo/scene_b.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
