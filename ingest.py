"""MathForge 摄取模块（T4/T5）：Markdown/PDF 讲义 -> 知识点结构化 JSON。

三层分工：LLM 管理解与抽取（本模块），验证与决策不在此。
Task5 扩展：PDF 文本通道（pymupdf 本地抽取，零 API）复用本模块 Markdown 链。
用法：python ingest.py <讲义.md|讲义.pdf> [输出.json]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from llm import chat_json

SYSTEM = """你是数学讲义摄取引擎。把 Markdown/LaTeX 讲义抽取为结构化知识点 JSON，供出题引擎使用。

要求：
1. LaTeX 保真：所有公式原样保留 LaTeX 语法（\\dfrac、\\begin{pmatrix} 等），禁止转写成纯文本
2. 知识点拆分粒度：一个知识点 = 可独立出题的最小单元（如「二阶矩阵求逆」独立于「矩阵乘法」）
3. prerequisites 只能引用本讲义内出现的知识点名或通用的前置知识点名
4. sympy_check 行是讲义自带的验证表达式，原样收入对应知识点的 sympy_checks
5. 只输出 JSON，schema：
{"subject": "科目", "chapter": "章名", "kp_list": [
  {"kp": "知识点名", "definition_md": "核心定义(含公式, LaTeX)",
   "prerequisites": ["前置知识点"], "key_formulas": ["关键公式(LaTeX)"],
   "common_mistakes": ["常见错误"], "example_count": 例题数,
   "sympy_checks": ["讲义自带的 sympy 验证表达式"],
   "difficulty_ceiling": "基础|进阶|综合"}
]}"""


def ingest_markdown(md_text: str, source: str = "") -> dict:
    """讲义文本 -> 结构化知识点 JSON（dict）。"""
    user = f"【讲义来源：{source}】\n\n{md_text}"
    return chat_json(
        [{"role": "system", "content": SYSTEM},
         {"role": "user", "content": user}],
        temperature=0.3,  # 摄取是理解任务，低温保稳
        namespace="ingest:",
    )


def _normalize_delims(text: str) -> str:
    """VLM 输出用 \\(...\\)/\\[...\\] 分隔符，归一化为 $...$/$$...$$（KaTeX 兼容）。"""
    for open_d, close_d, target in ((r"\(", r"\)", "$"), (r"\[", r"\]", "$$")):
        out, i, open_now = [], 0, False
        while i < len(text):
            if not open_now and text.startswith(open_d, i):
                out.append(target)
                open_now = True
                i += len(open_d)
            elif open_now and text.startswith(close_d, i):
                out.append(target)
                open_now = False
                i += len(close_d)
            else:
                out.append(text[i])
                i += 1
        text = "".join(out)
    return text


def extract_pdf_text(pdf_path: str) -> str:
    """PDF -> 纯文本（pymupdf 本地抽取，零 API）。

    Task5 主通道：文本型 PDF 直接在本地抽文字，之后复用 ingest_markdown 链。
    仅抽文本，不做任何外部请求；空页/无文本页自然跳过。
    """
    import pymupdf  # PyMuPDF>=1.24 官方命名（旧版为 fitz，同名兼容）

    doc = pymupdf.open(pdf_path)
    try:
        parts = []
        for i, page in enumerate(doc):
            text = page.get_text().strip()
            if text:
                parts.append(f"--- 第 {i + 1} 页 ---\n{text}")
        return "\n\n".join(parts)
    finally:
        doc.close()


def ingest_pdf(pdf_path: str) -> dict:
    """PDF 讲义 -> 知识点 JSON（文本抽取零 API，抽取后复用 Markdown 摄取链）。"""
    text = extract_pdf_text(pdf_path)
    if not text.strip():
        raise ValueError("PDF 未能抽取到文本（可能是纯扫描件，请走图片实验通道）")
    return ingest_markdown(text, source=Path(pdf_path).name)


def ingest_image(image_path: str) -> dict:
    """扫描讲义页 -> VLM 转写 -> 知识点 JSON（T5 前置通道）。"""
    from vlm import transcribe_image

    md = _normalize_delims(transcribe_image(image_path))
    return ingest_markdown(md, source=Path(image_path).name)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("用法: python ingest.py <讲义.md|讲义.pdf> [输出.json]")
        return 2
    src = Path(argv[1])
    if src.suffix.lower() == ".pdf":
        result = ingest_pdf(str(src))
    else:
        md = src.read_text(encoding="utf-8")
        result = ingest_markdown(md, source=src.name)
    out = Path(argv[2]) if len(argv) > 2 else src.with_suffix(".ingested.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    kps = result.get("kp_list", [])
    print(f"摄取完成：{src.name} -> {out.name}")
    print(f"科目={result.get('subject')} 知识点数={len(kps)}")
    for kp in kps:
        print(f"  - {kp['kp']}（前置: {', '.join(kp.get('prerequisites') or []) or '无'}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
