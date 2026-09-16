"""Skill ablation A/B 实验（P0-2）：同一评测计划双臂对比。

A 臂 = 现行为（加载 skills/math-examiner/SKILL.md）
B 臂 = 剥离 SKILL.md（MATHFORGE_ABLATION=1，其余 prompt 逐字一致，缓存命名空间升版防串）
评测计划 = M1 扩样轨道 big40（与 R6 同一 plan）——家族 kp 深轮转 + LLM kp 含 6 个新 kp 上下文。

用法：python scripts/ab_skill_ablation.py
  内部以子进程分臂运行（隔离 env），A 臂直接复用 cache/eval_R6.json 的 M1 结果（零 API）。
  产出 cache/ab_skill_ablation.json + 报告由另写的 证据/ab_skill_ablation.md 引用。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "v1"))  # eval 迁入 v1/（2026-09-12 重组）

ARM_SCRIPT = ROOT / "scripts" / "_ab_arm.py"


def run_arm(arm: str) -> dict:
    env = os.environ.copy()
    env["MATHFORGE_ABLATION"] = "1" if arm == "B" else "0"
    print(f"[arm {arm}] running big40 plan (ablation={'on' if arm == 'B' else 'off'})...")
    t0 = time.time()
    out = subprocess.run([sys.executable, str(ARM_SCRIPT)], env=env,
                         capture_output=True, text=True, encoding="utf-8", timeout=3600,
                         cwd=str(ROOT))
    if out.returncode != 0:
        print(out.stdout[-2000:])
        print(out.stderr[-2000:])
        raise RuntimeError(f"arm {arm} failed")
    print(f"[arm {arm}] done in {round(time.time()-t0,1)}s")
    return json.loads(out.stdout.strip().splitlines()[-1])


def main() -> int:
    r6_path = ROOT / "cache" / "eval_R6.json"
    if not r6_path.exists():
        raise SystemExit("缺 cache/eval_R6.json（A 臂基线）——先跑 python v1/eval.py R6 big40")
    r6 = json.load(open(r6_path, encoding="utf-8"))
    a_m1 = r6["M1"]
    b_m1 = run_arm("B")

    def subset(cases):
        llm = [c for c in cases if c.get("routed") != "family"]
        fam = [c for c in cases if c.get("routed") == "family"]
        return llm, fam

    def stats(cases):
        ok = sum(1 for c in cases if c["ok"])
        n = len(cases)
        from eval import wilson_ci
        return {"total": n, "ok": ok, "rate": round(ok / n, 3) if n else None,
                "ci95": wilson_ci(ok, n) if n else None}

    a_llm, a_fam = subset(a_m1["cases"])
    b_llm, b_fam = subset(b_m1["cases"])
    result = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "plan": "big40（与 R6 同一 40 例计划）",
        "A": {"overall": stats(a_m1["cases"]), "llm_path": stats(a_llm), "family": stats(a_fam),
              "errors": [c["error"] for c in a_m1["cases"] if not c["ok"]]},
        "B": {"overall": stats(b_m1["cases"]), "llm_path": stats(b_llm), "family": stats(b_fam),
              "errors": [c["error"] for c in b_m1["cases"] if not c["ok"]]},
        "A_cases": a_m1["cases"], "B_cases": b_m1["cases"],
    }
    out_path = ROOT / "cache" / "ab_skill_ablation.json"
    json.dump(result, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"A overall: {result['A']['overall']}")
    print(f"B overall: {result['B']['overall']}")
    print(f"A llm_path: {result['A']['llm_path']}")
    print(f"B llm_path: {result['B']['llm_path']}")
    print(f"saved -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
