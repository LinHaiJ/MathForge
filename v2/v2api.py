"""MathForge v2 填空闭环 HTTP 接线层（D024/D025/D026）。

保底线：router → 出题 → 作答判分 → 归因 → 事件 → 投影 → 决策，全链路可经 HTTP 跑通。

纪律（红线）：
- 本模块只调用既有模块（router/pack_loader/generate/verify/attribute/mem2/assembler/policy2），
  不复制业务逻辑、不改 v1 任何行为；v1 侧唯一改动是 app.py 末尾两行挂载。
- 记忆写入只走 mem2（attempt_events/patterns 两张 v2 表），绝不动 v1 三表（mastery/mistakes/policy_log）。
- 库路径可用环境变量 MATHFORGE_V2_DB 覆盖（测试隔离用），默认取 mem2.default_db_path()。
- 出题失败/拦截（blocked_pending_human）一律返回 200 + {ok:false, reason}，绝不 500 打断 UI。

端点（全部挂在 /v2 前缀下）：
  POST /v2/turn            选包 → 取 kp → 出题
  POST /v2/answer          判分 → 归因 → 事件落盘 → 投影 → 决策
  GET  /v2/review          复习清单（掌握度最低优先）
  GET  /v2/stats           全局投影 + 事件计数 + 包清单
  GET  /v2/packs           科目包列表
  GET  /v2/packs/{id}      单包明细（kp 树含 parents，UI 数据源）
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import date, timedelta

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.routing import APIRoute
from pydantic import BaseModel

import assembler
import mem2
import pack_loader
import policy2
import router as v2route
from attribute import attribute_error
from generate import generate_question, _FAMILY_KP, render_flaws
from verify import check_answer, parse_error

class _ProfileRoute(APIRoute):
    """把 X-MF-Profile 写入 contextvar 的自定义路由（每人一库）。

    不用 app 中间件：BaseHTTPMiddleware 的 call_next 在 set() 之前已孵化下游任务，
    contextvar 传不进线程池端点；也不子类化 APIRouter 重写 get_route_handler
    （路由实例仍是 APIRoute，钩子不会被调）。规范做法 = APIRoute 子类 + route_class 参数。
    """

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            raw = request.headers.get("x-mf-profile", "")
            if raw and not _PROFILE_RE.match(raw):
                # 非法档案名拒绝而非静默回落（复评 S1：回落会破坏隔离语义）
                from fastapi.responses import JSONResponse
                return JSONResponse({"ok": False, "code": "bad_profile",
                                     "reason": "档案名不合法（仅限中英文、数字、连字符，≤16 字符）"},
                                    status_code=422)
            set_profile(raw)
            return await original(request)

        return handler


router = APIRouter(route_class=_ProfileRoute, tags=["v2"])

# 库路径覆盖环境变量（测试隔离；未设置时用 mem2 默认库 mathforge.db）
DB_ENV = "MATHFORGE_V2_DB"

# 每人一库（2026-09-12，AI-PM 评审 B1）：请求头 X-MF-Profile 选择学习者档案库。
# app.py 中间件把请求头写入 contextvar；db_path() 按此切库（环境变量 MATHFORGE_V2_DB 仍最高优先，
# 保证测试隔离不受影响）。档案名强校验：中英数下划线连字符 ≤16 字符，杜绝路径注入。
import contextvars as _cvars
import re as _re

_PROFILE: "_cvars.ContextVar[str]" = _cvars.ContextVar("mf_profile", default="")
_PROFILE_RE = _re.compile(r"^[\w\u4e00-\u9fa5-]{1,16}$")
# 档案库根目录可注入（PM 复评 P0：此前 pytest rmtree 会清空真实学习者档案库）。
# 动态读取（函数封装）：测试夹具在导入后才设 env，模块常量会固化失效。
def _profiles_dir() -> str:
    return os.environ.get("MATHFORGE_PROFILES_DIR") or str(
        mem2.default_db_path().parent / "profiles")


def set_profile(name: str) -> None:
    """中间件入口：校验并设置当前请求的学习者档案（非法值一律回落默认库）。"""
    _PROFILE.set(name if name and _PROFILE_RE.match(name or "") else "")


def current_profile() -> str:
    return _PROFILE.get()

# v2 题型 → generate.py 生成通道。
# "fill"（填空）走绿标计算链：SymPy 构造答案 + 盲解对账，家族 kp 零 API 可用。
# "solution"（解答题自评）复用同一绿标链（构造即正确），仅呈现方式改为不做机器判分、
# 由学生对照标准解析三档自评（generate.py 已含 qtype="solution" 通道）。
_GEN_QTYPE = {"fill": "calculation", "填空": "calculation",
              "solution": "solution", "解答题": "solution"}

# 事件 result 取值（mem2.SCORE 口径）
_RESULT_CORRECT = "correct"
_RESULT_WRONG = "wrong"

# 从 generate_question 结果里透传给前端的字段（白名单，保证可 JSON 序列化）
_Q_FIELDS = (
    "statement_md", "answer_sympy", "answer_md", "analysis", "options", "correct",
    "verify_level", "params", "family", "routed", "difficulty", "attempt", "question_fp",
    "judge_flag", "verification", "design_note",
)

# --------------------------------------------------------------------------- #
# demo 模式「该 kp 可零 API 出题」判定（T9 降级引导）
# --------------------------------------------------------------------------- #
# 证据链（以 generate.py 实现为准，宁可保守）：
#   - generate.generate_question 在 qtype∈{calculation,solution} 且非变式时，
#     调用 _next_family_spec(kp)，kp 取自 kp_context["kp"]（v2_turn 传入 kp["name"] 中文名）。
#   - _next_family_spec 用 _FAMILY_KP.get(kp) 精确命中中文 kp 名 → 走
#     generate_from_family 确定性模板族（families.py：SymPy 从结构推导 ξ/答案，构造即正确，
#     零 LLM、零 API）。
#   - 未命中则落到 _gen_green/_gen_yellow，必须 LLM 生成；MATHFORGE_DEMO=1 下缓存未命中
#     即抛 RuntimeError("demo 模式且缓存未命中（<key>）…")，原样下发给 UI 即「出题失败」裸哈希。
# 故：demo 可出题 ⇔ 中文名 ∈ _FAMILY_KP。该集合即 generate.py 已证实有家族实现的考点
# （罗尔定理/拉格朗日中值定理/矩阵乘法/逆矩阵/行列式）；洛必达法则、连续性等不在内。
_DEMO_FAMILY_KP_NAMES = frozenset(_FAMILY_KP.keys())

# exam_freq 降序排序权重（high 优先）
_EXAM_FREQ_ORDER = {"high": 0, "mid": 1, "low": 2}


def _demo_family_ok(kp: dict) -> bool:
    """kp 在 demo（零 API）模式可确定性出题 ⇔ 中文 name 命中家族白名单。"""
    return (kp.get("name") or "") in _DEMO_FAMILY_KP_NAMES


# --------------------------------------------------------------------------- #
# 包内确定性族（D8-7）：零 API 出题接入点
# --------------------------------------------------------------------------- #
# 包（如 calculus）的 families/ 目录下每个模块是一个确定性参数化族，自带公共 API：
#   模块级常量 KP_ID/KP_NAME/FAMILY + enumerate_family(family, limit) 枚举参数格
#   返回通过全部合法性断言的实例；单实例含
#   statement_md/answer_sympy/params/family/difficulty/assert。
# v2api 只在「自己的包家族」里找 kp，找不到就交给 generate_question（其内部自会
# 命中 v1 root 家族或 LLM），与 v1 路径互不冲突——v1 族（calc.rolle 等）在 root
# families.py，不在任何包的 families/ 下，故 _try_pack_family 对它们必然返回 None。
def _module_covers_kp(mod, kp_id: str, kp_name: str | None) -> bool:
    """模块是否声明覆盖该 kp：显式 KP_ID 优先；未显式声明则文件名短名 + KP_NAME 双保险。"""
    if getattr(mod, "KP_ID", None) == kp_id:
        return True
    short = kp_id.split(".")[-1]
    fname = (getattr(mod, "__name__", "") or "").split(".")[-1]
    fam = getattr(mod, "FAMILY", "") or ""
    name_ok = bool(kp_name) and getattr(mod, "KP_NAME", None) == kp_name
    return bool(name_ok and (short in fname or short in fam))


def _try_pack_family(pack: dict, kp_id: str, seed: int | None = None) -> dict | None:
    """优先走包内确定性族：命中 → 产出 1 题（零 API）并归一成 /v2/turn 同构 question；
    未命中 → None（交给 generate_question 既有路径）。

    seed 给定 → 确定性取第 seed%N 个实例（可复现）；未给定 → 取参数格首个（同样确定）。
    """
    kp = pack_loader.get_kp(pack, kp_id)
    kp_name = kp["name"] if kp else None
    for mod in (pack.get("families") or {}).values():
        if not _module_covers_kp(mod, kp_id, kp_name):
            continue
        enum = getattr(mod, "enumerate_family", None)
        if enum is None:
            continue
        items = [it for it in enum(limit=24)
                 if it and all(bool(v) for _, v in it.get("assert", []))
                 and not render_flaws(it.get("statement_md") or "")
                 and not render_flaws(it.get("analysis") or "")]
        if not items:
            return None
        idx = 0 if seed is None else seed % len(items)
        out = _normalize_pack_question(items[idx], mod, kp_name or items[idx].get("kp"))
        out["question_fp"] = _question_fp(out.get("family"), out.get("params"))
        return out
    return None


def _normalize_pack_question(item: dict, mod, kp_name: str) -> dict:
    """把包内族实例归一成与 /v2/turn（v1 家族路径）同构的 question 字典。

    与 generate_from_family 输出同构：statement_md/answer_sympy/answer_md/analysis/
    options/correct/verify_level/params/family/routed/difficulty。analysis 透传族内
    参数化解题要点（2026-09-12：12 族全部自带，答错后「看正确答案与解法」有实content）；
    verify_level=green（构造即正确）；routed=family。
    """
    params = item.get("params", {}) or {}
    return {
        "kp": item.get("kp") or kp_name,                       # 中文 kp 名
        "family": item.get("family") or getattr(mod, "FAMILY", None),
        "params": params,
        "difficulty": item.get("difficulty", "基础"),
        "statement_md": item.get("statement_md", ""),
        "answer_sympy": item.get("answer_sympy"),
        "answer_md": None,
        "analysis": item.get("analysis") or "",
        "options": None,
        "correct": None,
        "verify_level": "green",
        "qtype": "fill",
        "routed": "family",
    }


def _demo_kp_has_pack_family(pack: dict, kp_id: str) -> bool:
    """该 kp 在包内存在确定性族（零 API 可出题）。"""
    kp = pack_loader.get_kp(pack, kp_id)
    kp_name = kp["name"] if kp else None
    return any(_module_covers_kp(mod, kp_id, kp_name)
               for mod in (pack.get("families") or {}).values())


def _demo_available_kps(pack: dict, difficulty: str | None = None) -> list[dict]:
    """当前包内 demo 可零 API 出题的 kp 清单（≤8，按 exam_freq 降序）。

    判定依据 _demo_family_ok（中文名 ∈ _FAMILY_KP）。字段契约（供 UI agent 渲染 chips）：
    [{kp: kp_id, name: 中文名, pack_id: 包id}, ...]。difficulty 仅作语义提示，
    家族实例自带难度、不按请求难度过滤（generate 家族路径忽略请求难度）。
    """
    out = []
    for k in pack["kp_graph"]:
        # 可出题 ⇔ v1 家族（中文名∈_FAMILY_KP）或 包内存在确定性族（D8-7 新接入）
        if not (_demo_family_ok(k) or _demo_kp_has_pack_family(pack, k["id"])):
            continue
        # 族链仅支持 fill/solution，至少其一才进清单
        if "fill" not in (k.get("qtypes") or []) and "solution" not in (k.get("qtypes") or []):
            continue
        out.append((_EXAM_FREQ_ORDER.get(k.get("exam_freq"), 99),
                    {"kp": k["id"], "name": k["name"],
                     "pack_id": pack["manifest"]["id"]}))
    out.sort(key=lambda t: t[0])
    return [item for _, item in out[:8]]


# 内部缓存 key 等长 hex 串（如 demo 报错里的 2bf09369…）；脱敏为前 8 位，避免裸抛哈希。
_HEX_HASH = re.compile(r"\b[0-9a-fA-F]{12,}\b")


def _sanitize_reason(msg: str) -> str:
    """把内部缓存 key 等长 hex 串截断为前 8 位，不让哈希原文漏给前端。"""
    return _HEX_HASH.sub(lambda m: m.group(0)[:8] + "…", msg)


def db_path() -> str:
    """当前 v2 库路径：学习者档案库 > 环境变量（测试隔离兜底）> 默认库。

    profile 优先于 env：带 X-MF-Profile 的请求永远落档案库（含测试内验证）；
    无档案头时 env/默认兜底不变。非法档案名在 set_profile 已回落 ""。
    """
    profile = current_profile()
    if profile:
        from pathlib import Path as _Path
        pd = _Path(_profiles_dir())
        pd.mkdir(parents=True, exist_ok=True)
        return str(pd / f"mathforge_{profile}.db")
    env = os.environ.get(DB_ENV)
    if env:
        return env
    return str(mem2.default_db_path())


def get_conn() -> sqlite3.Connection:
    """每请求新建连接（mem2.connect 内含幂等 init_schema），调用方负责 close。"""
    return mem2.connect(db_path())


# --------------------------------------------------------------------------- #
# 请求体
# --------------------------------------------------------------------------- #
class TurnBody(BaseModel):
    kp_id: str
    pack_id: str | None = None          # 指定包则跳过路由（stage1 旁路）
    qtype: str = "fill"
    difficulty: str = "基础"


class AnswerBody(BaseModel):
    pack_id: str
    kp: str                              # kp_id（英文 id，如 calc.rolle）
    qtype: str = "fill"
    student_answer: str
    standard_answer: str | None = None
    statement_md: str = ""
    analysis: str | None = None
    attribution_override: str | None = None   # 人工一键修正标签（优先于 LLM 归因）
    difficulty: str = "基础"
    question_fp: str = ""                # 服务端题目指纹（/v2/turn 下发，防重提交凭据）
    mode: str = "answer"                 # answer | variant（AI 变式作答，任务书 P2-b）


class SelfAssessBody(BaseModel):
    """解答题三档自评请求体（v2 大题模式，D022 红线：机器不判步骤分）。

    grade ∈ {会, 部分会, 不会}：映射 result = 会→correct / 部分会→partial / 不会→wrong。
    attribution 为四类归因标签（可选，由前端「采纳」AI 分步归因填入）；为空则记 None。
    student_steps 为按行拆分后的步骤数组，原样存入事件 meta 供复习清单回溯。
    """
    pack_id: str
    kp: str
    qtype: str = "solution"
    student_steps: list[str] = []
    grade: str                           # 会 | 部分会 | 不会
    attribution: str | None = None
    statement_md: str = ""
    analysis: str | None = None
    standard_answer: str | None = None


class StepsAttributeBody(BaseModel):
    """解答题整题分步归因（供参考）请求体。

    红线语义（advisory）：机器不判步骤分，本端点仅产出「供参考」的归因标签与置信，
    判分权完全在学生。MATHFORGE_DEMO=1 或无 API key 时优雅降级为
    200 {ok:false, degraded:true, reason}，绝不 500。
    """
    statement_md: str = ""
    analysis: str | None = None
    standard_answer: str | None = None
    steps: list[str] = []
    kp: str = ""


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #
def _kp_view(kp: dict) -> dict:
    """kp 节点对外视图（UI 直读；parents 供前置链展示）。"""
    return {
        "id": kp.get("id"),
        "name": kp.get("name"),
        "section": kp.get("section"),
        "level": kp.get("level"),
        "parents": kp.get("parents", []),
        "qtypes": kp.get("qtypes", []),
        "difficulty_floor": kp.get("difficulty_floor"),
        "typical_forms": kp.get("typical_forms", []),
        "pitfalls": kp.get("pitfalls", []),
    }


def _load_pack(pack_id: str) -> dict | None:
    try:
        return pack_loader.load_pack(pack_id)
    except Exception:  # noqa: BLE001 —— 包不存在/损坏都按未命中处理，不 500
        return None


def _resolve_pack(pack_id: str | None, kp_id: str) -> tuple[dict | None, dict]:
    """定位科目包：显式 pack_id 优先，否则走两段式路由。返回 (pack, route_info)。"""
    if pack_id:
        pack = _load_pack(pack_id)
        return pack, {"path": "explicit", "confidence": 1.0, "pack_id": pack_id}
    r = v2route.route({"kp_id": kp_id})
    if not r.get("pack_id"):
        return None, {"path": r.get("path"), "confidence": r.get("confidence"), "pack_id": None}
    return _load_pack(r["pack_id"]), {
        "path": r.get("path"), "confidence": r.get("confidence"), "pack_id": r["pack_id"],
    }


# --------------------------------------------------------------------------- #
# 1. 出题
# --------------------------------------------------------------------------- #
@router.post("/turn")
def v2_turn(body: TurnBody):
    """选包 → 取 kp（中文 name）→ 出题（填空=绿标计算链）。

    失败一律 200 + {ok:false, reason}：kp 无归属包 / 包内无该 kp / 生成异常 /
    验证链拦截（blocked_pending_human）。
    """
    pack, route_info = _resolve_pack(body.pack_id, body.kp_id)
    if pack is None:
        return {"ok": False, "reason": f"未找到科目包（kp_id={body.kp_id}, pack_id={body.pack_id}）",
                "route": route_info}
    pack_id = pack["manifest"]["id"]
    kp = pack_loader.get_kp(pack, body.kp_id)
    if kp is None:
        return {"ok": False, "reason": f"包 {pack_id} 内不存在知识点 {body.kp_id}",
                "pack_id": pack_id, "route": route_info}

    # 题型门禁：kp.qtypes 不含所请求题型时，返回 200 {ok:false, reason}（绝不 500）。
    # 解答题（solution）必须 kp 显式声明支持，否则退回填空闭环。
    if body.qtype not in (kp.get("qtypes") or []):
        return {"ok": False,
                "reason": f"知识点 {body.kp_id} 不支持题型「{body.qtype}」（qtypes={kp.get('qtypes')}）",
                "pack_id": pack_id, "kp": _kp_view(kp), "route": route_info}

    # 【D8-7 新确定性族接入】生成侧优先包内家族（零 API）。
    # 命中即出题，跳过 generate LLM 链；v1 家族（calc.rolle 等）不在此列，未命中时
    # 下方仍交 generate_question 命中 root 家族或 LLM，两者不冲突。
    gen_qtype = _GEN_QTYPE.get(body.qtype, body.qtype)
    if body.qtype in ("fill", "solution"):
        # 主练习入口也要出不同的题（复评 S2）：此前恒 seed=None → 恒出第 0 格，
        # 同一 kp 连打 N 次题干逐字节相同。随机 seed；守卫过滤在 _try_pack_family 内。
        import random as _random
        pack_q = _try_pack_family(pack, body.kp_id, seed=_random.randrange(2 ** 31))
        if pack_q is not None:
            question = {k: pack_q[k] for k in _Q_FIELDS if k in pack_q}
            question["kp"] = kp["name"]          # 出题侧中文 kp 名（判分/归因证据用）
            question["qtype"] = body.qtype
            question["gen_qtype"] = gen_qtype
            _selfgrade_if_matrix(question)
            return {
                "ok": True,
                "pack_id": pack_id,
                "kp": _kp_view(kp),
                "question": question,
                "difficulty": body.difficulty,
                "route": {**route_info, "pack_family": True},
            }

    # demo 模式降级引导（T9）：非家族链 kp 不硬试 LLM（缓存未命中即硬失败且裸抛哈希），
    # 直接返回 200 + 人类可读原因 + 可出题清单，供 UI 渲染引导 chips。
    if os.environ.get("MATHFORGE_DEMO") == "1" and not _demo_family_ok(kp):
        return {
            "ok": False,
            "code": "demo_llm_unavailable",
            "reason": "演示模式仅覆盖确定性模板考点（无需联网即可出题）；该考点需联网生成，演示环境不可用",
            "pack_id": pack_id,
            "kp": _kp_view(kp),
            "route": route_info,
            "demo_available_kps": _demo_available_kps(pack, body.difficulty),
        }

    try:
        # kp_context 只传中文 name —— 与 v1 /generate 完全一致的载荷，保证家族路由命中与缓存复用
        q = generate_question({"kp": kp["name"]}, difficulty=body.difficulty, qtype=gen_qtype)
    except Exception as e:  # noqa: BLE001 —— 生成链任何异常都降级为 ok:false，绝不 500
        return {"ok": False, "code": "generation_failed",
                "reason": _sanitize_reason(f"出题失败：{e}"), "pack_id": pack_id,
                "kp": _kp_view(kp), "route": route_info}

    if q.get("status") == "blocked_pending_human" or not (q.get("statement_md") or "").strip():
        return {"ok": False, "code": "blocked_pending_human",
                "reason": _sanitize_reason(q.get("error") or "题目未过验证，已拦截 [待人工]"),
                "status": "blocked_pending_human", "pack_id": pack_id,
                "kp": _kp_view(kp), "route": route_info}

    question = {k: q[k] for k in _Q_FIELDS if k in q}
    question["kp"] = kp["name"]          # 出题侧中文 kp 名（判分/归因证据用）
    question["qtype"] = body.qtype        # 对外题型口径（fill），生成通道见 gen_qtype
    question["gen_qtype"] = gen_qtype
    question["question_fp"] = _question_fp(question.get("family"), question.get("params"))
    _selfgrade_if_matrix(question)
    return {
        "ok": True,
        "pack_id": pack_id,
        "kp": _kp_view(kp),
        "question": question,
        "difficulty": body.difficulty,
        "route": route_info,
    }


# --------------------------------------------------------------------------- #
# 2. 作答闭环
# --------------------------------------------------------------------------- #
@router.post("/answer")
def v2_answer(body: AnswerBody):
    """判分（SymPy 等价）→ 归因（override 优先）→ 写事件流 → 投影 → P1-P6 决策。"""
    # 解析预检（2026-09-12 P0）：学生输入不可解析时【不记事件、不出决策】——
    # 这类输入判分恒 False 但零学习信号，记入只会污染记忆与蒸馏。
    perr = parse_error(body.student_answer)
    if perr:
        return {"ok": True, "correct": False, "parse_error": perr,
                "event_recorded": False, "event_id": None, "decision": None}

    correct = check_answer(body.student_answer, body.standard_answer or "")

    attribution = None
    overridden = False
    real_conf = None
    if not correct:
        if body.attribution_override:
            attribution = body.attribution_override
            overridden = True
        else:
            # attribute_error 自带兜底（LLM 不可用时返回「未归因」低置信占位），不阻塞闭环
            ad = attribute_error(
                body.statement_md, body.student_answer,
                body.standard_answer or "", body.analysis,
            )
            attribution = ad["attribution"]
            real_conf = ad.get("confidence")

    attr_payload = {"type": attribution,
                    "conf": 1.0 if overridden else (real_conf if not overridden else None)
                    } if attribution else None
    # 落库增强（B1）：meta 存题干/答案摘要与难度，供 /v2/variant 取「源题」与溯源区
    meta = {
        "stmt_summary": _safe_trunc_math(body.statement_md or "", 160),
        "standard_summary": _safe_trunc_math(body.standard_answer or "", 160),
        "difficulty": body.difficulty,
    }
    conn = get_conn()
    # 作答通道（任务书 P2-b）：answer=常规作答；variant=AI 变式作答（同核不同壳）。
    # 白名单外回退 answer，不 422——判分口径完全一致，只是事件标记不同。
    if body.mode not in ("answer", "variant"):
        body.mode = "answer"
    try:
        # 同题去重（复评 S4 + 全栈复审 P1 加固）：指纹优先（服务端下发，不可伪造），
        # 兜底题干摘要；比对该 kp 最近 8 条作答——交替两题的绕过也被覆盖。
        # P2 口径：按作答通道隔离比对——母题（answer）做过不拦同核变式（variant），
        # 重复提交同一变式仍被拦（q_fp 同 + mode 同）。
        recent = conn.execute(
            "SELECT meta FROM attempt_events WHERE kp=? AND mode=? "
            "ORDER BY id DESC LIMIT 8", (body.kp, body.mode)).fetchall()
        dup_reason = None
        for r in recent:
            m = json.loads(r[0]) if r[0] else {}
            if body.question_fp and m.get("q_fp") == body.question_fp:
                dup_reason = "同一道题已提交过（正确答案与解法已展示）。再做一道新题吧——「再练一题」或「按策略练」都可以。"
                break
            if not body.question_fp and m.get("stmt_summary") and m.get("stmt_summary") == _safe_trunc_math(
                    body.statement_md or "", 160):
                dup_reason = "同一道题已提交过。再做一道新题吧。"
                break
        if dup_reason:
            # 订正通道（学生复评 #3）：同一题之前答错、本次答对 → 记 retry 事件
            # （不进掌握度滑动窗，但修正「最近错因/连对」），给学生一条翻案路。
            # 幂等（PM 复评 #4 缝隙）：该题已订正过（最近事件含 retry）→ 返回 duplicate 不再落事件，
            # 堵「无限重交正确答案刷假连对触发 P5」的缝隙。
            already_retry = False
            for r in recent:
                m = json.loads(r[0]) if r[0] else {}
                if body.question_fp and m.get("q_fp") == body.question_fp and m.get("retry"):
                    already_retry = True
                    break
                if not body.question_fp and m.get("retry") and m.get(
                        "stmt_summary") == _safe_trunc_math(body.statement_md or "", 160):
                    already_retry = True
                    break
            first_row = conn.execute(
                "SELECT result FROM attempt_events WHERE kp=? AND mode=? "
                "ORDER BY id DESC LIMIT 1", (body.kp, body.mode)).fetchone()
            last_result = first_row[0] if first_row else None
            if correct and last_result in ("wrong", "partial") and not already_retry:
                retry_id = mem2.append_event(
                    conn, pack=body.pack_id, kp=body.kp, qtype=body.qtype,
                    mode="retry", result="correct", attribution=None,
                    meta={**meta, "q_fp": body.question_fp or None, "retry": True,
                          "retry_of": "wrong"}, sim=0)
                return {"ok": True, "correct": True, "duplicate": False, "retry": True,
                        "attribution": None, "overridden": False, "event_id": retry_id,
                        "event_recorded": True, "decision": None,
                        "reason": "订正成功！本次不计入掌握度，但错因记录已更新。"}
            return {"ok": True, "correct": correct, "duplicate": True,
                    "event_recorded": False, "event_id": None, "decision": None,
                    "parse_error": None, "reason": dup_reason}
        event_id = mem2.append_event(
            conn,
            pack=body.pack_id,
            kp=body.kp,
            qtype=body.qtype,
            mode=body.mode,
            result=_RESULT_CORRECT if correct else _RESULT_WRONG,
            attribution=attr_payload,
            user_override=attr_payload if overridden else None,
            meta={**meta, "parse_ok": True, "q_fp": body.question_fp or None},
            sim=0,
        )
        pack = _load_pack(body.pack_id)
        if pack is None:
            return {"ok": True, "correct": correct, "attribution": attribution,
                    "overridden": overridden, "event_id": event_id, "decision": None,
                    "event_recorded": True,
                    "reason": f"科目包不可用（pack_id={body.pack_id}），已记事件但跳过决策"}
        strategy = pack["strategy"]
        state = assembler.prepare_decision_state(conn, pack, body.kp, body.difficulty,
                                                 strategy=strategy)
        decision = policy2.decide(state, strategy)
        return {
            "ok": True,
            "correct": correct,
            "attribution": attribution,
            "overridden": overridden,
            "event_id": event_id,
            "event_recorded": True,
            "decision": {"rule": decision["rule"], "action": decision["action"]},
            "state": {k: v for k, v in state.items() if k != "conn"},
        }
    finally:
        conn.close()


def _question_fp(family, params) -> str:
    """服务端题目指纹：family + 参数排序哈希（复评 P1：去重键不信任客户端上报的题干）。"""
    import hashlib as _hl
    try:
        payload = json.dumps({"f": family, "p": params}, sort_keys=True,
                             ensure_ascii=False, default=str)
    except Exception:
        payload = str(family) + str(params)
    return _hl.md5(payload.encode("utf-8")).hexdigest()[:16]


class ParseCheckBody(BaseModel):
    """填空表达式可解析性预检（不落库、不调 LLM、零成本）。"""
    expr: str


# --------------------------------------------------------------------------- #
# 拍照识别（可选 sidecar：WorkBuddy mathforge_ingest.service，默认 127.0.0.1:8600）
# --------------------------------------------------------------------------- #
_INGEST_HINT = ("拍照识别是可选组件：先启动识别服务（在 mathforge_ingest 项目目录 "
                "python -m uvicorn mathforge_ingest.service:app --port 8600），再重试；"
                "也可以直接键盘输入。")


def _extract_answer_latex(markdown: str) -> str | None:
    """从转写 markdown 里启发式提取『最终答案』候选：最后一个数学块的内容。

    识别结果永远只作回填候选，由学生核对/修改后提交（AI 识别请核对红线），
    因此这里宁缺毋滥：取不到就返回 None，前端不回填。
    """
    if not markdown:
        return None
    blocks = re.findall(r"\$\$(.+?)\$\$", markdown, re.S) or \
        re.findall(r"\$([^$\n]+?)\$", markdown)
    if not blocks:
        return None
    cand = blocks[-1].strip().strip("` ")
    # 推导链形态（如 "[..]_{1}^{3} = 12"）→ 取最后一个等号后的最终值
    if "=" in cand:
        cand = cand.split("=")[-1].strip()
    # 明显不是答案的形态（纯文字/问号掩码）→ 放弃
    if not cand or "⟨" in cand or len(cand) > 120 or "\n" in cand:
        return None
    return cand


@router.post("/photo-recognize")
async def v2_photo_recognize(file: UploadFile = File(...)):
    """手写作答拍照 → 识别 sidecar → 回填候选（不直接判分、不落事件）。

    学生核对/修改后走 /v2/answer 正常闭环（记忆单一入口）。
    sidecar 未启动 → 200 + {ok:false, code:"ingest_unavailable"}（可选组件不阻断）。
    """
    try:
        import httpx
    except ImportError:
        return {"ok": False, "code": "ingest_unavailable",
                "reason": "服务端缺少 httpx 依赖（pip install httpx）", "hint": _INGEST_HINT}
    data = await file.read()
    if not data:
        return {"ok": False, "code": "empty_file", "reason": "空文件", "hint": _INGEST_HINT}
    if len(data) > 20 * 1024 * 1024:
        return {"ok": False, "code": "too_large", "reason": "图片超过 20MB，请压缩后重试",
                "hint": _INGEST_HINT}
    ingest_url = os.environ.get("MATHFORGE_INGEST_URL", "http://127.0.0.1:8600")
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.post(
                f"{ingest_url}/recognize",
                files={"file": (file.filename or "photo.png", data,
                                file.content_type or "image/png")},
                data={"strict": "true"},
            )
            sidecar = resp.json()
    except Exception:
        return {"ok": False, "code": "ingest_unavailable", "reason": "识别服务未响应",
                "hint": _INGEST_HINT}

    if sidecar.get("status") != "ok":
        return {"ok": False, "code": sidecar.get("status") or "sidecar_error",
                "reason": sidecar.get("reject_reason") or sidecar.get("error") or "识别失败",
                "hint": sidecar.get("agent_hint") or _INGEST_HINT}

    md = sidecar.get("markdown") or ""
    return {"ok": True,
            "decision": sidecar.get("decision"),          # accept / review / unclear
            "answer_latex": _extract_answer_latex(md),
            "markdown": md,
            "issues": sidecar.get("issues") or [],
            "unclear_count": sidecar.get("unclear_count", 0),
            "agent_hint": sidecar.get("agent_hint")}


@router.post("/parse-check")
def v2_parse_check(body: ParseCheckBody):
    """提交前预检：学生输入能否被 SymPy 解析并参与判分。

    不可解析 → {parseable:false, error}：前端拦截提交并给出可读提示，
    避免"判错但学不到任何东西"的作答进入事件流（2026-09-12 P0）。
    """
    err = parse_error(body.expr)
    return {"ok": True, "parseable": err is None, "error": err}


# --------------------------------------------------------------------------- #
# 2b. 解答题三档自评（v2 大题模式，D022 红线：机器不判步骤分）
# --------------------------------------------------------------------------- #
_GRADE_MAP = {"会": "correct", "部分会": "partial", "不会": "wrong"}
_GRADES = set(_GRADE_MAP)


def _llm_available() -> bool:
    """LLM 是否可用：演示模式或无 key 一律不可用（steps-attribute 走降级）。"""
    if os.environ.get("MATHFORGE_DEMO") == "1":
        return False
    return bool(os.environ.get("DEEPSEEK_API_KEY"))


def _mastery_after(pack_id: str, kp: str, conn) -> dict | None:
    """append 事件后即时投影掌握度（assembler 侧 project_mastery 值）。

    strategy 取自科目包（缺失则 None，project_mastery 回退默认半衰期）。
    复用传入 conn（已含本事件），无需再开连接。
    """
    pack = _load_pack(pack_id)
    strategy = pack["strategy"] if pack else None
    return mem2.project_mastery(conn, kp, strategy=strategy)


@router.post("/selfassess")
def v2_selfassess(body: SelfAssessBody):
    """解答题三档自评：映射 result → 写 selfassess 事件 → 返回 mastery_after。

    底座=用户三档自评（D022 红线：绝不做机器判步骤分）；增强=可选 LLM 归因标签
    （由 /v2/steps-attribute 采纳后带入）。事件 mode='selfassess'，
    result ∈ {correct,partial,wrong}，meta 存原始步骤数组与步数。

    grade 非法 → 200 {ok:false, reason}（与 /v2/turn 错误风格一致，绝不 500）。
    """
    grade = (body.grade or "").strip()
    if grade not in _GRADES:
        return {"ok": False,
                "reason": f"grade 必须 ∈ {{会,部分会,不会}}，收到：{body.grade!r}"}
    result = _GRADE_MAP[grade]
    attr_payload = {"type": body.attribution, "conf": 1.0} if body.attribution else None
    conn = get_conn()
    try:
        event_id = mem2.append_event(
            conn,
            pack=body.pack_id,
            kp=body.kp,
            qtype=body.qtype,
            mode="selfassess",
            result=result,
            attribution=attr_payload,
            meta={"steps": list(body.student_steps),
                  "step_count": len(body.student_steps),
                  # 题干/答案摘要：供复习卡「最近」与 /v2/variant 源题复用（B1 落库增强延伸）
                  "stmt_summary": _safe_trunc_math(body.statement_md or "", 160),
                  "standard_summary": _safe_trunc_math(body.standard_answer or "", 160)},
            sim=0,
        )
        m = _mastery_after(body.pack_id, body.kp, conn)
    finally:
        conn.close()
    return {
        "ok": True,
        "result": result,
        "grade": grade,
        "event_id": event_id,
        "attribution": body.attribution,          # 回显采纳的标签（无则 None）
        "mastery_after": m,                        # assembler 侧 project_mastery 值
    }


# --------------------------------------------------------------------------- #
# 2c. 解答题整题分步归因（供参考，advisory）
# --------------------------------------------------------------------------- #
_STEP_NOTE_SYSTEM = """你是考研数学解题观察员。给定标准解析与学生的分步作答（每行一步），\
用 ≤3 句中文给出整体观察：步骤是否连贯、是否命中关键定理/方法、明显跳步或方向偏差在哪。\
只输出 JSON：{"step_notes": "..."}。不判分、不替学生下结论。"""


@router.post("/steps-attribute")
def v2_steps_attribute(body: StepsAttributeBody):
    """解答题整题分步归因（供参考）。

    复用 attribute.py 的 attribute_error 对「整题作答」做四类归因 + 置信；另用一次
    chat_json（走缓存）产出简短按步骤观察。两条调用任一失败（DEMO 无 key / 缓存未命中）
    一律降级为 200 {ok:false, degraded:true, reason}，绝不 500。

    advisory 语义（红线 D022）：机器不判步骤分，本端只产出「供参考」归因，判分权在学生。
    """
    if not _llm_available():
        return {"ok": False, "degraded": True,
                "reason": "演示模式或未配置 API key：分步归因不可用（机器不判步骤分，请对照解析自评）"}

    student_answer = "\n".join(body.steps)
    try:
        d = attribute_error(body.statement_md, student_answer,
                            body.standard_answer or "", body.analysis)
        attribution = d.get("attribution")
        conf = d.get("confidence", 0.0)
        # 按步骤观察：取前 ≤6 步拼接，一次 chat_json（命名空间缓存）
        step_notes = None
        joined = "\n".join(body.steps[:6])
        if joined.strip():
            user = (f"题干：{body.statement_md}\n标准答案：{body.standard_answer}\n"
                    f"标准解析：{(body.analysis or '')[:400]}\n学生步骤：\n{joined}")
            try:
                from llm import chat_json  # 函数内延迟导入：此前漏 import，NameError 被吞成 step_notes=None（对抗审查 P1）
                nd = chat_json([{"role": "system", "content": _STEP_NOTE_SYSTEM},
                                {"role": "user", "content": user}],
                               temperature=0.3, namespace="steps-attr:")
                step_notes = nd.get("step_notes")
            except Exception:  # noqa: BLE001 —— 观察是增值项，失败不阻断主归因
                step_notes = None
    except Exception as e:  # noqa: BLE001 —— 任意 LLM 失败都降级，绝不 500
        return {"ok": False, "degraded": True,
                "reason": f"分步归因服务暂不可用：{e}"}

    return {
        "ok": True,
        "attribution": attribution,
        "conf": conf,
        "step_notes": step_notes,
        "source": "llm",
        "advisory": True,            # 机器不判步骤分，仅供自评参考
    }


# --------------------------------------------------------------------------- #
# 3. 复习清单（掌握度最低优先）
# --------------------------------------------------------------------------- #
def _kp_meta() -> dict:
    """kp_id → {name, pack_id, section} 索引（UI 卡片禁止裸 id 展示用）。"""
    out = {}
    for pid in pack_loader.list_packs():
        pack = pack_loader.load_pack(pid)
        for k in pack["kp_graph"]:
            out[k["id"]] = {"name": k.get("name"), "pack_id": pid,
                            "section": k.get("section")}
    return out


def _kp_recent(conn, kp_id: str, limit: int = 6) -> list[dict]:
    """该 kp 最近作答事件摘要（卡片：最近错题题干/对错流）。"""
    rows = conn.execute(
        "SELECT ts, mode, result, meta FROM attempt_events "
        "WHERE kp=? AND mode IN ('answer','selfassess') ORDER BY ts DESC LIMIT ?",
        (kp_id, int(limit)),
    ).fetchall()
    out = []
    for r in rows:
        meta = json.loads(r[3]) if r[3] else {}
        out.append({
            "result": r[2], "mode": r[1],
            "stmt": _safe_trunc_math(meta.get("stmt_summary") or "", 100),
        })
    return out


@router.get("/review")
def v2_review(limit: int = 20, detail: int = 0):
    """按 decayed_value 升序取前 N（掌握度最低优先）。空库返回空列表。

    detail=1 时每项附加 kp.name/pack_id/最近事件（题干摘要与对错流）——UI 卡片渲染用，
    禁裸 schema id（卡片一律显示 name）。
    """
    limit = max(1, min(int(limit), 500))
    kp_meta = _kp_meta() if detail else {}
    conn = get_conn()
    try:
        allp = mem2.project_all(conn)
        if detail:
            recents = {kp: _kp_recent(conn, kp) for kp in allp}
    finally:
        conn.close()
    items = []
    for kp, v in allp.items():
        it = {
            "kp": kp,
            "mastery": v.get("mastery"),
            "decayed_value": v.get("decayed_value"),
            "n": v.get("n"),
            "last_ts": v.get("last_ts"),
            "streak_correct": v.get("streak_correct"),
            "last_attribution": v.get("last_attribution"),
        }
        if detail:
            m = kp_meta.get(kp, {})
            it["name"] = m.get("name") or kp
            it["pack_id"] = m.get("pack_id")
            it["section"] = m.get("section")
            rc = recents.get(kp, [])
            it["recent"] = rc
            it["last_ok"] = bool(rc) and rc[0]["result"] == "correct"
            it["trend_str"] = "".join(
                {"correct": "对", "wrong": "错", "partial": "半"}.get(x["result"], "·")
                for x in reversed(rc)
            )
        items.append(it)
    items.sort(key=lambda it: (it["decayed_value"] if it["decayed_value"] is not None else 0.0, it["kp"]))
    return {"items": items[:limit], "total": len(items)}


@router.get("/timeline")
def v2_timeline(limit: int = 15):
    """只读策略时间线：决策事件（mode='policy'）最近 N 条（页 B 折叠区）。"""
    limit = max(1, min(int(limit), 100))
    kp_meta = _kp_meta()
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT ts, kp, meta FROM attempt_events WHERE mode='policy' "
            "ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        meta = json.loads(r[2]) if r[2] else {}
        out.append({
            "ts": r[0],
            "kp_id": r[1],
            "kp_name": (kp_meta.get(r[1]) or {}).get("name") or r[1],
            "rule": (meta.get("rule") or ""),
            "action": meta.get("action"),
        })
    return {"items": out}


# --------------------------------------------------------------------------- #
# 4. 全局统计
# --------------------------------------------------------------------------- #
@router.get("/stats")
def v2_stats():
    """{kp_count, event_count, kps: 全量投影, packs: 包清单}。空库不 500。"""
    conn = get_conn()
    try:
        allp = mem2.project_all(conn)
        event_count = conn.execute("SELECT COUNT(*) FROM attempt_events").fetchone()[0]
        # 归因质量监控（AI-PM 评审）：override 率 = 人工修正数 / 错答数，漂移>40% 说明归因该重训了
        wrong_total = conn.execute(
            "SELECT COUNT(*) FROM attempt_events WHERE result='wrong' AND mode IN ('answer','selfassess')"
        ).fetchone()[0]
        override_total = conn.execute(
            "SELECT COUNT(*) FROM attempt_events WHERE mode='override'"
        ).fetchone()[0]
    finally:
        conn.close()
    packs = []
    for pid in pack_loader.list_packs():
        pack = _load_pack(pid)
        if pack is None:
            continue
        packs.append({"id": pid, "name": pack["manifest"].get("name"),
                      "kp_count": len(pack["kp_graph"])})
    return {"kp_count": len(allp), "event_count": event_count, "kps": allp, "packs": packs,
            "wrong_total": wrong_total, "override_total": override_total,
            "override_rate": round(override_total / wrong_total, 3) if wrong_total else None}


# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# 4a. 今日计划（AI-PM 评审「明日三题」缺口：已练最弱优先，冷启动给零 API 族保底）
# --------------------------------------------------------------------------- #
@router.get("/plan")
def v2_plan(n: int = 3):
    """今日练习计划 n 条（默认 3）：

    已练 kp 按衰减掌握度升序（最弱优先，附上次错因）；不足部分用「从未练过的
    零 API 确定性族 kp」补齐（冷启动保底，离线可出题）。理由文案只用中文名。
    """
    n = max(1, min(n, 10))
    zero_api: set = set()
    kp_index: dict = {}
    for pid in pack_loader.list_packs():
        pack = _load_pack(pid)
        if pack is None:
            continue
        for mod in (pack.get("families") or {}).values():
            kid = getattr(mod, "KP_ID", None)
            if kid:
                zero_api.add(kid)
        for k in pack["kp_graph"]:
            kp_index[k["id"]] = (pid, k.get("name") or k["id"])
    conn = get_conn()
    try:
        allp = mem2.project_all(conn)
    finally:
        conn.close()

    items = []

    def entry(kid, reason):
        pid, name = kp_index[kid]
        return {"kp_id": kid, "name": name, "pack_id": pid,
                "zero_api": kid in zero_api, "reason": reason}

    practiced = [(kid, proj) for kid, proj in allp.items() if kid in kp_index]
    practiced.sort(key=lambda kv: (kv[1].get("decayed_value") or 0))
    for kid, proj in practiced:
        if len(items) >= n:
            break
        dv = proj.get("decayed_value") or 0
        la = proj.get("last_attribution")
        attr = la.get("type") if isinstance(la, dict) else None
        if isinstance(attr, str) and attr == "未归因":
            attr = None
        reason = f"掌握度 {round(dv * 100)}%" + (f" · 上次错因：{attr}" if attr else "")
        items.append(entry(kid, reason))
    if len(items) < n:
        for kid in sorted(kp_index):
            if len(items) >= n:
                break
            if kid in allp or kid not in zero_api:
                continue
            items.append(entry(kid, "还没练过（基础模板，离线可出题）"))
    # 计划落库（复评 M7）：每天一条 mode='plan' 事件——「关浏览器不失忆」的最小实现
    today = date.today().isoformat()
    try:
        conn2 = get_conn()
        try:
            already = conn2.execute(
                "SELECT 1 FROM attempt_events WHERE mode='plan' AND day=? LIMIT 1", (today,)
            ).fetchone()
            if not already and items:
                mem2.append_event(conn2, pack="", kp="", qtype="", mode="plan", result="plan",
                                  meta={"items": [{"kp_id": it["kp_id"], "name": it["name"]}
                                                  for it in items]}, sim=0)
                conn2.commit()
        finally:
            conn2.close()
    except Exception:  # noqa: BLE001 —— 落库失败不阻断计划返回
        pass
    return {"ok": True, "date": date.today().isoformat(), "items": items}


# 4b. 足迹热力图（近 N 天每日作答计数）
# --------------------------------------------------------------------------- #
@router.get("/heatmap")
def v2_heatmap(days: int = 180):
    """近 N 天每天作答计数 [{day:"YYYY-MM-DD", count, correct}]；空库返回 []。

    与 attempt_events 同源（day 为事件落库当天的 YYYY-MM-DD 字符串）。
    口径照 v2api 设计：SELECT day, COUNT(*), SUM(result='correct')
    FROM attempt_events GROUP BY day；仅取近 N 天窗口（含今日）。
    前端热力图对缺失日补 0；今日有作答时由前端高亮当日格。
    """
    days = max(1, min(int(days), 2000))
    cutoff = (date.today() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT day, COUNT(*), SUM(result='correct') "
            "FROM attempt_events WHERE day >= ? AND mode IN ('answer', 'selfassess', 'variant') "
            "GROUP BY day ORDER BY day",
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"day": r[0], "count": r[1], "correct": int(r[2] or 0)}
        for r in rows
    ]


@router.get("/meta")
def v2_meta():
    """运行元信息：当前 v2 库、事件统计、是否全为模拟数据。

    sim_mode 判定（诚实口径，防演示/模拟数据被误当真实作答）：
    - 事件数>0 且全部 sim=1 → True；
    - 无事件时看库文件名是否含 sim/demo/演示 → True；
    - 否则 False。UI 据此显示「模拟数据」角标。
    """
    path = db_path()
    conn = get_conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM attempt_events").fetchone()[0]
        sim_n = (
            conn.execute("SELECT COUNT(*) FROM attempt_events WHERE sim=1").fetchone()[0]
            if total
            else 0
        )
    finally:
        conn.close()
    fname = os.path.basename(path).lower()
    sim_mode = (total > 0 and sim_n == total) or (
        total == 0 and any(k in fname for k in ("sim", "demo", "演示"))
    )
    return {
        "db": fname,
        "events": total,
        "sim_mode": sim_mode,
        "demo": bool(os.environ.get("MATHFORGE_DEMO")),
    }


# --------------------------------------------------------------------------- #
# 5. 科目包（UI 数据源）
# --------------------------------------------------------------------------- #
@router.get("/packs")
def v2_packs():
    """科目包列表（id/名称/版本/kp 数）。"""
    out = []
    for pid in pack_loader.list_packs():
        pack = _load_pack(pid)
        if pack is None:
            continue
        m = pack["manifest"]
        out.append({"id": pid, "name": m.get("name"), "version": m.get("version"),
                    "description": m.get("description"), "kp_count": len(pack["kp_graph"]),
                    "maturity": pack.get("maturity")})
    return {"packs": out}


@router.get("/packs/{pack_id}")
def v2_pack_detail(pack_id: str):
    """单包明细：kp 树（含 parents）+ 策略，供 UI 渲染前置链与阈值。"""
    pack = _load_pack(pack_id)
    if pack is None:
        raise HTTPException(404, f"科目包不存在：{pack_id}")
    m = pack["manifest"]
    return {
        "id": pack_id,
        "name": m.get("name"),
        "version": m.get("version"),
        "description": m.get("description"),
        "kps": [_kp_view(k) for k in pack["kp_graph"]],
        "strategy": pack["strategy"],
        "qtypes": pack["qtypes"],
    }



def _selfgrade_if_matrix(question: dict) -> None:
    """Matrix 类答案（如逆矩阵）无法用填空文本判分 → 降级为自评模式。

    语义：机器不判步骤分/矩阵判分不可靠时，交学生对照折叠解答三档自评
    （D022 红线内）。前端按 qtype==='solution' 渲染自评区。
    """
    if question.get("qtype") == "fill":
        ans = str(question.get("answer_sympy") or question.get("answer_md") or "")
        if ans.startswith("Matrix") or ans.startswith("["):
            question["qtype"] = "solution"


# --------------------------------------------------------------------------- #
# 6. 两页 UI 支撑：意图解析（B1，规则版零 LLM）/ 变式溯源 / 空态推荐
# --------------------------------------------------------------------------- #
import v2intent as _intent


class IntentBody(BaseModel):
    text: str = ""


class VariantBody(BaseModel):
    pack_id: str | None = None
    kp_id: str
    qtype: str = "fill"
    difficulty: str = "基础"


def _source_event(conn, kp_id: str, prefer_wrong: bool = True):
    """取该 kp 最近一道可作源题的事件（answer/selfassess，meta 含 stmt_summary）。

    先取错/部分对（复习语境），没有则取最近任意作答。返回 dict 或 None。
    """
    rows = conn.execute(
        "SELECT id, ts, mode, result, attribution, meta, pack "
        "FROM attempt_events WHERE kp=? AND mode IN ('answer','selfassess') "
        "ORDER BY ts DESC LIMIT 8",
        (kp_id,),
    ).fetchall()
    for r in rows:
        meta = json.loads(r[5]) if r[5] else {}
        if not (meta.get("stmt_summary") or "").strip():
            continue
        if prefer_wrong and r[3] in ("correct",) and r[2] == "answer":
            continue  # 复习语境优先错题
        return {
            "event_id": r[0], "ts": r[1], "mode": r[2], "result": r[3],
            "attribution": json.loads(r[4]) if r[4] else None,
            "stmt_summary": meta.get("stmt_summary", ""),
            "standard_summary": meta.get("standard_summary", ""),
            "difficulty": meta.get("difficulty"),
        }
    # 兜底：允许对错不限（事件少时）
    for r in rows:
        meta = json.loads(r[5]) if r[5] else {}
        if (meta.get("stmt_summary") or "").strip():
            return {
                "event_id": r[0], "ts": r[1], "mode": r[2], "result": r[3],
                "attribution": json.loads(r[4]) if r[4] else None,
                "stmt_summary": meta.get("stmt_summary", ""),
                "standard_summary": meta.get("standard_summary", ""),
                "difficulty": meta.get("difficulty"),
            }
    return None


def _family_new_instance(pack: dict, kp_id: str, source_params: dict | None = None,
                         qtype: str = "fill"):
    """包内族生成新实例；有 source_params 时避开相同参数（参数扰动）。"""
    import time as _t
    base = int(_t.time() * 1000) % 2**31
    last = None
    for i in range(12):
        q = _try_pack_family(pack, kp_id, seed=base + i)
        if q is None:
            return None, "no_family"
        last = q
        params = q.get("params")
        if isinstance(params, dict) and source_params is not None:
            if params != source_params:
                diffs = [f"{k}:{source_params.get(k)}→{params[k]}"
                         for k in params if source_params.get(k) != params[k]]
                return q, ("param", diffs)
        else:
            return q, ("fresh", None)
    return last, ("fresh", None)  # 未分到不同参数也返回（同族新实例语义成立）


@router.post("/intent")
def v2_intent(body: IntentBody):
    """意图条：解析一句话 → 槽位。命中才生成（调用方 /v2/variant），低置信回退不生成。

    每次解析落 mem2 事件（mode='intent'，meta 存 raw/slots/conf），不绕记忆闭环。
    """
    res = _intent.parse(body.text)
    conn = get_conn()
    try:
        mem2.append_event(
            conn,
            pack=(res.get("slots", {}).get("pack_id") or "") if res.get("ok") else "",
            kp=(res.get("slots", {}).get("kp_id") or "") if res.get("ok") else "",
            qtype=(res.get("slots", {}).get("qtype") or "") if res.get("ok") else "",
            mode="intent",
            result="hit" if res.get("ok") else "miss",
            meta={"raw": (body.text or "")[:200], "slots": res.get("slots"),
                  "confidence": res.get("confidence"), "reason": res.get("reason"),
                  "second": res.get("second")},
            sim=0,
        )
    finally:
        conn.close()
    if not res.get("ok"):
        rec = _intent.recommend_start(3)
        return {"ok": False, "confidence": res.get("confidence"),
                "reason": res.get("reason"), "fallback": rec}
    return {"ok": True, "slots": res["slots"], "confidence": res["confidence"],
            "second": res.get("second")}


@router.get("/recommend-start")
def v2_recommend_start():
    """空态引导：3 个基础档家族 kp（零 API），点选做第一题建画像。"""
    return {"items": _intent.recommend_start(3)}


@router.post("/variant")
def v2_variant(body: VariantBody):
    """以源题/家族生成变式（两页 UI 出题入口，含溯源区）。

    逻辑（用户定案）：
    - 有该 kp 历史源题（复习清单错题优先）→ 包内族参数扰动出变式；非族 kp 且非 demo
      走 LLM 变式链；demo 且非族 → ok:false code=variant_llm_unavailable。
    - 无源题但 kp 有家族 → 家族新实例（同族即变式，provenance.family_instance）。
    - 两者皆无 → ok:false code=need_mother（提示先做一道母题，不裸生成）。
    """
    pack, route_info = _resolve_pack(body.pack_id, body.kp_id)
    if pack is None:
        return {"ok": False, "reason": f"未找到科目包（kp_id={body.kp_id}）", "route": route_info}
    pack_id = pack["manifest"]["id"]
    kp = pack_loader.get_kp(pack, body.kp_id)
    if kp is None:
        return {"ok": False, "reason": f"包 {pack_id} 内不存在知识点 {body.kp_id}",
                "pack_id": pack_id}
    if body.qtype not in (kp.get("qtypes") or []):
        return {"ok": False, "reason": f"知识点 {body.kp_id} 不支持题型 {body.qtype}",
                "pack_id": pack_id, "kp": _kp_view(kp)}

    conn = get_conn()
    try:
        src = _source_event(conn, body.kp_id)
    finally:
        conn.close()

    if src is not None:
        # 有历史源题 → 参数扰动变式（优先包内族；无族则按环境走 LLM 或拒绝）
        q, outcome = _variant_with_source(pack, kp, src, body)
        return q

    # 无源题：家族 → 同族新实例；无家族 → 提示先做母题
    q, outcome = _family_new_instance(pack, body.kp_id, None, body.qtype)
    if q is None:
        return {"ok": False, "code": "need_mother", "pack_id": pack_id, "kp": _kp_view(kp),
                "reason": "还没有该考点的母题记录，先通过复习卡/意图做一道母题，再生成变式"}
    fam_name = q.get("family") or outcome[0]
    provenance = {
        "source_kind": "family",
        "source_summary": None,
        "changes": [{"type": "family_instance", "desc": f"同族 {fam_name} 新实例（无历史源题，同族即变式）"}],
        "consistency": True,
    }
    return _variant_response(pack_id, kp, q, body, provenance)


def _variant_response(pack_id: str, kp: dict, q: dict, body: VariantBody,
                      provenance: dict) -> dict:
    question = {k: q[k] for k in _Q_FIELDS if k in q}
    question["kp"] = kp["name"]
    question["qtype"] = body.qtype
    question["gen_qtype"] = _GEN_QTYPE.get(body.qtype, body.qtype)
    question["question_fp"] = _question_fp(question.get("family"), question.get("params"))
    _selfgrade_if_matrix(question)
    return {"ok": True, "pack_id": pack_id, "kp": _kp_view(kp), "question": question,
            "provenance": provenance, "difficulty": body.difficulty}


def _variant_with_source(pack: dict, kp: dict, src: dict, body: VariantBody) -> tuple[dict, str]:
    """有源题分支：包内族参数扰动 > 非 demo LLM 变式 > demo 拒绝。"""
    pack_id = pack["manifest"]["id"]
    q, outcome = _family_new_instance(pack, kp["id"], None, body.qtype)
    if q is not None:
        fam_name = q.get("family") or "family"
        changes = [{"type": "param", "desc": f"源题→变式：同族 {fam_name} 换参（参数扰动，知识点一致）"}]
        prov = {"source_kind": "history", "source_summary": _safe_trunc_math(src["stmt_summary"]),
                "changes": changes, "consistency": True}
        return _variant_response(pack_id, kp, q, body, prov), "history-family"

    # 无确定性族 → LLM 变式链（非 demo）；demo → 拒绝
    if os.environ.get("MATHFORGE_DEMO") == "1":
        return {"ok": False, "code": "variant_llm_unavailable", "pack_id": pack_id,
                "kp": _kp_view(kp), "reason": "该考点无确定性模板族，变式需联网生成，演示模式不可用"}, "refused"
    try:
        from generate import generate_variant
        src_q = {"statement_md": src["stmt_summary"], "answer_sympy": src["standard_summary"]}
        q = generate_variant(kp["name"], src_q)
        changes = [{"type": "llm_variant", "desc": "基于源题的 LLM 变式（验证链对账）"}]
        prov = {"source_kind": "history", "source_summary": _safe_trunc_math(src["stmt_summary"]),
                "changes": changes, "consistency": bool(q.get("verify_level") == "green")}
        return _variant_response(pack_id, kp, q, body, prov), "history-llm"
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "code": "variant_failed", "pack_id": pack_id, "kp": _kp_view(kp),
                "reason": _sanitize_reason(f"变式生成失败：{e}")}, "failed"


class VariantAiBody(BaseModel):
    pack_id: str | None = None   # 缺省按 kp 全局路由（与 VariantBody 一致——前端从不传 pack_id）
    kp_id: str
    seed: int | None = None
    k: int = 3
    strategy: str | None = None   # None=按记忆自动选择；显式 contextual|multistep 覆盖


def _pick_variant_strategy(conn: sqlite3.Connection, kp_id: str) -> str:
    """记忆驱动变式策略（任务书 P2-a；蒸馏任务书 §5.1 分诊表的运行时化）。

    该 kp 最近答错/半会 → contextual（同核换外壳 = 识别练习）；
    最近做对且连对 ≥2 → multistep（多步拆分 = 迁移练习）；
    无记录 / 连对不足 → contextual 默认。纯规则，不调 LLM。
    """
    row = conn.execute(
        "SELECT result FROM attempt_events WHERE kp=? AND result IN ('correct','wrong','partial') "
        "AND mode NOT IN ('override','retry') ORDER BY ts DESC, id DESC LIMIT 1",
        (kp_id,),
    ).fetchone()
    if row is None or row[0] in ("wrong", "partial"):
        return "contextual"
    if mem2._streak_correct(conn, kp_id) >= 2:
        return "multistep"
    return "contextual"


@router.post("/variant/ai")
def v2_variant_ai(body: VariantAiBody):
    """AI 变式（任务书 P1，2026-09-15）：确定性数学核 + LLM 考察计划/外壳 + 三闸门。

    三权分立：调度不生成、生成不验证、验证不调度。答案恒为核答案（闸门 G2 与 /v2/answer 双保险）；
    换问型（答案目标改变）一期禁止。任何失败 → 参数扰动降级（degraded:true）。
    策略（P2）：strategy 缺省时由记忆驱动——答错换壳识别、连对多步迁移。
    """
    import random as _random
    import ai_variant as _aiv

    pack, route_info = _resolve_pack(body.pack_id, body.kp_id)
    if pack is None:
        return {"ok": False, "reason": f"未找到科目包（kp_id={body.kp_id}）", "route": route_info}
    pack_id = pack["manifest"]["id"]
    kp = pack_loader.get_kp(pack, body.kp_id)
    if kp is None:
        return {"ok": False, "reason": f"包 {pack_id} 内不存在知识点 {body.kp_id}", "pack_id": pack_id}

    seed = body.seed if body.seed is not None else _random.randrange(2 ** 31)
    kernel = _try_pack_family(pack, body.kp_id, seed=seed)
    if kernel is None:
        return {"ok": False, "code": "no_kernel", "pack_id": pack_id, "kp": _kp_view(kp),
                "reason": "该考点暂无确定性数学核（模板族），AI 变式需先建族"}

    strategy_src = "explicit"
    strategy = body.strategy
    if strategy not in ("contextual", "multistep"):
        conn = get_conn()
        try:
            strategy = _pick_variant_strategy(conn, body.kp_id)
        finally:
            conn.close()
        strategy_src = "auto"
    res = _aiv.make_ai_variants(kernel, k=max(1, min(body.k, 5)), strategy=strategy,
                                usage_count=kp.get("usage_count"))

    vb = VariantBody(pack_id=pack_id, kp_id=body.kp_id, qtype="fill",
                     difficulty=kernel.get("difficulty", "基础"))
    if res.get("ok") and res.get("variants"):
        pick = res["variants"][_random.randrange(len(res["variants"]))]
        q = dict(kernel)
        q["statement_md"] = pick["stem_md"]   # 只换外壳：答案/参数/解析沿用数学核
        provenance = {
            "source_kind": "ai_variant",
            "source_summary": kernel.get("statement_md"),
            "changes": [{"type": "ai_shell", "desc": f"AI {pick['kind']}：{pick.get('note', '')}"}],
            "consistency": True,
        }
        out = _variant_response(pack_id, kp, q, vb, provenance)
        out["variant_meta"] = {
            "engine": "ai", "kind": pick["kind"], "g2_kind": pick.get("g2_kind"),
            "strategy": strategy, "strategy_src": strategy_src,
            "plan": res.get("plan"), "stats": res.get("stats"),
            "alternates": len(res["variants"]) - 1, "degraded": False,
        }
        return out

    # 降级链：参数扰动（同族新格，答案仍构造即正确）
    q_fb = _try_pack_family(pack, body.kp_id, seed=_random.randrange(2 ** 31))
    if q_fb is None:
        return {"ok": False, "code": "variant_failed", "pack_id": pack_id, "kp": _kp_view(kp),
                "reason": _sanitize_reason(f"AI 变式不可用：{res.get('reason', '未知')}，且该考点无备用参数格")}
    provenance = {"source_kind": "family", "source_summary": kernel.get("statement_md"),
                  "changes": [{"type": "param", "desc": f"AI 变式降级：{res.get('reason', '')}（参数扰动兜底）"}],
                  "consistency": True}
    out = _variant_response(pack_id, kp, q_fb, vb, provenance)
    out["variant_meta"] = {"engine": "param", "degraded": True, "reason": res.get("reason", ""),
                           "strategy": strategy, "strategy_src": strategy_src}
    return out


# --------------------------------------------------------------------------- #
# 6b. 两页 UI 微端点：归因人工覆盖（override 事件）/ 当日明细
# --------------------------------------------------------------------------- #
class OverrideBody(BaseModel):
    event_id: int
    type: str          # 四类归因



def _safe_trunc_math(s: str, n: int = 140) -> str:
    """截断文本但尽量不断在 $...$ 数学区内（防 KaTeX 渲染炸）。"""
    s = (s or "").strip()
    if len(s) <= n:
        return s
    for cut in range(n, 0, -1):
        head = s[:cut]
        if head.count("$") % 2 == 0 and not head.endswith("\\"):
            return head
    return s[:n]


_ATTRIB_CLASSES = ("概念混淆", "计算失误", "方法选错", "审题错误")


@router.post("/attribution-override")
def v2_attribution_override(body: OverrideBody):
    """人工修正归因：追加 override 事件（meta.origin_event_id），不改判不重记作答。

    语义（mem2）：override 事件不进掌握度投影，仅修正错题归因视图——
    与「重发 /v2/answer + attribution_override」相比不产生第二次作答事件。
    """
    if body.type not in _ATTRIB_CLASSES:
        return {"ok": False, "reason": f"归因必须是 {_ATTRIB_CLASSES}"}
    conn = get_conn()
    try:
        mem2.override_attribution(conn, body.event_id, body.type)
    finally:
        conn.close()
    return {"ok": True, "event_id": body.event_id, "type": body.type}


@router.get("/daily")
def v2_daily(day: str | None = None, limit: int = 12):
    """当日明细：指定日（默认今天）的作答/自评/意图事件（页 B 热力图下）。"""
    from datetime import date as _d
    day = day or _d.today().strftime("%Y-%m-%d")
    kp_meta = _kp_meta()
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT ts, kp, qtype, mode, result, meta FROM attempt_events "
            "WHERE day=? AND mode IN ('answer','selfassess','intent') "
            "ORDER BY ts DESC LIMIT ?", (day, int(limit))
        ).fetchall()
    finally:
        conn.close()
    items = []
    for r in rows:
        meta = json.loads(r[5]) if r[5] else {}
        items.append({
            "ts": r[0], "kp_id": r[1],
            "kp_name": (kp_meta.get(r[1]) or {}).get("name") or (r[1] or "—"),
            "qtype": r[2], "mode": r[3], "result": r[4],
            "stmt": _safe_trunc_math(meta.get("stmt_summary") or meta.get("raw") or "", 140),
            "rule": meta.get("rule"),
        })
    return {"day": day, "items": items}


@router.get("/patterns")
def v2_patterns(limit: int = 8):
    """薄弱模式卡（规则版蒸馏器，source=rule；LLM 精炼版 deferred）。

    从错题事件聚合：每 kp 取近 N 错题，主导归因 × kp_graph 坑位/典型形态 →
    模式文案。只读投影数据；patterns 表仍留待 LLM 蒸馏器落库。
    """
    from collections import Counter
    kp_meta = _kp_meta()
    conn = get_conn()
    try:
        # 用 project_mistakes（归因已按 override 合并）而非裸事件——
        # 与错因分布/复习卡口径一致（judge 复验发现的口径矛盾根因）
        mistakes = mem2.project_mistakes(conn, limit=500)
    finally:
        conn.close()
    agg = {}
    for mk in mistakes:
        kp = mk.get("kp")
        if not kp:
            continue
        a = agg.setdefault(kp, Counter())
        t = (mk.get("attribution") or {}).get("type") if isinstance(mk.get("attribution"), dict) else None
        a[t or "未归因"] += 1
    out = []
    for kp, cnt in agg.items():
        m = kp_meta.get(kp)
        if not m:
            continue
        attr, w = cnt.most_common(1)[0]
        wrong_n = sum(cnt.values())
        pitfall = ""
        for pid in pack_loader.list_packs():
            pk = pack_loader.load_pack(pid)
            k = pack_loader.get_kp(pk, kp)
            if k:
                pitfall = (k.get("pitfalls") or [""])[0]
                break
        out.append({
            "kp": kp,
            "name": m["name"], "pack_id": m["pack_id"],
            "wrong_count": wrong_n,
            "attribution": attr,
            "pattern": f"「{m['name']}」近期 {wrong_n} 次错题以「{attr}」为主",
            "advice": f"建议回看典型坑位：{pitfall}" if pitfall else "建议做一次该考点的变式训练",
            "source": "rule",
        })
    out.sort(key=lambda x: -x["wrong_count"])
    # 物化（2026-09-12）：规则模式卡写入 patterns 表（source=rule, 未确认）——
    # 表从此不再是空壳，为 LLM 精炼蒸馏器与 confirmed 工作流备好数据底座
    conn = get_conn()
    try:
        for it in out[:int(limit)]:
            try:
                mem2.patterns_set(conn, it["pack_id"], it["kp"],
                                  f"{it['pattern']}。{it['advice']}",
                                  source="rule", confirmed=False)
            except Exception:  # noqa: BLE001 —— 物化失败不影响读时聚合返回
                pass
    finally:
        conn.close()
    return {"items": out[:int(limit)]}
