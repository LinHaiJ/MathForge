# 模块 M（占位符契约换代 + 守卫重构）测试：docs/2026-09-12_夜间优化/09 号执行计划。
#
# 背景：用户盲评实测产出率 2/12，误拦诊断——\frac{dy}{dx} 的 {dx}、\iint\limits_{D} 的 {D}
# 被旧 {([a-zA-Z_]+)} 正则当"未代入参数"拦截（同类 bug 第三次出现）。定案：正则只拦
# "确定的错"（模式即证据），猜测性判断用结构化定界符消灭——占位符契约 {名} → <<名>>。
#
# 覆盖：
#   M1 新契约：<<name>> 模板假 LLM 全链路正常出题；漏代入 <<dx>> 拦截（人话，题干与
#     answer_expr 两侧）；替换等价性（旧 {a} 与新 <<a>> 渲染结果逐字节一致）
#   M1 审查 m4(major)：占位符字符集放宽 [A-Za-z_]* + 怪名残余兜底——大写 <<A>>/数字开头/
#     含空格占位符一律拦截，绝不静默漏发
#   M1 审查 m7(major)：契约与守卫对齐——system 明示系数位 2~5（系数 1 触发渲染守卫），
#     a=1 形态真拦、a∈2~5 零拦截
#   M2 过渡兼容：用户盲评 p001/p007/p009 真实题干形态重放（\frac{dy}{dx}、\iint\limits_{D}）
#     → 不拦 + verification.legacy_note 留痕；已知参数旧 {a} 仍正常替换（不原样下发）
#   M3 确定错：x=1(t-\sin t)、1x+3 被 render_flaws 拦；Rational(1,3)/f(1)/a_1(/11x 等
#     合法形态零误伤；packs 三包全部族 + root 六族 statement_md 零命中扫描（误伤清零验收）
#   M4 人话化/瞬态：缺 answer_expr/statement_template 字段人话报错；llm.chat 空响应
#     两次 → 第三次成功（独立缓存键）；空串不落缓存；缓存命中路径不变；3 次耗尽 Retryable
#   M5 hint：罗尔/拉格朗日变式注入构造提示；无命中 kp 变式载荷逐字节不变
#   M6 命名空间：gen-green v9（占位符契约换代 + 系数位契约对齐）/ yellow v6 不动
#
# 零出网纪律：generate 侧 monkeypatch 假 chat_json（做法同 test_fake_kp_gate）；
# llm 侧假 client（做法同 test_llm_errors）；MATHFORGE_DEMO 由 conftest 密封为 1。

import hashlib
import importlib.util
import json
import os
import sys
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import generate  # noqa: E402
import llm  # noqa: E402

KP_CTX = {"kp": "泰勒公式", "definition_md": "泰勒公式：用多项式逼近光滑函数。",
          "key_formulas": ["f(x)=Σ f⁽ⁿ⁾(a)/n!·(x-a)ⁿ"], "common_mistakes": ["漏余项"]}
ANCHOR = "【真题锚 1】设 $f(x)=x^2$，求 $f'(x)$。\n答案：2x\n解析：幂函数求导。"

# 假 LLM 绿标产物（新契约 <<>>；params 单候选保证 random.choice 确定性）
GREEN_PRODUCT_NEW = {"statement_template": "求 $f(x)=<<a>>x$ 在 $x=1$ 处的导数值。",
                     "params": {"a": [3]}, "answer_expr": "<<a>>",
                     "analysis": "对 $3x$ 求导得 3。", "design_note": "考察求导"}

# 用户盲评 p001 真实题干形态（参数方程求导）：含 \frac{dy}{dx}（LaTeX 记号），系数 1 已消
P001_PRODUCT = {
    "statement_template": (r"设 $\left\{\begin{aligned}x=t-\sin t\\ y=1-\cos t\end{aligned}\right.$，"
                           r"求 $\frac{dy}{dx}$ 在 $t=<<t0>>$ 处的值。"),
    "params": {"t0": [1]},
    "answer_expr": "sin(<<t0>>)/(1-cos(<<t0>>))",
    "analysis": "参数方程求导。", "design_note": "dy/dx = y'(t)/x'(t)",
}

# 用户盲评 p007/p009 真实题干形态（二重积分）：含 \iint\limits_{D} 下标组
DI_PRODUCT = {
    "statement_template": (r"计算 $\iint\limits_{D} xy\,dA$，其中 $D$ 为矩形区域 "
                           r"$0\le x\le <<a>>$，$0\le y\le <<a>>$。"),
    "params": {"a": [2]},
    "answer_expr": "<<a>>**4/4",
    "analysis": "直角坐标二次积分。", "design_note": "∫x dx·∫y dy",
}


# --------------------------------------------------------------------------- #
# 假 LLM（零出网）：按 namespace 分发并记录 (namespace, messages 深拷贝)
# --------------------------------------------------------------------------- #
def _fake_llm(captured, product=None, solve_answer="3"):
    def fake(messages, **kw):
        ns = kw.get("namespace", "")
        captured.append((ns, json.loads(json.dumps(messages, ensure_ascii=False))))
        if ns.startswith("gen-green"):
            return json.loads(json.dumps(product or GREEN_PRODUCT_NEW))
        if ns.startswith("solve"):
            return {"answer": solve_answer}
        if ns.startswith("analysis"):
            return {"analysis": "解析。"}
        return {}
    return fake


# --------------------------------------------------------------------------- #
# M1：新契约 <<name>>
# --------------------------------------------------------------------------- #
def test_new_contract_template_generates_end_to_end(monkeypatch):
    """<<>> 模板假 LLM 全链路：代入、SymPy 构造、盲解对账全过；出题 system 契约文本
    已换代（含 <<name>>，无旧 {{name}} 双花括号写法）。"""
    captured = []
    monkeypatch.setattr(generate, "chat_json", _fake_llm(captured))
    q = generate._gen_green(dict(KP_CTX), "基础", [ANCHOR], attempt=1)
    assert q["statement_md"] == "求 $f(x)=3x$ 在 $x=1$ 处的导数值。"
    assert q["answer_sympy"] == "3"
    assert q["verification"]["solver_agree"] is True
    assert "legacy_note" not in q["verification"]
    sys_content = captured[0][1][0]["content"]
    assert "<<name>>" in sys_content and "<<a>>" in sys_content
    assert "{{name}}" not in sys_content and "{{a}}" not in sys_content
    assert "系数位建议 2~5 的正整数" in sys_content  # 契约与渲染守卫对齐（审查 m7）


def test_unsubstituted_new_placeholder_in_stem_blocked(monkeypatch):
    """漏代入 <<dx>>（题干侧）→ 拦截 [待人工]，error 为人话（新定界符零误判）。"""
    product = {**GREEN_PRODUCT_NEW, "statement_template": "求 $f(x)=<<a>>x$ 关于 <<dx>> 的导数。"}
    monkeypatch.setattr(generate, "chat_json", _fake_llm(captured := [], product))
    q = generate.generate_question(dict(KP_CTX), difficulty="基础", qtype="calculation")
    assert q["status"] == "blocked_pending_human"
    assert "未代入" in q["error"] and "dx" in q["error"]
    assert "<<dx>>" in product["statement_template"]  # 原始形态确为新契约记号


def test_unsubstituted_new_placeholder_in_answer_blocked(monkeypatch):
    """漏代入 <<dx>>（answer_expr 侧）→ 拦截，error 人话并指认字段。"""
    product = {**GREEN_PRODUCT_NEW, "answer_expr": "<<a>>+<<dx>>"}
    monkeypatch.setattr(generate, "chat_json", _fake_llm([], product))
    with pytest.raises(ValueError) as ei:
        generate._gen_green(dict(KP_CTX), "基础", [])
    assert "answer_expr" in str(ei.value) and "未代入" in str(ei.value) and "dx" in str(ei.value)


def test_placeholder_charset_widened_and_weird_names_blocked(monkeypatch):
    """占位符字符集放宽（审查 m4）：大写 <<A>> 不得静默漏发；数字开头/含空格等怪名
    经残余 <<...>> 兜底同样拦（定界符 LaTeX 不可能出现，放宽零误伤风险）。"""
    # 直测：大写（主通道）+ 数字开头/含空格（怪名兜底）
    assert generate._unknown_placeholders("求 $f(x)=<<A>>x$。", {"a": 1}) == {"A"}
    assert generate._unknown_placeholders("求 <<1a>> 的导数。", {}) == {"1a"}
    assert generate._unknown_placeholders("求 <<a b>> 的导数。", {}) == {"a b"}
    assert generate._unknown_placeholders("已知 <<a>>。", {"a": 1}) == set()
    # 全链路：<<A>> 拦截留痕于人话 error，绝不原样下发
    product = {**GREEN_PRODUCT_NEW, "statement_template": "求 $f(x)=<<A>>x$ 在 $x=1$ 处的导数值。"}
    monkeypatch.setattr(generate, "chat_json", _fake_llm([], product))
    q = generate.generate_question(dict(KP_CTX), difficulty="基础", qtype="calculation")
    assert q["status"] == "blocked_pending_human"
    assert "未代入" in q["error"] and "A" in q["error"]
    assert "<<A>>" not in (q.get("statement_md") or "")


def test_coeff_contract_guides_away_from_one(monkeypatch):
    """契约自打架修复（审查 m7）：system 明示系数位 2~5、系数 1 会触发渲染守卫；
    a=1 代入成 "1x" 形态被渲染守卫真拦（守卫与契约同侧证据）；契约引导后 LLM 输出
    系数 2~5（如 a=3）→ 零拦截（test_new_contract_template_generates_end_to_end 已证）。"""
    captured = []
    monkeypatch.setattr(generate, "chat_json", _fake_llm(captured))
    generate._gen_green(dict(KP_CTX), "基础", [])
    sys_content = captured[0][1][0]["content"]
    assert "系数位建议 2~5 的正整数" in sys_content
    assert "系数 1 会触发渲染守卫" in sys_content
    assert "1~5" not in sys_content  # 旧口径不再出现
    product = {**GREEN_PRODUCT_NEW, "params": {"a": [1]}}
    monkeypatch.setattr(generate, "chat_json", _fake_llm([], product))
    with pytest.raises(ValueError) as ei:
        generate._gen_green(dict(KP_CTX), "基础", [])
    assert "渲染瑕疵" in str(ei.value)


def test_substitution_equivalence_old_vs_new(monkeypatch):
    """替换等价性：同参数同模板内容、仅占位符记法不同 → 题干与 answer_sympy 一致；
    已知参数旧 {a} 照常替换（不原样下发），且无留痕（无非记号花括号）。"""
    prod_new = {"statement_template": "设 $a=<<a>>$，求 $<<a>>x^2$ 的最小值。",
                "params": {"a": [2]}, "answer_expr": "<<a>>**2/3+1"}
    prod_old = {"statement_template": "设 $a={a}$，求 ${a}x^2$ 的最小值。",
                "params": {"a": [2]}, "answer_expr": "{a}**2/3+1"}
    captured = []
    monkeypatch.setattr(generate, "chat_json", _fake_llm(captured, prod_new, solve_answer="7/3"))
    q_new = generate._gen_green(dict(KP_CTX), "基础", [])
    monkeypatch.setattr(generate, "chat_json", _fake_llm(captured, prod_old, solve_answer="7/3"))
    q_old = generate._gen_green(dict(KP_CTX), "基础", [])
    assert q_new["statement_md"] == q_old["statement_md"] == "设 $a=2$，求 $2x^2$ 的最小值。"
    assert q_new["answer_sympy"] == q_old["answer_sympy"] == "7/3"
    assert "legacy_note" not in q_old["verification"]


# --------------------------------------------------------------------------- #
# M2：过渡兼容（不误杀 + 留痕）
# --------------------------------------------------------------------------- #
def test_p001_real_stem_replay_no_block_with_legacy_note(monkeypatch):
    """p001 真实题干形态重放：\\frac{dy}{dx} 的 {dx} 不再误拦，题干 LaTeX 记号原样保留，
    verification.legacy_note 双路留痕（审查 m5）：未知 ['dx'] + \\命令 豁免分支
    ['aligned','dy']（{aligned}/{dy} 不静默吞）。"""
    captured = []
    monkeypatch.setattr(generate, "chat_json",
                        _fake_llm(captured, P001_PRODUCT, solve_answer="sin(1)/(1-cos(1))"))
    q = generate._gen_green(dict(KP_CTX), "基础", [], attempt=1)
    assert q.get("status") != "blocked_pending_human"
    assert r"\frac{dy}{dx}" in q["statement_md"]
    assert "t=1" in q["statement_md"]  # <<t0>> 已代入
    assert q["verification"]["legacy_note"] == \
        "legacy_placeholder_unknown:['dx'];legacy_command_args:['aligned', 'dy']"
    assert q["verification"]["solver_agree"] is True


def test_p007_p009_double_integral_form_replay(monkeypatch):
    """p007/p009 形态重放：\\iint\\limits_{D} 的 {D} 下标组不再误拦，留痕 ['D']。"""
    monkeypatch.setattr(generate, "chat_json",
                        _fake_llm([], DI_PRODUCT, solve_answer="4"))
    q = generate._gen_green(dict(KP_CTX), "基础", [], attempt=1)
    assert q.get("status") != "blocked_pending_human"
    assert r"\iint\limits_{D}" in q["statement_md"]
    assert q["verification"]["legacy_note"] == "legacy_placeholder_unknown:['D']"
    assert q["answer_sympy"] == "4"


def test_legacy_known_param_replaced_not_leaked(monkeypatch):
    """旧格式题的已知参数 {a} 精确替换保留：LLM 偶发仍输出 {a} 时不会把 {a} 原样发给用户。"""
    product = {"statement_template": "求 $f(x)={a}x$ 在 $x=1$ 处的导数值。",
               "params": {"a": [3]}, "answer_expr": "{a}"}
    monkeypatch.setattr(generate, "chat_json", _fake_llm([], product))
    q = generate._gen_green(dict(KP_CTX), "基础", [])
    assert q["statement_md"] == "求 $f(x)=3x$ 在 $x=1$ 处的导数值。"
    assert "{a}" not in q["statement_md"]


# --------------------------------------------------------------------------- #
# M3：确定错守卫补强（系数 1 未消）
# --------------------------------------------------------------------------- #
def test_coeff_one_flaws_blocked():
    """确定错：系数 1 未消形态（p001 实测 ``x=1(t-\\sin t)`` 及 1x 变体）必须被拦。"""
    assert generate.render_flaws(r"设 $\left\{\begin{aligned}x=1(t-\sin t)\\ y=1-\cos t\end{aligned}\right.$，求 $\frac{dy}{dx}$。")
    assert generate.render_flaws("求 $y=1x+3$ 的斜率。")
    assert generate.render_flaws(r"求 $y=1\left(t-\sin t\right)$ 的导数。")
    assert generate.render_flaws("f(x)=1x^2") is not None  # 既有 1x\^ 用例不回归


def test_coeff_one_legit_forms_clean():
    """合法形态零误伤：Rational(1,3)（括号在 1 前）、f(1)（自变量位）、a_1(（下标豁免）、
    11x / 21(（数字前缀豁免）、2.1x / 1.1x（小数简写，审查 m4）、图1(a)（紧邻中文行文，
    审查 m4；空格变体"图 1(a)"不豁免——豁免空格会放走 "y = 1x" 主缺陷形态）。"""
    assert generate.render_flaws("答案用 Rational(1,3) 表示。") is None
    assert generate.render_flaws("求曲线 $f(1) = 2$ 处的切线。") is None
    assert generate.render_flaws(r"设 $a_1(x-1)$ 为首项。") is None
    assert generate.render_flaws("设 $11x$ 与 $21(t+1)$。") is None
    assert generate.render_flaws(r"\frac{1}{3}x 的渐近线。") is None
    assert generate.render_flaws(r"逆矩阵 $\lambda^{-1}(x)$ 记号。") is None
    assert generate.render_flaws(r"通项 $2.1x$ 与 $1.1x$。") is None  # 小数简写（m4）
    assert generate.render_flaws("对照 图1(a) 与 图1(b)。") is None  # 中文行文（m4）


def test_packs_all_family_statements_zero_flaw_hit():
    """零误伤验收：packs 三包全部族 + root 六族的 statement_md 全量（实测 584 条）过守卫
    断言零命中（render_flaws 清单扩强后既有合法题干不受影响；规模收紧防扫描面缩水，
    审查 m9）。只扫 statement_md——分析文本的 e^(1x) 类既有形态属 v2api 家族直出过滤面
    职责（该面在改动前即存在；ode 族 analysis 治理登记下一批，见 09 号文档）。"""
    import pack_loader as pl
    import families as root_families

    checked = 0
    for subject in ("calculus", "linear", "probability"):
        pack = pl.load_pack(subject)
        for fam, mod in pack["families"].items():
            for it in mod.enumerate_family(limit=24):
                s = it.get("statement_md") or ""
                assert generate.render_flaws(s) is None, f"{subject}/{fam}: {s}"
                checked += 1
    for fam in root_families._families_spec():
        for it in root_families.enumerate_family(fam, limit=12):
            s = it.get("statement_md") or ""
            assert generate.render_flaws(s) is None, f"root/{fam}: {s}"
            checked += 1
    assert checked == 584  # 实测规模（审查 m9）：族产出异常时这里先炸


# --------------------------------------------------------------------------- #
# M4：报错人话化 + 瞬态加固
# --------------------------------------------------------------------------- #
def test_missing_answer_expr_field_human_error(monkeypatch):
    """LLM 漏 answer_expr 字段 → 人话 ValueError（旧代码裸 KeyError('answer_expr')）。"""
    product = {"statement_template": "求 $f(x)=x$ 的导数。", "params": {"a": [1]}}
    monkeypatch.setattr(generate, "chat_json", _fake_llm([], product))
    with pytest.raises(ValueError) as ei:
        generate._gen_green(dict(KP_CTX), "基础", [])
    assert "LLM 未按契约输出 answer_expr 字段" in str(ei.value)
    # 全链路：拦截卡 error 同为人话
    monkeypatch.setattr(generate, "chat_json", _fake_llm([], product))
    q = generate.generate_question(dict(KP_CTX), difficulty="基础", qtype="calculation")
    assert q["status"] == "blocked_pending_human"
    assert "LLM 未按契约输出 answer_expr 字段" in q["error"]


def test_missing_statement_template_field_human_error(monkeypatch):
    product = {"answer_expr": "1", "params": {"a": [1]}}
    monkeypatch.setattr(generate, "chat_json", _fake_llm([], product))
    with pytest.raises(ValueError) as ei:
        generate._gen_green(dict(KP_CTX), "基础", [])
    assert "LLM 未按契约输出 statement_template 字段" in str(ei.value)


def test_missing_empty_string_answer_expr_field_human_error(monkeypatch):
    """空串字段同按缺失处理（裸 d["answer_expr"] 时代空串会走更深的谜语报错）。"""
    product = {"statement_template": "求 $f(x)=x$ 的导数。", "params": {"a": [1]}, "answer_expr": "  "}
    monkeypatch.setattr(generate, "chat_json", _fake_llm([], product))
    with pytest.raises(ValueError) as ei:
        generate._gen_green(dict(KP_CTX), "基础", [])
    assert "LLM 未按契约输出 answer_expr 字段" in str(ei.value)


# ---- llm.chat 空响应瞬态加固（假 client 设施同 test_llm_errors）---- #
class _FakeClient:
    """仿 OpenAI client：chat.completions.create 按脚本逐次返回。"""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        self.calls += 1
        item = self._script[self.calls - 1]
        if isinstance(item, Exception):
            raise item
        return item


def _resp(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


@pytest.fixture()
def _offline_llm(monkeypatch, tmp_path):
    """conftest 已密封（DEMO=1 + tmp 缓存目录）；本组用例走网络路径分支，显式离线化：
    DEMO=0 + 假 client + sleep 打桩（不触真实网络，仅走分类重试代码路径）。"""
    monkeypatch.setenv("MATHFORGE_DEMO", "0")
    monkeypatch.setenv("MATHFORGE_CACHE_DIR", str(tmp_path / "llm_cache"))
    sleeps: list[float] = []
    monkeypatch.setattr(llm.time, "sleep", sleeps.append)
    return sleeps


def test_empty_response_twice_then_success(monkeypatch, _offline_llm, tmp_path):
    """空响应两次 → 第三次成功（模块 M4：模型偶发空回不再一击毙命）。"""
    fake = _FakeClient([_resp(""), _resp("  \n"), _resp('{"answer": "ok"}')])
    monkeypatch.setattr(llm, "_get_client", lambda: fake)
    out = llm.chat([{"role": "user", "content": "hi"}], namespace="tempty")
    assert out == '{"answer": "ok"}'
    assert fake.calls == 3
    assert _offline_llm == [2, 4]  # 沿用既有退避节奏
    # 空串不落缓存：唯一缓存文件是成功结果（落独立键，见下例）
    assert len(list((tmp_path / "llm_cache").glob("*.json"))) == 1


def test_empty_retry_success_lands_on_independent_cache_key(monkeypatch, _offline_llm, tmp_path):
    """空回后的成功采样落「独立缓存键」（与 attempt 命名空间机制同一思想：重试是
    不同采样、键也独立）；原键无缓存条目。"""
    fake = _FakeClient([_resp(""), _resp("ok")])
    monkeypatch.setattr(llm, "_get_client", lambda: fake)
    assert llm.chat([{"role": "user", "content": "hi"}], namespace="tind") == "ok"
    payload = {"m": [{"role": "user", "content": "hi"}], "model": "deepseek-chat",
               "t": 1.0, "j": False}
    orig_key = hashlib.sha256(("tind" + json.dumps(payload, ensure_ascii=False)).encode()) \
        .hexdigest()[:40]
    files = [f.stem for f in (tmp_path / "llm_cache").glob("*.json")]
    assert len(files) == 1
    assert files[0] != orig_key  # 唯一缓存条目在独立键上，原键无缓存


def test_empty_cache_hit_returned_as_is(monkeypatch, _offline_llm, tmp_path):
    """缓存命中路径不变：历史空串条目原样返回、零重试、零 client 调用。"""
    payload = {"m": [{"role": "user", "content": "hi"}], "model": "deepseek-chat",
               "t": 1.0, "j": False}
    key = hashlib.sha256(("thit" + json.dumps(payload, ensure_ascii=False)).encode()) \
        .hexdigest()[:40]
    cp = tmp_path / "llm_cache" / f"{key}.json"
    cp.parent.mkdir(parents=True, exist_ok=True)  # _cache_path 只在 chat 调用时建目录
    cp.write_text('{"text": "", "model": "deepseek-chat", "ts": 0}', encoding="utf-8")
    sentinel = _FakeClient([AssertionError("缓存命中不应触发 client")])
    monkeypatch.setattr(llm, "_get_client", lambda: sentinel)
    assert llm.chat([{"role": "user", "content": "hi"}], namespace="thit") == ""
    assert sentinel.calls == 0
    assert _offline_llm == []  # 零退避（未进重试链）


def test_all_empty_responses_exhaust_retries(monkeypatch, _offline_llm):
    """连续空回 3 次 → Retryable 耗尽报错（人话含「空响应」），不静默返回空串。"""
    fake = _FakeClient([_resp(""), _resp(""), _resp("")])
    monkeypatch.setattr(llm, "_get_client", lambda: fake)
    with pytest.raises(llm.LLMRetryableError) as ei:
        llm.chat([{"role": "user", "content": "hi"}], namespace="tdry")
    assert fake.calls == 3
    assert "空响应" in str(ei.value)
    assert _offline_llm == [2, 4, 6]  # 三次退避后耗尽


# --------------------------------------------------------------------------- #
# M5：定理型考点变式构造提示
# --------------------------------------------------------------------------- #
def test_variant_hint_injected_for_rolle(monkeypatch):
    captured = []
    monkeypatch.setattr(generate, "chat_json", _fake_llm(captured))
    generate._gen_green({"kp": "罗尔定理"}, "基础", [], variant_of="原题：设 f(x)=x² 于 [0,2]。")
    user = captured[0][1][1]["content"]
    assert "【构造提示】" in user
    assert "罗尔类变式" in user and "g(x)·(x−a)(x−b)" in user
    assert "端点函数值天然相等" in user


def test_variant_hint_injected_for_lagrange_via_name_field(monkeypatch):
    """kp 为斜杠路径 id、name 为中文名时（v2 侧两字段口径）hint 同样命中；
    calc.lagrange 现经 id 英文短名别名直接命中（审查 m8）。"""
    captured = []
    monkeypatch.setattr(generate, "chat_json", _fake_llm(captured))
    generate._gen_green({"kp": "calc.lagrange", "name": "拉格朗日中值定理"},
                        "基础", [], variant_of="原题：设 f(x)=x² 于 [0,2]。")
    user = captured[0][1][1]["content"]
    assert "拉格朗日类变式" in user and "【构造提示】" in user


def test_variant_hint_injected_for_rolle_via_kp_id(monkeypatch):
    """v1 斜杠 kp id 路径（审查 m8）：kp="calc.rolle" 经英文短名别名命中罗尔 hint。"""
    captured = []
    monkeypatch.setattr(generate, "chat_json", _fake_llm(captured))
    generate._gen_green({"kp": "calc.rolle"}, "基础", [],
                        variant_of="原题：设 f(x)=x² 于 [0,2]。")
    user = captured[0][1][1]["content"]
    assert "【构造提示】" in user and "罗尔类变式" in user
    assert "g(x)·(x−a)(x−b)" in user


def test_variant_payload_byte_identical_without_hint(monkeypatch):
    """无命中 kp：变式载荷与无 hint 构造逐字节一致（缓存键稳定，红线）。"""
    captured = []
    monkeypatch.setattr(generate, "chat_json", _fake_llm(captured))
    generate._gen_green(dict(KP_CTX), "基础", [ANCHOR], variant_of="原题干")
    expected_user = (generate._base_user(KP_CTX, "基础")
                     + generate._format_few_shot([ANCHOR])
                     + "\n\n【变式要求】以原题为母本做参数扰动（§三），结构与步数不变：\n原题干")
    assert captured[0][1][1]["content"] == expected_user


def test_variant_hint_not_injected_without_variant_of(monkeypatch):
    """非变式路径（variant_of 为空）不注入 hint——罗尔 kp 的常规出题载荷不受影响。"""
    captured = []
    monkeypatch.setattr(generate, "chat_json", _fake_llm(captured))
    generate._gen_green({"kp": "罗尔定理"}, "基础", [])
    assert "构造提示" not in captured[0][1][1]["content"]


# --------------------------------------------------------------------------- #
# M6：命名空间代次
# --------------------------------------------------------------------------- #
def test_namespace_green_v9():
    """gen-green v9（占位符契约换代 + 系数位契约对齐）；yellow 无参数化模板不动 v6；
    ablation 臂不随动。"""
    spec = importlib.util.spec_from_file_location(
        "generate_fresh_m", os.path.join(ROOT, "generate.py"))
    fresh = importlib.util.module_from_spec(spec)
    ablation = os.environ.get("MATHFORGE_ABLATION")
    if ablation:
        del os.environ["MATHFORGE_ABLATION"]  # fresh 实例按默认（非 ablation）分支断言
    try:
        spec.loader.exec_module(fresh)
    finally:
        if ablation:
            os.environ["MATHFORGE_ABLATION"] = ablation
    assert fresh._NS_GREEN == "gen-green:v9:a{}:"
    assert fresh._NS_YELLOW == "gen-yellow:v6:a{}:"
    assert generate._NS_GREEN.startswith("gen-green-ab:v2") if generate._ABLATION \
        else generate._NS_GREEN == "gen-green:v9:a{}:"
