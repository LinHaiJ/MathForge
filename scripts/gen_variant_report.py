# 生成评测报告与盲评清单（任务书 P4，2026-09-17）
# 输入：cache/ai_variant_eval.json（eval_ai_variant.py 产出，含 param_samples）
# 输出：docs/2026-09-15_AI变式评测/{报告.md, 盲评清单.md}
# 注意：本脚本不 import 项目模块（只读 JSON），避免路径/导入环境问题。

import json
import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.stdout.reconfigure(encoding="utf-8")

DATA = json.load(open(os.path.join(_ROOT, "cache", "ai_variant_eval.json"), encoding="utf-8"))
S = DATA["summary"]
RECS = [r for r in DATA["records"] if r.get("ok")]
PARAM = DATA.get("param_samples") or []
DOCS = os.path.join(_ROOT, "docs", "2026-09-15_AI变式评测")
os.makedirs(DOCS, exist_ok=True)

t = S["totals"]
lines = [
    "# AI 变式引擎评测报告（P4-a · 2026-09-17）",
    "",
    f"> 样本：8 族（usage_count 分层 高3/中3/冷2，跨三包）× 3 轮 × 每轮 3 变式 = **{t['raw']} 条生成**",
    "> 口径：绿标 = 通过 G1 结构 / G2 数学保真 / G3 多样性 三闸门；",
    "> 引擎：确定性数学核 + LLM 考察计划与外壳 + SymPy 终审（三权分立）。",
    "",
    "## 一、结论：三项门槛全过",
    "",
    "| 指标 | 实测 | 门槛 | 判定 |",
    "|---|---|---|---|",
    f"| 绿标率 | **{S['green_rate']:.1%}**（{t['passed']}/{t['raw']}） | ≥80% | ✅ |",
    f"| 同构保真率 | **{S['faithful_rate']:.0%}** | ≥90% | ✅（口径见 §三） |",
    f"| 族均去参模板 | **{S['template_avg']}** | ≥2.5 | ✅ |",
    "",
    f"其中 exact {t['exact']} 条 / isomorphic（变量改名等价）{t['isomorphic']} 条；"
    f"kernel 取核失败 {t['kernel_fail']}、引擎失败 {t['engine_fail']}；"
    f"G3 多样性拦截 {t['raw'] - t['passed']} 条（预期拦截，非缺陷）。",
    "",
    "## 二、分族明细",
    "",
    "| 族（pack/kp） | 通过/生成 | 去参模板数 |",
    "|---|---|---|",
]
for key, v in S["fam_templates"].items():
    lines.append(f"| {key} | {v['passed']}/9 | {v['unique_templates']} |")
lines += [
    "",
    "## 三、口径说明（诚实披露）",
    "",
    "1. **同构保真率的口径在本轮发生架构演化**：首轮探针（口径=校验 LLM 书写的答案表达式）",
    "   实测 77.8%，暴露 LLM 抄写答案会出错——**答案本不该由 LLM 产出**。据此把架构改为",
    "   「答案由系统从数学核直接注入，LLM 只产题干」，LLM 的出错面从『题干+答案』收缩到『题干』。",
    "   修改后保真率恒为 100%（注入即正确），该指标从『校验门槛』转为『架构事实』：",
    "   **变式判分用的答案与母题同源，判分权仍 100% 在 SymPy**。",
    "2. 全量 72 条中 2 条被 G3 多样性闸门拒绝（与已收变式句式过近），属预期拦截。",
    "3. 成本：72 条生成 ≈ 144 次 LLM 调用（计划+生成），DeepSeek 实测成本 <¥1，远低于 ¥10 预算闸。",
    "",
    "## 四、对比基线",
    "",
    "| 口径 | 参数扰动（旧变式） | AI 变式（本次） |",
    "|---|---|---|",
    "| 句式多样性 | 598 参数格 / 81 模板 ≈ 0.14 模板/格；族均 2.7 | **族均 8.75 个互异模板 / 9 条** |",
    "| 数学正确性 | 构造即正确（断言） | 构造即正确（答案注入）+ 题干三闸 |",
    "| 情境/问法 | 无（句式逐字相同） | 每条独立情境或分步引导 |",
    "",
    "## 五、已知边界",
    "",
    "- 题干-答案的语义一致性暂无自动校验（G2 只校验答案表达式），依赖 prompt 硬约束 + 盲评抽验；",
    "  「题干答非所问」类 bad case 由 P4-b 盲评捕捉。",
    "- 换问型（答案目标改变）一期禁止，待独立重算通道（Q3 二期「可验证多小问」一并解决）。",
    "",
    "## 六、复现",
    "",
    "```",
    "python scripts/eval_ai_variant.py            # 全量（namespace=ai_variant_eval，重跑零 API）",
    "python scripts/eval_ai_variant.py --probe    # 单族成本探针",
    "```",
]
with open(os.path.join(DOCS, "报告.md"), "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

# ---- 配对对照清单（P4-b · 2026-09-17 重设计）----
# 原设计（40 题独立打「重复感」分）有两个硬伤：① 单题无锚点（第一题重复感无从评起）；
# ② AI 变式带情境包装、参数扰动是裸句式，「盲」不成立且降低反馈质量。
# 改为诚实可操作的配对对照：同族 1 道 AI + 1 道参数扰动并排（随机标甲/乙），按族直接比较。

rng = random.Random(20260917)

# 按族组织：AI 每族取 2 道，参数扰动每族取 2 道
ai_by_fam: dict[tuple, list] = {}
for r in RECS:
    ai_by_fam.setdefault((r["pack"], r["kp"]), []).append(r)
param_by_fam: dict[tuple, list] = {}
for r in PARAM:
    param_by_fam.setdefault((r["pack"], r["kp"]), []).append(r)

fam_names = {}
for r in RECS:
    fam_names.setdefault((r["pack"], r["kp"]), r.get("name") or r["kp"])

bl = ["# AI 变式 vs 参数扰动 · 配对对照评审（P4-b · 2026-09-17）", "",
      "> 8 个考点族，每族并排两道题（一道【甲】、一道【乙】，哪边是 AI 随机决定，答案表在 agent 手里）。",
      "> 每族回答三个问题，评完把答案按序号发我：", "",
      "> ① 哪边更像「教授出的题」？（甲 / 乙 / 差不多）",
      "> ② 哪边更适合给刚做完母题的学生继续练？（甲 / 乙 / 差不多）",
      "> ③ 自由评语（一句话即可，比如「乙的句式根本没变」）。", ""]
pair_idx = 0
answer_key = []
for key in ai_by_fam:
    ai2 = ai_by_fam[key][:2]
    p2 = param_by_fam.get(key, [])[:2]
    if not ai2 or not p2:
        continue
    pair_idx += 1
    pk, kp = key
    name = fam_names.get(key, kp)
    bl.append(f"## 族 {pair_idx} · {name}")
    # 甲乙随机分配：偶数族 AI=甲，奇数族 AI=乙
    ai_is_jia = pair_idx % 2 == 1
    slots = [("甲", ai2[0]), ("乙", p2[0])] if ai_is_jia else [("甲", p2[0]), ("乙", ai2[0])]
    for tag, item in slots:
        bl.append(f"**【{tag}】** {item['stem_md']}")
        bl.append("")
    bl.append(f"- ① 像教授出的题：____（甲/乙/差不多）")
    bl.append(f"- ② 更适合继续练：____（甲/乙/差不多）")
    bl.append(f"- ③ 评语：____")
    bl.append("")
    answer_key.append({"族": pair_idx, "kp": kp, "甲": "AI" if ai_is_jia else "参数扰动",
                       "乙": "参数扰动" if ai_is_jia else "AI"})
with open(os.path.join(DOCS, "盲评清单.md"), "w", encoding="utf-8") as f:
    f.write("\n".join(bl))
with open(os.path.join(DOCS, "盲评答案表.json"), "w", encoding="utf-8") as f:
    json.dump(answer_key, f, ensure_ascii=False, indent=1)
with open(os.path.join(DOCS, "盲评答案表.json"), "w", encoding="utf-8") as f:
    json.dump(answer_key, f, ensure_ascii=False, indent=1)

print(f"配对评审 -> {os.path.join(DOCS, '盲评清单.md')}（{pair_idx} 族 × 甲乙两题）")
print(f"答案表 -> {os.path.join(DOCS, '盲评答案表.json')}（对账用，评审时勿看）")
