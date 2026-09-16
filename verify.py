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


def normalize_expr(s) -> str:
    """学生输入归一化（2026-09-12，AI-PM 评审 B3）：把人写习惯翻译成 SymPy 可解析形态，
    在 parse_error/check_answer 之前统一调用——从「拦截+教语法」变成「自动修复」。

    覆盖：Unicode 数学符号（π∑√∞≤≥≠±×÷·−）、全角字符、中文标点、
    常见函数名中写（根号）、多余空白。不做语义改写（区间、方程组仍不支持）。
    """
    s = str(s or "")
    import re as _re
    table = {
        "π": "pi", "𝜋": "pi", "∑": "sum",
        "∞": "oo", "≤": "<=", "≥": ">=", "≠": "!=", "±": "+",
        "×": "*", "·": "*", "⋅": "*", "÷": "/", "−": "-", "–": "-", "—": "-",
        "，": ", ", "。": ".", "（": "(", "）": ")", "：": ":",
        "０": "0", "１": "1", "２": "2", "３": "3", "４": "4",
        "５": "5", "６": "6", "７": "7", "８": "8", "９": "9",
        "ｅ": "e", "＋": "+", "－": "-", "＊": "*", "／": "/", "＝": "=",
    }
    for k, v in table.items():
        s = s.replace(k, v)
    # LaTeX 习惯写法支持（复评 S3：题干印什么学生写什么）
    for _ in range(12):  # 迭代展开嵌套 \frac（收敛为止，上限防病态输入）
        new = _re.sub(r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"((\1)/(\2))", s)
        if new == s:
            break
        s = new
    s = _re.sub(r"\\sqrt\s*\{([^{}]*)\}", r"sqrt(\1)", s)
    s = _re.sub(r"\\cdot", "*", s)
    # e^x / e^(...) → exp 形态（sympy 里裸 e 是 Symbol，语义会错）
    # 系数紧贴形态（2e^x）也转换：隐式乘法会把 2exp(x) 解析成 2*exp(x)
    s = _re.sub(r"(?<![A-Za-z_])e\^\(([^()]+)\)", r"exp(\1)", s)
    s = _re.sub(r"(?<![A-Za-z_])e\^\{([^{}]+)\}", r"exp(\1)", s)
    s = _re.sub(r"(?<![A-Za-z_])e\^(-?[A-Za-z0-9]+)", r"exp(\1)", s)
    # 根号：√(...) 与 √原子 → sqrt(...)（普通替换会产出无括号的 sqrtx）
    s = _re.sub(r"√\(([^()]*)\)", r"sqrt(\1)", s)
    s = _re.sub(r"√([^\s()+*/,^]+)", r"sqrt(\1)", s)
    # 上标数字 → ^ 形式（x² → x^(2)）
    sup = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
    if any(ch in s for ch in "⁰¹²³⁴⁵⁶⁷⁸⁹"):
        s = _re.sub(r"([⁰¹²³⁴⁵⁶⁷⁸⁹]+)",
                    lambda m: "^(" + "".join(m.group(0).translate(sup)) + ")", s)
    # 前缀剥除（复评 S3）：题干印「y = ______」「E(X) = ______」时学生会连前缀输入。
    # 仅当等号左侧是纯标识符（字母/数字/括号/下标/集合符号，无运算符）时剥除——
    # "x+y=3" 这类方程不剥。
    if "=" in s:
        lhs, _, rhs = s.partition("=")
        if _re.fullmatch(r"[A-Za-z0-9_（）()\s∪∩≤≥'\u4e00-\u9fa5\u0391-\u03c9]+", lhs or "") and _re.search(
                r"[A-Za-z\u4e00-\u9fa5\u03b1-\u03c9]", lhs or ""):
            s = rhs.strip()
    return s.strip()


def check_answer(student, standard) -> bool:
    """判分：数学等价即正确。异常（语法错误/不可解析/不可化简）一律 False——错杀优于放行。

    矩阵答案：差为全零矩阵即等价（Matrix == 0 是结构比较恒 False，Day3 修复的假阴性根因）。
    双侧先经 normalize_expr 归一化（学生可写 π、×、全角数字等）。
    """
    try:
        a = _parse(normalize_expr(student))
        b = _parse(normalize_expr(standard))
        diff = sp.simplify(a - b)
        if isinstance(diff, sp.MatrixBase):
            return bool(diff == sp.zeros(*diff.shape))
        return bool(diff == 0)
    except Exception:
        return False


def parse_error(s) -> str | None:
    """学生输入可解析性预检：可判分返回 None，否则返回一句人类可读原因。

    用途：提交前拦截"无法识别的表达式"——这类输入判分恒 False 但毫无学习信号，
    不应记入事件流污染记忆（2026-09-12 P0：填空解析预检）。
    判定口径与 check_answer 一致：能 parse 且是可参与 simplify 运算的对象。
    错误文案面向学生（不暴露解释器内部 repr）。输入先经 normalize_expr 归一化。
    """
    s = normalize_expr(s)
    try:
        expr = _parse(s)
    except Exception as e:
        raw = str(e)
        if "unexpected EOF" in raw or "expected" in raw.lower() and "eof" in raw.lower():
            return "无法识别的表达式：看起来没写完（括号或运算符不完整）"
        if "invalid" in raw.lower() or "syntax" in raw.lower():
            return "无法识别的表达式：有识别不了的写法（试试 pi/2、\\frac{a}{b} 这种形式）"
        msg = raw.strip().splitlines()[0][:80] if raw.strip() else "未知解析错误"
        return f"无法识别的表达式：{msg}"
    if not isinstance(expr, sp.Expr):
        # 裸函数名（sin）/元组（"x,"）/矩阵以外的不完整对象——解析成功但无法判分
        return "无法识别的表达式：请输入完整的数学表达式（如 (x+1)^2、sin(x)/x）"
    # 纯文字作答（「不会」「略」）不是数学表达式：拦在事件流之外（学生复评 P0：
    # 此前 SymPy 把中文当 Symbol 名，parse 成功 → 落库成错题污染记忆）
    import re as _re2
    if _re2.search(r"[\u4e00-\u9fa5]", str(s or "")) and not _re2.search(
            r"[0-9]|[A-Za-z]", str(s or "").replace("pi", "").replace("√", "")):
        return "看不懂这题没关系——先「收起来」换一道题，或用拍照识别；作答请输入数学表达式。"
    return None


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
