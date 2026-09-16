# P0-b 收尾分析 + P0-c kp_graph 增强（AI 变式引擎任务书，2026-09-15）
#
# 动作：
#   1. 未对齐路径聚类（按首段+次段）→ 说明 86.8% 覆盖率缺口来源
#   2. 人工抽验清单 20 条（high 12 / medium 6 / no_match 2，固定种子可复现）
#   3. kp_graph 增强：每 kp 追加 usage_count / source_categories / aligned_at（仅新增字段）
# 产出：docs/2026-09-12_夜间优化/kp_align_review.md + cache/kp_graph_diff.json + 三包 kp_graph 更新

from __future__ import annotations

import json
import os
import random
import sys
from collections import Counter, defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.stdout.reconfigure(encoding="utf-8")

CACHE = os.path.join(_ROOT, "cache")
PACKS = ("calculus", "linear", "probability")
REVIEW = os.path.join(_ROOT, "docs", "2026-09-12_夜间优化", "kp_align_review.md")
DIFF = os.path.join(CACHE, "kp_graph_diff.json")

align = json.load(open(os.path.join(CACHE, "kp_align.json"), encoding="utf-8"))
al = align["alignments"]
all_paths = [r["path"] for r in
             json.load(open(os.path.join(CACHE, "bank_stats.json"), encoding="utf-8"))["by_category"]
             if r["path"] != "unknown"]
unaligned = [p for p in all_paths if p not in al]

# ---- 1. 未对齐聚类 ----
cluster: Counter = Counter()
for p in unaligned:
    segs = [s.strip() for s in p.split("/")]
    cluster[" / ".join(segs[:2]) if len(segs) >= 2 else segs[0]] += 1

# ---- 2. 抽验清单（固定种子） ----
rng = random.Random(20260915)
high = [p for p, v in al.items() if v["confidence"] == "high"]
med = [p for p, v in al.items() if v["confidence"] == "medium"]
pick = [("high", p) for p in rng.sample(high, min(12, len(high)))]
pick += [("medium", p) for p in rng.sample(med, min(6, len(med)))]
pick += [("no_match", p) for p in rng.sample(unaligned, min(2, len(unaligned)))]

lines = ["# kp 对齐人工抽验清单（P0-b · 2026-09-15）", "",
         f"> 待抽验 {len(pick)} 条（high 12 / medium 6 / no_match 2）。判定标准：映射是否符合数学语义，",
         "> 不需要精确——一对多是合法的。请逐条标 ✓ / ✗ / 改，回填本文件。", ""]
for i, (kind, p) in enumerate(pick, 1):
    v = al.get(p, {})
    lines.append(f"### {i}. [{kind}] {p}")
    lines.append(f"- kp_ids: {v.get('kp_ids', '—')}")
    lines.append(f"- reason: {v.get('reason', '—')}")
    lines.append(f"- 判定: （✓ / ✗ / 改→___）")
    lines.append("")
with open(REVIEW, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

# ---- 3. kp_graph 增强 ----
kp_usage: dict[str, int] = defaultdict(int)
kp_srcs: dict[str, list] = defaultdict(list)
for p, v in al.items():
    for kid in v["kp_ids"]:
        kp_usage[kid] += v.get("question_count", 0)
        kp_srcs[kid].append(p)

diff = {}
for pack in PACKS:
    fp = os.path.join(_ROOT, "packs", pack, "kp_graph.json")
    g = json.load(open(fp, encoding="utf-8"))
    kps = g if isinstance(g, list) else (g.get("kp_graph") or [])
    touched, usage_rows = 0, {}
    for k in kps:
        kid = k.get("id")
        if not kid:
            continue
        k["usage_count"] = kp_usage.get(kid, 0)
        srcs = kp_srcs.get(kid, [])
        k["source_categories"] = srcs[:10]
        if len(srcs) > 10:
            k["source_categories_more"] = len(srcs) - 10
        k["aligned_at"] = "2026-09-15"
        touched += 1
        usage_rows[kid] = k["usage_count"]
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(g, f, ensure_ascii=False, indent=1)
        f.write("\n")
    diff[pack] = {"kps_touched": touched, "usage": usage_rows,
                  "zero_usage": [k for k, v in usage_rows.items() if v == 0]}

with open(DIFF, "w", encoding="utf-8") as f:
    json.dump(diff, f, ensure_ascii=False, indent=1)

# ---- 摘要 ----
print(f"unaligned={len(unaligned)} top_clusters={cluster.most_common(8)}")
print(f"review -> {REVIEW}")
for pack, d in diff.items():
    nz = len(d["usage"]) - len(d["zero_usage"])
    print(f"{pack}: {d['kps_touched']} kps, usage>0 {nz}, zero {len(d['zero_usage'])}")
print(f"diff -> {DIFF}")
