# 分类路径 → packs kp 映射（AI 变式引擎任务书 P0-b，2026-09-15）
#
# 输入：cache/bank_stats.json（P0-a 产出，748 条分类路径）+ 三包 kp_graph.json（87 kp）
# 输出：cache/kp_align.json  {path: {kp_ids, confidence, reason, question_count}}
# 纪律：namespace=bank_align + temperature=0；允许一对多（≤3）；无匹配如实空数组；
#      单批 JSON 失败重试 1 次，仍失败记入 failed_batches，不硬塞。
# 用法：python scripts/kp_align.py [--batches N]   # 探针用 --batches 1

from __future__ import annotations

import argparse
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, os.path.join(_ROOT, "v2"), os.path.join(_ROOT, "v1")):
    if p not in sys.path:
        sys.path.insert(0, p)

sys.stdout.reconfigure(encoding="utf-8")

import llm  # noqa: E402

OUT = os.path.join(_ROOT, "cache", "kp_align.json")
BATCH = 25
# LLM 会把 listing 里的「（62 题）」计数尾巴原样带回 → 匹配前规范化剥掉
_TAIL_Q = re.compile(r"[（(]\s*\d+\s*题\s*[)）]\s*$")


def _norm(s: str) -> str:
    return _TAIL_Q.sub("", str(s or "")).replace(" ", "").strip()
SYSTEM = """你是考研数学真题分类对齐助手。给定【知识点清单】（id | 名称 | 章节）与一批【真题分类路径】
（题库站点的多级分类，如「高等数学 / 极限 / 极限 / 极限计算 / 函数极限 / 0/0」），
为每条路径映射到 0-3 个最匹配的知识点 id。规则：
1. 只能使用清单中的 id，禁止发明。
2. 一条路径可对应多个 kp（综合题），最多 3 个；按匹配强度排序。
3. 清单中没有合理对应（如物理应用、竞赛向、清单未覆盖的章节）→ kp_ids 空数组。
4. confidence：high=语义直译；medium=需跨级理解；low=勉强或无匹配。
5. reason 一句话（≤20 字）。
输出 JSON：{"alignments":[{"path":"与输入逐字一致","kp_ids":[],"confidence":"high|medium|low","reason":"..."}]}"""


def load_kp_list() -> list[dict]:
    rows = []
    for pack in ("calculus", "linear", "probability"):
        g = json.load(open(os.path.join(_ROOT, "packs", pack, "kp_graph.json"), encoding="utf-8"))
        kps = g if isinstance(g, list) else (g.get("kp_graph") or g.get("kps") or [])
        for k in kps:
            rows.append({"pack": pack, "id": k.get("id"), "name": k.get("name"),
                         "section": k.get("section", "")})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batches", type=int, default=0, help="0=全量；N=只跑前 N 批（探针）")
    args = ap.parse_args()

    stats = json.load(open(os.path.join(_ROOT, "cache", "bank_stats.json"), encoding="utf-8"))
    paths = [(r["path"], r["count"]) for r in stats["by_category"] if r["path"] != "unknown"]
    paths.sort(key=lambda t: -t[1])
    kp_rows = load_kp_list()
    kp_block = "\n".join(f"{r['id']} | {r['name']} | {r['section']}" for r in kp_rows)

    batches = [paths[i:i + BATCH] for i in range(0, len(paths), BATCH)]
    if args.batches:
        batches = batches[:args.batches]

    aligned: dict[str, dict] = {}
    failed: list[int] = []
    for bi, batch in enumerate(batches):
        listing = "\n".join(f"{i + 1}. {p}（{c} 题）" for i, (p, c) in enumerate(batch))
        user = f"【知识点清单】\n{kp_block}\n\n【真题分类路径】\n{listing}"
        try:
            data = llm.chat_json(
                [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                temperature=0, namespace="bank_align",
            )
            got = {a.get("path"): a for a in (data.get("alignments") or [])}
        except Exception as e:  # noqa: BLE001
            failed.append(bi)
            print(f"batch {bi}: FAILED {str(e)[:80]}")
            continue
        idx = {_norm(p): p for p, _ in batch}
        hit_index = {}
        for raw, a in got.items():
            p = idx.get(_norm(raw))
            if p:
                hit_index[p] = a
        n_hit = 0
        for p, c in batch:
            a = hit_index.get(p)
            if a and a.get("kp_ids"):
                aligned[p] = {"kp_ids": a["kp_ids"][:3], "confidence": a.get("confidence", "low"),
                              "reason": a.get("reason", ""), "question_count": c}
                n_hit += 1
        print(f"batch {bi}: {len(batch)} paths, aligned {n_hit}")

    conf = {}
    for v in aligned.values():
        conf[v["confidence"]] = conf.get(v["confidence"], 0) + 1
    out = {
        "total_paths": len(paths),
        "aligned_paths": len(aligned),
        "failed_batches": failed,
        "confidence_dist": conf,
        "alignments": aligned,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"aligned {len(aligned)}/{len(paths)} paths, failed_batches={failed}, conf={conf}")
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
