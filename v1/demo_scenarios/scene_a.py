"""演示场景 A（PRD §13 验收标准 3）：连错 2 次 → 策略按 P4 降难度（非随机）。

构造记忆状态，断言决策是规则驱动而非随机：
  两次计算失误错题后 → P4 命中、难度降一档、变式 ×2、policy_log 有完整快照。
"""

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # v1/（db/policy）
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 仓库根（llm 等）
import db  # noqa: E402
from policy import decide  # noqa: E402


def main() -> int:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = db.connect(tmp.name)

    kp, diff = "拉格朗日中值定理", "进阶"
    state = {
        "conn": conn,
        "current_kp": kp,
        "current_difficulty": diff,
        "watch_kps": [kp, "罗尔定理"],
        "prerequisites": {kp: ["罗尔定理"]},
    }

    # 用户画像（二战，PRD §3）：前置知识点掌握健康 → P1 不触发
    db.record_answer(conn, "罗尔定理", correct=True)
    db.record_answer(conn, "罗尔定理", correct=True)

    # 记忆写入：连错 2 次，归因=计算失误
    db.record_answer(conn, kp, correct=False, attribution="计算失误", question_id="t6-2",
                     student_answer="2", standard_answer="1")
    db.record_answer(conn, kp, correct=False, attribution="计算失误", question_id="t6-5",
                     student_answer="3", standard_answer="1")

    d = decide(state)
    print("决策输出:", d)

    # 断言 1：命中 P4（计算失误），非随机兜底
    assert d["trigger_rule"] == "P4", f"应命中 P4，实际 {d['trigger_rule']}"
    # 断言 2：难度降一档（进阶→基础），同输入必同输出（确定性=非随机）
    assert d["difficulty"] == "基础", f"应降档到基础，实际 {d['difficulty']}"
    assert d["count"] == 2 and d.get("plus_variant") is None
    d2 = decide(state)
    assert d2["trigger_rule"] == d["trigger_rule"] and d2["difficulty"] == d["difficulty"], \
        "相同记忆状态必须产生相同决策（非随机）"
    # 断言 3：policy_log 有完整决策记录（规则/输入快照/输出）
    logs = db.get_policy_log(conn)
    assert len(logs) >= 2 and logs[0]["trigger_rule"] == "P4"
    assert logs[0]["input_snapshot"]["current_kp"] == kp
    assert logs[0]["output"]["count"] == 2
    # 断言 4：mastery 表已被错题刷新（半衰期参数在库）
    m = db.get_mastery(conn, kp)
    assert m["wrong_count"] == 2 and m["streak_correct"] == 0 and m["mastery"] < 0.5

    # 反向对照：清库后连对 2 次 → 必须 P5 上移，证明决策由记忆状态驱动
    conn2 = db.connect(tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)
    db.record_answer(conn2, kp, correct=True)
    db.record_answer(conn2, kp, correct=True)
    d3 = decide({**state, "conn": conn2})
    assert d3["trigger_rule"] == "P5" and d3["difficulty"] == "综合", \
        f"连对 2 次应 P5 上移到综合，实际 {d3['trigger_rule']}/{d3['difficulty']}"

    conn.close()
    conn2.close()
    os.unlink(tmp.name)

    lines = ["# 演示场景 A · 记忆驱动（PRD §13 验收标准 3）", "",
             f"- 决策输出：{json.dumps(d, ensure_ascii=False)}",
             "- 断言 1：连错 2 次(计算失误) → P4 命中（非 P6 随机兜底）",
             "- 断言 2：难度进阶→基础降一档，×2 数值变式",
             "- 断言 3：相同记忆状态 → 相同决策（确定性=非随机）",
             "- 断言 4：policy_log 完整（规则/输入快照/输出）；mastery 表连错 2 次刷新",
             "- 反向对照：连对 2 次 → P5 升到综合（决策由记忆驱动）", ""]
    out = "\n".join(lines)
    print(out)
    ev = Path(__file__).resolve().parents[2] / "证据" / "demo"
    ev.mkdir(parents=True, exist_ok=True)
    (ev / "scene_a.md").write_text(out + "\n决策全文：\n" + json.dumps(d, ensure_ascii=False, indent=1) + "\n",
                                   encoding="utf-8")
    print("证据已存：证据/demo/scene_a.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
