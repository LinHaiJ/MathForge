"""T5: 扫描讲义页 -> 多模态模型 -> LaTeX/MD 转写（DeepSeek 官方视觉端点）。

用法：python vlm.py <图.png> [图2.png ...] [输出.md]
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

from llm import chat

VISION_MODEL = "deepseek-v4-flash-vision-exp"

PROMPT = """你是数学讲义数字化引擎。把这张扫描讲义页完整转写为 Markdown+LaTeX 文档。

要求：
1. 公式全部用 LaTeX（行内 $...$，独立公式 $$...$$），保真还原每一个符号、上下标、括号
2. 保留原文结构：标题层级、编号（如 (1)(2)(3)）、重点标注（★ 用文字注明）
3. 手写旁注/箭头批注用 `> 旁注：` 引用块单独标注，不与正文混排
4. 表格用 MD 表格；图形用 `【图：一句话描述】` 占位
5. 不要翻译、不要改写、不要补充原文没有的内容；无法辨认的字用 `[?]` 标注
6. 直接输出转写结果，不要任何前后解释"""


def transcribe_image(image_path: str) -> str:
    b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
    data_uri = f"data:image/png;base64,{b64}"
    messages = [
        {"role": "system", "content": PROMPT},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": data_uri}},
            {"type": "text", "text": "转写这张讲义页。"},
        ]},
    ]
    return chat(messages, model=VISION_MODEL, temperature=0.1, namespace="vlm:")


def main(argv: list[str]) -> int:
    paths = [a for a in argv[1:] if a.lower().endswith((".png", ".jpg", ".jpeg"))]
    out = Path(argv[-1] if not argv[-1].lower().endswith((".png", ".jpg", ".jpeg")) else "证据/vlm_transcribe.md")
    parts = ["# VLM 扫描讲义转写（T5）\n"]
    for p in paths:
        print(f"转写 {p} ...")
        md = transcribe_image(p)
        parts.append(f"\n---\n\n## 源图：{Path(p).name}\n\n{md}\n")
        print(f"  -> {len(md)} 字符")
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"完成 -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
