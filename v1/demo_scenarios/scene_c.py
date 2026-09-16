"""演示场景 C（Day2 任务 4）：评测迭代——四轮指标演化回放 + K1 确定性族现场出题。

数据源：cache/eval_R1/R2/R3/R4.json（评测器全量落盘）；K1 族实例走确定性枚举，
LLM 调用（盲解/解析）命中 cache/llm 预热缓存 → MATHFORGE_DEMO=1 可零 API 复演。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # v1/（db/policy）
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 仓库根（llm 等）
import generate as G  # noqa: E402
from families import enumerate_family  # noqa: E402

EV_DIR = Path(__file__).resolve().parents[2] / "证据" / "demo"


def main() -> int:
    lines = ["# 演示场景 C · 评测迭代（R1 → R2 → R3 → R4）", ""]

    # 1) 四轮指标演化
    rounds = [("R1", "cache/eval_R1.json"), ("R2", "cache/eval_R2.json"),
              ("R3", "cache/eval_R3.json"), ("R4", "cache/eval_R4.json")]
    lines += ["## 指标演化", "",
              "| 轮次 | M1 绿标成功率 | M2 黄标自洽（≥90%） | M3 归因 | M4 变式 |",
              "|---|---|---|---|---|"]
    for tag, path in rounds:
        p = Path(__file__).resolve().parents[2] / path
        if not p.exists():
            continue
        r = json.load(open(p, encoding="utf-8"))
        m3 = f"合成 {r['M3']['synth']['ok']}/{r['M3']['synth']['total']} + 边界 {r['M3']['boundary']['ok']}/4"
        m2 = (f"{r['M2']['ok']}/{r['M2']['total']} = {r['M2']['rate']:.0%}"
              if "M2" in r else "（复测未跑，见 R3）")
        m4 = f"{r['M4']['rate']:.0%}" if "M4" in r else "—"
        lines.append(f"| {tag} | {r['M1']['ok']}/{r['M1']['total']} = {r['M1']['rate']:.0%} "
                     f"| {m2} | {m3} | {m4} |")
    lines += ["",
              "- R1→R2：修订对账公平性与黄标链路 → M2 83%→100%",
              "- R3→R4：扩样 n=20 → 抓获评测器守卫误伤并修复；定位矩阵域为生成弱区（Day3 建 K1 式确定性族）",
              ""]

    # 2) K1 确定性族现场出题（确定性枚举 → 缓存命中的盲解与解析）
    spec = enumerate_family("lm_xi", limit=3)[0]
    q = G.generate_from_family(spec)
    lines += ["## K1 修复后现场出题（拉格朗日 ξ 家族）", "",
              f"题干：{q['statement_md']}", "",
              f"- 答案（SymPy 从结构推导）：`{q['answer_sympy']}`",
              f"- SymPy 断言：{q['sympy_asserts']}",
              f"- 盲解对账：{q['verification']['solver_ans']}（一致={q['verification']['solver_agree']}）",
              f"- 解析：{(q.get('analysis') or '')[:160]}", ""]

    out = "\n".join(lines)
    print(out)
    EV_DIR.mkdir(parents=True, exist_ok=True)
    (EV_DIR / "scene_c.md").write_text(out, encoding="utf-8")
    print("证据已存：证据/demo/scene_c.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
