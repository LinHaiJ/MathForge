"""MathForge 数学验证模块（PRD §7 绿标层：SymPy 确定性验证）。

三层分工中的第三层：SymPy 只做验证，不做决策与生成。
- check_answer(student, standard) -> bool   判分：simplify(学生答 − 标准答) == 0，任何异常返回 False
- gen_parametric(template, params) -> Expr  参数化出题（构造即正确的变式来源）
- is_valid_expr(s) -> bool                  生成侧自检：表达式可解析且无残留自由符号之外的问题
"""

from __future__ import annotations

import re

import sympy as sp
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)

# 宽松解析：支持 2x、x^2 等人写习惯，仍禁止任意代码执行（parse_expr 不走 eval 外部命名空间）
_TRANSFORMS = standard_transformations + (implicit_multiplication_application, convert_xor)


# ---------------------------------------------------------------------------
# 解析器沙箱（模块 L 对抗审查 B1，2026-09-30；复审加固同日）：显式 global_dict，剥离 builtins。
#
# 威胁模型：这个解析器同时吃两路不可信文本——
#   1. 学生作答面：check_answer / parse_error / normalize_expr 直接解析学生键入的字符串；
#   2. LLM 输出面：绿标 answer_expr（generate._gen_green）、重算器 x_expr/y_expr/t0
#      （v2/recompute.recompute）等 schema 字段。
# parse_expr 在不给 global_dict 时内部 exec('from sympy import *') 并把真实 builtins
# 留在 eval 命名空间——`__import__('os').system(...)` / open(...) 会被真实执行
# （审查员已实测：可创建文件）。修法（审查员验证有效）：构建一次 SAFE 字典
# （sympy 全命名空间 + max/min→Max/Min、abs→Abs 人写习惯映射），并显式置空
# __builtins__——eval 出的表达式拿不到 __import__/exec/eval/open/input。
#
# 复审加固（三层纵深，全部经 _parse 唯一入口）：
#   a. pop 字符串求值 shim：sympify/S/parse_expr/lambdify/nsimplify/symarray/var 内部
#      自建命名空间残留真 builtins（复审实测 sympify("__import__('os')…") 真实执行），
#      是绕过本沙箱的逃逸面；plot/preview/test/doctest/python/source 及 plot 变体、
#      textplot 有挂起/GUI/外部进程副作用面。合法数学表达式不需要它们。
#   b. _parse 入口预拒 dunder（__\w+__）：合法数学表达式永不含 dunder，连带封死
#      对象图游走（S.__class__.__init__.__globals__['__builtins__']['__import__'] 等）。
#   c. _parse 入口预拒引号字符串字面量：学生作答与 LLM schema 字段无合法带引号场景，
#      一条规则封死全部「字符串参数型 API」——即使 shim 名以任何途径存活也不可达
#      （内层求值器要吃到载荷必须先有字符串，而字符串只能来自源文本里的引号字面量）。
# 合法表达式语义不变（2x+sin(t) / Rational(3,4) / max(2,3) / abs(-3) 等审查员实测均正常）；
# 沙箱构建一次、只读共享。__builtins__ 置空必须放在 exec 之后（exec 会填入真 builtins）。
# 顺序确认：check_answer/parse_error 先 normalize_expr 后 _parse——normalize 只做记号
# 翻译（全角→ASCII、\frac/\sqrt 展开、e^→exp），从不产生引号或 dunder，故入口预拒
# 在 _parse 内部第一行即可覆盖全部消费方（normalize 并不绕过预拒）。
# ---------------------------------------------------------------------------
def _build_safe_globals() -> dict:
    d: dict = {}
    exec("from sympy import *", d)  # 常量字符串（非用户输入），仅用于装配沙箱命名空间
    d["max"] = d.get("Max")   # builtins 清空后人写习惯兜底（语义等价：max(2,3)→Max(2,3)）
    d["min"] = d.get("Min")
    d["abs"] = d.get("Abs")   # 学生常写 abs(x)：映射 Abs，避免退化成隐式乘法 a*b*s*x
    d["__builtins__"] = {}    # 关键一步：eval 命名空间剥离全部 builtins
    # 复审 a：字符串求值 shim 与副作用面可调用一律 pop——名字离开命名空间后，
    # split_symbols 变换把未知多字母名拆成单字母符号乘积（如 plot(x)→l*o*p*t*x）：
    # 纯无害表达式，永不发生任何调用与内层求值（即便载荷经某途径构造出字符串，
    # 也没有可调用的 shim 可达——双保险）
    for _name in ("sympify", "S", "parse_expr", "lambdify", "nsimplify", "symarray",
                  "var",  # var 经 inspect 向调用方命名空间注入符号（副作用面）
                  "plot", "plot_backends", "plot_implicit", "plot_parametric",
                  "plotting", "textplot", "preview",
                  "test", "doctest", "python", "source"):
        d.pop(_name, None)
    return d


_SAFE_GLOBALS = _build_safe_globals()

# 复审 b/c：_parse 入口预拒模式（合法数学表达式永不命中）
_DUNDER_RE = re.compile(r"__\w+__")


def _parse(s) -> sp.Expr:
    """字符串 -> SymPy 表达式；解析失败抛异常，由调用方决定语义。

    global_dict 用显式沙箱 _SAFE_GLOBALS（sympy 命名空间、__builtins__ 置空、字符串
    求值 shim 已 pop）——默认命名空间带真实 builtins，学生/LLM 文本可借 __import__/
    eval/sympify/S/parse_expr 执行任意代码（威胁模型与三层纵深见沙箱注释块）。
    入口预拒（第一行，先于 normalize 产物流入 parse_expr；全部消费方唯一入口）：
    dunder 与引号字面量直接抛人话异常。合法表达式语义不变。
    """
    s = str(s)
    if _DUNDER_RE.search(s):
        raise ValueError("表达式含非法记号：内部属性（__xxx__）不允许出现在数学表达式中")
    if "'" in s or '"' in s:
        raise ValueError("表达式含非法字符：引号字符串不允许出现在数学表达式中")
    return parse_expr(s, transformations=_TRANSFORMS, global_dict=_SAFE_GLOBALS)


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
