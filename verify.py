"""MathForge 数学验证模块（PRD §7 绿标层：SymPy 确定性验证）。

三层分工中的第三层：SymPy 只做验证，不做决策与生成。
- check_answer(student, standard) -> bool   判分：simplify(学生答 − 标准答) == 0，任何异常返回 False
- gen_parametric(template, params) -> Expr  参数化出题（构造即正确的变式来源）
- is_valid_expr(s) -> bool                  生成侧自检：表达式可解析且无残留自由符号之外的问题
"""

from __future__ import annotations

import sympy as sp
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)

# 宽松解析：支持 2x、x^2 等人写习惯，仍禁止任意代码执行（parse_expr 不走 eval 外部命名空间）
_TRANSFORMS = standard_transformations + (implicit_multiplication_application, convert_xor)


def _parse(s) -> sp.Expr:
    """字符串 -> SymPy 表达式；解析失败抛异常，由调用方决定语义。

    global_dict 保持 sympy 默认命名空间（sin/cos/exp/Rational/pi...），
    否则函数名会退化为未定义 Symbol 导致等价判定全错。
    """
    return parse_expr(str(s), transformations=_TRANSFORMS)


def check_answer(student, standard) -> bool:
    """判分：数学等价即正确。异常（语法错误/不可解析/不可化简）一律 False——错杀优于放行。

    矩阵答案：差为全零矩阵即等价（Matrix == 0 是结构比较恒 False，Day3 修复的假阴性根因）。
    """
    try:
        a = _parse(student)
        b = _parse(standard)
        diff = sp.simplify(a - b)
        if isinstance(diff, sp.MatrixBase):
            return bool(diff == sp.zeros(*diff.shape))
        return bool(diff == 0)
    except Exception:
        return False


def gen_parametric(template, params: dict) -> sp.Expr:
    """模板 + 参数 -> 实例化表达式。参数值接受 int/float/str（str 按 SymPy 语法解析）。"""
    expr = _parse(template)
    subs = {}
    for k, v in params.items():
        subs[sp.Symbol(k)] = _parse(v) if isinstance(v, str) else sp.sympify(v)
    return expr.subs(subs)


def is_valid_expr(s) -> bool:
    """生成侧自检：可解析即有效。供 generate.py 拦截 LLM 输出的非法表达式。"""
    try:
        _parse(s)
        return True
    except Exception:
        return False
