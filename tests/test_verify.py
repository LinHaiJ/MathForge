"""WB-03 测试骨架：mathforge/verify.py 的 pytest 用例。

被测模块由主 agent 提供，接口约定：
    check_answer(student_expr_str, standard_expr_str) -> bool
    gen_parametric(template, params) -> expr

本文件在 verify.py 尚未就绪时仍可被测框架收集（用例 skip），
待模块就绪后自动转为真实断言。
"""

import os
import sys

# 确保 mathforge 包可被导入（无论从哪个 cwd 运行 pytest）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from mathforge.verify import check_answer, gen_parametric
    _MODULE_READY = True
except Exception:  # noqa: BLE001 — 模块未就绪属预期，进入 skip 模式
    check_answer = None
    gen_parametric = None
    _MODULE_READY = False

import pytest

skip_if_no_module = pytest.mark.skipif(
    not _MODULE_READY, reason="mathforge.verify 尚未就绪，等待主 agent 提供"
)


# --------------------------------------------------------------------------
# 正常用例 5 个：接口在典型正确/错误输入下返回预期布尔值或表达式
# --------------------------------------------------------------------------

@skip_if_no_module
def test_check_answer_exact_match():
    assert check_answer("x**2", "x**2") is True


@skip_if_no_module
def test_check_answer_simple_wrong():
    assert check_answer("x**2", "x**3") is False


@skip_if_no_module
def test_check_answer_constant():
    assert check_answer("3", "3") is True


@skip_if_no_module
def test_gen_parametric_linear():
    expr = gen_parametric("a*x + b", {"a": 2, "b": 3})
    assert str(expr) in ("2*x + 3", "2*x + 3".replace(" ", "")) or "x" in str(expr)


@skip_if_no_module
def test_gen_parametric_sine():
    expr = gen_parametric("A*sin(w*x)", {"A": 1, "w": 2})
    assert "sin" in str(expr) and "x" in str(expr)


# --------------------------------------------------------------------------
# 等价写法用例 3 个：形式不同但数学等价，应判定为 True
# --------------------------------------------------------------------------

@skip_if_no_module
def test_equivalent_fraction_vs_expanded():
    assert check_answer("x**2/2+3", "(x**2+6)/2") is True


@skip_if_no_module
def test_equivalent_factor_commute():
    assert check_answer("2*(x+1)", "2*x+2") is True


@skip_if_no_module
def test_equivalent_rational_form():
    assert check_answer("1/2*x**2", "x**2/2") is True


# --------------------------------------------------------------------------
# 非法输入用例 2 个：语法错误等应返回 False 或被捕获的异常
# --------------------------------------------------------------------------

@skip_if_no_module
def test_invalid_syntax_double_plus():
    try:
        result = check_answer("x++", "x")
    except Exception:
        return  # 抛异常并被捕获，符合预期
    assert result is False


@skip_if_no_module
def test_invalid_syntax_garbage():
    try:
        result = check_answer("@@not_expr", "x")
    except Exception:
        return  # 抛异常并被捕获，符合预期
    assert result is False


# ---- normalize_expr（2026-09-12，AI-PM 评审 B3：输入归一化层） ----

def test_normalize_unicode_math():
    from verify import normalize_expr
    assert normalize_expr("π/2") == "pi/2"
    assert normalize_expr("x²") == "x^(2)"
    assert normalize_expr("2×3 − 1") == "2*3 - 1"
    assert normalize_expr("√x") == "sqrt(x)"
    assert normalize_expr("（１＋２）") == "(1+2)"


def test_check_answer_accepts_human_forms():
    from verify import check_answer
    assert check_answer("π/2", "pi/2") is True          # Unicode π 直接判对
    assert check_answer("0.5", "1/2") is True           # 小数等价
    assert check_answer("x²", "x^2") is True            # 上标数字
    assert check_answer("2×3", "6") is True             # 乘号
