"""MathForge FastAPI 服务（T8）：摄取/出题/答题闭环/策略台。

最小可用口径：无前端美化要求，能 curl 即可；策略台读 policy_log 渲染决策时间线。
启动：uvicorn app:app --port 8000
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _PkgPath

# v1/ v2/ 目录注册进 sys.path：模块内保持 flat import（import db / import mem2），
# 物理目录分离与导入兼容解耦（重组说明见 docs/2026-09-12_夜间优化/）。
for _d in ("v1", "v2"):
    _p = _PkgPath(__file__).resolve().parent / _d
    if _p.is_dir() and str(_p) not in _sys.path:
        _sys.path.insert(0, str(_p))

import sqlite3
from pathlib import Path

import db
from attribute import CLASSES, attribute_error
from bank import facets, filter_questions, judge_choice, load_bank, similar_questions
from fastapi import FastAPI, HTTPException, UploadFile, File
from generate import generate_question, generate_variant
from ingest import ingest_markdown
from policy import decide
from pydantic import BaseModel
from review import kp_history, today_list
from verify import check_answer

app = FastAPI(title="MathForge", description="记忆驱动的数学训练 Agent")

# ui/ 静态服务（Day5 练习界面/播放器）+ CORS（file:// 打开页面时也能调 API）——纯接入层，不改端点逻辑
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
_UI_DIR = Path(__file__).resolve().parent / "v1" / "ui"
if _UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(_UI_DIR), html=True), name="ui")


def _conn() -> sqlite3.Connection:
    return db.connect()


class IngestBody(BaseModel):
    md: str
    source: str = ""


class AnswerBody(BaseModel):
    kp: str
    statement_md: str
    student_answer: str
    standard_answer: str | None = None
    verify_level: str = "green"
    options: list[str] | None = None
    correct: str | None = None
    question_id: str | None = None
    attribution_override: str | None = None   # 用户一键修正标签
    current_difficulty: str = "基础"
    analysis: str | None = None


@app.post("/ingest")
def api_ingest(body: IngestBody):
    """Markdown 讲义 → 知识点结构化 JSON（走缓存，演示零 API）。"""
    try:
        return {"ok": True, "data": ingest_markdown(body.md, source=body.source)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"摄取失败：{e}")


@app.post("/ingest-file")
async def api_ingest_file(file: UploadFile = File(...)):
    """文件摄取入口（Task5）：PDF 主通道 / 图片实验通道 / MD·TXT。

    - PDF：pymupdf 本地抽文本（零 API）→ 复用 Markdown 摄取链，返回结构化结果
    - 图片：VLM 实验通道；演示模式/无 key 时优雅降级为提示（不报错不崩溃）
    - MD/TXT：与粘贴通道一致（现状保留）
    失败一律返回 200 + {ok:false,...}，由前端展示友好提示，不让 500 打断 UI。
    """
    import os
    import tempfile

    from ingest import extract_pdf_text, ingest_image, ingest_markdown

    name = file.filename or "upload"
    suffix = Path(name).suffix.lower()
    raw = await file.read()
    if len(raw) > 20 * 1024 * 1024:
        return {"ok": False, "error": "文件过大（上限 20MB）"}

    # ---- PDF 主通道：本地抽文本 → 既有 ingest 链（零 API 在文本抽取段）----
    if suffix == ".pdf":
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(raw)
                tmp_path = tmp.name
            try:
                text = extract_pdf_text(tmp_path)
            finally:
                os.unlink(tmp_path)
            if not text.strip():
                return {"ok": False, "channel": "pdf",
                        "error": "PDF 未能抽取到文本（疑似扫描件）——请改用图片实验通道"}
            try:
                data = ingest_markdown(text, source=name)
            except Exception as e:  # noqa: BLE001 —— 演示模式缓存未命中/无 key：友好提示不 500
                return {"ok": False, "channel": "pdf",
                        "error": f"知识点抽取失败（演示模式需缓存预热，或配置 API key）：{e}"}
            return {"ok": True, "channel": "pdf", "source": name,
                    "text_preview": text[:400], "data": data}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "channel": "pdf", "error": f"PDF 解析失败：{e}"}

    # ---- 图片实验通道：VLM（无 key/演示模式优雅降级）----
    if suffix in (".png", ".jpg", ".jpeg"):
        if os.environ.get("MATHFORGE_DEMO") == "1":
            return {"ok": False, "channel": "image", "degraded": True,
                    "message": "图片通道为实验通道（VLM 需 API），演示模式不可用——请改用 PDF / MD 通道"}
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(raw)
                tmp_path = tmp.name
            try:
                data = ingest_image(tmp_path)
            finally:
                os.unlink(tmp_path)
            return {"ok": True, "channel": "image", "source": name, "data": data}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "channel": "image", "degraded": True,
                    "message": f"图片通道（实验）暂不可用：{e}"}

    # ---- MD/TXT：现状保留 ----
    if suffix in (".md", ".markdown", ".txt"):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return {"ok": False, "channel": "md", "error": "文本解码失败（请确认文件为 UTF-8 编码）"}
        try:
            data = ingest_markdown(text, source=name)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "channel": "md", "error": f"知识点抽取失败：{e}"}
        return {"ok": True, "channel": "md", "source": name, "data": data}

    return {"ok": False, "error": f"不支持的文件类型：{suffix or '（无扩展名）'}（支持 .pdf/.png/.jpg/.jpeg/.md/.markdown/.txt）"}


@app.get("/generate")
def api_generate(kp: str, difficulty: str = "基础", qtype: str = "calculation",
                 current_difficulty: str | None = None):
    """策略决策（记忆驱动）→ 出题（双级验证）。未过验证的题绝不外推。"""
    conn = _conn()
    try:
        decision = decide({
            "conn": conn, "current_kp": kp,
            "current_difficulty": current_difficulty or difficulty,
            "watch_kps": [kp],
        })
        q = generate_question({"kp": kp}, difficulty=difficulty, qtype=qtype)
        return {"decision": decision, "question": q}
    finally:
        conn.close()


@app.post("/answer")
def api_answer(body: AnswerBody):
    """判分（分级验证）→ 归因 → 记忆写入 → 策略决策下一步。"""
    conn = _conn()
    try:
        if body.verify_level == "green":
            correct = check_answer(body.student_answer, body.standard_answer or "")
        else:
            from generate import _canon
            claimed = _canon(body.correct or body.standard_answer or "")
            correct = _canon(body.student_answer) == claimed
        attribution = None
        if not correct:
            if body.attribution_override:
                attribution = body.attribution_override
            else:
                attribution = attribute_error(body.statement_md, body.student_answer,
                                              body.standard_answer or "", body.analysis)["attribution"]
        db.record_answer(conn, body.kp, correct=correct, attribution=attribution,
                         question_id=body.question_id, student_answer=body.student_answer,
                         standard_answer=body.standard_answer)
        decision = decide({
            "conn": conn, "current_kp": body.kp,
            "current_difficulty": body.current_difficulty,
            "watch_kps": [body.kp],
        })
        return {"correct": correct, "attribution": attribution, "next": decision}
    finally:
        conn.close()


class VariantBody(BaseModel):
    """变式出题（Day6 任务 4）：源错题上下文 → 变式题 + 溯源块。"""
    kp: str
    source_statement_md: str = ""
    source_params: dict = {}
    source_kp: str | None = None
    source_question_id: str | None = None
    source_difficulty: str | None = None
    source_qtype: str | None = None


@app.post("/variant")
def api_variant(body: VariantBody):
    """变式出题（Day6 任务 4）：以源错题为母本，题卡附「源题与变化」溯源块。

    家族 kp 确定性换参（零 API 演示可用）；其余 kp 走 LLM 扰动链，
    演示模式缓存未命中时优雅拦截（不崩溃、不出网）。
    """
    q = generate_variant(body.kp, {
        "kp": body.source_kp or body.kp,
        "statement_md": body.source_statement_md,
        "params": body.source_params,
        "question_id": body.source_question_id,
        "difficulty": body.source_difficulty,
        "qtype": body.source_qtype,
    })
    return {"question": q}


@app.get("/policy-log")
def api_policy_log(limit: int = 20):
    conn = _conn()
    try:
        return {"log": db.get_policy_log(conn, limit=limit)}
    finally:
        conn.close()


# ---------------- 题库直刷（Day6 任务 1）----------------

class BankAnswerBody(BaseModel):
    """题库选择题作答：字母匹配本地判分（bank.judge_choice）→ 归因 → 记忆 → 决策。"""
    kp: str
    statement_md: str = ""
    student_answer: str                       # 选项字母，如 "B"
    answer: str                               # 标准选项字母
    question_id: str | None = None
    student_answer_md: str | None = None      # 选项全文（LLM 归因证据用）
    answer_md: str | None = None
    analysis: str | None = None
    attribution_override: str | None = None
    current_difficulty: str = "基础"


class SelfAssessBody(BaseModel):
    """解答题自评（任务 2）：不机器判分，三档自评写入记忆。"""
    kp: str
    statement_md: str = ""
    grade: str                                   # 会 / 部分会 / 不会
    question_id: str | None = None
    standard_answer: str | None = None
    analysis: str | None = None
    student_answer: str | None = None            # 可选：粘贴自己的作答/思路 → LLM 辅助归因
    current_difficulty: str = "基础"


class FixAttributionBody(BaseModel):
    kp: str
    attribution: str                             # 四类之一


@app.get("/bank/facets")
def api_bank_facets():
    """题库筛选维度（科目×年份×题型×知识点；难度字段全空，不提供难度筛选）。"""
    return {"facets": facets(load_bank())}


@app.get("/bank/questions")
def api_bank_questions(subject: str | None = None, year: str | None = None,
                       qtype: str | None = None, kp: str | None = None,
                       kp_root: str | None = None, limit: int = 200, offset: int = 0):
    """筛选题库（本地数据不出服务）。answer/analysis 随题下发但 UI 默认折叠防瞄。"""
    questions = filter_questions(load_bank(), subject=subject, year=year,
                                 qtype=qtype, kp=kp, kp_root=kp_root,
                                 limit=limit, offset=offset)
    if questions is None:
        raise HTTPException(status_code=400, detail="非法筛选参数（year 须为 4 位年份；limit/offset 须非负）")
    return {"questions": questions}


@app.get("/bank/similar")
def api_bank_similar(kp: str, exclude_id: str | None = None, limit: int = 3):
    """推荐同类：同 kp 优先，不足补同知识板块。"""
    return {"questions": similar_questions(kp, exclude_id=exclude_id, limit=limit)}


@app.post("/bank/answer")
def api_bank_answer(body: BankAnswerBody):
    """题库选择题：选项字母匹配判分 → 错题 LLM 四类归因 → 写记忆 → 策略决策下一步。"""
    correct = judge_choice(body.student_answer, body.answer)
    attribution = None
    if not correct:
        if body.attribution_override:
            attribution = body.attribution_override
        else:
            attribution = attribute_error(
                body.statement_md,
                body.student_answer_md or body.student_answer,
                body.answer_md or body.answer, body.analysis)["attribution"]
    conn = _conn()
    try:
        db.record_answer(conn, body.kp, correct=correct, attribution=attribution,
                         question_id=body.question_id,
                         student_answer=body.student_answer_md or body.student_answer,
                         standard_answer=body.answer_md or body.answer)
        decision = decide({
            "conn": conn, "current_kp": body.kp,
            "current_difficulty": body.current_difficulty, "watch_kps": [body.kp],
        })
        return {"correct": correct, "attribution": attribution, "next": decision}
    finally:
        conn.close()


@app.post("/bank/self-assess")
def api_bank_self_assess(body: SelfAssessBody):
    """解答题三档自评 → 记忆写入 → 策略决策下一步（复用练习页同一记忆系统）。

    会=掌握度+；部分会/不会=掌握度−并记 mistakes（驱动复习清单）。
    粘贴了作答/思路时走 LLM 四类归因（attribute.py，走缓存，演示零 API 可用），
    失败/未粘贴则不写归因（可人工修正，不阻塞闭环）。
    """
    if body.grade not in ("会", "部分会", "不会"):
        raise HTTPException(400, "grade 必须为 会/部分会/不会")
    correct = body.grade == "会"
    attribution = None
    if not correct and body.student_answer:
        try:
            attribution = attribute_error(body.statement_md, body.student_answer,
                                          body.standard_answer or "", body.analysis)["attribution"]
        except Exception:  # noqa: BLE001 —— 演示模式缓存未命中时不阻塞自评闭环
            attribution = None
    conn = _conn()
    try:
        db.record_answer(conn, body.kp, correct=correct, attribution=attribution,
                         question_id=body.question_id,
                         student_answer=body.student_answer or f"自评：{body.grade}",
                         standard_answer=body.standard_answer)
        decision = decide({
            "conn": conn, "current_kp": body.kp,
            "current_difficulty": body.current_difficulty, "watch_kps": [body.kp],
        })
        return {"grade": body.grade, "correct": correct, "attribution": attribution,
                "next": decision}
    finally:
        conn.close()


@app.post("/bank/fix-attribution")
def api_bank_fix_attribution(body: FixAttributionBody):
    """人工修正最近一次错题归因（四类，写记忆，不重复计分）。"""
    if body.attribution not in CLASSES:
        raise HTTPException(400, f"attribution 必须为四类之一：{'/'.join(CLASSES)}")
    conn = _conn()
    try:
        ok = db.update_last_attribution(conn, body.kp, body.attribution)
        if not ok:
            raise HTTPException(404, "该 kp 无错题记录可修正")
        return {"ok": True, "attribution": body.attribution}
    finally:
        conn.close()


@app.get("/mastery")
def api_mastery():
    conn = _conn()
    try:
        rows = conn.execute("SELECT * FROM mastery").fetchall()
        return {"mastery": [dict(r) for r in rows]}
    finally:
        conn.close()


# ---------------- 今日复习清单 + 遗忘可视化（Day6 任务 3）----------------

@app.get("/review/today")
def api_review_today(limit: int = 20):
    """待复习 kp：掌握度低 × 距上次久优先（半衰期 7 天模型），含建议动作。"""
    conn = _conn()
    try:
        return {"items": today_list(conn, limit=limit), "total": len(today_list(conn, limit=10**9))}
    finally:
        conn.close()


@app.get("/review/history")
def api_review_history(kp: str):
    """单 kp 掌握度随时间的点列（policy_log 决策快照 + 当前值），供遗忘曲线 SVG。"""
    conn = _conn()
    try:
        return {"kp": kp, "points": kp_history(conn, kp)}
    finally:
        conn.close()


_BOARD = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>MathForge 策略台</title><style>
body{font-family:system-ui,sans-serif;max-width:860px;margin:24px auto;background:#f7f8fa;color:#222}
h1{font-size:20px} .card{background:#fff;border:1px solid #e3e6ea;border-radius:8px;padding:12px 16px;margin:10px 0;box-shadow:0 1px 2px rgba(0,0,0,.04)}
.rule{display:inline-block;padding:1px 8px;border-radius:10px;background:#1a73e8;color:#fff;font-size:12px;margin-right:8px}
.muted{color:#888;font-size:12px} pre{background:#f0f2f5;padding:8px;border-radius:6px;font-size:12px;overflow:auto}
</style></head><body>
<h1>🎯 MathForge 策略台 · 决策时间线</h1>
<p class="muted">每条 = 一次策略引擎决策：触发规则 → 输入记忆快照 → 输出（知识点×题型×难度×数量）。记忆驱动，非随机。</p>
<div id="timeline">加载中…</div>
<script>
fetch('/policy-log?limit=50').then(r=>r.json()).then(d=>{
  const el=document.getElementById('timeline');
  el.innerHTML=d.log.map(l=>`<div class="card">
    <span class="rule">${l.trigger_rule}</span><b>${l.output.kp||''}</b>
    <span class="muted">${new Date(l.created_at*1000).toLocaleString()}</span>
    <div>${l.output.reason||''}</div>
    <div>输出：难度 <b>${l.output.difficulty}</b> × ${l.output.count}　题型 ${l.output.qtype}${l.output.explanation?`<br>💡 ${l.output.explanation}`:''}</div>
    <details><summary class="muted">输入快照</summary><pre>${JSON.stringify(l.input_snapshot,null,1)}</pre></details>
  </div>`).join('') || '暂无决策记录';
});
</script></body></html>"""


@app.get("/strategy-board")
def strategy_board():
    """策略台页：渲染 policy_log 决策时间线（触发规则/输入快照/输出题目）。"""
    from fastapi.responses import HTMLResponse
    return HTMLResponse(_BOARD)


from v2api import router as v2_router  # noqa: E402 —— v2 填空闭环接线层（只挂载，不改 v1 端点）
# 每人一库：X-MF-Profile 档案路由在 v2api._ProfileRoute 内处理（路由级 contextvar，
# 不走 app 中间件——BaseHTTPMiddleware 的下游任务孵化时序会吞掉 contextvar）
app.include_router(v2_router, prefix="/v2")

# ui2/ 静态服务（T6 v2 四视图界面 + 填空表达式输入面板；独立目录，零改动 v1 端点）
_UI2_DIR = Path(__file__).resolve().parent / "ui2"
if _UI2_DIR.exists():
    app.mount("/ui2", StaticFiles(directory=str(_UI2_DIR), html=True), name="ui2")
