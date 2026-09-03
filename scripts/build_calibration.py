"""人工抽验入口构建器（晨间数学抽验 30min 配套）。

产出 证据/人工抽验_绿标+归因校准.html：
  A. 绿标抽验：T6 已验证绿标 3 题 + 拉格朗日家族恢复题 3 题（含判官分歧案例）
  B. 归因真人校准 20 题：LLM 仿真四类错误学生作答（目标类型隐藏），用户盲标
侧车 证据/人工校准_数据.json 保存目标类型+系统预测，供回传结果后离线评分。
"""

import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sympy as sp  # noqa: E402
from attribute import attribute_error  # noqa: E402
from llm import chat_json  # noqa: E402
from verify import _parse  # noqa: E402

HERE = Path(__file__).resolve().parent.parent

# ---------- A. 绿标抽验集 ----------
t6 = json.load(open(HERE / "证据" / "t6_questions.json", encoding="utf-8"))["questions"]
greens = []
for q in t6:
    if q["verify_level"] != "green":
        continue
    v = q.get("verification") or {}
    greens.append({
        "id": q.get("params", {}).get("id") or f"绿-{len(greens)+1}",
        "stem": q["statement_md"],
        "sys_answer": q.get("answer_sympy"),
        "trace": f"双级验证通过（SymPy构造+盲解对账一致={v.get('solver_agree')}）│ 参数={q.get('params')}",
        "family": "罗尔/中值通用",
    })

# 拉格朗日家族恢复题（缓存的参数化模板实例 + 判官分歧记录）
lag = []
# L1: [1,e] ln 题（实例化完整版已在缓存 statement_md）
lag.append({
    "id": "拉格朗日-L1",
    "stem": "设 $f(x)=\\ln x$，在区间 $[1,\\mathrm e]$ 上应用拉格朗日中值定理，则存在 $\\xi\\in(1,\\mathrm e)$，使得 $f'(\\xi)=\\dfrac{f(\\mathrm e)-f(1)}{\\mathrm e-1}$，求 $\\xi$ 的值。",
    "sys_answer": "exp(1) - 1（即 ξ = e−1 ≈ 1.718，∈(1,e) 前提自洽）",
    "trace": "该实例为缓存恢复；注意同族 [1,5] 版本题干曾把分子错写丢失，此版公式完整",
    "family": "拉格朗日家族",
})
# L2/L3: x²+ax on [b,b+1] 判官分歧案例（构造 6/7 vs 盲解 5）
for a, b in [(1, 2), (2, 2)]:
    f = lambda x: x**2 + a * x
    diff = sp.simplify(f(sp.Symbol("x") + 1) - f(sp.Symbol("x")))
    lag.append({
        "id": f"拉格朗日-L{len(lag)+1}",
        "stem": f"设 $f(x)=x^2+{a}x$ 在区间 $[{b},{b}+1]$ 上满足拉格朗日中值定理条件，则存在 $\\xi\\in({b},{b}+1)$ 使 $f'(\\xi)=f({b}+1)-f({b})$，求 $f'(\\xi)$ 的值。",
        "sys_answer": f"{sp.simplify(diff)}（SymPy 直接计算差分 f({b+1})−f({b})）",
        "trace": f"判官分歧案例：系统构造={sp.simplify(diff)}，盲解判官曾答 5（当时被拦/换题）。请人工裁定构造侧是否正确",
        "family": "拉格朗日家族",
    })

greens += lag

# ---------- B. 归因校准 20 题 ----------
eval_set = json.load(open(HERE / "cache" / "eval_set.json", encoding="utf-8"))
pool = [json.loads(l) for l in open(HERE / "题集" / "cxyonly.jsonl", encoding="utf-8")]
used_ids = {q["id"] for q in eval_set}
extra = next(q for q in pool if q["id"] not in used_ids and q["type"] == "subjective"
             and "证明" not in q["answer"])
items = (eval_set + [extra])[:20]

TYPES = ["概念混淆", "计算失误", "方法选错", "审题错误"]
GEN_SYSTEM = """你是"学生错误仿真器"。给定数学题（含标准答案与解析），按指定错误类型生成一个真实自然的学生错误作答（像真实错题本里的，不荒谬）。
- 概念混淆：对定义/定理条件或结论形式理解错误（如忽略定理前提、答非结论形式）
- 计算失误：思路正确但运算出错（符号/数值/移项）
- 方法选错：解题路径选错导致错答
- 审题错误：看错/漏看题目所求（答了另一个量或提前停笔）
选择题：student_answer 给错误选项字母。只输出 JSON：
{"student_answer": "错误最终答案", "student_reasoning": "学生的一两句作答口述（自然、体现该错误类型、不点破类型名）"}"""

calib = []
for i, q in enumerate(items):
    t = TYPES[i % 4]
    try:
        d = chat_json(
            [{"role": "system", "content": GEN_SYSTEM},
             {"role": "user", "content": f"【题目】{q['statement_md'][:600]}\n【标准答案】{q['answer'][:200]}\n【解析】{q['analysis'][:300]}\n【指定错误类型】{t}"}],
            temperature=0.9, namespace="calib-gen:")
        stu, reasoning = d.get("student_answer", ""), d.get("student_reasoning", "")
    except Exception as e:  # noqa: BLE001
        stu, reasoning = "[生成失败]", str(e)[:60]
    pred = attribute_error(q["statement_md"][:400], stu, q["answer"][:150], q["analysis"][:200])
    calib.append({
        "id": q["id"], "kp": q.get("kp") or q["科目"], "type_src": q["type"],
        "stem": q["statement_md"], "options": q.get("options"),
        "standard_answer": q["answer"], "analysis": q["analysis"][:280],
        "student_answer": stu, "student_reasoning": reasoning,
        "intended_type": t,
        "system_prediction": pred.get("attribution"),
        "user_label": None,
    })
    print(f"[{i+1}/20] {q['id']} 目标={t} 仿真答案={stu[:30]} 系统预测={pred.get('attribution')}")

# ---------- 落盘 ----------
json.dump({"greens": greens, "calib": calib},
          open(HERE / "证据" / "人工校准_数据.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print(f"\n数据就绪：绿标抽验 {len(greens)} 题 + 归因校准 {len(calib)} 题")
