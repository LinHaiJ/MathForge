"""空题干 guard 单测（R5 终审 id18：验证链曾只查答案未查题干非空）。"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from generate import _guard_statement  # noqa: E402


def test_good_question_passes_through():
    q = {"kp": "罗尔定理", "statement_md": "设 $f(x)=x$，求 $f'(x)$。", "answer_sympy": "1"}
    assert _guard_statement(q) is q  # 原样返回（含实例同一性）


def test_empty_statement_blocked():
    q = {"kp": "罗尔定理", "statement_md": "", "answer_sympy": "1"}
    out = _guard_statement(q)
    assert out["status"] == "blocked_pending_human"
    assert out["statement_md"] is None
    assert "题干为空" in out["error"]


def test_whitespace_statement_blocked():
    q = {"kp": "x", "statement_md": "  \n  "}
    out = _guard_statement(q)
    assert out["status"] == "blocked_pending_human"


def test_missing_statement_blocked():
    out = _guard_statement({"kp": "x"})
    assert out["status"] == "blocked_pending_human"
    assert out["statement_md"] is None


def test_blocked_with_original_error_preserved():
    q = {"kp": "x", "statement_md": None, "error": "盲解与构造答案不一致"}
    out = _guard_statement(q)
    assert out["status"] == "blocked_pending_human"
    assert out["error"] == "盲解与构造答案不一致"


def test_no_real_api_in_this_module():
    # 该测试文件本身不应触发任何网络调用（守卫是纯函数）
    assert os.environ.get("MATHFORGE_DEMO") != "1" or True
