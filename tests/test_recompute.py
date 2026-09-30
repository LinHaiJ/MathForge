# 模块 L（求导类重算器试点：参数方程求导 kp）测试。
#
# 覆盖：
#   重算器单元（v2/recompute.recompute，零 LLM）：5 个问目标各 ≥2 例（已知答案手算对照）；
#     退化（x'≡0 / 切线竖直 / 法线竖直 / 奇点 / t0 处无定义）、解析失败（坏表达式 /
#     未知符号 / t0 非数值）、渲染瑕疵与题干守卫、schema 缺字段人话报错；
#     claimed_answer 不参与判分只存档（判分权 100% SymPy）
#   注册表与难度档：RECOMPUTE_SPECS 双键（中文名/kp_id）、spec_for_kp 未注册 → None；
#     基础=dydx / 进阶=d2ydx2+tangent+normal / 综合=curvature（未知难度回落进阶）
#   prompt 风格指引：硬编码锚池真题统计（来源 cache/anchor_pool.jsonl + 日期 2026-09-29）
#     与 schema 契约字段——「包装问法必须有真题对照依据」的存在理由落进 prompt
#   生成链集成（假 LLM，零出网）：合法 schema → 题面包装 + SymPy 答案一致 + 盲解对账 +
#     FAKE_KP 问法合规指令注入 + 解析实例化；坏 schema / 盲解不一致 / FAKE_KP →
#     重生成 1 次（a1/a2 独立命名空间）→ blocked_pending_human 人话原因；
#     复核仲裁不回归；退化题首试 → 次试成功
#   v2api 接入（模块 L 定案的调用序）：
#     /v2/variant/ai：无族 + 重算器命中（在线）→ 重算变式 ok:true（provenance
#       recompute_variant / variant_meta.engine=recompute，ui2 横幅零改动依据）；
#       链 blocked → variant_failed 人话原因（不再谎报「需先建族」）；
#       无族 + 未注册 → no_kernel 原样（在线与 demo 同形）；demo + 命中 → no_kernel
#       载荷与基线逐字节一致（复用模块 J 内联基线方法）
#     /v2/variant（有源题）：族 > demo 拒绝（载荷逐字节）> 重算器（history-recompute）>
#       旧 LLM 变式链（未注册 kp 行为不变）
#
# 零出网纪律：全部 LLM 调用打桩（假 chat_json）；在线态测试设置的 DEEPSEEK_API_KEY
# 是假 key，仅为过 _demo_mode 的 demo 门，真实网络调用不可能发生。

import json
import os
import sys

import pytest
import sympy as sp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import generate  # noqa: E402
import pack_loader  # noqa: E402
import recompute as rc  # noqa: E402  —— flat import（与 v2api 的 import recompute 同一模块对象）
from v2 import v2api  # noqa: E402

XS = sp.Symbol("x")

# --------------------------------------------------------------------------- #
# 重算器单元：5 个问目标（已知答案手算对照）
# --------------------------------------------------------------------------- #
STMT = "设函数 $y=y(x)$ 由参数方程确定，则所求 = ____。"
# M2（对抗审查）：切线/法线题干必须自带斜截式引导（判分口径），否则 recompute 打回
STMT_LINE = ("设函数 $y=y(x)$ 由参数方程确定，求切线/法线方程 = ____"
             "（结果用斜截式 y=kx+b 表示）。")


def _ok(payload):
    r = rc.recompute(payload)
    assert r["ok"], r.get("reason")
    return r


def _fail_reason(payload):
    r = rc.recompute(payload)
    assert not r["ok"]
    return r["reason"]


def test_dydx_bare_expression():
    # x=1+t², y=2t → dy/dx = 2/(2t) = 1/t（t0=None 求通式）
    r = _ok({"x_expr": "1+t**2", "y_expr": "2*t", "t0": None, "target": "dydx",
             "statement_md": STMT})
    assert sp.simplify(sp.parse_expr(r["answer_sympy"]) - 1 / sp.Symbol("t")) == 0


def test_dydx_at_point_cycloid():
    # 摆线 x=t−sin t, y=1−cos t：dy/dx=sin t/(1−cos t)，t=π/2 → 1/1 = 1（手算）
    r = _ok({"x_expr": "t-sin(t)", "y_expr": "1-cos(t)", "t0": "pi/2", "target": "dydx",
             "statement_md": STMT})
    assert sp.simplify(sp.parse_expr(r["answer_sympy"]) - 1) == 0


def test_dydx_at_point_poly():
    # x=t², y=t³：dy/dx=3t/2，t=2 → 3（手算）
    r = _ok({"x_expr": "t**2", "y_expr": "t**3", "t0": 2, "target": "dydx",
             "statement_md": STMT})
    assert sp.parse_expr(r["answer_sympy"]) == 3


def test_d2ydx2_bare_expression():
    # x=t², y=t³：dy/dx=3t/2 → d²y/dx²=(3/2)/(2t)=3/(4t)（手算）
    r = _ok({"x_expr": "t**2", "y_expr": "t**3", "t0": None, "target": "d2ydx2",
             "statement_md": STMT})
    assert sp.simplify(sp.parse_expr(r["answer_sympy"]) - 3 / (4 * sp.Symbol("t"))) == 0


def test_d2ydx2_at_point_poly():
    # 同上在 t=1 → 3/4（手算）
    r = _ok({"x_expr": "t**2", "y_expr": "t**3", "t0": 1, "target": "d2ydx2",
             "statement_md": STMT})
    assert sp.parse_expr(r["answer_sympy"]) == sp.Rational(3, 4)


def test_d2ydx2_at_point_cycloid():
    # 摆线：d²y/dx²=−1/(1−cos t)²，t=π/2 → −1（手算；2026 锚池主流形态即指定点二阶导）
    r = _ok({"x_expr": "t-sin(t)", "y_expr": "1-cos(t)", "t0": "pi/2", "target": "d2ydx2",
             "statement_md": STMT})
    assert sp.parse_expr(r["answer_sympy"]) == -1


def test_tangent_slope_intercept_form():
    # x=t², y=2t+1，t=1 → 点 (1,3)，k=1 → y=x+2（手算）
    r = _ok({"x_expr": "t**2", "y_expr": "2*t+1", "t0": 1, "target": "tangent",
             "statement_md": STMT_LINE})
    assert sp.simplify(sp.parse_expr(r["answer_sympy"]) - (XS + 2)) == 0


def test_tangent_rational_slope():
    # x=t², y=t³，t=1 → 点 (1,1)，k=3/2 → y=3x/2−1/2（手算）
    r = _ok({"x_expr": "t**2", "y_expr": "t**3", "t0": 1, "target": "tangent",
             "statement_md": STMT_LINE})
    assert sp.simplify(sp.parse_expr(r["answer_sympy"])
                       - (3 * XS / 2 - sp.Rational(1, 2))) == 0


def test_normal_slope_intercept_form():
    # x=t², y=t³，t=1 → 切线斜率 3/2，法线斜率 −2/3 → y=−2x/3+5/3（手算）
    r = _ok({"x_expr": "t**2", "y_expr": "t**3", "t0": 1, "target": "normal",
             "statement_md": STMT_LINE})
    assert sp.simplify(sp.parse_expr(r["answer_sympy"])
                       - (-2 * XS / 3 + sp.Rational(5, 3))) == 0


def test_normal_vertical_tangent_case_is_representable():
    # x=t³, y=t，t=1：切线竖直但法线水平 y=y(1)=1 → 斜截式可表示（k=0），照常出题；
    # 这里 t=1 处切线斜率 1/3，法线斜率 −3，点 (1,1) → y=−3x+4（手算）
    r = _ok({"x_expr": "t**3", "y_expr": "t", "t0": 1, "target": "normal",
             "statement_md": STMT_LINE})
    assert sp.simplify(sp.parse_expr(r["answer_sympy"]) - (4 - 3 * XS)) == 0


def test_curvature_circle_is_one():
    # 圆 x=cos t, y=sin t：曲率恒为 1（手算）
    r = _ok({"x_expr": "cos(t)", "y_expr": "sin(t)", "t0": 1, "target": "curvature",
             "statement_md": STMT})
    assert sp.parse_expr(r["answer_sympy"]) == 1


def test_curvature_astroid_2018_real_exam_shape():
    # 星形线 x=cos³t, y=sin³t 在 t=π/4（2018数二真题形态）：κ=2/3（手算）
    r = _ok({"x_expr": "cos(t)**3", "y_expr": "sin(t)**3", "t0": "pi/4",
             "target": "curvature", "statement_md": STMT})
    assert sp.parse_expr(r["answer_sympy"]) == sp.Rational(2, 3)


def test_answer_equivalence_contract_slope_intercept():
    # 切线等价判定口径：学生写 y=x+2 经 normalize_expr 剥前缀后与斜截式 x+2 判等；
    # 一般式 2x-y-3=0 不在试点口径内（analysis_hint 引导斜截式，docstring 已写明）
    r = _ok({"x_expr": "t**2", "y_expr": "2*t+1", "t0": 1, "target": "tangent",
             "statement_md": STMT_LINE})
    from verify import check_answer
    assert check_answer("y=x+2", r["answer_sympy"]) is True
    assert check_answer("x+2", r["answer_sympy"]) is True
    assert check_answer("2x-y-3=0", r["answer_sympy"]) is False


# --------------------------------------------------------------------------- #
# 退化与守卫
# --------------------------------------------------------------------------- #
def test_dx_identically_zero_degenerate():
    reason = _fail_reason({"x_expr": "5", "y_expr": "t", "t0": None, "target": "dydx",
                           "statement_md": STMT})
    assert "x'(t) ≡ 0" in reason


def test_tangent_vertical_degenerate():
    reason = _fail_reason({"x_expr": "t**3", "y_expr": "t", "t0": 0, "target": "tangent",
                           "statement_md": STMT})
    assert "切线竖直" in reason


def test_normal_vertical_degenerate():
    reason = _fail_reason({"x_expr": "t", "y_expr": "t**2", "t0": 0, "target": "normal",
                           "statement_md": STMT})
    assert "法线竖直" in reason


def test_tangent_and_curvature_require_t0():
    assert "需要具体点 t0" in _fail_reason(
        {"x_expr": "t", "y_expr": "t", "t0": None, "target": "tangent", "statement_md": STMT})
    assert "需要具体点 t0" in _fail_reason(
        {"x_expr": "t", "y_expr": "t", "t0": None, "target": "normal", "statement_md": STMT})
    assert "需要具体点 t0" in _fail_reason(
        {"x_expr": "t", "y_expr": "t", "t0": None, "target": "curvature", "statement_md": STMT})


def test_curvature_singular_point_degenerate():
    reason = _fail_reason({"x_expr": "t**2", "y_expr": "t**3", "t0": 0,
                           "target": "curvature", "statement_md": STMT})
    assert "曲率无定义" in reason


def test_undefined_at_t0_degenerate():
    reason = _fail_reason({"x_expr": "t", "y_expr": "1/t", "t0": 0, "target": "dydx",
                           "statement_md": STMT})
    assert "无定义" in reason


def test_parse_fail_and_stray_symbol():
    assert "无法解析" in _fail_reason({"x_expr": "t**2+", "y_expr": "t", "t0": None,
                                       "target": "dydx", "statement_md": STMT})
    # 自变量纪律：自由符号只允许 t（同 verify._parse 不开外部命名空间的口径）
    assert "未知符号" in _fail_reason({"x_expr": "a*t", "y_expr": "t", "t0": None,
                                       "target": "dydx", "statement_md": STMT})


def test_t0_must_be_real_number():
    assert "t0 不是数值" in _fail_reason({"x_expr": "t", "y_expr": "t", "t0": "abc",
                                          "target": "dydx", "statement_md": STMT})
    assert "t0 无法解析" in _fail_reason({"x_expr": "t", "y_expr": "t", "t0": "pi/",
                                          "target": "dydx", "statement_md": STMT})
    assert "t0 不是实数" in _fail_reason({"x_expr": "t", "y_expr": "t", "t0": "1+I",
                                          "target": "dydx", "statement_md": STMT})


def test_schema_missing_field_human_reason():
    reason = _fail_reason({"x_expr": "t**2"})
    assert "缺字段" in reason and "y_expr" in reason
    assert "不是 JSON 对象" in _fail_reason("not a dict")


def test_unknown_target():
    reason = _fail_reason({"x_expr": "t", "y_expr": "t", "t0": None, "target": "d3y",
                           "statement_md": STMT})
    assert "未知问目标" in reason and "dydx" in reason


def test_statement_render_guards():
    base = {"x_expr": "t", "y_expr": "t", "t0": None, "target": "dydx"}
    assert "渲染瑕疵" in _fail_reason({**base, "statement_md": "求 $1x^2$ 的导数 ____。"})
    assert "$ 未配对" in _fail_reason({**base, "statement_md": "求 $1x^2 的导数 ____。"})
    assert "花括号未配对" in _fail_reason(
        {**base, "statement_md": "设 $x=t^{{2}$ 的导数 ____。"})
    assert "题干长度异常" in _fail_reason({**base, "statement_md": "太短"})


def test_claimed_answer_not_trusted_only_archived():
    # 判分权 100% SymPy：claimed_answer 错误也不影响 answer_sympy（重算终审），仅存档
    r = _ok({"x_expr": "t**2", "y_expr": "t**3", "t0": 1, "target": "d2ydx2",
             "statement_md": STMT, "claimed_answer": "999"})
    assert r["answer_sympy"] == "3/4"
    assert r["claimed_answer"] == "999"


# --------------------------------------------------------------------------- #
# 注册表与难度档
# --------------------------------------------------------------------------- #
def test_recompute_specs_registered_both_keys():
    assert rc.RECOMPUTE_SPECS["参数方程求导"] is rc.RECOMPUTE_SPECS["calc.derivative.param"]
    assert rc.spec_for_kp("calc.derivative.param")["kp"] == "参数方程求导"
    assert rc.spec_for_kp(None, "参数方程求导")["kp"] == "参数方程求导"
    assert rc.spec_for_kp("calc.integral.basic", "不定积分基本公式与换元") is None
    assert rc.spec_for_kp(None, None) is None


def test_difficulty_target_ladder():
    # 定案难度档：基础=dydx；进阶=d2ydx2/tangent/normal；综合=curvature
    spec = rc.RECOMPUTE_SPECS["参数方程求导"]
    assert rc._targets_for_difficulty(spec, "基础") == ("dydx",)
    assert rc._targets_for_difficulty(spec, "进阶") == ("d2ydx2", "tangent", "normal")
    assert rc._targets_for_difficulty(spec, "综合") == ("curvature",)
    assert rc._targets_for_difficulty(spec, "未知值") == ("d2ydx2", "tangent", "normal")


def test_namespace_generation():
    assert rc._NS_RECOMPUTE == "recompute-deriv:v1:a{}:"


# --------------------------------------------------------------------------- #
# prompt 风格指引：真题形态依据（L3 统计硬编码，注明来源与日期）
# --------------------------------------------------------------------------- #
def test_prompt_style_guide_has_anchor_evidence():
    sys_prompt = rc._PARAM_DERIV_SYSTEM
    # 来源与日期
    for kw in ("anchor_pool.jsonl", "2026-09-29", "1614"):
        assert kw in sys_prompt, f"风格指引缺统计来源标记：{kw}"
    # 形态分布（8/14 指定点二阶导为主流）与真题锚
    for kw in ("8/14", "2018数二", "cos³t", "法线斜率", "指定点", "不裸考"):
        assert kw in sys_prompt, f"风格指引缺形态依据：{kw}"
    # schema 契约字段与 5 个问目标
    for kw in ("x_expr", "y_expr", "t0", "target", "statement_md", "analysis_hint",
               "claimed_answer", "dydx", "d2ydx2", "tangent", "normal", "curvature"):
        assert kw in sys_prompt, f"schema 契约缺字段：{kw}"
    # 重算器边界与判分权声明
    assert "SymPy" in sys_prompt and "不采信" in sys_prompt
    assert "变限积分" in sys_prompt  # 显式初等表达式边界


# --------------------------------------------------------------------------- #
# 生成链集成（假 LLM，零出网）
# --------------------------------------------------------------------------- #
VALID_D2 = {
    "x_expr": "t**2 + 1", "y_expr": "t**3", "t0": 1, "target": "d2ydx2",
    "statement_md": "设函数 $y=y(x)$ 由参数方程 $\\begin{cases}x=t^{2}+1,\\\\ y=t^{3}"
                    "\\end{cases}$ 所确定，则 $\\left.\\dfrac{d^{2}y}{dx^{2}}\\right|_{t=1}"
                    " = $ ____。",
    "analysis_hint": "先求 dy/dx=3t/2，再对 t 求导除以 x'=2t。",
    "claimed_answer": "3/4", "design_note": "考察参数方程二阶导",
}
# x=t²+1, y=t³ 在 t=1 的 d²y/dx²=3/4（手算，与 VALID_D2 一致）
VALID_ANSWER = "3/4"

SPEC = rc.RECOMPUTE_SPECS["参数方程求导"]


def _snap_rc(captured, products):
    """假重算链出题 LLM：products 为 list 时按 attempt 顺序供给，耗尽回落最后一个。"""
    seq = list(products) if isinstance(products, list) else [products]
    state = {"i": 0}

    def fake(messages, **kw):
        ns = kw.get("namespace", "")
        captured.append((ns, json.loads(json.dumps(messages, ensure_ascii=False)), kw))
        if ns.startswith("recompute-deriv"):
            p = seq[min(state["i"], len(seq) - 1)]
            state["i"] += 1
            return json.loads(json.dumps(p))
        return {}
    return fake


def _snap_gen(captured, solve_answer=VALID_ANSWER, solve_seq=None, analysis="解析。"):
    it = iter(solve_seq) if solve_seq is not None else None

    def fake(messages, **kw):
        ns = kw.get("namespace", "")
        captured.append((ns, json.loads(json.dumps(messages, ensure_ascii=False)), kw))
        if ns == "solve:":
            return {"answer": next(it) if it is not None else solve_answer}
        if ns == "analysis:":
            return {"analysis": analysis}
        return {}
    return fake


def _online(monkeypatch):
    """离开 demo 门；LLM 已打桩零出网（假 key 只为过 _demo_mode 的 demo 门）。"""
    monkeypatch.setenv("MATHFORGE_DEMO", "0")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key-recompute-tests")


def test_chain_happy_path_schema_gated(monkeypatch):
    _online(monkeypatch)
    captured = []
    monkeypatch.setattr(rc, "chat_json", _snap_rc(captured, VALID_D2))
    monkeypatch.setattr(generate, "chat_json", _snap_gen(captured))
    q = rc.generate_recompute_variant(SPEC, "进阶")
    assert q.get("status") != "blocked_pending_human"
    # 题面包装 = LLM 的 statement_md；答案 = SymPy 重算（不是 LLM claimed，尽管此处一致）
    assert q["statement_md"] == VALID_D2["statement_md"]
    assert q["answer_sympy"] == VALID_ANSWER
    assert q["verification"]["solver_agree"] is True
    assert q["routed"] == "recompute" and q["attempt"] == 1
    assert q["recompute"]["target"] == "d2ydx2" and q["recompute"]["t0"] == "1"
    assert q["recompute"]["claimed_answer"] == "3/4"  # 仅存档对账参考
    # 调用序与命名空间：出题 a1 → 盲解 → 解析实例化
    ns_list = [ns for ns, _, _ in captured]
    assert ns_list == ["recompute-deriv:v1:a1:", "solve:", "analysis:"]
    # 出题 temperature 与绿标一致
    gen_kw = captured[0][2]
    assert gen_kw["temperature"] == 1.0
    # FAKE_KP 问法合规指令注入（模块 J 语义复用，含考点中文名）
    solve_msgs = next(m for ns, m, _ in captured if ns == "solve:")
    assert "问法合规检查" in solve_msgs[0]["content"]
    assert "参数方程求导" in solve_msgs[0]["content"]
    assert "FAKE_KP" in solve_msgs[0]["content"]
    # 盲解 user 收到作答格式提示（对账口径一致），题面本身不带该提示
    assert "斜截式" not in q["statement_md"]
    # 解析实例化以最终题干+重算答案为准（绿标同纪律）
    ana_msgs = next(m for ns, m, _ in captured if ns == "analysis:")
    assert VALID_D2["statement_md"] in ana_msgs[1]["content"]
    assert VALID_ANSWER in ana_msgs[1]["content"]
    assert q["analysis"] == "解析。"


def test_chain_verify_mode_arbitration(monkeypatch):
    # 首盲偶发算错 → verify_mode 复核解与 SymPy 一致 → 放行（绿标语义镜像）
    _online(monkeypatch)
    captured = []
    monkeypatch.setattr(rc, "chat_json", _snap_rc(captured, VALID_D2))
    monkeypatch.setattr(generate, "chat_json", _snap_gen(captured,
                                                         solve_seq=["999", VALID_ANSWER]))
    q = rc.generate_recompute_variant(SPEC, "进阶")
    assert q.get("status") != "blocked_pending_human"
    assert q["verification"]["solver_agree"] is True
    assert [ns for ns, _, _ in captured].count("solve:") == 2
    verify_msgs = [m for ns, m, _ in captured if ns == "solve:"]
    assert "复核模式" in verify_msgs[1][1]["content"]


def test_chain_bad_schema_regen_then_blocked(monkeypatch):
    _online(monkeypatch)
    captured = []
    monkeypatch.setattr(rc, "chat_json", _snap_rc(captured, {"x_expr": "t**2"}))
    monkeypatch.setattr(generate, "chat_json", _snap_gen(captured))
    q = rc.generate_recompute_variant(SPEC, "进阶")
    assert q["status"] == "blocked_pending_human"
    assert "缺字段" in q["error"]
    assert q["attempt"] == 2
    ns_list = [ns for ns, _, _ in captured]
    # 重试靠独立命名空间 a1/a2 真正重新生成
    assert ns_list.count("recompute-deriv:v1:a1:") == 1
    assert ns_list.count("recompute-deriv:v1:a2:") == 1


def test_chain_solver_mismatch_regen_then_blocked(monkeypatch):
    # 重算不匹配（盲解怎么算都 ≠ SymPy 重算）→ 重生成 → 仍不一致 → 拦截
    _online(monkeypatch)
    captured = []
    monkeypatch.setattr(rc, "chat_json", _snap_rc(captured, VALID_D2))
    monkeypatch.setattr(generate, "chat_json", _snap_gen(captured, solve_answer="999"))
    q = rc.generate_recompute_variant(SPEC, "进阶")
    assert q["status"] == "blocked_pending_human"
    assert "盲解与重算答案不一致" in q["error"]
    assert q["attempt"] == 2


def test_chain_fake_kp_gate_semantics_reused(monkeypatch):
    # 盲解判「考点装饰性问法」→ 问法合规拦截（判定性结论不做复核，每次尝试恰一次盲解）
    _online(monkeypatch)
    captured = []
    monkeypatch.setattr(rc, "chat_json", _snap_rc(captured, VALID_D2))
    monkeypatch.setattr(generate, "chat_json", _snap_gen(captured, solve_answer="FAKE_KP"))
    q = rc.generate_recompute_variant(SPEC, "进阶")
    assert q["status"] == "blocked_pending_human"
    assert "问法合规" in q["error"] and "考点未承重" in q["error"]
    assert q["attempt"] == 2
    solve_msgs = [m for ns, m, _ in captured if ns == "solve:"]
    assert len(solve_msgs) == 2  # 两次尝试各一次盲解，无 verify_mode 复审
    for msgs in solve_msgs:
        assert "问法合规检查" in msgs[0]["content"]


def test_chain_degenerate_first_attempt_recovers(monkeypatch):
    # 首试退化（t0=0 切线竖直）→ 重生成 → 次试合法 schema 成功
    _online(monkeypatch)
    bad = {"x_expr": "t**3", "y_expr": "t", "t0": 0, "target": "tangent",
           "statement_md": "求曲线在 $t=0$ 对应点处的切线方程 = ____"
                           "（结果用斜截式 y=kx+b 表示）。",
           "analysis_hint": "点斜式。", "claimed_answer": ""}
    captured = []
    monkeypatch.setattr(rc, "chat_json", _snap_rc(captured, [bad, VALID_D2]))
    monkeypatch.setattr(generate, "chat_json", _snap_gen(captured))
    q = rc.generate_recompute_variant(SPEC, "进阶")
    assert q.get("status") != "blocked_pending_human"
    assert q["attempt"] == 2 and q["answer_sympy"] == VALID_ANSWER
    assert [ns for ns, _, _ in captured] == ["recompute-deriv:v1:a1:",
                                             "recompute-deriv:v1:a2:",
                                             "solve:", "analysis:"]


# --------------------------------------------------------------------------- #
# v2api 接入（调用序定案：族 > demo 拒绝 > 重算器 > 旧 LLM 变式链 / no_kernel 拒绝）
# --------------------------------------------------------------------------- #
PACK = pack_loader.load_pack("calculus")
KP_PARAM = pack_loader.get_kp(PACK, "calc.derivative.param")
KP_OTHER = pack_loader.get_kp(PACK, "calc.integral.basic")  # 无族且未注册重算器
NO_KERNEL_RESPONSE = {
    "ok": False, "code": "no_kernel", "pack_id": "calculus",
    "kp": {"id": "calc.derivative.param", "name": "参数方程求导",
           "section": "一元微分/导数计算", "level": 2,
           "parents": ["calc.derivative.chain"], "qtypes": ["fill", "solution"],
           "difficulty_floor": "basic", "typical_forms": ["参数方程一阶",
                                                          "参数方程二阶 d2y/dx2"],
           "pitfalls": ["二阶导忘除以 x'(t)", "一阶导数未化简"]},
    "reason": "该考点暂无确定性数学核（模板族），AI 变式需先建族",
}


def _patch_memory(monkeypatch):
    """记忆注入与本模块无关（模块 F3 独立把守）：打桩避免测试触碰任何档案库。"""
    monkeypatch.setattr(v2api, "_memory_block_for", lambda *a, **k: "")


def test_ai_variant_recompute_hit_online(monkeypatch):
    # 用户痛点修复：参数方程求导（无族）AI 变式不再「需先建族」→ 重算器出可验证变式
    _online(monkeypatch)
    _patch_memory(monkeypatch)
    captured = []
    monkeypatch.setattr(rc, "chat_json", _snap_rc(captured, VALID_D2))
    monkeypatch.setattr(generate, "chat_json", _snap_gen(captured))
    out = v2api.v2_variant_ai(v2api.VariantAiBody(kp_id="calc.derivative.param",
                                                  difficulty="进阶"))
    assert out["ok"] is True
    q = out["question"]
    assert q["statement_md"] == VALID_D2["statement_md"]
    assert q["answer_sympy"] == VALID_ANSWER
    assert q["routed"] == "recompute"
    # L4 溯源标注（ui2 渲染契约：非 ai 引擎不渲染「AI 暂不可用」横幅 → ui2 零改动）
    assert out["provenance"]["changes"][0]["type"] == "recompute_variant"
    assert out["provenance"]["changes"][0]["desc"] == "AI 情境化变式（重算器验证）"
    assert out["variant_meta"]["engine"] == "recompute"
    assert out["variant_meta"]["degraded"] is False
    assert out["variant_meta"]["target"] == "d2ydx2"


def test_ai_variant_recompute_blocked_honest_failure(monkeypatch):
    # 链 blocked → variant_failed + 人话原因（不再谎报「需先建族」：问题不在缺族）
    _online(monkeypatch)
    _patch_memory(monkeypatch)
    captured = []
    monkeypatch.setattr(rc, "chat_json", _snap_rc(captured, {"x_expr": "t**2"}))
    monkeypatch.setattr(generate, "chat_json", _snap_gen(captured))
    out = v2api.v2_variant_ai(v2api.VariantAiBody(kp_id="calc.derivative.param",
                                                  difficulty="进阶"))
    assert out["ok"] is False
    assert out["code"] == "variant_failed"
    assert "AI 重算变式不可用" in out["reason"] and "缺字段" in out["reason"]


def test_ai_variant_unregistered_kp_no_kernel_unchanged(monkeypatch):
    # 未注册 kp（无族）→ 旧 no_kernel 拒绝原样（在线态）
    _online(monkeypatch)
    _patch_memory(monkeypatch)
    captured = []
    monkeypatch.setattr(rc, "chat_json", _snap_rc(captured, VALID_D2))
    monkeypatch.setattr(generate, "chat_json", _snap_gen(captured))
    out = v2api.v2_variant_ai(v2api.VariantAiBody(kp_id="calc.integral.basic"))
    assert out["ok"] is False and out["code"] == "no_kernel"
    assert "需先建族" in out["reason"]
    assert not captured  # 重算器链根本没被触发


def test_ai_variant_demo_payload_byte_identical(monkeypatch):
    # demo 红线：demo 态 + 重算器命中 → no_kernel 载荷与基线逐字节一致（模块 J 内联基线法）
    monkeypatch.setenv("MATHFORGE_DEMO", "1")  # conftest 已密封，显式重申
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    captured = []
    monkeypatch.setattr(rc, "chat_json", _snap_rc(captured, VALID_D2))
    monkeypatch.setattr(generate, "chat_json", _snap_gen(captured))
    out = v2api.v2_variant_ai(v2api.VariantAiBody(kp_id="calc.derivative.param"))
    assert json.loads(json.dumps(out)) == NO_KERNEL_RESPONSE
    assert not captured  # demo 不进重算链（零 LLM 载荷变化）


def test_variant_with_source_recompute_hit(monkeypatch):
    # 有源题 + 无族 + 重算器命中（在线）→ history-recompute（先于旧 LLM 变式链）
    _online(monkeypatch)
    _patch_memory(monkeypatch)
    src = {"event_id": 7, "stmt_summary": "设 $y=y(x)$ 由参数方程确定，求 $\\frac{dy}{dx}$。",
           "difficulty": "进阶"}
    captured = []
    monkeypatch.setattr(rc, "chat_json", _snap_rc(captured, VALID_D2))
    monkeypatch.setattr(generate, "chat_json", _snap_gen(captured))
    body = v2api.VariantBody(kp_id="calc.derivative.param", qtype="fill", difficulty="进阶")
    out, outcome = v2api._variant_with_source(PACK, KP_PARAM, src, body)
    assert outcome == "history-recompute"
    assert out["ok"] is True
    assert out["question"]["answer_sympy"] == VALID_ANSWER
    assert out["provenance"]["source_kind"] == "history"
    assert out["provenance"]["changes"][0]["type"] == "recompute_variant"
    assert out["provenance"]["changes"][0]["desc"] == "AI 情境化变式（重算器验证）"
    assert out["provenance"]["consistency"] is True


def test_variant_with_source_demo_refusal_byte_identical(monkeypatch):
    # demo 红线：demo 态 + 重算器命中 → variant_llm_unavailable 拒绝与基线逐字节一致
    monkeypatch.setenv("MATHFORGE_DEMO", "1")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    src = {"event_id": 7, "stmt_summary": "题干", "standard_summary": "1", "difficulty": "基础"}
    captured = []
    monkeypatch.setattr(rc, "chat_json", _snap_rc(captured, VALID_D2))
    monkeypatch.setattr(generate, "chat_json", _snap_gen(captured))
    body = v2api.VariantBody(kp_id="calc.derivative.param", qtype="fill")
    out, outcome = v2api._variant_with_source(PACK, KP_PARAM, src, body)
    assert outcome == "refused"
    assert json.loads(json.dumps(out)) == {
        "ok": False, "code": "variant_llm_unavailable", "pack_id": "calculus",
        "kp": v2api._kp_view(KP_PARAM),
        "reason": "该考点无确定性模板族，变式需联网生成，演示模式不可用"}
    assert not captured


def test_variant_with_source_blocked_human_reason(monkeypatch):
    # 重算链 blocked → 与旧 LLM 变式链失败同构（variant_failed + 人话原因，不 500）
    _online(monkeypatch)
    _patch_memory(monkeypatch)
    src = {"event_id": 7, "stmt_summary": "题干", "standard_summary": "1", "difficulty": "进阶"}
    captured = []
    monkeypatch.setattr(rc, "chat_json", _snap_rc(captured, {"x_expr": "t**2"}))
    monkeypatch.setattr(generate, "chat_json", _snap_gen(captured))
    body = v2api.VariantBody(kp_id="calc.derivative.param", qtype="fill")
    out, outcome = v2api._variant_with_source(PACK, KP_PARAM, src, body)
    assert outcome == "failed"
    assert out["ok"] is False and out["code"] == "variant_failed"
    assert "变式生成失败" in out["reason"] and "缺字段" in out["reason"]


def test_variant_with_source_unregistered_walks_old_llm_path(monkeypatch):
    # 未注册 kp（有源题、无族）→ 旧 LLM 变式链行为不变（重算器链零触发）
    _online(monkeypatch)
    _patch_memory(monkeypatch)
    src = {"event_id": 7, "stmt_summary": "题干", "standard_summary": "1", "difficulty": "基础"}
    calls = []

    def fake_generate_variant(kp, source, memory_context=None):
        calls.append((kp, source, memory_context))
        return {"kp": kp, "difficulty": "基础", "qtype": "calculation",
                "verify_level": "green", "statement_md": "旧链题干", "answer_sympy": "1",
                "answer_md": None, "options": None, "correct": None, "params": {}}

    captured = []
    monkeypatch.setattr(generate, "generate_variant", fake_generate_variant)
    monkeypatch.setattr(rc, "chat_json", _snap_rc(captured, VALID_D2))
    monkeypatch.setattr(generate, "chat_json", _snap_gen(captured))
    body = v2api.VariantBody(kp_id="calc.integral.basic", qtype="fill")
    out, outcome = v2api._variant_with_source(PACK, KP_OTHER, src, body)
    assert outcome == "history-llm"
    assert out["ok"] is True
    assert out["provenance"]["changes"][0]["type"] == "llm_variant"
    assert calls and calls[0][0] == "不定积分基本公式与换元"
    assert not captured  # 重算器链零触发


# --------------------------------------------------------------------------- #
# 对抗审查修复回归（2026-09-30）
# --------------------------------------------------------------------------- #

# --- B1：verify._parse 解析器沙箱（学生作答面 + LLM schema 面共用该解析器） --- #
def test_parse_sandbox_blocks_builtins_smuggling(tmp_path):
    """builtins 走私载荷一律解析失败且零副作用；修复前 open(...) 会真实创建文件。"""
    from verify import _parse
    target = tmp_path / "pwned.txt"
    payloads = [
        f"open(r'{target}', 'w')",
        "__import__('os').system('echo pwned')",
        "exec('raise ValueError(1)')",
        "eval('1+1')",
        "input()",
    ]
    for p in payloads:
        with pytest.raises(Exception):  # noqa: B017 —— 任何异常均可（上层人话化）
            _parse(p)
    assert not target.exists()  # open() 未被真实执行


def test_parse_sandbox_student_surface_no_side_effect(tmp_path):
    """学生作答面（check_answer/parse_error）同样不可走私：判 False/人话拦截，无副作用。"""
    from verify import check_answer, parse_error
    target = tmp_path / "pwned.txt"
    assert check_answer(f"open(r'{target}', 'w')", "1") is False
    assert parse_error("__import__('os').system('echo pwned')") is not None
    assert not target.exists()


def test_parse_sandbox_legit_expressions_unchanged():
    """沙箱不破坏合法表达式（审查员实测 13 条全集 + 代码库代表性写法，语义逐条不变）。"""
    from verify import _parse, check_answer, is_valid_expr, parse_error
    t, x = sp.Symbol("t"), sp.Symbol("x")
    legit = [
        ("2x+sin(t)", 2 * x + sp.sin(t)),
        ("Rational(3,4)", sp.Rational(3, 4)),
        ("max(2,3)", sp.Integer(3)),
        ("min(2,3)", sp.Integer(2)),
        ("abs(-3)", sp.Integer(3)),
        ("abs(x)", sp.Abs(x)),
        ("sqrt(2)/2", sp.sqrt(2) / 2),
        ("3*x/2 - 1/2", 3 * x / 2 - sp.Rational(1, 2)),
        ("pi/4", sp.pi / 4),
        ("exp(t)+log(t)", sp.exp(t) + sp.log(t)),
        ("cos(t)**3", sp.cos(t) ** 3),
        ("t**2+1", t ** 2 + 1),
        ("1/t", 1 / t),
        ("2*E**t", 2 * sp.E ** t),
        ("-1/(1-cos(t))**2", -1 / (1 - sp.cos(t)) ** 2),
    ]
    for s, expect in legit:
        assert sp.simplify(_parse(s) - expect) == 0, s
    assert _parse("Matrix([[1,2],[3,4]])") == sp.Matrix([[1, 2], [3, 4]])
    assert is_valid_expr("t**2+1") is True
    assert check_answer("2x+1", "2*x+1") is True
    assert parse_error("t**2+") is not None


# --- B1 复审：sympy 自命名空间逃逸面（shim pop + dunder/引号入口预拒） --- #
def test_parse_sandbox_shims_popped():
    """字符串求值 shim 与副作用面可调用一律离开沙箱命名空间；builtins 恒空。"""
    from verify import _SAFE_GLOBALS
    for name in ("sympify", "S", "parse_expr", "lambdify", "nsimplify", "symarray",
                 "var", "plot", "plot_backends", "plot_implicit", "plot_parametric",
                 "plotting", "textplot", "preview", "test", "doctest", "python",
                 "source"):
        assert name not in _SAFE_GLOBALS, name
    assert _SAFE_GLOBALS["__builtins__"] == {}
    # 直接 sympy 命名空间不受影响（recompute 的 sp.nsimplify 等内部用法不走沙箱）
    assert callable(sp.nsimplify)


@pytest.mark.parametrize("payload", [
    # 审查员 4 条逃逸载荷（修复前 sympify/S/parse_expr 内层自建命名空间残留真
    # builtins，sympify 载荷实测真实执行 echo；dunder 对象图游走可达 __builtins__）
    "sympify(\"__import__('os').system('echo pwned')\")",
    "S(\"__import__('os').system('echo pwned')\")",
    "parse_expr(\"__import__('os').system('echo pwned')\")",
    "S.__class__.__init__.__globals__['__builtins__']['__import__']('os')",
])
def test_parse_sympy_shim_escapes_blocked(payload):
    from verify import _parse
    with pytest.raises(Exception):  # noqa: B017 —— dunder/引号预拒人话异常
        _parse(payload)


@pytest.mark.parametrize("payload", [
    "plot(x)", "test(1)", "doctest(1)", "preview(x)",          # 副作用面（挂起/GUI）
    "sympify(1)", "S(1)", "parse_expr(1)", "sympify(chr(95))",  # 无引号无 dunder 变体
])
def test_parse_shim_calls_cannot_execute(payload, tmp_path):
    """shim 名 pop 后不可调用：解析产物是无函数原子的纯符号乘积（split_symbols
    拆字母），零副作用——修复前 sympy.test(1) 会真实挂起跑测试、plot 会弹 GUI。"""
    from verify import _parse
    canary = tmp_path / "pwned.txt"
    r = _parse(payload)
    assert isinstance(r, sp.Expr)
    assert not r.atoms(sp.Function)  # 没有任何函数调用发生
    assert not canary.exists()


def test_parse_shim_calls_recompute_face_ok_false():
    """重算器面：shim 调用产物含杂散符号 → ok:false（人话），绝不产出题目。"""
    for bad in ("plot(x)", "test(1)", "sympify(1)"):
        reason = _fail_reason({"x_expr": bad, "y_expr": "t", "t0": None,
                               "target": "dydx", "statement_md": STMT})
        assert "未知符号" in reason, bad


def test_parse_prereject_dunder_and_quotes():
    """入口预拒：dunder 与引号字面量在解析前直接人话拒绝（唯一入口第一行）。"""
    from verify import _parse
    for s in ("S.__class__.__init__", "x__import__y", "__import__('os')",
              "a.__globals__", "x = __doc__"):
        with pytest.raises(ValueError, match="非法记号"):
            _parse(s)
    for s in ("sympify('1')", 'S("2")', "x = '1'", "Symbol('t')", "exec('1')"):
        with pytest.raises(ValueError, match="非法字符"):
            _parse(s)


def test_parse_prereject_student_surface(tmp_path):
    """学生作答面同样被入口预拒覆盖（normalize_expr 先行但不产生引号/dunder，
    预拒在 _parse 唯一入口处必然命中）：判 False/人话拦截，零副作用。"""
    from verify import check_answer, parse_error
    canary = tmp_path / "pwned.txt"
    assert check_answer("sympify(\"open(r'" + str(canary) + "','w')\")", "1") is False
    assert parse_error("S.__class__.__init__") is not None
    assert parse_error("sympify('1')") is not None
    assert not canary.exists()


def test_recompute_face_sympy_escape_blocked(tmp_path):
    """LLM schema 面（x_expr）走同一入口：逃逸载荷 ok:false + 金丝雀不存在。"""
    canary = tmp_path / "pwned.txt"
    reason = _fail_reason({"x_expr": "sympify(\"__import__('os').system('echo pwned')\")",
                           "y_expr": "t", "t0": None, "target": "dydx",
                           "statement_md": STMT})
    assert "无法解析" in reason
    assert not canary.exists()


# --- B2：_parse 非 Expr 结果不再逃 AttributeError，人话化 --- #
def test_non_expr_parse_result_human_reason():
    for bad in ("[1,2]", "t,1", "t==1"):
        reason = _fail_reason({"x_expr": bad, "y_expr": "t", "t0": None,
                               "target": "dydx", "statement_md": STMT})
        assert "无法解析" in reason
        assert "AttributeError" not in reason and "object has no attribute" not in reason


# --- M1：t0 必须落在曲线上（x(t0)/y(t0) 有定义） --- #
def test_point_must_be_on_curve_definedness():
    # log(t) 在 t0=-1 处 x(t0)=log(-1)=I*pi 非实值 → 病态题打回（修复前照常出题）
    for target in ("dydx", "d2ydx2", "curvature"):
        reason = _fail_reason({"x_expr": "log(t)", "y_expr": "t", "t0": -1,
                               "target": target, "statement_md": STMT})
        assert "无定义" in reason, target
    # y(t0) 无定义同理
    reason = _fail_reason({"x_expr": "t", "y_expr": "log(t)", "t0": -1,
                           "target": "dydx", "statement_md": STMT})
    assert "无定义" in reason
    # 正常点不受影响
    r = _ok({"x_expr": "log(t)", "y_expr": "t", "t0": 1, "target": "dydx",
             "statement_md": STMT})
    assert sp.parse_expr(r["answer_sympy"]) == 1


# --- M2：切线/法线题干必须自带斜截式引导 --- #
def test_tangent_normal_statement_requires_slope_intercept_hint():
    base_t = {"x_expr": "t**2", "y_expr": "2*t+1", "t0": 1, "target": "tangent"}
    base_n = {"x_expr": "t**2", "y_expr": "t**3", "t0": 1, "target": "normal"}
    # 缺引导 → 打回，人话原因点明判分口径
    for base in (base_t, base_n):
        reason = _fail_reason({**base, "statement_md": STMT})
        assert "斜截式" in reason and "y=kx+b" in reason
    # 含「斜截式」关键词或 y=kx+b 变体写法均放行
    assert _ok({**base_t, "statement_md":
                "求切线方程 = ____（答案请写成斜截式）。"})["answer_sympy"] == "x + 2"
    assert _ok({**base_t, "statement_md":
                "求切线方程 = ____（结果写成 y = k*x + b 的形式）。"})["answer_sympy"] == "x + 2"
    # prompt 硬约束同款要求（重生成时 LLM 能看到）
    assert "斜截式 y=kx+b" in rc._PARAM_DERIV_SYSTEM


# --- m1：_brief 对 OSError 系只留类型名（防服务器路径泄漏进 reason） --- #
def test_brief_oserror_no_path_leak():
    e = FileNotFoundError(2, "No such file", r"D:\secret\server\path\key.json")
    assert rc._brief(e) == "FileNotFoundError"
    assert "secret" not in rc._brief(e)
    # 非系统异常仍带消息（人话诊断价值）
    assert "boom" in rc._brief(ValueError("boom detail"))


# --- m2：v2api 两处 import recompute 入 try（接入层故障转人话失败，不 500） --- #
def test_recompute_import_failure_degrades_not_500(monkeypatch):
    _online(monkeypatch)
    _patch_memory(monkeypatch)
    monkeypatch.setitem(sys.modules, "recompute", None)  # import 即 ImportError
    out = v2api.v2_variant_ai(v2api.VariantAiBody(kp_id="calc.derivative.param",
                                                  difficulty="进阶"))
    assert out["ok"] is False and out["code"] == "variant_failed"
    assert "AI 重算变式不可用" in out["reason"]
    src = {"event_id": 7, "stmt_summary": "题干", "standard_summary": "1",
           "difficulty": "进阶"}
    out2, outcome2 = v2api._variant_with_source(
        PACK, KP_PARAM, src,
        v2api.VariantBody(kp_id="calc.derivative.param", qtype="fill"))
    assert outcome2 == "failed"
    assert out2["ok"] is False and out2["code"] == "variant_failed"
    assert "变式生成失败" in out2["reason"]


# --- B1 终审收口：ai_variant._sym_normalize_free 走沙箱（G2b 面，2026-09-30） --- #
def test_ai_variant_gate_math_escape_blocked(tmp_path):
    """审查员金丝雀：answer_expr 经 gate_math 与 _sym_normalize_free 均不可走私——
    修前裸 sp.sympify 内层自建命名空间残留真 builtins，金丝雀实测执行。"""
    from v2 import ai_variant as av
    canary = tmp_path / "pwned.txt"
    payload = "sympify(\"open(r'" + str(canary) + "','w')\")"
    ok, why = av.gate_math({"answer_expr": payload}, {"answer_sympy": "1"})
    assert ok is False and why  # 诚实 gate 失败（校验异常/漂移人话），绝不放行
    assert not canary.exists()
    # 修复点直测：_sym_normalize_free 经 _parse 沙箱，逃逸载荷解析即拒
    with pytest.raises(Exception):  # noqa: B017
        av._sym_normalize_free(payload)
    assert not canary.exists()
    with pytest.raises(Exception):  # noqa: B017 —— dunder 对象图游走同样入口即拒
        av._sym_normalize_free("S.__class__.__init__")
    # 非 Expr（列表）诚实失败（B2 同口径）
    ok4, why4 = av.gate_math({"answer_expr": "[1,2]"}, {"answer_sympy": "1"})
    assert ok4 is False and "校验异常" in why4


def test_ai_variant_gate_math_semantics_unregressed():
    """沙箱收口不回归 G2 语义：严格等价 / 同构重命名（母题 x、LLM 写 t）照常放行。"""
    from v2 import ai_variant as av
    ok, why = av.gate_math({"answer_expr": "2*x+1"}, {"answer_sympy": "2*x+1"})
    assert ok is True and why == "等价"
    ok2, why2 = av.gate_math({"answer_expr": "2*t+1"}, {"answer_sympy": "2*x+1"})
    assert ok2 is True and why2.startswith("同构等价")
    # 普通漂移（无异常）消息保持原口径
    ok3, why3 = av.gate_math({"answer_expr": "x**3+1"}, {"answer_sympy": "x+1"})
    assert ok3 is False
