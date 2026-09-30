# 模块 L：求导类重算器试点（参数方程求导 kp，2026-09-29）
#
# 背景：用户本地实测「参数方程求导」考点——AI 变式被拒（提示「需先建族」）、降级同源换数、
# 且问法裸（"求 dy/dx"）。考研真题几乎不裸考：会包装成指定点二阶导/切线/法线/曲率等形态。
# 本模块把「重算器」从「族」里解耦（第一个试点）：LLM 只填结构化 schema 与题面包装，
# 答案一律由 SymPy 从 schema 重算给出——schema-gated 的「包装过的、答案可验证的」AI 变式。
#
# 红线（判分权 100% SymPy）：
#   1. LLM 的 claimed_answer 不采信，只作对账参考；标准答案 = recompute() 的重算结果。
#   2. 表达式解析同 verify._parse 纪律：parse_expr 不走 eval 外部命名空间，且自由符号
#      只允许自变量 t——LLM 写不出的记号根本进不了答案。
#   3. 盲解对账与拦截语义与绿标链（generate._gen_green）逐条同构：PREMISE_BROKEN 前提
#      审查、FAKE_KP 问法合规闸门（模块 J，kp_label 注入）、首盲不一致 → verify_mode
#      复核仲裁 → 仍不一致拒绝。不复用 _gen_green 本体的原因：绿标契约是
#      statement_template+params+answer_expr（参数化模板 machinery），本链契约是
#      x_expr/y_expr/t0/target schema——schema 本身就是数学内核，无参数代入步；
#      复用会迫使 schema 穿过模板机制、模糊「LLM 只填结构化字段」的边界。
#      对账/拦截语义一致性由本模块逐条镜像保证（见 generate_recompute_variant 注释）。
#   4. 失败语义：重算失败/退化 → 重生成 1 次（独立缓存命名空间 a2）→ 仍败 →
#      blocked_pending_human（人话原因），绝不外推。
#   5. demo 态：调用方（v2api）在 demo 下不进入本链（维持 demo 拒绝，载荷逐字节不变）。
#
# 依赖方向：本模块不 import v2api（v2api → recompute 单向，同 ai_variant 纪律）。

from __future__ import annotations

import re

import sympy as sp

from generate import _FAKE_KP_ERROR, _blind_solve, _is_fake_kp, _kp_gate_label, render_flaws
from llm import chat_json
from verify import _parse, check_answer

# 生成缓存命名空间（attempt 分 a1/a2：重生成真的换缓存键，而非重放同一载荷）。
# 载荷文本（风格指引/契约）变化 → hash 变化 → 旧缓存自然失效；代次 bump 惯例同 generate。
_NS_RECOMPUTE = "recompute-deriv:v1:a{}:"

# 问目标枚举（定案）：5 个问目标各配一个 SymPy 计算函数（_TARGET_COMPUTE）。
# 难度档（与 v2 三档难度阶梯对齐，定案）：基础=dydx；进阶=d2ydx2/tangent/normal；综合=curvature。
_TARGET_ORDER = ("dydx", "d2ydx2", "tangent", "normal", "curvature")
_TARGET_LABEL = {
    "dydx": "dydx（求 dy/dx，可指定点）",
    "d2ydx2": "d2ydx2（求二阶导 d²y/dx²，主流形态为指定点代入）",
    "tangent": "tangent（求 t=t0 对应点处的切线方程）",
    "normal": "normal（求 t=t0 对应点处的法线方程）",
    "curvature": "curvature（求 t=t0 对应点处的曲率）",
}
_DIFFICULTY_TARGETS = {
    "基础": ("dydx",),
    "进阶": ("d2ydx2", "tangent", "normal"),
    "综合": ("curvature",),
}


class _Degenerate(ValueError):
    """退化/无定义（人话原因进 ok:false 的 reason，触发调用方重生成）。"""


def _brief(e: BaseException, limit: int = 120) -> str:
    """异常单行摘要（截断），拼进人话 reason。

    m1（对抗审查）：OSError 系（含 FileNotFoundError/PermissionError 等子类）只留
    类型名——其消息常含服务器绝对路径/文件名，泄漏进 reason 会打到前端与值班侧
    （内部细节留给服务端日志）。其余异常保留消息（人话诊断价值）。"""
    name = type(e).__name__
    if isinstance(e, OSError):
        return name
    return " ".join(f"{name}: {e}".split())[:limit]


# --------------------------------------------------------------------------- #
# 5 个问目标的 SymPy 计算函数（签名统一 (xt, yt, t, t0) → sympy 表达式）
#
# 等价判定口径（判分用 verify.check_answer，即 simplify(学生−标准)==0）：
#   - dydx / d2ydx2：t0 给定 → 该点数值；t0=None → t 的表达式（题面须自含 x'≠0 条件）。
#   - tangent / normal：斜截式 y = k*x + b（以 Symbol('x') 表达的标准答案）。
#     学生/盲解写 "y=2x+1" 经 verify.normalize_expr 剥 "y=" 前缀后与 k*x+b 判等；
#     一般式 Ax+By+C=0 不在试点判分口径内（analysis_hint 引导答案用斜截式表示）。
#     竖直切线（x'(t0)=0）/竖直法线（y'(t0)=0）无法用斜截式表示 → 退化不出（试点口径）。
#   - curvature：非负数值（κ 恒 ≥ 0，内部守卫断言）。
# --------------------------------------------------------------------------- #
def _eval_at(expr, t, t0):
    """代入 t0 并做数值健全性检查；未得到确定实数值 → _Degenerate（人话原因）。"""
    v = sp.simplify(expr.subs(t, t0))
    if v.free_symbols:
        raise _Degenerate(f"在 t={t0} 处未得到确定数值（含自由符号）")
    if v.is_real is not True:  # zoo/nan/复值一律拒绝（无定义或非实值）
        raise _Degenerate(f"在 t={t0} 处表达式无定义或非实值（如分母为零）")
    return v


def _t_dydx(xt, yt, t, t0):
    """参数方程求导 dy/dx = y'(t)/x'(t)。t0 给定 → 该点数值；None → t 的表达式。"""
    dx = sp.diff(xt, t)
    if sp.simplify(dx) == 0:
        raise _Degenerate("x'(t) ≡ 0（x 为常数），dy/dx 无定义")
    dydx = sp.simplify(sp.diff(yt, t) / dx)
    if t0 is None:
        return dydx
    if dx.subs(t, t0) == 0:
        raise _Degenerate(f"x'({t0})=0，该点 dy/dx 无定义（切线竖直），请换目标点")
    # M1（对抗审查）：t0 必须落在曲线上——x(t0)/y(t0) 本身须有定义（如 log(t) 在 t0=-1）
    _eval_at(xt, t, t0)
    _eval_at(yt, t, t0)
    return _eval_at(dydx, t, t0)


def _t_d2ydx2(xt, yt, t, t0):
    """二阶导 d²y/dx² = d/dt(dy/dx) / x'(t)——除以 x'(t) 而非 d²x/dt²
    （锚池该考点 pitfalls 首条「二阶导忘除以 x'(t)」，重算器天然不犯）。"""
    dx = sp.diff(xt, t)
    if sp.simplify(dx) == 0:
        raise _Degenerate("x'(t) ≡ 0（x 为常数），d²y/dx² 无定义")
    dydx = sp.simplify(sp.diff(yt, t) / dx)
    d2 = sp.simplify(sp.diff(dydx, t) / dx)
    if t0 is None:
        return d2
    if dx.subs(t, t0) == 0:
        raise _Degenerate(f"x'({t0})=0，该点 d²y/dx² 无定义（切线竖直），请换目标点")
    # M1（对抗审查）：t0 必须落在曲线上——x(t0)/y(t0) 本身须有定义
    _eval_at(xt, t, t0)
    _eval_at(yt, t, t0)
    return _eval_at(d2, t, t0)


def _t_tangent(xt, yt, t, t0):
    """t0 处切线：点 (x(t0), y(t0))，斜率 k=y'/x'，点斜式展开为 k*x + (y0−k*x0)（斜截式）。
    x'(t0)=0 → 切线竖直 → 退化不出（斜截式表示不了，试点口径）。"""
    if t0 is None:
        raise _Degenerate("切线方程需要具体点 t0（题干须指定 t=t0）")
    dx0 = sp.diff(xt, t).subs(t, t0)
    dy0 = sp.diff(yt, t).subs(t, t0)
    if dx0 == 0 and dy0 == 0:
        raise _Degenerate(f"x'({t0})=y'({t0})=0：曲线在该点不光滑（奇点），切线无定义")
    if dx0 == 0:
        raise _Degenerate(f"x'({t0})=0：切线竖直，无法写成 y=k*x+b（试点口径不出）")
    x0 = _eval_at(xt, t, t0)
    y0 = _eval_at(yt, t, t0)
    k = _eval_at(sp.simplify(sp.diff(yt, t) / sp.diff(xt, t)), t, t0)
    return sp.expand(k * (sp.Symbol("x") - x0) + y0)


def _t_normal(xt, yt, t, t0):
    """t0 处法线：斜率 k_n = −x'(t0)/y'(t0)（由 k_t=y'/x' 取负倒数化简，避免除以 0 的
    两步写法），点斜式展开为斜截式。y'(t0)=0 → 切线水平、法线竖直 → 退化不出。
    注：x'(t0)=0 且 y'(t0)≠0 时切线竖直、法线水平 y=y0——斜截式可表示（k_n=0），照常出题。"""
    if t0 is None:
        raise _Degenerate("法线方程需要具体点 t0（题干须指定 t=t0）")
    dx0 = sp.diff(xt, t).subs(t, t0)
    dy0 = sp.diff(yt, t).subs(t, t0)
    if dx0 == 0 and dy0 == 0:
        raise _Degenerate(f"x'({t0})=y'({t0})=0：曲线在该点不光滑（奇点），法线无定义")
    if dy0 == 0:
        raise _Degenerate(f"y'({t0})=0：法线竖直，无法写成 y=k*x+b（试点口径不出）")
    x0 = _eval_at(xt, t, t0)
    y0 = _eval_at(yt, t, t0)
    kn = _eval_at(sp.simplify(-sp.diff(xt, t) / sp.diff(yt, t)), t, t0)
    return sp.expand(kn * (sp.Symbol("x") - x0) + y0)


def _t_curvature(xt, yt, t, t0):
    """曲率 κ = |x'y'' − y'x''| / (x'²+y'²)^(3/2)，在 t0 处取值（恒 ≥ 0）。
    分母为零（x'=y'=0 奇点）→ 退化；结果非实数/非数值 → 退化。"""
    if t0 is None:
        raise _Degenerate("曲率需要具体点 t0（题干须指定 t=t0）")
    # M1（对抗审查）：t0 必须落在曲线上——x(t0)/y(t0) 本身须有定义
    _eval_at(xt, t, t0)
    _eval_at(yt, t, t0)
    dx0 = sp.diff(xt, t).subs(t, t0)
    dy0 = sp.diff(yt, t).subs(t, t0)
    d2x0 = sp.diff(xt, t, 2).subs(t, t0)
    d2y0 = sp.diff(yt, t, 2).subs(t, t0)
    denom0 = sp.simplify((dx0**2 + dy0**2) ** sp.Rational(3, 2))
    num0 = sp.simplify(dx0 * d2y0 - dy0 * d2x0)
    if denom0.is_real is not True or denom0 == 0:
        raise _Degenerate(f"x'({t0})=y'({t0})=0（或分母无定义）：该点曲率无定义")
    kap = sp.simplify(sp.Abs(num0) / denom0)
    if kap.free_symbols:
        raise _Degenerate(f"在 t={t0} 处未得到确定曲率值")
    if kap.is_real is not True:
        raise _Degenerate(f"在 t={t0} 处曲率无定义（表达式非实值）")
    if kap.is_number and kap < 0:  # 曲率非负守卫（Abs 保证，不应触发；触发即内部错误）
        raise _Degenerate("曲率为负（内部守卫，不应发生）")
    return kap


_TARGET_COMPUTE = {
    "dydx": _t_dydx,
    "d2ydx2": _t_d2ydx2,
    "tangent": _t_tangent,
    "normal": _t_normal,
    "curvature": _t_curvature,
}

_REQUIRED_FIELDS = ("x_expr", "y_expr", "target", "statement_md")

# M2（对抗审查）：切线/法线判分口径是斜截式 y=kx+b（等价判定见 _t_tangent/_t_normal
# docstring）——学生写点斜式/一般式（数学正确）会被判错，故题干必须自带「斜截式」
# 引导，缺失则打回重生成（prompt 硬约束同款要求，重试时 LLM 能看到）。
_SLOPE_INTERCEPT_HINT = re.compile(r"斜截式|y\s*=\s*k\s*\*?\s*[x·]\s*\+\s*b")

# 盲解作答格式提示（拼在题干后进盲解 user 消息，不进题面；保证对账口径一致）。
_SOLVE_FORMAT = {
    "dydx": "\n（作答格式：题干指定点则输出该点数值；未指定点则输出 t 的 SymPy 表达式）",
    "d2ydx2": "\n（作答格式：题干指定点则输出该点数值；未指定点则输出 t 的 SymPy 表达式）",
    "tangent": "\n（作答格式：切线方程写成斜截式，只输出形如 2*x+1 的 k*x+b 表达式）",
    "normal": "\n（作答格式：法线方程写成斜截式，只输出形如 2*x+1 的 k*x+b 表达式）",
    "curvature": "\n（作答格式：输出曲率数值，根号用 sqrt()、分数用 Rational(a,b) 或 a/b）",
}


# --------------------------------------------------------------------------- #
# 重算器注册表（按 kp 注册；本批只注册「参数方程求导」，结构支持后续 kp 扩展：
# 新 kp = 新 spec dict（自带 system_prompt/难度档）+ 一个注册键，计算函数按需复用/新增）
# --------------------------------------------------------------------------- #
# 【包装形态风格指引的统计依据（L3，cache/anchor_pool.jsonl 真题锚池，2026-09-29 统计）】
# 全池 1614 道；"参数方程"宽匹配（kp/statement/answer/analysis 任一命中）17 道，其中
# 参数方程求导问法（求 dy/dx 或 d²y/dx²）14 道，形态分布：
#   二阶导指定点代入 |_{t=t0}：8 道（cxy-1539/1533/1544/8326/1548/1541/1537/8369）——主流；
#   二阶导裸问：4 道（cxy-1530/1527/10804/1524）；一阶导指定点：1 道（cxy-1543）；
#   一阶导裸问（方程组形态）：1 道（cxy-1542）。
# 另：参数方程曲线曲率 1 道（cxy-8394，2018数二星形线 t=π/4 处曲率）；
#     参数方程曲线法线 1 道（cxy-10841，t=π/4 对应点处法线斜率）；
#     全池"点处切线/法线方程"问法 17 道（如 cxy-10783 隐函数曲线点处切线方程）。
# —— 裸问存在但非主流，真题最稳定的包装维度是「指定点代入」，故 prompt 风格指引
#    要求尽量指定具体点；5 个问目标均有（或紧邻）真题形态依据。
_PARAM_DERIV_SYSTEM = """你是考研数学命题人。任务：为「参数方程求导」考点出一道【包装过的】填空变式题——\
考研真题几乎不裸考"求 dy/dx"，会包装成指定点二阶导/切线/法线/曲率等形态（见风格指引）。\
你只负责设计数学内容与题面包装：输出结构化 schema，标准答案由系统用 SymPy 从 schema 重算得出\
——不要试图给出最终答案（claimed_answer 仅供对账参考，系统不采信）。

【输出 schema（只输出一个 JSON 对象；除注明外全部必填）】
{
  "x_expr": "x(t) 的 SymPy 表达式（自变量必须是 t；初等函数：多项式/分式/根式/三角/反三角/exp/ln）",
  "y_expr": "y(t) 的 SymPy 表达式（同上）",
  "t0": 具体点数值或 null（null 仅允许 dydx/d2ydx2 求通式；tangent/normal/curvature 必须给出，可写 pi/4 等精确值）,
  "target": "dydx | d2ydx2 | tangent | normal | curvature 五选一",
  "statement_md": "完整题干（中文叙述+行内 LaTeX $...$；显式给出参数方程 x=x(t), y=y(t)；按问目标包装问法；条件自含；单题单问单空）",
  "analysis_hint": "解析要点 1-3 句（点明参数方程求导公式与关键步骤；切线/法线答案注明用斜截式 y=kx+b 表示）",
  "claimed_answer": "你自己算出的答案（仅对账参考）"
}

【命题硬约束】
1. x_expr/y_expr 必须是显式初等表达式——禁止变限积分、隐方程、方程组定义（重算器边界）。
2. t0 必须使 x'(t0)≠0；normal 还须 y'(t0)≠0；curvature 须 x'(t0)²+y'(t0)²≠0；
   且 x(t0)、y(t0) 本身有定义（t0 必须落在曲线上）。
3. 数值自然：系数取 1~5 的整数或简分数；t0 常取 0、1、2、pi/6、pi/4 等整洁值，且代入后式子自然。
4. 禁止在题干泄露答案或解题步骤；禁止"（xx 为常数）"元注释；不得出现渲染瑕疵（如 "+ -3x"、"1x^2"、"+ 0"）。
5. target 为 tangent/normal 时，题干必须注明「结果用斜截式 y=kx+b 表示」——系统按斜截式判分，
   缺注明会被打回（否则点斜式/一般式等正确作答无法识别）。

【包装形态风格指引（来自 cache/anchor_pool.jsonl 真题锚池，2026-09-29 统计：全池 1614 道；
"参数方程"宽匹配 17 道，其中参数方程求导问法 14 道）】
- 主流：二阶导 d²y/dx² 且指定点代入 |_{t=t0}（8/14，如 2021数一 x=2e^t+t+1, y=4(t-1)e^t+t² |_{t=0}）；
  二阶导裸问 4/14；一阶导指定点 1/14；一阶导裸问 1/14——尽量指定具体点，不裸考。
- 曲率：参数方程曲线曲率 1 道（2018数二：x=cos³t, y=sin³t 在 t=π/4 对应点处的曲率）。
- 切法线：参数方程曲线法线 1 道（t=π/4 对应点处法线斜率）；全池"点处切线/法线方程"问法 17 道
  ——切线/法线务必包装成「曲线在 t=t0 对应点处的切线/法线方程」。
- 题干范式（模仿锚池表述）：「设函数 $y=y(x)$ 由参数方程 $\\begin{cases}x=…,\\\\ y=…\\end{cases}$ 所确定，
  则 <包装问法> = ____。」

只输出 JSON。"""

_PARAM_DERIV_SPEC = {
    "kp": "参数方程求导",
    "kp_id": "calc.derivative.param",
    "targets": _TARGET_ORDER,
    # 难度档 → 允许问目标（定案，与 v2 三档难度阶梯对齐）：
    # 基础=dydx（一阶导，锚池有指定点/裸问两形态）；进阶=d2ydx2/tangent/normal
    # （锚池主流形态二阶导指定点属此档，切法线同档）；综合=curvature（需二阶导+公式复合）。
    # 未知难度值按进阶处理（中间档，LLM 可在三形态中择一）。
    "difficulty_targets": _DIFFICULTY_TARGETS,
    "system_prompt": _PARAM_DERIV_SYSTEM,
}

# 双键注册（中文 kp 名 / kp_graph kp_id 均可命中）：同一 spec 对象。
RECOMPUTE_SPECS: dict[str, dict] = {
    "参数方程求导": _PARAM_DERIV_SPEC,
    "calc.derivative.param": _PARAM_DERIV_SPEC,
}


def spec_for_kp(kp_id: str | None, kp_name: str | None = None) -> dict | None:
    """按 kp_id 或中文 kp 名查重算器注册表；未注册 → None（调用方走旧路径，行为不变）。"""
    for key in (kp_id, kp_name):
        if key and key in RECOMPUTE_SPECS:
            return RECOMPUTE_SPECS[key]
    return None


def _targets_for_difficulty(spec: dict, difficulty: str) -> tuple[str, ...]:
    """难度档 → 允许问目标；未知难度值回落进阶档（中间档，见 spec 注释）。"""
    mapping = spec.get("difficulty_targets") or _DIFFICULTY_TARGETS
    return mapping.get(difficulty) or mapping["进阶"]


# --------------------------------------------------------------------------- #
# L1 核心：recompute()——schema 校验 → SymPy 重算 → 答案
# --------------------------------------------------------------------------- #
def recompute(payload) -> dict:
    """求导类重算器（任务书名 spec 的入参即 LLM 输出的 schema，此处称 payload）。

    入参（schema 契约）：{x_expr, y_expr, t0, target, statement_md, analysis_hint, claimed_answer?}
    出参：{ok: True, answer_sympy, target, t0, x_expr, y_expr, statement_md, analysis_hint,
           claimed_answer}
       或 {ok: False, reason: 人话原因}——任何解析失败/退化（x'(t0)=0 切线竖直、x'≡0、
       奇点、t0 处无定义）都给人话原因，由生成链重生成。

    解析纪律：x_expr/y_expr/t0 经 verify._parse（parse_expr 不走 eval 外部命名空间），
    且自由符号只允许自变量 t——杜绝 LLM 引入未知符号进答案。
    """
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "schema 不是 JSON 对象（重算器需要结构化字段）"}
    missing = [k for k in _REQUIRED_FIELDS if not str(payload.get(k) or "").strip()]
    if missing:
        return {"ok": False,
                "reason": f"schema 缺字段：{'、'.join(missing)}"
                          f"（重算器需要 {'/'.join(_REQUIRED_FIELDS)}）"}
    target = str(payload["target"]).strip()
    if target not in _TARGET_COMPUTE:
        return {"ok": False,
                "reason": f"未知问目标 {target}（允许：{'/'.join(_TARGET_ORDER)}）"}

    t = sp.Symbol("t")
    try:
        xt = _parse(payload["x_expr"])
        yt = _parse(payload["y_expr"])
        # B2（对抗审查）：_parse 对列表/元组/等式等输入返回非 Expr（Python list/tuple/Eq），
        # 直接取 free_symbols 会 AttributeError 逃出 ok:false 契约 → 显式守卫人话化
        if not isinstance(xt, sp.Expr) or not isinstance(yt, sp.Expr):
            return {"ok": False,
                    "reason": "表达式无法解析：x_expr/y_expr 必须是单个数学表达式"
                              "（不能是列表、元组或方程）"}
        stray = (xt.free_symbols | yt.free_symbols) - {t}
        if stray:
            return {"ok": False,
                    "reason": f"表达式含未知符号 {sorted(str(s) for s in stray)}"
                              f"（自变量只允许 t）"}
    except Exception as e:  # noqa: BLE001 —— 解析失败给人话原因，由调用方重生成
        return {"ok": False,
                "reason": f"表达式无法解析：{_brief(e)}（只支持 SymPy 可解析的初等表达式）"}

    raw_t0 = payload.get("t0")
    t0 = None
    if raw_t0 is not None and str(raw_t0).strip() != "":
        try:
            t0 = _parse(raw_t0) if isinstance(raw_t0, str) else sp.nsimplify(raw_t0, rational=True)
        except Exception:  # noqa: BLE001
            return {"ok": False, "reason": f"t0 无法解析为数值：{raw_t0}（可写 0、1、pi/4 等）"}
        if not isinstance(t0, sp.Expr) or t0.free_symbols:
            return {"ok": False, "reason": f"t0 不是数值：{raw_t0}（可写 0、1、pi/4 等）"}
        if t0.is_real is False:
            return {"ok": False, "reason": f"t0 不是实数：{raw_t0}"}

    need_point = target in ("tangent", "normal", "curvature")
    if need_point and t0 is None:
        return {"ok": False,
                "reason": f"问目标 {target} 需要具体点 t0（题干须指定 t=t0）"}

    try:
        answer = _TARGET_COMPUTE[target](xt, yt, t, t0)
    except _Degenerate as e:
        return {"ok": False, "reason": f"退化：{e}"}
    except Exception as e:  # noqa: BLE001 —— SymPy 计算异常（如病态输入）按重算失败处理
        return {"ok": False, "reason": f"计算失败：{_brief(e)}"}

    answer = sp.simplify(answer)
    # 指定点代入的 dydx/d2ydx2 必须是确定实数值（curvature 已在 _t_curvature 内自检；
    # tangent/normal 的答案是斜截式 y=k*x+b，含 Symbol('x') 属预期，不做数值检查）
    if target in ("dydx", "d2ydx2") and t0 is not None:
        if answer.free_symbols or answer.is_real is not True:
            return {"ok": False, "reason": f"在 t={t0} 处未得到确定实数值（该点可能无定义）"}

    statement = str(payload["statement_md"]).strip()
    if not (10 <= len(statement) <= 600):
        return {"ok": False, "reason": f"题干长度异常（{len(statement)} 字，需 10~600）"}
    if statement.count("$") % 2 != 0:
        return {"ok": False, "reason": "题干 LaTeX $ 未配对"}
    if statement.count("{") != statement.count("}"):
        return {"ok": False, "reason": "题干花括号未配对"}
    flaw = render_flaws(statement)
    if flaw:
        return {"ok": False, "reason": f"题干渲染瑕疵（{flaw}）"}
    # M2（对抗审查）：切线/法线判分只认斜截式 y=kx+b——题干缺「斜截式」引导则正确的
    # 点斜式/一般式作答会被误判，打回重生成（人话原因说明为何必须注明）
    if target in ("tangent", "normal") and not _SLOPE_INTERCEPT_HINT.search(statement):
        return {"ok": False,
                "reason": "切线/法线答案按斜截式 y=kx+b 判分：题干须注明"
                          "「结果用斜截式 y=kx+b 表示」，否则点斜式/一般式等正确作答会被误判"}

    return {
        "ok": True,
        "answer_sympy": str(answer),
        "target": target,
        "t0": str(t0) if t0 is not None else None,
        "x_expr": str(payload["x_expr"]).strip(),
        "y_expr": str(payload["y_expr"]).strip(),
        "statement_md": statement,
        "analysis_hint": str(payload.get("analysis_hint") or "").strip(),
        # claimed_answer 仅存档对账参考，绝不参与判分（判分权 100% SymPy）
        "claimed_answer": str(payload.get("claimed_answer") or "").strip() or None,
    }


# --------------------------------------------------------------------------- #
# L2 生成链：LLM 填 schema → recompute() 重算 → 盲解对账（语义与绿标链逐条同构）
# --------------------------------------------------------------------------- #
def _user_prompt(spec: dict, difficulty: str, memory_context: str | None) -> str:
    targets = _targets_for_difficulty(spec, difficulty)
    parts = [
        f"知识点：{spec['kp']}（{spec['kp_id']}）",
        f"难度目标：{difficulty}（问目标限定：{'；'.join(_TARGET_LABEL[x] for x in targets)}）",
    ]
    # 模块 F3 同款纪律：记忆块非空才追加（空/缺失时 user 消息与无记忆版逐字节一致，
    # 缓存键稳定）；非 str 值忽略。
    if isinstance(memory_context, str) and memory_context.strip():
        parts.append(memory_context.strip())
    return "\n".join(parts)


def _one_attempt(spec: dict, difficulty: str, memory_context: str | None, attempt: int) -> dict:
    """单次尝试：生成 → 重算 → 盲解对账。任何一环不过即抛异常（外层走重生成链）。

    对账/拦截语义与 generate._gen_green 逐条同构（FAKE_KP 语义复用）：
      ① 盲解 system 注入「问法合规检查」（kp_label 非空时，模块 J 口径——demo 恒 None）；
      ② PREMISE_BROKEN → 前提矛盾拒绝；
      ③ FAKE_KP（判定性结论）→ 不做 verify_mode 复审，直接按 _FAKE_KP_ERROR 拒绝重生成；
      ④ 首盲不一致 → verify_mode 复核仲裁一次 → 仍不一致拒绝；
    区别仅在「构造侧」：绿标由参数化模板构造，本链由 schema 重算——判分权同为 SymPy。
    """
    d = chat_json(
        [{"role": "system", "content": spec["system_prompt"]},
         {"role": "user", "content": _user_prompt(spec, difficulty, memory_context)}],
        temperature=1.0,  # 与绿标出题一致
        namespace=_NS_RECOMPUTE.format(attempt))

    r = recompute(d)  # 生成后立即重算：答案由 SymPy 给出，claimed_answer 不采信
    if not r.get("ok"):
        raise ValueError(r.get("reason") or "重算未通过")
    statement = r["statement_md"]
    answer = r["answer_sympy"]

    kp_label = _kp_gate_label({"kp": spec["kp"]})
    fmt = _SOLVE_FORMAT.get(r["target"], "")
    solver_ans, raw = _blind_solve(statement + fmt, None, kp_label=kp_label)
    if solver_ans == "PREMISE_BROKEN":
        raise ValueError(f"盲解判定题目前提矛盾：{statement[:80]}")
    if _is_fake_kp(solver_ans):
        # 问法合规闸门（模块 J 语义复用）：判定性结论不复审，直接重生成
        raise ValueError(_FAKE_KP_ERROR)
    agree = solver_ans is not None and check_answer(solver_ans, answer)
    if not agree:
        # 复核仲裁：重算侧由 SymPy 背书，盲解偶发算错时给第二次独立求解机会
        solver_ans2, _raw2 = _blind_solve(statement + fmt, None, verify_mode=True,
                                          kp_label=kp_label)
        if _is_fake_kp(solver_ans2):
            raise ValueError(_FAKE_KP_ERROR)
        agree = solver_ans2 is not None and check_answer(solver_ans2, answer)
        raw += f" | 复核解={solver_ans2}"
    if not agree:
        raise ValueError(f"盲解与重算答案不一致（solver={solver_ans}, sympy={answer}）")

    q = {
        "kp": spec["kp"], "difficulty": difficulty,
        "qtype": "calculation", "verify_level": "green",
        "statement_md": statement, "answer_sympy": answer, "answer_md": None,
        "options": None, "correct": None,
        "analysis": r["analysis_hint"], "design_note": str(d.get("design_note") or ""),
        "params": {}, "routed": "recompute",
        "recompute": {"target": r["target"], "t0": r["t0"], "x_expr": r["x_expr"],
                      "y_expr": r["y_expr"], "claimed_answer": r.get("claimed_answer")},
        "verification": {"solver_agree": True, "solver_raw": raw[:200]},
    }
    # 与绿标同纪律：以最终题干+重算答案重写解析（generate._instantiate_analysis，
    # 走 "analysis:" 命名空间），杜绝解析与实例脱节。
    q["analysis"] = generate_instantiate_analysis(q)
    return q


def generate_instantiate_analysis(q: dict) -> str:
    """generate._instantiate_analysis 的透传（延迟引用，便于测试打桩与依赖方向清晰）。"""
    from generate import _instantiate_analysis
    return _instantiate_analysis(q)


def generate_recompute_variant(spec: dict, difficulty: str = "进阶",
                               memory_context: str | None = None) -> dict:
    """重算器出题主链（在线态专用；demo 由调用方拦截，不进入本函数）。

    失败重生成 1 次 → 仍败 → blocked_pending_human（镜像 generate.generate_question 的
    重试链；重试靠独立缓存命名空间 a1/a2 真正重新生成，而非重放同一载荷）。
    返回题目 dict（_gen_green 输出同构 + recompute 溯源块 + routed="recompute"），
    或 {status: "blocked_pending_human", error: 人话原因, ...}。
    """
    last_err: Exception | None = None
    for attempt in (1, 2):
        try:
            q = _one_attempt(spec, difficulty, memory_context, attempt)
            q["attempt"] = attempt
            return q
        except Exception as e:  # noqa: BLE001 —— 验证失败走重生成链（推题纪律）
            last_err = e
    return {
        "kp": spec["kp"], "difficulty": difficulty, "qtype": "calculation",
        "verify_level": "green", "statement_md": None, "routed": "recompute",
        "status": "blocked_pending_human", "error": str(last_err), "attempt": 2,
    }
