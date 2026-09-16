"""错题归因（PRD §8）：闭环四类 = 概念混淆/计算失误/方法选错/审题错误 + few-shot 1 例/类。

用户可一键修正标签（/answer 接口传 attribution_override），修正写入记忆系统。
"""

from __future__ import annotations

from llm import chat_json

CLASSES = ["概念混淆", "计算失误", "方法选错", "审题错误"]

_SYSTEM = """你是考研数学错题归因器。把学生错答归入且仅归入四类之一：
- 概念混淆：对定义/定理条件/记号含义/所求量之间关系的理解错误（如把 dy/dx 当成 dy/dt 求、把"同阶无穷小"理解为比值极限为 1、忽略罗尔定理要求端点值相等）
- 计算失误：思路正确但运算出错（符号、数值、漏乘、移项、行列式算错）
- 方法选错：选择了错误或不优的解题路径导致错答（如该用换元却硬展开、分部方向反、该用行列式判别却直接消元）
- 审题错误：漏看/看错题目给定的条件或所求（答了另一个量、提前停在中间量、用了题目没有的条件）

判定优先级（按学生口述/作答中的证据）：
1. 口述直接表明"算错了"（算错/忘乘/抄错），即使错误连带影响概念判断 → 计算失误
2. 求错对象或理解错量间关系（把 A 量当 B 量求、定理结论形式理解错）→ 概念混淆。
   注意：这不等于审题错误——审题错误是"没看清题目要什么"，概念混淆是"对量与量之间的关系理解错"
3. 明确描述路径选择失误 → 方法选错
4. 答案与所求明显无关或停在中间量 → 审题错误
5. 选择题以口述主因判定；无口述时按选项与典型错误形态匹配

真人校准边界例（领域专家标注，2026-08-30）：
- 参数方程求 dy/dx，学生"把 dy/dx 当成 dy/dt，没除以 dx/dt" → 概念混淆（非审题/非计算）
- "同阶无穷小"被理解为"比值极限等于 1" → 概念混淆（非审题）
- "行列式算错了，以为系数矩阵可逆" → 计算失误（主因是算错，非概念混淆）
- "引力分量算成 G/(x^2+1)，积分忘乘 x" → 计算失误（非审题）

- 扰动幅度分层（合成错答场景）：±1/丢负号/差一个因子 → 计算失误；差 10^k 倍（单位/进位/幂次滑误）→ 计算失误；仅当解题结构本身不同才考虑方法选错/概念混淆

只输出 JSON：{"attribution": "四类之一", "confidence": 0~1, "reason": "一句话依据"}"""


def attribute_error(statement: str, student_answer: str, standard_answer: str,
                    analysis: str | None = None) -> dict:
    """LLM 归因；失败时返回「未归因」占位（不阻塞练习闭环，且不污染记忆——
    「未归因」不命中任何 P 规则，UI 引导人工修正；2026-09-12 P0 前旧默认「计算失误」
    会把演示模式全部错答系统性记成计算失误，已废弃）。"""
    user = f"题干：{statement}\n学生答案：{student_answer}\n标准答案：{standard_answer}"
    if analysis:
        user += f"\n参考解析：{analysis[:400]}"
    try:
        d = chat_json([{"role": "system", "content": _SYSTEM},
                       {"role": "user", "content": user}],
                      temperature=0.2, namespace="attr:")
        if d.get("attribution") in CLASSES:
            return d
    except Exception:  # noqa: BLE001
        pass
    return {"attribution": "未归因", "confidence": 0.0,
            "reason": "归因服务不可用（无 LLM key/演示模式），请人工点选修正"}
