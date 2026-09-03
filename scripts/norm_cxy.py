"""T3 规范化：cache/cxy_raw.jsonl -> 题集/cxyonly.jsonl（分层筛选，本地私有）。"""

import json
import re
from collections import defaultdict

RAW = "cache/cxy_raw.jsonl"
OUT = "题集/cxyonly.jsonl"

# 各(科目组, 题型)配额：覆盖高数/线代/概率/真题 × subjective/choice
QUOTA = {
    ("高等数学", "subjective"): 40, ("高等数学", "single_choice"): 20,
    ("线性代数", "subjective"): 25, ("线性代数", "single_choice"): 15,
    ("概率统计", "subjective"): 15, ("概率统计", "single_choice"): 10,
    ("真题", "subjective"): 15, ("真题", "single_choice"): 10,
}

def norm(it: dict) -> dict | None:
    path = (it.get("category_full_path") or "").split(" / ")
    seg0 = path[0] if path and path[0] else "?"
    if seg0 == "历年真题":
        subj = path[1] if len(path) > 1 else "数?"   # 数一/数二/数三
        group, kp = "真题", None
    elif seg0 in ("高等数学", "线性代数", "概率统计"):
        group = seg0
        kp = " / ".join(path[1:]) or None
    else:
        return None  # 模拟专区等杂项，本次不入集
    src = it.get("source") or ""
    m = re.search(r"(19|20)\d{2}", src)
    year = int(m.group(0)) if m else None
    ans = (it.get("correct_answer") or (it.get("answer") or {}).get("reference_answer_md") or "").strip()
    stem = (it.get("stem") or "").strip()
    ana = (it.get("answer_explanation") or "").strip()
    if len(stem) < 15 or not ans or not ana:
        return None  # 质量门：题干/答案/解析三者齐全
    opts = it.get("options") or []
    return {
        "id": f"cxy-{it['id']}",
        "年份": year,
        "科目": subj if group == "真题" else group,
        "kp": kp,
        "难度": None,  # 源数据无难度字段，诚实留空
        "type": it.get("question_type"),
        "statement_md": stem,
        "options": opts if len(opts) == 4 else None,
        "answer": ans,
        "analysis": ana,
        "source_raw": src,
        "is_core": bool(it.get("is_core")),
    }


def main():
    items = [norm(json.loads(l)) for l in open(RAW, encoding="utf-8")]
    items = [x for x in items if x]
    buckets = defaultdict(list)
    for x in items:
        buckets[(x["科目"] if x["科目"] in ("高等数学", "线性代数", "概率统计") else "真题",
                 x["type"])].append(x)

    picked, leaf_count = [], defaultdict(int)
    for key, quota in QUOTA.items():
        pool = sorted(buckets.get(key, []), key=lambda x: (x["年份"] is None, -(x["年份"] or 0)))
        cnt = 0
        for x in pool:
            if cnt >= quota:
                break
            leaf = (x["kp"] or "真题卷")[:40]
            if leaf_count[leaf] >= 3:   # kp 多样性：同一叶节点最多 3 题
                continue
            leaf_count[leaf] += 1
            picked.append(x)
            cnt += 1

    with open(OUT, "w", encoding="utf-8") as f:
        for x in picked:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")

    # 分布报告
    from collections import Counter
    print("总量:", len(picked))
    print("科目:", dict(Counter(x["科目"] for x in picked)))
    print("题型:", dict(Counter(x["type"] for x in picked)))
    print("有年份(真题):", sum(1 for x in picked if x["年份"]))
    kps = Counter(x["kp"].split(" / ")[0] if x["kp"] else "真题卷" for x in picked)
    print("一级kp:", dict(kps))


if __name__ == "__main__":
    main()
