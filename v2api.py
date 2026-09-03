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

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import assembler
import mem2
import pack_loader
import policy2
import router as v2route
from attribute import attribute_error
from generate import generate_question, _FAMILY_KP
from verify import check_answer

router = APIRouter(tags=["v2"])

# 库路径覆盖环境变量（测试隔离；未设置时用 mem2 默认库 mathforge.db）
DB_ENV = "MATHFORGE_V2_DB"

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
    "verify_level", "params", "family", "routed", "difficulty", "attempt",
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
                 if it and all(bool(v) for _, v in it.get("assert", []))]
        if not items:
            return None
        idx = 0 if seed is None else seed % len(items)
        return _normalize_pack_question(items[idx], mod, kp_name or items[idx].get("kp"))
    return None


def _normalize_pack_question(item: dict, mod, kp_name: str) -> dict:
    """把包内族实例归一成与 /v2/turn（v1 家族路径）同构的 question 字典。

    与 generate_from_family 输出同构：statement_md/answer_sympy/answer_md/analysis/
    options/correct/verify_level/params/family/routed/difficulty。analysis 留空
    （包内族无 LLM 解析，零 API）；verify_level=green（构造即正确）；routed=family
    （与 v1 家族路由证据一致，前端无需区分来源）。
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
        "analysis": "",
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
    """当前 v2 库路径（环境变量优先，便于测试隔离）。"""
    return os.environ.get(DB_ENV) or str(mem2.default_db_path())


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
        pack_q = _try_pack_family(pack, body.kp_id)
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

    gen_qtype = _GEN_QTYPE.get(body.qtype, body.qtype)
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
    correct = check_answer(body.student_answer, body.standard_answer or "")

    attribution = None
    overridden = False
    if not correct:
        if body.attribution_override:
            attribution = body.attribution_override
            overridden = True
        else:
            # attribute_error 自带兜底（LLM 不可用时返回低置信占位），不阻塞闭环
            attribution = attribute_error(
                body.statement_md, body.student_answer,
                body.standard_answer or "", body.analysis,
            )["attribution"]

    attr_payload = {"type": attribution, "conf": 1.0 if overridden else 0.9} if attribution else None
    # 落库增强（B1）：meta 存题干/答案摘要与难度，供 /v2/variant 取「源题」与溯源区
    meta = {
        "stmt_summary": (body.statement_md or "")[:160],
        "standard_summary": (body.standard_answer or "")[:160],
        "difficulty": body.difficulty,
    }
    conn = get_conn()
    try:
        event_id = mem2.append_event(
            conn,
            pack=body.pack_id,
            kp=body.kp,
            qtype=body.qtype,
            mode="answer",
            result=_RESULT_CORRECT if correct else _RESULT_WRONG,
            attribution=attr_payload,
            user_override=attr_payload if overridden else None,
            meta=meta,
            sim=0,
        )
        pack = _load_pack(body.pack_id)
        if pack is None:
            return {"ok": True, "correct": correct, "attribution": attribution,
                    "overridden": overridden, "event_id": event_id, "decision": None,
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
            "decision": {"rule": decision["rule"], "action": decision["action"]},
            "state": {k: v for k, v in state.items() if k != "conn"},
        }
    finally:
        conn.close()


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
                  "stmt_summary": (body.statement_md or "")[:160],
                  "standard_summary": (body.standard_answer or "")[:160]},
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
                "reason": "演示模式或未配置 API key：分步归因不可用在（机器不判步骤分，请对照解析自评）"}

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
            "stmt": (meta.get("stmt_summary") or "")[:100],
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
    finally:
        conn.close()
    packs = []
    for pid in pack_loader.list_packs():
        pack = _load_pack(pid)
        if pack is None:
            continue
        packs.append({"id": pid, "name": pack["manifest"].get("name"),
                      "kp_count": len(pack["kp_graph"])})
    return {"kp_count": len(allp), "event_count": event_count, "kps": allp, "packs": packs}


# --------------------------------------------------------------------------- #
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
            "FROM attempt_events WHERE day >= ? GROUP BY day ORDER BY day",
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
            "stmt": _safe_trunc_math(meta.get("stmt_summary") or meta.get("raw") or "", 60),
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
        rows = conn.execute(
            "SELECT kp, attribution FROM attempt_events "
            "WHERE result IN ('wrong','partial') AND mode IN ('answer','selfassess') "
            "AND attribution IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    agg = {}
    for kp, at in rows:
        if not kp:
            continue
        a = agg.setdefault(kp, Counter())
        try:
            t = (json.loads(at) or {}).get("type") if at and at.startswith("{") else at
        except Exception:
            t = at
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
    return {"items": out[:int(limit)]}
