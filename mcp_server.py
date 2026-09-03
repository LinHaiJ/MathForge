"""MathForge MCP Server（T11）：把出题/记忆/归因暴露为 MCP 工具（JD② Skill 调度与 MCP）。

启动（stdio）：python mcp_server.py
工具：generate_quiz / get_mastery / diagnose
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
from attribute import attribute_error  # noqa: E402
from generate import generate_question  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402
from verify import check_answer  # noqa: E402

mcp = MCPServer(
    name="mathforge",
    instructions="记忆驱动的考研数学训练 Agent：generate_quiz 出题（带双级验证）、"
                 "get_mastery 读掌握度画像、diagnose 判分+错因归因。",
)


@mcp.tool()
def generate_quiz(kp: str, difficulty: str = "基础", qtype: str = "calculation") -> str:
    """按知识点×难度×题型生成一道经过验证的数学题。

    qtype: calculation(计算/绿标) | concept(概念判断) | order(步骤排序) | choice(单选)——后三类为黄标。
    未通过对应等级验证的题不会外推（返回 status=blocked_pending_human 与原因）。
    """
    q = generate_question({"kp": kp}, difficulty=difficulty, qtype=qtype)
    return json.dumps(q, ensure_ascii=False)


@mcp.tool()
def get_mastery() -> str:
    """读取当前掌握度画像（含 7 天半衰期衰减后的有效掌握度）。"""
    conn = db.connect()
    try:
        rows = conn.execute("SELECT * FROM mastery").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["mastery_decayed"] = db.decayed(d["mastery"], d["updated_at"])
            out.append(d)
        return json.dumps({"mastery": out, "half_life_days": db.HALF_LIFE_DAYS}, ensure_ascii=False)
    finally:
        conn.close()


@mcp.tool()
def diagnose(kp: str, statement_md: str, student_answer: str, standard_answer: str,
             verify_level: str = "green") -> str:
    """判分 + 错因归因（四类：概念混淆/计算失误/方法选错/审题错误），并写入记忆系统。"""
    conn = db.connect()
    try:
        if verify_level == "green":
            correct = check_answer(student_answer, standard_answer)
        else:
            from generate import _canon
            correct = _canon(student_answer) == _canon(standard_answer)
        attribution = None
        if not correct:
            attribution = attribute_error(statement_md, student_answer, standard_answer)
        db.record_answer(conn, kp, correct=correct,
                         attribution=(attribution or {}).get("attribution"),
                         student_answer=student_answer, standard_answer=standard_answer)
        return json.dumps({"correct": correct, "attribution": attribution}, ensure_ascii=False)
    finally:
        conn.close()


if __name__ == "__main__":
    mcp.run()  # 默认 stdio 传输
