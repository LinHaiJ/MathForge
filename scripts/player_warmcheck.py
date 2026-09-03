"""player 演示脚本缓存探测（MATHFORGE_DEMO=1 下逐项验证零 API 可复演性）。

用法：MATHFORGE_DEMO=1 python scripts/player_warmcheck.py
输出每步 HIT/MISS——HIT 的步骤可进 player 脚本；MISS 的步骤需预热或绕开。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402
from attribute import attribute_error  # noqa: E402
from generate import generate_question  # noqa: E402
from ingest import ingest_markdown  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LEC_DIR = ROOT.parent / "样例讲义"


def probe(name: str, fn):
    try:
        out = fn()
        ok = out is not False
        print(f"[{'HIT ' if ok else 'MISS'}] {name}")
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[MISS] {name} -> {type(e).__name__}: {str(e)[:90]}")
        return None


def main() -> int:
    print("== 1) ingest 讲义 ==")
    for f in sorted(LEC_DIR.glob("*.md")):
        md = f.read_text(encoding="utf-8")
        probe(f"ingest {f.name} ({len(md)}B)", lambda md=md, f=f: ingest_markdown(md, source=f.name))

    print("== 2) 家族绿标深度（每个 kp 连打 3 发，看第几发断） ==")
    for kp in ["罗尔定理", "拉格朗日中值定理", "矩阵乘法", "逆矩阵", "行列式"]:
        for i in range(3):
            r = probe(f"{kp} 绿标 #{i+1}", lambda kp=kp: generate_question({"kp": kp}, difficulty="基础", qtype="calculation"))
            if r is None:
                break

    print("== 3) 黄标 concept（demo_checklist 预热两条） ==")
    probe("罗尔定理 concept", lambda: generate_question(_load_kp_zwdl("罗尔"), difficulty="基础", qtype="concept"))
    probe("条件概率公式 concept", lambda: generate_question(_load_kp_gl("条件概率公式"), difficulty="基础", qtype="concept"))

    print("== 4) 归因（M3 边界集 payload，R3 修订后 prompt） ==")
    probe("边界例 审题错误", lambda: attribute_error(
        "题目要求用罗尔定理求 ξ，学生答了 f(b)-f(a) 的数值", "2", "ξ=1/2"))
    probe("边界例 计算失误", lambda: attribute_error(
        "1/ξ=ln5/4 应得 ξ=4/ln5，学生化简后写成 ξ=4/5", "4/5", "4/ln5"))

    print("== 5) 真实题干+错答 归因（用第 2 步罗尔实例题干） ==")
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = db.connect(tmp.name)
    q = generate_question({"kp": "罗尔定理"}, difficulty="基础", qtype="calculation")
    if q and q.get("statement_md"):
        probe("罗尔实例题干 错答归因", lambda: attribute_error(
            q["statement_md"], "999", q["answer_sympy"], q.get("analysis")))
    conn.close()
    return 0


def _load_kp_zwdl(name: str) -> dict:
    kps = json.load(open(ROOT / "cache" / "ingest_zwdl.json", encoding="utf-8"))["kp_list"]
    return next(k for k in kps if name in k["kp"])


def _load_kp_gl(name: str) -> dict:
    kps = json.load(open(ROOT / "证据" / "ingest_vlm_sample.json", encoding="utf-8"))["kp_list"]
    return next(k for k in kps if name in k["kp"])


if __name__ == "__main__":
    raise SystemExit(main())
