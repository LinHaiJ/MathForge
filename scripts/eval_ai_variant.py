# AI 变式评测（任务书 P4-a，2026-09-17）
#
# 设计（任务书口径）：
#   样本：首批 8 族（跨三包，按 kp usage_count 高/中/冷分层）× 每族 3 轮 × 每轮 k=3 → ≤72 条生成
#   指标：
#     绿标率     = 通过三闸条数 / 生成总条数（门槛 ≥80%）
#     同构保真率 = 通过 G2 条数 / 通过 G1 条数（exact+isomorphic 都算保真；门槛 ≥90%）
#     族均去参模板 = 各族通过条数的去参模板去重数均值（门槛 ≥2.5）
#   成本：namespace=ai_variant_eval 隔离；任务书预算闸 ≤¥10（--probe 探针后估算再全量）
# 产出：cache/ai_variant_eval.json + docs/2026-09-15_AI变式评测/报告.md（P4-a 脚本出 JSON，报告由汇总步生成）
#
# 用法：python scripts/eval_ai_variant.py [--probe]   # --probe=只跑第 1 族估成本

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, os.path.join(_ROOT, "v2"), os.path.join(_ROOT, "v1")):
    if p not in sys.path:
        sys.path.insert(0, p)

sys.stdout.reconfigure(encoding="utf-8")

import ai_variant  # noqa: E402
import pack_loader  # noqa: E402
from v2.v2api import _try_pack_family  # noqa: E402

OUT_JSON = os.path.join(_ROOT, "cache", "ai_variant_eval.json")
DOCS_DIR = os.path.join(_ROOT, "docs", "2026-09-15_AI变式评测")
ROUNDS, K = 3, 3


def pick_families() -> list[dict]:
    """按 kp_graph usage_count 分层选 8 族：高(top3)/中(next3)/冷(usage=0 优先取 2)。"""
    rows = []
    for pack_id in pack_loader.list_packs():
        pack = pack_loader.load_pack(pack_id)
        if pack is None:
            continue
        for k in pack["kp_graph"]:
            rows.append({"pack": pack_id, "kp": k["id"], "name": k.get("name"),
                         "usage": k.get("usage_count") or 0})
    with_fam = []
    for r in rows:
        pack = pack_loader.load_pack(r["pack"])
        for mod in (pack.get("families") or {}).values():
            if getattr(mod, "KP_ID", None) == r["kp"] and hasattr(mod, "enumerate_family"):
                with_fam.append(r)
                break
    with_fam.sort(key=lambda r: -r["usage"])
    hi = with_fam[:3]
    mid = with_fam[3:6]
    cold_pool = with_fam[6:]
    cold = [r for r in cold_pool if r["usage"] == 0][:2] or cold_pool[-2:]
    picked, seen = [], set()
    for r in hi + mid + cold:
        key = (r["pack"], r["kp"])
        if key not in seen:
            seen.add(key)
            picked.append(r)
    return picked[:8]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="只跑第 1 族（成本探针）")
    args = ap.parse_args()

    fams = pick_families()
    if args.probe:
        fams = fams[:1]
    print(f"picked {len(fams)} families:")
    for r in fams:
        print(f"  {r['pack']}/{r['kp']} usage={r['usage']} {r['name']}")

    rng = random.Random(20260917)
    totals = {"raw": 0, "g1": 0, "g2": 0, "passed": 0,
              "exact": 0, "isomorphic": 0, "kernel_fail": 0, "engine_fail": 0}
    fam_records: dict[tuple, dict] = defaultdict(
        lambda: {"raw": 0, "g1": 0, "passed": 0, "templates": [], "variants": []})
    records = []

    for fi, f in enumerate(fams):
        pack = pack_loader.load_pack(f["pack"])
        for rnd in range(ROUNDS):
            kernel = _try_pack_family(pack, f["kp"], seed=rng.randrange(2 ** 31))
            if kernel is None:
                totals["kernel_fail"] += 1
                records.append({"pack": f["pack"], "kp": f["kp"], "round": rnd,
                                "ok": False, "stage": "kernel"})
                print(f"[{fi + 1}/{len(fams)}] {f['kp']} r{rnd}: kernel None")
                continue
            strategy = "contextual" if rnd % 2 == 0 else "multistep"
            res = ai_variant.make_ai_variants(kernel, k=K, strategy=strategy,
                                              usage_count=f["usage"],
                                              namespace="ai_variant_eval")
            st = res.get("stats") or {}
            totals["raw"] += st.get("raw", 0)
            totals["g1"] += st.get("g1_pass", 0)
            totals["g2"] += st.get("g2_pass", 0)
            fr = fam_records[(f["pack"], f["kp"])]
            fr["raw"] += st.get("raw", 0)
            fr["g1"] += st.get("g1_pass", 0)
            if res.get("ok"):
                for v in res["variants"]:
                    totals["passed"] += 1
                    totals["exact" if v.get("g2_kind") == "exact" else "isomorphic"] += 1
                    fr["passed"] += 1
                    tmpl = ai_variant._mask(v["stem_md"])
                    fr["templates"].append(tmpl)
                    rec = {"pack": f["pack"], "kp": f["kp"], "name": f["name"], "round": rnd,
                           "ok": True, "kind": v["kind"], "g2_kind": v.get("g2_kind"),
                           "stem_md": v["stem_md"], "answer": kernel.get("answer_sympy"),
                           "note": v.get("note", ""), "strategy": strategy}
                    fr["variants"].append(rec)
                    records.append(rec)
            else:
                totals["engine_fail"] += 1
                records.append({"pack": f["pack"], "kp": f["kp"], "round": rnd,
                                "ok": False, "stage": res.get("stage"),
                                "reason": res.get("reason")})
            print(f"[{fi + 1}/{len(fams)}] {f['kp']} r{rnd}({strategy}): "
                  f"raw={st.get('raw', 0)} passed={len(res.get('variants', []))} "
                  f"{str(res.get('reason', ''))[:50]}")

    # ---- 参数扰动对照样本（P4-b 盲评用，同 8 族不同 seed 的族实例） ----
    param_samples = []
    for f in fams:
        pack = pack_loader.load_pack(f["pack"])
        for _ in range(3):
            q = _try_pack_family(pack, f["kp"], seed=rng.randrange(2 ** 31))
            if q:
                param_samples.append({"pack": f["pack"], "kp": f["kp"],
                                      "stem_md": q["statement_md"]})

    # ---- 指标 ----
    green_rate = round(totals["passed"] / totals["raw"], 4) if totals["raw"] else None
    faithful_rate = round(totals["g2"] / totals["g1"], 4) if totals["g1"] else None
    fam_tpl = {}
    for key, fr in fam_records.items():
        uniq = len(set(fr["templates"]))
        fam_tpl["/".join(key)] = {"passed": fr["passed"], "unique_templates": uniq}
    tpl_avg = round(sum(v["unique_templates"] for v in fam_tpl.values()) / len(fam_tpl), 2) \
        if fam_tpl else None

    summary = {
        "families": len(fams),
        "totals": totals,
        "green_rate": green_rate,
        "faithful_rate": faithful_rate,
        "template_avg": tpl_avg,
        "fam_templates": fam_tpl,
        "thresholds": {"green_rate": 0.80, "faithful_rate": 0.90, "template_avg": 2.5},
        "pass": bool(green_rate and green_rate >= 0.80
                     and faithful_rate and faithful_rate >= 0.90
                     and tpl_avg and tpl_avg >= 2.5),
    }
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "records": records,
                   "param_samples": param_samples}, f, ensure_ascii=False, indent=1)

    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"-> {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
