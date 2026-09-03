"""Day6 任务 5：摄取入口补全（PDF 主通道 / 图片实验通道 / MD·TXT 现状保留）。

- extract_pdf_text：pymupdf 本地抽文本（零 API），夹具含 2 个可识别知识点
- /ingest-file 端点：PDF 抽取 → 复用 Markdown 摄取链 → 结构化结果
- 演示模式（MATHFORGE_DEMO=1）下：
  · 图片通道（VLM 需 API）→ 优雅降级提示（不报错不崩溃）
  · PDF 文本抽取零 API 可用；摄取链缓存未命中 → 友好错误提示（非 500）
- 不支持的扩展名 → ok:false 明确提示

夹具 tests/fixtures/sample_ingest.pdf 由 pymupdf 现做（china-ss 内嵌中文字体），
内容为「微分中值定理」小样讲义（罗尔定理 / 拉格朗日中值定理），测试完保留。
"""

import os
import sys
from pathlib import Path

os.environ["MATHFORGE_DEMO"] = "1"  # 零 API：LLM 路径缓存未命中按降级提示，绝不出网

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ingest  # noqa: E402
from ingest import extract_pdf_text  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "sample_ingest.pdf"


def _client():
    from app import app
    from fastapi.testclient import TestClient

    return TestClient(app)


# ---------------- 纯函数：PDF 文本抽取（零 API） ----------------

def test_extract_pdf_text_local():
    text = extract_pdf_text(str(FIXTURE))
    assert "罗尔定理" in text
    assert "拉格朗日中值定理" in text
    assert "sympy_check" in text          # 讲义自带验证行随文本保留
    assert "第 1 页" in text


# ---------------- 端点：PDF 主通道 → 结构化结果 ----------------

def test_ingest_file_pdf_endpoint(monkeypatch):
    fake = {
        "subject": "高数", "chapter": "微分中值定理", "kp_list": [
            {"kp": "罗尔定理", "definition_md": "设 $f(a)=f(b)$ 则存在 $\\xi$ 使 $f'(\\xi)=0$",
             "prerequisites": ["连续"], "key_formulas": ["$f'(\\xi)=0$"],
             "common_mistakes": ["忘记检查端点值相等"], "difficulty_ceiling": "基础"},
            {"kp": "拉格朗日中值定理", "definition_md": "$f'(\\xi)=\\dfrac{f(b)-f(a)}{b-a}$",
             "prerequisites": ["罗尔定理"], "key_formulas": [], "common_mistakes": [],
             "difficulty_ceiling": "进阶"},
        ],
    }
    # 端点内 from ingest import ingest_markdown，须补丁 ingest 模块级名字
    monkeypatch.setattr(ingest, "ingest_markdown", lambda md, source="": fake)

    client = _client()
    r = client.post("/ingest-file",
                    files={"file": (FIXTURE.name, FIXTURE.read_bytes(), "application/pdf")})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["channel"] == "pdf"
    assert d["source"] == FIXTURE.name
    assert d["text_preview"]                          # 前端结构化结果卡用
    assert [k["kp"] for k in d["data"]["kp_list"]] == ["罗尔定理", "拉格朗日中值定理"]


def test_ingest_file_pdf_demo_cache_miss_degrades():
    """演示模式 + 未预热文件名（缓存未命中）：摄取链 raise 被拦截为 ok:false 友好提示，非 500。"""
    client = _client()
    # source=文件名 参与缓存键；换文件名即缓存未命中（同名 sample_ingest.pdf 已预热命中）
    r = client.post("/ingest-file",
                    files={"file": ("未预热_讲义.pdf", FIXTURE.read_bytes(), "application/pdf")})
    assert r.status_code == 200                      # 不 500、不崩溃
    d = r.json()
    assert d["ok"] is False
    assert d["channel"] == "pdf"
    assert d["error"]                                # 友好错误提示


def test_pdf_demo_preheated_dod():
    """DoD 零 API 复演：夹具 PDF 同名上传（演示缓存已预热）→ 结构化结果可进入出题流。

    演示缓存为 gitignored 的本地预热产物（cache/llm/<hash>.json），全新检出时跳过。
    """
    preheated = Path("cache/llm/667fe06f1f0fcbd157cbb18d3cd56d1aab4a92dc.json")
    if not preheated.exists():
        import pytest

        pytest.skip("演示缓存未预热（gitignored），跳过 DoD 复演")
    client = _client()
    r = client.post("/ingest-file",
                    files={"file": (FIXTURE.name, FIXTURE.read_bytes(), "application/pdf")})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["channel"] == "pdf"
    assert [k["kp"] for k in d["data"]["kp_list"]] == ["罗尔定理", "拉格朗日中值定理"]


# ---------------- 端点：图片实验通道（演示模式降级） ----------------

def test_ingest_file_image_demo_degrades():
    client = _client()
    r = client.post("/ingest-file",
                    files={"file": ("讲义页.png", b"\x89PNG fake bytes", "image/png")})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is False
    assert d["channel"] == "image"
    assert d["degraded"] is True
    assert "演示模式" in d["message"]                # 明确提示不可用原因


# ---------------- 端点：MD/TXT 保留 + 非法类型 ----------------

def test_ingest_file_md_channel(monkeypatch):
    monkeypatch.setattr(ingest, "ingest_markdown",
                        lambda md, source="": {"subject": "线代", "kp_list": [
                            {"kp": "矩阵乘法", "definition_md": "定义", "prerequisites": [],
                             "key_formulas": [], "common_mistakes": [], "difficulty_ceiling": "基础"}]})
    client = _client()
    r = client.post("/ingest-file",
                    files={"file": ("讲义.txt", "矩阵乘法：$C=AB$".encode("utf-8"), "text/plain")})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True and d["channel"] == "md"
    assert d["data"]["kp_list"][0]["kp"] == "矩阵乘法"


def test_ingest_file_unsupported_type():
    client = _client()
    r = client.post("/ingest-file",
                    files={"file": ("notes.docx", b"MZ fake docx", "application/octet-stream")})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is False
    assert "不支持的文件类型" in d["error"]
