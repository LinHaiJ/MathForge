"""MathForge 出题引擎（T6）：加载出题专家 skill + 真题 few-shot 锚 + 双级验证。

- 🟢 绿标（计算题）：LLM 产出参数化模板，标准答案由 SymPy 从参数计算（构造即正确），
  再加一次 LLM 盲解对账（独立求解 vs SymPy 答案，check_answer 判等）拦截题干-答案错位。
- 🟡 黄标（概念/排序/选择）：生成后由两次独立 LLM 盲做，答案一致才通过（PRD §7）。
- 伪考点闸门（模块 J）：在线态盲解兼任「问法合规」审题人——考点装饰性问法
  （定理只是外衣、纯代数可解）打回重生成/拦截 [待人工]；demo 态载荷逐字节不变
  （见 _BAN_FAKE_KP_SUFFIX 注释块）。判分权不在 LLM：答案对错仍由 SymPy/双盲终审。
- 全部 LLM 调用走 llm.py 磁盘缓存；验证失败的题绝不外推（推题纪律）。
"""

from __future__ import annotations

import json
import os
import random
import re
from pathlib import Path

from llm import chat_json
from families import enumerate_family
from verify import check_answer, is_valid_expr

# Skill ablation 开关（P0-2 A/B 实验）：MATHFORGE_ABLATION=1 时剥离 SKILL.md，
# 其余 prompt 完全一致；缓存命名空间同步升版防串（默认关闭，行为与基线完全一致）。
_ABLATION = os.environ.get("MATHFORGE_ABLATION") == "1"
# 命名空间代次：载荷文本变化 → 载荷 hash 变化 → 旧缓存自然失效无串染；按仓库惯例
# bump 一版与旧代次彻底隔离。代次史：模块 H 锚池扩容（green v5→v6 / yellow v4→v5）；
# 模块 J 伪考点闸门+SKILL 禁止清单代次——在线态 system prompt 末尾追加「考点装饰性
# 问法❌」禁止后缀、盲解追加问法合规指令（demo 态后缀为空、载荷逐字节不变，但同一
# 命名空间无法区分新旧在线代次，仍须 bump：green v6→v7 / yellow v5→v6；ablation 臂
# 同步 v1→v2——后缀对两臂同等生效，见 _BAN_FAKE_KP_SUFFIX 注释）；
# 模块 M 占位符契约换代——绿标出题契约占位符 {name} → <<name>>（_GREEN_SYSTEM 改写，
# demo/在线两态共享同一契约文本，绿标载荷变化由本代次与旧缓存隔离；演示预热重跑
# 本就欠账待用户执行，见 docs/2026-09-12_夜间优化/09 号）。yellow 无参数化模板，不动；
# 模块 M 审查——系数位契约对齐：契约「系数位 1~5」与渲染守卫「1x/1( 确定错拦截」
# 自打架，改「系数位 2~5（系数 1 会触发渲染守卫）」，green v8→v9。
_NS_GREEN = "gen-green-ab:v2:a{}:" if _ABLATION else "gen-green:v9:a{}:"
_NS_YELLOW = "gen-yellow-ab:v2:a{}:" if _ABLATION else "gen-yellow:v6:a{}:"


def generate_from_family(spec: dict) -> dict:
    """K1/Day3 修复路径：确定性模板族出题（families.py）。

    数学正确性由 SymPy 从题目结构推导保证（断言内建于 spec）；
    LLM 仅负责解析实例化；盲解对账降级为 advisory——若判官与构造不一致，
    题目仍外推但打 judge_flag 留档（构造可证 > 判官直觉）。
    """
    q = {
        "kp": spec["kp"], "difficulty": spec["difficulty"], "qtype": "calculation",
        "verify_level": "green", "statement_md": spec["statement_md"],
        "answer_sympy": spec["answer_sympy"], "answer_md": None,
        "options": None, "correct": None, "family": spec["family"], "params": spec["params"],
        "sympy_asserts": {k: bool(v) for k, v in spec["assert"]},
    }
    if not all(q["sympy_asserts"].values()):
        q["status"] = "blocked_pending_human"
        q["error"] = "SymPy 断言失败（不该发生：家族枚举已过滤）"
        return q
    solver_ans, raw = _blind_solve(q["statement_md"], None)
    agree = solver_ans is not None and check_answer(solver_ans, spec["answer_sympy"])
    if not agree:
        s2, raw2 = _blind_solve(q["statement_md"], None, verify_mode=True)
        agree = s2 is not None and check_answer(s2, spec["answer_sympy"])
        raw += f" | 复核解={s2}"
    q["verification"] = {"solver_agree": bool(agree), "solver_ans": solver_ans, "raw": raw[:200]}
    if not agree:
        q["judge_flag"] = "solver_mismatch_but_sympy_provable"
    q["analysis"] = _instantiate_analysis(q)
    return q

_HERE = Path(__file__).resolve().parent
SKILL_PATH = _HERE / "skills" / "math-examiner" / "SKILL.md"
QUESTION_POOL = _HERE / "题集" / "cxyonly.jsonl"
# 模块 H（真题锚池扩容）：全量真题锚池，由 scripts/build_anchor_pool.py 从 cxy_raw 产出
# （gitignored，S0-S6 惯例：产出落 cache/）。存在即优先于 cxyonly 使用。
ANCHOR_POOL = _HERE / "cache" / "anchor_pool.jsonl"


def load_skill() -> str:
    if _ABLATION:  # B 臂：剥离 SKILL.md（A/B 实验对照臂）
        return ""
    return SKILL_PATH.read_text(encoding="utf-8")


def _load_jsonl(path: Path) -> list[dict]:
    """逐行解析 JSONL（抗审 major-1 设防）：单行 JSONDecodeError（截断/垃圾）跳过不炸，
    非 dict 条目忽略；条目若缺必备键则由 generate_question 的重试链兜底（拦截[待人工]），
    不在本函数静默放行。"""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:  # json.JSONDecodeError ⊂ ValueError：坏行跳过
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _kp_hit(q: dict, kp: str) -> bool:
    """kp 命中判据（与 score 中的语义逐字一致）：输入 kp 切词后任一 token 是锚条目 kp 字段子串。"""
    return bool(kp) and any(tok and tok in (q.get("kp") or "") for tok in kp.split())


def pick_few_shot(kp: str, qtype: str, k: int = 2) -> list[str]:
    """从真题池选 k 道风格锚（评分仅三项：同题型 +2、kp 词命中 +3、有解析 +1）。

    锚源与回退（模块 H 锚池扩容 + 抗审 major-2 合并保底）：
      1. cache/anchor_pool.jsonl（约 1.6k 道全量真题池，scripts/build_anchor_pool.py 产出）；
      2. 合并保底：锚池对当前 kp 零命中时，并入 题集/cxyonly.jsonl（128 道人工清洗子集）
         的同 kp 候选（按 id 去重），保住旧池覆盖下限；
      3. 锚池缺失或解析为空 → 回退 cxyonly（历史行为）；两池皆无 → 空列表
         （skill 降级用 §四风格锚）。
    条目 schema 两池对齐（statement_md/options/answer/analysis/kp/type 必备），评分与
    渲染逻辑共用。kp 匹配语义不变：输入 kp（斜杠路径 id 或中文名，kp_context["kp"]
    通常是 kp_graph 中文名）按空白切词，任一 token 是锚条目 kp 字段子串即 +3；
    锚池 kp 字段记录的 kp 名均来自 kp_graph 词表，与输入同一词汇空间。
    写入侧双保险：build_anchor_pool 以 .tmp + os.replace 原子落盘，构建中断不留残缺池；
    即便残缺，上方逐行容错也保证可用行正常出锚。
    锚池内容变化 → few-shot 文本变化 → 载荷 hash 变化，旧缓存自然失效无串染
    （命名空间代次同步 bump，见 _NS_GREEN/_NS_YELLOW 注释）。
    """
    pool: list[dict] = []
    if ANCHOR_POOL.exists():
        pool = _load_jsonl(ANCHOR_POOL)
    if pool and not any(_kp_hit(q, kp) for q in pool) and QUESTION_POOL.exists():
        # 合并保底（major-2）：池内 kp 命中为零 → 并入 cxyonly 的同 kp 候选。
        # 按 id 去重 + 同 id 顶替：cxyonly 的 128 个 id 全部 ⊆ 锚池（同一身份空间），
        # 同 id 且池内副本恰恰无 kp 标注时，用 cxyonly 精标副本顶替该槽位——
        # id 在合并列表中仍唯一，且精标 kp 得以参与评分（否则保底被去重架空）。
        by_id = {q.get("id"): i for i, q in enumerate(pool)}
        for q in _load_jsonl(QUESTION_POOL):
            if not _kp_hit(q, kp):
                continue
            i = by_id.get(q.get("id"))
            if i is None:
                pool.append(q)
                by_id[q.get("id")] = len(pool) - 1
            elif not _kp_hit(pool[i], kp):
                pool[i] = q
    if not pool and QUESTION_POOL.exists():
        pool = _load_jsonl(QUESTION_POOL)  # 锚池缺失/解析为空 → 回退 cxyonly（现行为）
    if not pool:
        return []
    want_type = "single_choice" if qtype == "choice" else "subjective"

    def score(q):
        s = 0
        if q["type"] == want_type:
            s += 2
        if kp and any(tok and tok in (q["kp"] or "") for tok in kp.split()):
            s += 3
        if q.get("analysis"):
            s += 1
        return -s

    ranked = sorted(pool, key=score)
    out = []
    for q in ranked:
        if len(out) >= k:
            break
        opts = q.get("options")
        if opts:
            parts = [o if isinstance(o, str) else f"{o.get('label', '')}. {o.get('content_md', '')}"
                     for o in opts]
            opts = f"\n选项：{' | '.join(p for p in parts if len(p) > 2)}"
        else:
            opts = ""
        out.append(
            f"【真题锚 {len(out)+1}】{q['statement_md']}{opts}\n答案：{q['answer']}\n解析：{q['analysis'][:260]}"
        )
    return out


_GREEN_SYSTEM = """{skill}

【本次任务：出 1 道🟢绿标计算题】额外输出契约（覆盖 §九 中的 answer 字段要求）：
- "statement_template"：题干模板，参数用 <<name>> 占位（如 <<a>>；花括号一律留给 LaTeX
  记号，禁止用花括号形态写参数——那是旧契约，会与 \\frac{{dy}}{{dx}} 之类记号冲突），
  条件自含、单题单问。
  参数只出现在数值位，且候选值应使代入后式子自然（系数位建议 2~5 的正整数，
  系数 1 会触发渲染守卫被按确定错拦截；也避免代入后出现 "+ -3x" 或 "+ 0" 的丑形态）；
  禁止在题干中出现"（xx 为常数）"之类元注释，常数项直接省略或自然融入
- "params"：{{"参数名": [候选值1, 候选值2, ...]}}，全部是小整数或简分数，至少 2 个候选
- "answer_expr"：SymPy 语法的标准答案表达式，可含 <<name>> 占位参数（如 "<<a>>**2/3+1"），
  最终值由程序代入参数计算——你必须保证它数学上就是该题的正确答案
【示范（answer_expr 必须真正随参数变化）】
  statement_template: "求 $f(x)=<<a>>x^2+<<b>>x$ 在 $x=1$ 处的导数值。"
  params: {{"a": [1,2,3], "b": [1,2]}}，answer_expr: "2*<<a>>+<<b>>"
  反例（禁止）：中值定理"求 ξ"类写不出可靠参数化闭式时，改出导数值/简单积分/极限等直接计算题
只输出 JSON。"""

_YELLOW_SYSTEM = """{skill}

【本次任务：出 1 道🟡黄标{qtype_label}】答案必须是可规范化短串（独立解题者只凭题面作答，必须可判卷）：
- 概念判断→题面必须是"判断如下陈述是否正确：…"，答案只有 正确 或 错误（陈述要客观、无歧义、教科书可判）
- 排序→题面给出带标号的步骤，答案如 B→A→C（用题面标号体系，唯一确定）
- 单项选择→恰好 4 个选项，答案为单个字母
只输出 JSON（按 §九 契约，含 options/correct 若为选择题）。"""

# ---------------------------------------------------------------------------
# 模块 J：伪考点闸门（在线态专属；demo 载荷逐字节不变）
#
# 背景：LLM 出题路径（绿标计算/黄标概念）出现过「考点装饰性问法」——如罗尔定理题
# 只问"解 f'(x)=0"，解题根本不需要罗尔定理（定理只是外衣）。本闸门让盲解者在解题时
# 顺带判断「这道题是否纯代数/机械计算即可求解、标注考点未承重」，是则打回重生成，
# 仍不合规则拦截 [待人工]。判分权不在 LLM：闸门只拦「教学价值」（问法合规），
# 答案对错仍由 SymPy 构造（绿）/双盲一致（黄）终审。
#
# demo 红线（定案）：SKILL.md 的内容经 load_skill() 同时进 demo/在线两态的 system
# prompt——改 SKILL.md 一个字节都会使 demo 预热缓存全量失效（演示直接炸）。因此
# 「考点装饰性问法❌」不写入 SKILL.md，改为 _BAN_FAKE_KP_SUFFIX 以「demo 态后缀为空、
# 在线态追加到 system 末尾」的方式生效，盲解侧指令同理（kp_label demo 态恒 None）：
# demo 态全部载荷与旧版逐字节一致，预热缓存零影响；在线态载荷变化由命名空间代次
# bump（green v7 / yellow v6）与旧代隔离。家族路径（generate_from_family）不参与
# 闸门——它是零 API 演示路径，一个字节都不许变，且其判分权在 SymPy 构造、盲解仅
# advisory 留档，问法合规无拦截意义。
# ---------------------------------------------------------------------------


def _demo_mode() -> bool:
    """demo 判定（口径同 v2/router._demo_mode / v2/v2intent._demo_mode）：
    MATHFORGE_DEMO=1 或无 DEEPSEEK_API_KEY。伪考点闸门只在在线态生效。"""
    if os.environ.get("MATHFORGE_DEMO") == "1":
        return True
    return not os.environ.get("DEEPSEEK_API_KEY")


_FAKE_KP_ERROR = "问法合规：纯代数可解，考点未承重（考点装饰性问法，定理只是外衣）"

# 在线态出题 system prompt 专属后缀（J2：SKILL.md 禁止清单条款的等效落点，见上）。
# 判据与 _FAKE_KP_INSTRUCTION 同一显式二分（对抗审查 major-1）：「考点核心运算即解题
# 运算」（求导/积分/展开/求极限本身是考点承重的工作，如泰勒求极限/二重积分/渐近线/
# 微分近似）是合规侧，不因「直接计算可解」被当成装饰；装饰仅指「考点名只是标签」。
_BAN_FAKE_KP_SUFFIX = """

【命题红线补充】禁止「考点装饰性问法」（考点名只是标签，定理只是外衣）❌
注意二分：考点核心运算本身就是解题运算（该考点要求的工作就是求导/积分/展开/
求极限/近似计算等）→ 合规，放心出（正例：泰勒展开求极限、二重积分计算、
求渐近线、微分近似）；遮住考点名后题面照常成立、解法完全用不到该考点的任何
知识或定理性质 → 不合规，必须重新设计。
反例：罗尔定理题只问"解 f'(x)=0"（纯代数可解，罗尔定理全程未承重）。"""

# 在线态盲解 system prompt 专属后缀（J1：kp_label 非空时追加；{kp} 为考点中文名）。
# 判据显式二分（对抗审查 major-1 修复）：「直接求导/积分/化简可解」不再等于 FAKE——
# 计算型考点的核心运算本身就是解题运算（泰勒求极限/二重积分/渐近线/微分近似均合规）；
# FAKE 只留给「考点名仅是标签」的装饰性问法，并保留「拿不准按合规」的豁免条款。
_FAKE_KP_INSTRUCTION = """

【问法合规检查】本题标注考点为「{kp}」。请做二分判断：
一、考点核心运算本身就是解题运算（该考点要求你做的工作就是求导/积分/展开/求极限/
近似计算等）→ 合规，正常作答。正例：用泰勒展开求极限、二重积分计算、求曲线渐近线
（需算极限定斜率截距）、微分近似计算（核心就是微分）。
二、考点名只是外衣标签：遮住考点名后题面照常成立，解法完全用不到该考点的任何知识、
定理前提或性质（纯代数即可机械求解）→ 输出 {{"answer": "FAKE_KP"}} 而非正常作答。
反例：罗尔定理题只解 f'(x)=0（纯代数，罗尔定理未承重）、拉格朗日中值定理题解方程
求 ξ（解方程即可，定理全程未用）。
拿不准时按合规处理、正常作答。"""


def _is_fake_kp(ans: str | None) -> bool:
    """盲解 FAKE_KP 判定（抗规范化扰动）：去下划线大写后比较，FAKE_KP/FAKEKP/fake_kp 均认。
    正常数学答案不可能归一成 FAKEKP，无误伤面。"""
    return (ans or "").replace("_", "").upper() == "FAKEKP"


def _kp_gate_label(kp_context: dict) -> str | None:
    """伪考点闸门的考点标签：取 kp_context 的 kp（v2 传入 kp["name"] 中文名；
    题库变式路径可能是斜杠路径 id，作为标签仍有判别力）。demo 态恒 None——
    盲解 payload 与旧版逐字节一致（预热缓存安全）。"""
    if _demo_mode():
        return None
    kp = str(kp_context.get("kp") or kp_context.get("name") or "").strip()
    return kp or None

_SOLVE_SYSTEM = """你是独立的考研数学解题者。只根据题面独立作答，禁止臆测出题人意图。
作答前先检查题面前提是否自洽（例：罗尔定理要求端点函数值相等、所求点必须落在给定开区间内；
参数化题目代入具体值后条件是否仍成立）。若发现前提自相矛盾或无解，输出 {"answer": "PREMISE_BROKEN"}。
选择→只输出字母；排序→只输出如 A→B→C（用题面标号体系，输出完整序列）；
判断→只输出 正确 或 错误；计算→只输出最终结果的 SymPy 表达式
（常数用 E/pi、分数用 Rational(a,b)、根号用 sqrt()、自然对数必须写 log() 禁用 ln）。
只输出 JSON：{"answer": "<规范化短串>"}"""

# 渲染瑕疵守卫：题干出现即拒绝（PRD §13 验收线"渲染正确"）。
# 模块 M3 补「系数 1 未消」确定错（模式即证据，无猜测面；清单全保留不降级）：
#   (?<![.\d_中文])1\(        x=1(t-\sin t)：系数 1 乘括号。负向断言豁免——数字/下标前缀
#                        （Rational(1,3) 是 "(1" 不命中、a_1( 下标、11( 数字）、
#                        小数简写（2.1x/1.1x，审查 m4）、紧邻中文行文（图1(a)，审查 m4；
#                        空格变体"图 1(a)"不豁免——豁免空格会放走 "y = 1x" 主缺陷形态）；
#   (?<![.\d_中文])1\\left\(  同上的 \left( 定界括号形态；
#   (?<![.\d_中文])1x         1x+3 / 1xy：系数 1 乘变量（泛化既有 1x\^ 的非幂形态）。
# 零误伤基线：packs 三包全部族 + root 六族 statement_md 共 584 条断言零命中
# （tests/test_placeholder_contract.py 扫描守住）；分析文本的 e^(1x) 类既有形态由
# v2api 家族直出的过滤面消化（v2api 对 statement/analysis 双过滤，非本清单职责扩张；
# ode 族 analysis 治理登记下一批，见 09 号文档）。
_RENDER_FLAWS = [r"1x\^", r"\+\s*0(?![\.\d])", r"（[^（）]*为常数）", r"\+\s*-",
                 r"1\\(sin|cos|tan|cot|log|ln)",
                 r"(?<![.\d_\u4e00-\u9fff])1\(", r"(?<![.\d_\u4e00-\u9fff])1\\left\(",
                 r"(?<![.\d_\u4e00-\u9fff])1x"]


def render_flaws(statement: str) -> "str | None":
    """渲染瑕疵检查（2026-09-12：包族直出路径也须过守卫，见 v2api/_try_pack_family）。
    命中返回首个瑕疵的模式说明；干净返回 None。"""
    for pat in _RENDER_FLAWS:
        if re.search(pat, statement or ""):
            return pat
    return None


# 新契约占位符（模块 M1）：<<name>> —— LaTeX 不可能出现的定界符。正则只认这个形态，
# 精确零误判：旧 {name} 与 LaTeX 花括号（\frac{dy}{dx}、_{D}）无法靠正则区分，
# 「猜哪个花括号是记号」正是三代误拦的根因，换代后从源头消灭猜测。
# 字符集放宽（M 审查 m4）：[A-Za-z_][A-Za-z0-9_]* —— <<>> 定界符 LaTeX 不可能出现，
# 放宽零误伤风险，堵 <<A>> 大写参数静默漏发通道。
_RE_PLACEHOLDER = re.compile(r"<<([A-Za-z_][A-Za-z0-9_]*)>>")
# 怪名兜底（M 审查 m4）：数字开头/含空格等非法标识符形态同样不可能出现在合法 LaTeX
# 里，任何残余 <<...>> 都是未代入占位符——一并计入未知集，杜绝一切静默漏发。
_RE_PLACEHOLDER_RESIDUAL = re.compile(r"<<([^<>]{1,60})>>")


def _unknown_placeholders(text: str, params: dict) -> set[str]:
    """找出未代入的 <<name>> 占位符（新契约，精确零误判）。

    模块 M1 重写：旧版用 ``{([a-zA-Z_]+)}`` 加花括号前缀启发式猜参数记号，
    ``\\frac{dy}{dx}`` 的 ``{dx}``、``\\iint\\limits_{D}`` 的 ``{D}`` 被误判为未代入
    参数而拦题（用户盲评实测误拦 3-4/10）。契约换代后参数只可能是 <<名>>，命名不在
    params 即确定错，照旧拦截；旧 {name} 形态交 _legacy_placeholders 留痕、不再拦截。
    """
    unknown = {m.group(1) for m in _RE_PLACEHOLDER.finditer(text or "")
               if m.group(1) not in params}
    # 怪名兜底：数字开头/含空格/超长等 <<...>> 残余（定界符不可能属合法 LaTeX）
    unknown |= {m.group(1) for m in _RE_PLACEHOLDER_RESIDUAL.finditer(text or "")
                if m.group(1) not in params}
    return unknown


def _legacy_placeholders(text: str, params: dict) -> tuple[set[str], set[str]]:
    """旧契约 {name} 的形态检测（模块 M2：只留痕，不拦截）。

    返回 (未知参数形态, 命令参数形态)：
    - 未知参数形态：已知参数的 {name} 已被精确替换（str.replace 无歧义），剩下的
      {name} 绝大多数是 LaTeX 记号（``\\frac{dy}{dx}`` 的 {dx}、``_{D}``）——判哪个
      花括号是记号属于猜测，猜测必误伤，故降级为留痕（不杀题）；
    - 命令参数形态（M 审查 m5）：{ 前紧跟反斜杠命令的 {word}（``\\frac{dy}``、
      ``\\begin{aligned}``）按 R3 教训豁免不计入未知——但豁免分支同样留痕：
      静默吞掉「LLM 真把参数写进 \\命令 参数位」的缺失不可观测。
    两路都记入 verification.legacy_note，供盲评破绽清单统计旧契约残存量。
    """
    unknown: set[str] = set()
    cmd_args: set[str] = set()
    for m in re.finditer(r"\{([a-zA-Z_]+)\}", text or ""):
        prefix = text[max(0, m.start() - 24):m.start()]
        if re.search(r"\\[a-zA-Z]+$", prefix):
            cmd_args.add(m.group(1))
            continue
        if m.group(1) not in params:
            unknown.add(m.group(1))
    return unknown, cmd_args


def _base_user(kp_context: dict, difficulty: str) -> str:
    parts = [
        f"知识点：{kp_context.get('kp', kp_context.get('name', ''))}",
        f"定义：{kp_context.get('definition_md', '')}",
    ]
    if kp_context.get("key_formulas"):
        parts.append("关键公式：" + "；".join(kp_context["key_formulas"][:4]))
    if kp_context.get("common_mistakes"):
        parts.append("常见错误（可作陷阱素材）：" + "；".join(kp_context["common_mistakes"][:3]))
    parts.append(f"难度目标：{difficulty}")
    # 模块 F3（记忆×出题闭环）：kp_context 可选键 memory_context（学生错因记忆块，
    # 由 v2 侧 mem2.recall_for_generation + format_memory_block 产出）。
    # 仅在非空字符串时追加到末尾：空/缺失时输出与无记忆版本逐字节一致
    # （缓存键稳定，旧缓存可继续命中）；非 str 值（脏数据）一律忽略。
    memory = kp_context.get("memory_context")
    if isinstance(memory, str) and memory.strip():
        parts.append(memory.strip())
    return "\n".join(parts)


def _format_few_shot(blocks: list[str]) -> str:
    if not blocks:
        return ""
    return "\n\n【真题风格锚（模仿其表述与难度，禁止抄题）】\n" + "\n\n".join(blocks)


def _blind_solve(statement: str, options: list | None, verify_mode: bool = False,
                 kp_label: str | None = None) -> tuple[str | None, str]:
    """独立盲做一次，返回规范化答案短串。verify_mode 追加复核指令换缓存键（二次仲裁用）。

    kp_label（模块 J 伪考点闸门，仅 _gen_green/_gen_yellow 在线态传入）：非空时在盲解
    system 末尾追加「问法合规检查」指令——盲解者顺带判断该题是否纯代数可解（考点
    装饰性问法），是则返回 FAKE_KP。demo 态恒 None：payload 与旧版逐字节一致（预热
    缓存安全）；家族路径（generate_from_family）也不传——零 API 演示路径一字节不变，
    且其盲解仅 advisory 留档、判分权在 SymPy 构造。"""
    sys_prompt = _SOLVE_SYSTEM
    if kp_label:
        sys_prompt += _FAKE_KP_INSTRUCTION.format(kp=kp_label)
    user = statement + ("\n（复核模式：请逐步验算后再给最终答案）" if verify_mode else "")
    if options:
        user += "\n选项：" + " | ".join(options)
    try:
        d = chat_json([{"role": "system", "content": sys_prompt},
                       {"role": "user", "content": user}],
                      temperature=0.0, namespace="solve:")
        return _canon(d.get("answer", "")), json.dumps(d, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001 —— 盲解失败按不一致处理
        return None, str(e)


def _canon(s: str) -> str:
    """答案规范化（对账用）：去空白与全角标点，保留 ASCII 括号（Rational(1,3) 等不可破坏），
    统一箭头，修复常见记法（sqrt3→sqrt(3)、ln→log）。"""
    s = (s or "").strip()
    s = re.sub(r"[\s，。；．!！?？、：:\"'“”‘’]+", "", s)  # 保留 ASCII 逗号: Rational(1,3) 不可破坏
    s = s.replace("->", "→").replace("⟶", "→")
    s = re.sub(r"sqrt(\d)", r"sqrt(\1)", s)
    s = s.replace("ln(", "log(")
    return s


# ---------------------------------------------------------------------------
# 模块 M5：定理型考点变式构造提示（变式路径 variant_of 非空时按 kp 名注入）。
#
# 背景（用户盲评 p004/p005）：罗尔/拉格朗日类变式 LLM 常造出端点函数值不等的假题
# （定理前提被破坏）——盲解前提审查真拦，属诚实拦截但浪费重生成预算。构造 hint 从
# 源头给「天然满足前提」的构造形态，降低真拦率。映射表只含罗尔/拉格朗日（本批）；
# kp 名按子串命中（罗尔定理 / 罗尔中值定理、拉格朗日中值定理等变体均覆盖）；
# 无命中的 kp 不追加任何文本——载荷与无 hint 版本逐字节一致（缓存键稳定）。
# 变式路径的罗尔/拉格朗日在线态实际可达（generate_variant 的家族路径只接管非变式与
# 家族 kp 场景，generate_question 变式请求恒走 LLM 路径）；hint 对 demo/在线两态同等
# 注入——绿标命名空间已随占位符契约换代与系数位契约对齐 bump（v9），变式载荷变化一并
# 隔离。
# 真拦率改善留待用户重跑盲评实测（docs/…/09 号「用户配合项」）。
# 匹配键（M 审查 m8）：中文名子串（罗尔定理/罗尔中值定理）+ kp id 英文短名
# （v1 斜杠路径 id：calc.rolle / calc.lagrange）。
_ROLLE_HINT = ("【构造提示】罗尔类变式建议取 f(x)=g(x)·(x−a)(x−b) 形态（端点函数值天然相等，"
               "罗尔前提自动满足），再校验区间内可导；严禁端点值不等的假题。")
_LAGRANGE_HINT = ("【构造提示】拉格朗日类变式建议取 f(x)=ax²+bx+c 于整数端点闭区间 [p,q] 形态"
                  "（连续可导天然满足），中值结论 f'(ξ)=(f(q)−f(p))/(q−p) 可随参数计算；"
                  "严禁定理前提不成立的假题。")
_VARIANT_HINTS = {"罗尔": _ROLLE_HINT, "rolle": _ROLLE_HINT,
                  "拉格朗日": _LAGRANGE_HINT, "lagrange": _LAGRANGE_HINT}


def _variant_construction_hint(kp_context: dict) -> str:
    """变式构造 hint（M5）：kp 名（kp 与 name 字段都查——v2 侧两字段可能是斜杠路径 id
    或中文名）子串命中映射表才返回，否则空串（载荷零变化）。"""
    fields = [str(kp_context.get("kp") or ""), str(kp_context.get("name") or "")]
    for key, hint in _VARIANT_HINTS.items():
        if any(key in f for f in fields):
            return hint
    return ""


def _gen_green(kp_context: dict, difficulty: str, few_shot: list[str], variant_of: str | None = None,
               attempt: int = 1) -> dict:
    # 模块 J：在线态 system prompt 末尾追加「考点装饰性问法❌」禁止后缀；
    # demo 态后缀为空，system prompt 与旧版逐字节一致（预热缓存安全）。
    sys_prompt = _GREEN_SYSTEM.format(skill=load_skill())
    if not _demo_mode():
        sys_prompt += _BAN_FAKE_KP_SUFFIX
    user = _base_user(kp_context, difficulty) + _format_few_shot(few_shot)
    if variant_of:
        user += f"\n\n【变式要求】以原题为母本做参数扰动（§三），结构与步数不变：\n{variant_of}"
        hint = _variant_construction_hint(kp_context)  # 模块 M5：定理型考点构造提示
        if hint:
            user += "\n" + hint
    d = chat_json([{"role": "system", "content": sys_prompt},
                   {"role": "user", "content": user}], temperature=1.0,
                  namespace=_NS_GREEN.format(attempt))

    params = {k: random.choice(v) for k, v in (d.get("params") or {}).items() if v}
    if not params:
        raise ValueError("绿标必须参数化（构造即正确）；零参数题的答案是硬编码值，无法确定性保证，请转黄标题型")
    # M4 人话化：LLM 偶发漏字段时旧代码裸 KeyError('answer_expr')，上抛到拦截卡只剩
    # "'answer_expr'" 一个词，值班人无法定位错因——改为显式人话（模块 M4）。
    if not str(d.get("answer_expr") or "").strip():
        raise ValueError("LLM 未按契约输出 answer_expr 字段"
                         "（绿标契约：statement_template + params + answer_expr）")
    if not str(d.get("statement_template") or "").strip():
        raise ValueError("LLM 未按契约输出 statement_template 字段"
                         "（绿标契约：statement_template + params + answer_expr）")
    # 参数代入（模块 M1 契约换代：占位符 {name} → <<name>>）：
    #   1) 新契约 <<k>>：answer_expr 括号包裹保运算优先级，题干原样代入；
    #   2) 过渡兼容（M2）：LLM 偶发仍输出旧 {k}——已知参数的精确替换保留（str.replace
    #      无歧义，旧格式题不至于把 {a} 原样发给用户）；未知 {k} 绝大多数是 LaTeX 记号
    #      （\frac{dy}{dx}、_{D}），「猜花括号」正是三代误拦根因 → 停用拦截，只留痕；
    #   3) 未代入 <<k>> 是确定错（新定界符零误判）→ 照旧拦截，报错人话。
    from verify import _parse

    answer_expr = str(d["answer_expr"])
    statement = str(d["statement_template"])
    for k, v in params.items():
        answer_expr = answer_expr.replace("<<" + k + ">>", "(" + str(v) + ")")
        statement = statement.replace("<<" + k + ">>", str(v))
    for k, v in params.items():  # 过渡兼容：旧 {k} 已知参数精确替换（M2）
        answer_expr = answer_expr.replace("{" + k + "}", "(" + str(v) + ")")
        statement = statement.replace("{" + k + "}", str(v))
    legacy_unknown: set[str] = set()
    legacy_cmd_args: set[str] = set()
    for _part in (statement, answer_expr):  # M 审查 m5：\命令 豁免分支同样留痕
        _u, _c = _legacy_placeholders(_part, params)
        legacy_unknown |= _u
        legacy_cmd_args |= _c
    unk_expr = _unknown_placeholders(answer_expr, params) if params else set()
    if unk_expr:
        raise ValueError(f"answer_expr 存在未代入占位符 {sorted(unk_expr)}"
                         f"（契约：参数用 <<名>> 占位并在 params 给候选值）：{answer_expr[:60]}")
    answer_val = _parse(answer_expr)
    # 兜底：answer_expr 用裸参数名（无 <<>>/{} 占位括号）时走符号代入——两种契约
    # 代入后都归一到「占位符已消、裸符号可能残留」的形态，此处按 params 统一收口。
    try:
        answer_val = answer_val.subs({__import__("sympy").Symbol(k): v for k, v in params.items()})
    except Exception:  # noqa: BLE001 —— 表达式已无参数符号时代入是空操作
        pass
    unk_stem = _unknown_placeholders(statement, params)
    if unk_stem:
        raise ValueError(f"题干存在未代入参数 {sorted(unk_stem)}"
                         f"（契约：参数用 <<名>> 占位并在 params 给候选值）：{statement[:60]}")
    for pat in _RENDER_FLAWS:
        if re.search(pat, statement):
            raise ValueError(f"模板渲染瑕疵（{pat}）：{statement[:60]}")
    if not is_valid_expr(str(answer_val)):
        raise ValueError("答案表达式不可解析")
    # 盲解对账：独立 LLM 求解 vs SymPy 构造答案（含题目前提自洽审查）。
    # 模块 J：在线态盲解兼任「问法合规」审题人（kp_label 非空时注入指令）——
    # 判分权不在 LLM：闸门只拦「教学价值」（考点装饰性问法），答案对错仍由
    # SymPy 构造终审；demo 态 kp_label=None，载荷与旧版逐字节一致。
    kp_label = _kp_gate_label(kp_context)
    solver_ans, raw = _blind_solve(statement, None, kp_label=kp_label)
    if solver_ans == "PREMISE_BROKEN":
        raise ValueError(f"盲解判定题目前提矛盾：{statement[:80]}")
    if _is_fake_kp(solver_ans):
        # 问法合规闸门：视为验证失败 → 走既有重生成链（attempt 2）；两次仍 FAKE_KP
        # 则由 generate_question 拦截 [待人工]（error 即 _FAKE_KP_ERROR 人话原因）。
        # 判定性结论不做 verify_mode 复审——同一盲解者对同一判定不会翻供，直接重生成。
        raise ValueError(_FAKE_KP_ERROR)
    agree = solver_ans is not None and check_answer(solver_ans, str(answer_val))
    if not agree:
        # 复核仲裁：构造侧由 SymPy 背书，盲解偶发算错时给第二次独立求解机会
        solver_ans2, raw2 = _blind_solve(statement, None, verify_mode=True, kp_label=kp_label)
        if _is_fake_kp(solver_ans2):
            raise ValueError(_FAKE_KP_ERROR)
        agree = solver_ans2 is not None and check_answer(solver_ans2, str(answer_val))
        raw += f" | 复核解={solver_ans2}"
    if not agree:
        raise ValueError(f"盲解与构造答案不一致（solver={solver_ans}, sympy={answer_val}）")

    verification = {"solver_agree": True, "solver_raw": raw[:200]}
    if legacy_unknown or legacy_cmd_args:
        # M2 过渡留痕：旧 {k} 形态不拦但记录——未知形态（几乎必为 LaTeX 记号）与
        # \命令 豁免分支（防静默吞掉真缺失参数，M 审查 m5）分别可观测；盲评破绽
        # 清单据此统计旧契约残存量，驱动下一批收敛。
        note = "legacy_placeholder_unknown:" + repr(sorted(legacy_unknown))
        if legacy_cmd_args:
            note += ";legacy_command_args:" + repr(sorted(legacy_cmd_args))
        verification["legacy_note"] = note
    return {
        "kp": kp_context.get("kp", ""), "difficulty": difficulty,
        "qtype": "calculation", "verify_level": "green",
        "statement_md": statement, "answer_sympy": str(answer_val), "answer_md": None,
        "options": None, "correct": None,
        "analysis": d.get("analysis", ""), "design_note": d.get("design_note", ""),
        "params": params, "verification": verification,
    }


def _gen_yellow(kp_context: dict, difficulty: str, qtype: str, few_shot: list[str],
                attempt: int = 1) -> dict:
    labels = {"concept": "概念判断", "order": "步骤排序", "choice": "单项选择"}
    # 模块 J：在线态 system prompt 末尾追加「考点装饰性问法❌」禁止后缀；
    # demo 态后缀为空，system prompt 与旧版逐字节一致（预热缓存安全）。
    sys_prompt = _YELLOW_SYSTEM.format(skill=load_skill(), qtype_label=labels.get(qtype, "概念判断"))
    if not _demo_mode():
        sys_prompt += _BAN_FAKE_KP_SUFFIX
    user = _base_user(kp_context, difficulty) + _format_few_shot(few_shot)
    d = chat_json([{"role": "system", "content": sys_prompt},
                   {"role": "user", "content": user}], temperature=1.0,
                  namespace=_NS_YELLOW.format(attempt))
    claimed = _canon(d.get("correct") or d.get("answer_md") or "")
    if not claimed:
        raise ValueError("黄标题缺答案")
    if qtype == "order" and "→" not in claimed:
        raise ValueError(f"排序题答案必须是完整序列（含→），实际：{claimed}")
    # 模块 J：在线态双盲兼任「问法合规」审题人（demo 态 kp_label=None 载荷不变）。
    kp_label = _kp_gate_label(kp_context)
    a1, raw1 = _blind_solve(d["statement_md"], d.get("options"), kp_label=kp_label)
    a2, raw2 = _blind_solve(d["statement_md"], d.get("options"), kp_label=kp_label)
    if _is_fake_kp(a1) and _is_fake_kp(a2):
        # 双盲都判装饰性问法 → 问法合规闸门拦截（走既有重生成链，两次仍败则 [待人工]）
        raise ValueError(_FAKE_KP_ERROR)
    if a1 != claimed or a2 != claimed:
        # 单盲 FAKE_KP 单盲正常落在此：不一致既有语义覆盖，不特判放行。
        # 对抗审查 minor-4：含 FAKE_KP 判定时 error 追加人话提示，值班人不把
        # 「问法合规分歧」误归因为答案抖动（错因记忆/蒸馏链会读到该 error 文案）。
        hint = "（含问法合规判定，建议人工复核）" if (_is_fake_kp(a1) or _is_fake_kp(a2)) else ""
        raise ValueError(f"双盲不一致（claim={claimed},盲1={a1},盲2={a2}）{hint}")
    return {
        "kp": kp_context.get("kp", ""), "difficulty": difficulty,
        "qtype": qtype, "verify_level": "yellow",
        "statement_md": d["statement_md"], "answer_sympy": None, "answer_md": d.get("answer_md") or d.get("correct"),
        "options": d.get("options"), "correct": d.get("correct"),
        "analysis": d.get("analysis", ""), "design_note": d.get("design_note", ""),
        "verification": {"blind_1": a1, "blind_2": a2, "raw1": raw1[:150], "raw2": raw2[:150]},
    }


# 已知弱区路由（Day3）：这些 kp 的参数化闭式已证实 LLM 写不稳（K1/矩阵域 0/7），
# 一律走确定性模板族（SymPy 从结构推导）。variant_of 变式请求不路由（保留 LLM 扰动路径供 M4 独立评测）。
_FAMILY_KP = {"罗尔定理": "rolle_xi", "拉格朗日中值定理": "lm_xi",
              "矩阵乘法": "matmul_entry", "逆矩阵": "inverse_2x2", "行列式": "det_3x3"}
_family_cycles: dict[str, object] = {}


def _next_family_spec(kp: str) -> dict | None:
    """按 kp 轮转返回下一个族实例（确定性序列：同调用序列 → 同实例序列 → 缓存可复演）。"""
    import itertools

    fam = _FAMILY_KP.get(kp)
    if not fam:
        return None
    if fam not in _family_cycles:
        specs = enumerate_family(fam, limit=8)
        if not specs:
            return None
        _family_cycles[fam] = itertools.cycle(specs)
    return next(_family_cycles[fam])


def _guard_statement(q: dict) -> dict:
    """空题干 guard（R5 数学语义终审 id18 教训：验证链只查答案未查题干非空）。
    statement_md 为空/空白的题一律转拦截 [待人工]，绝不下发——推题纪律的最后一道闸。"""
    if (q.get("statement_md") or "").strip():
        return q
    return {"kp": q.get("kp", ""), "difficulty": q.get("difficulty", ""), "qtype": q.get("qtype", ""),
            "verify_level": q.get("verify_level", "green"), "statement_md": None,
            "status": "blocked_pending_human",
            "error": q.get("error") or "题干为空（空题干 guard，R5 终审 id18）",
            "attempt": q.get("attempt", 1)}


def generate_question(kp_context: dict, difficulty: str = "基础", qtype: str = "calculation",
                      variant_of: str | None = None) -> dict:
    """生成一道题，带完整验证链：失败重生成 1 次 → 仍败 → 拦截 [待人工]（PRD §7）。
    重试使用独立缓存命名空间，确保第二次真正重新生成而非命中同一缓存。
    通过验证后追加一步「解析实例化」：以最终题干+答案为准重写解析，杜绝解析与实例脱节。
    已知弱区 kp（_FAMILY_KP）且非变式请求时，直接走确定性模板族（构造即正确）。
    qtype="solution"（Day6 任务 2）：解答题自评模式——题目内容走与计算题一致的绿标
    验证链（SymPy 构造 + 盲解对账，解析推导仍经验证），仅呈现方式改为不做机器判分、
    由学生对照标准解析三档自评（证明类无验证手段，仍不出）。"""
    if qtype in ("calculation", "solution") and variant_of is None:
        spec = _next_family_spec(str(kp_context.get("kp", "")))
        if spec is not None:
            q = generate_from_family(spec)
            if qtype == "solution":
                q["qtype"] = "solution"
            q["routed"] = "family"
            return _guard_statement(q)
    few_shot = pick_few_shot(kp_context.get("kp", ""), qtype)
    last_err = None
    for attempt in range(1, 3):
        try:
            if qtype == "calculation":
                q = _gen_green(kp_context, difficulty, few_shot, variant_of, attempt=attempt)
            elif qtype == "solution":
                q = _gen_green(kp_context, difficulty, few_shot, variant_of, attempt=attempt)
                q["qtype"] = "solution"
            else:
                q = _gen_yellow(kp_context, difficulty, qtype, few_shot, attempt=attempt)
            q["attempt"] = attempt
            q["analysis"] = _instantiate_analysis(q)
            return _guard_statement(q)
        except Exception as e:  # noqa: BLE001 —— 验证失败走重生成链
            last_err = e
    return {
        "kp": kp_context.get("kp", ""), "difficulty": difficulty, "qtype": qtype,
        "verify_level": "green" if qtype in ("calculation", "solution") else "yellow",
        "statement_md": None, "status": "blocked_pending_human",
        "error": str(last_err), "attempt": 2,
    }


def _kp_consistent(a: str, b: str) -> bool:
    """知识点一致性：相等或一方是另一方的子串（题库斜杠路径 kp vs 家族短名）。"""
    a, b = (a or "").strip(), (b or "").strip()
    if not a or not b:
        return False
    return a == b or a in b or b in a


def _attach_variant(q: dict, src: dict) -> dict:
    """为变式题附加「源题与变化」溯源块（UI 直读；字段缺失也不崩）。"""
    new_params = q.get("params") or {}
    changed = {k: {"from": v, "to": new_params[k]}
               for k, v in (src.get("params") or {}).items()
               if k in new_params and str(v) != str(new_params[k])}
    introduced = {} if src.get("params") else dict(new_params)  # 源题无参数格（如题库题）时标注新引入参数
    _ORDER = {"基础": 0, "进阶": 1, "综合": 2}
    dims = []
    if changed or new_params:
        dims.append("参数扰动")
    if src.get("qtype") and q.get("qtype") and src["qtype"] != q["qtype"]:
        dims.append("题型转换")
    if _ORDER.get(q.get("difficulty", ""), 0) > _ORDER.get(src.get("difficulty", "基础"), 0):
        dims.append("难度上移")
    q["variant"] = {
        "source_kp": src.get("kp", ""),
        "source_statement_md": (src.get("statement_md") or "")[:240],
        "source_params": src.get("params") or {},
        "source_question_id": src.get("question_id"),
        "source_difficulty": src.get("difficulty") or "基础",
        "dimensions": dims or ["参数扰动"],
        "changed_params": changed,
        "introduced_params": introduced,
        "kp_consistent": _kp_consistent(src.get("kp", ""), q.get("kp", "")),
    }
    return q


def generate_variant(kp: str, source: dict, memory_context: str | None = None) -> dict:
    """变式出题（Day6 任务 4）：以源错题为母本，出题并附「源题与变化」溯源块。

    - 家族 kp（含题库斜杠路径 kp 的子串匹配）：确定性族内换参数格（构造即正确，
      零 API 演示可用——用户能直接看出「换了哪些参数」）
    - 其余 kp：LLM 扰动路径（variant_of §三）；演示模式缓存未命中时走既有拦截链
      优雅降级（blocked 卡片仍带溯源块），绝不崩溃、绝不出网
    - memory_context（模块 F3，可选）：学生错因记忆块，非空时并入 kp_context 注入
      LLM 变式 prompt（缺省/空 = 无记忆，kp_context 与旧版逐字节一致，缓存不受影响）；
      家族路径为确定性出题，不消费该参数
    """
    src = {
        "kp": source.get("kp") or kp,
        "statement_md": (source.get("statement_md") or "")[:240],
        "params": source.get("params") or {},
        "question_id": source.get("question_id"),
        "difficulty": source.get("difficulty") or "基础",
        "qtype": source.get("qtype"),
    }
    key = (kp or src["kp"]).strip()
    fam = _FAMILY_KP.get(key) or next(
        (f for k, f in _FAMILY_KP.items() if k in key), None)
    if fam is not None:
        for spec in enumerate_family(fam, limit=16):
            if src["params"] and all(
                    str(spec["params"].get(k)) == str(v)
                    for k, v in src["params"].items() if k in spec["params"]):
                continue  # 与源题同参数格 → 不是变式，换下一实例
            q = generate_from_family(spec)
            return _guard_statement(_attach_variant(q, src))
    ctx = {"kp": kp or src["kp"]}
    if isinstance(memory_context, str) and memory_context.strip():
        ctx["memory_context"] = memory_context
    q = generate_question(ctx, difficulty=src["difficulty"],
                          qtype="calculation", variant_of=src["statement_md"])
    return _attach_variant(q, src)


_ANALYSIS_SYSTEM = """你是数学题解析工程师。给定最终题干与标准答案，写出与该实例严格一致的解析：
3-5 句，含关键步骤、最终答案核对、以及该题的常见错误点破。所有数值必须与题干/答案一致，禁止出现题干以外的其他参数取值。只输出 JSON：{"analysis": "..."}"""


def _instantiate_analysis(q: dict) -> str:
    """以最终题干+答案重写解析（修复模板解析与实例化后的题干脱节）。"""
    if not q.get("statement_md"):
        return q.get("analysis", "")
    payload = f"题干：{q['statement_md']}\n标准答案：{q.get('answer_sympy') or q.get('answer_md')}"
    if q.get("options"):
        payload += f"\n选项：{q['options']}"
    try:
        d = chat_json([{"role": "system", "content": _ANALYSIS_SYSTEM},
                       {"role": "user", "content": payload}],
                      temperature=0.3, namespace="analysis:")
        return d.get("analysis") or q.get("analysis", "")
    except Exception:  # noqa: BLE001 —— 解析重写失败保留原解析
        return q.get("analysis", "")
