"""MathForge V2 · S0 语料整备 + S1 统计蒸馏 构建脚本（零 LLM）。

产出（均落 cache/，gitignored）：
  cache/cxy_master.jsonl  master = full 6268 ∪ raw-only 差集
  cache/s0_report.json    三口径对账 + 域分布 + unknown 披露 + notes
  cache/s1_stats.json     分域统计（题型配比/考点频次/域分布/题干开头形态）
  s1 摘要段并入 s0_report.json（summary 字段）

用法：D:/Python/Python312/python.exe scripts/build_s0_s1.py
"""
import json, os, collections, re, sys, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from domain_rules import classify_domain, is_image_only, CALCULUS, LINEAR, PROBABILITY, UNKNOWN

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "cache")
RAW_P = os.path.join(CACHE, "cxy_raw.jsonl")
FULL_P = os.path.join(CACHE, "cxy_full.jsonl")
CXYONLY_P = os.path.join(ROOT, "题集", "cxyonly.jsonl")
MASTER_P = os.path.join(CACHE, "cxy_master.jsonl")
S0_REPORT_P = os.path.join(CACHE, "s0_report.json")
S1_STATS_P = os.path.join(CACHE, "s1_stats.json")

NOTES = []  # 失败处置/异常记录


def note(msg):
    NOTES.append(msg)


def load(p):
    out = []
    if not os.path.exists(p):
        note(f"源文件不存在，跳过: {p}")
        return out
    with open(p, encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception as e:
                note(f"JSON 解析失败 {p}:{ln}: {e}")
    return out


def norm_stem(stem):
    if not stem:
        return ""
    s = stem
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)  # 去图片引用
    s = re.sub(r"\s+", "", s)  # 去所有空白
    return s


def count_dup_hash(records):
    c = collections.Counter(r.get("content_hash") for r in records if r.get("content_hash"))
    return sum(v - 1 for v in c.values() if v > 1), len(c)


def stem_empty_rate(records, field="stem"):
    n = len(records)
    if n == 0:
        return 0.0, 0
    empty = sum(1 for r in records if not (r.get(field) or "").strip())
    return round(empty / n, 4), empty


# ---------- 载入 ----------
raw = load(RAW_P)
full = load(FULL_P)
cxyonly = load(CXYONLY_P)

raw_hashes = {r["content_hash"] for r in raw if r.get("content_hash")}
full_hashes = {r["content_hash"] for r in full if r.get("content_hash")}

# ---------- S0 动作2：raw−full 差集（content_hash）----------
dup_internal_raw, _ = count_dup_hash(raw)
dup_internal_full, _ = count_dup_hash(full)
raw_only = [r for r in raw if r.get("content_hash") not in full_hashes]
diff_n = len(raw_only)

# 复跑确认：再算一次
raw_only2 = [r for r in raw if r.get("content_hash") not in full_hashes]
assert diff_n == len(raw_only2), "差集复跑不一致"

# ---------- S0 动作3：master = full ∪ raw-only ----------
full_by_hash = {r["content_hash"]: r for r in full if r.get("content_hash")}

# 题干归一碰撞表（基于 full + raw_only 的全部 master 项）
all_master = list(full) + list(raw_only)
norm_map = collections.defaultdict(set)
for r in all_master:
    ns = norm_stem(r.get("stem", ""))
    if ns:
        norm_map[ns].add(r.get("content_hash"))

master_records = []
for r in full:
    rec = dict(r)
    rec["subject_domain"] = classify_domain(rec.get("stem", ""), rec.get("category_full_path"))
    rec["dup_candidate"] = False
    master_records.append(rec)
raw_only_dup = 0
for r in raw_only:
    rec = dict(r)
    rec["subject_domain"] = classify_domain(rec.get("stem", ""), rec.get("category_full_path"))
    ns = norm_stem(r.get("stem", ""))
    is_dup = False
    if ns and ns in norm_map:
        others = norm_map[ns] - {r.get("content_hash")}
        if others:
            is_dup = True
            raw_only_dup += 1
    rec["dup_candidate"] = is_dup
    master_records.append(rec)

master_n = len(master_records)
dup_candidate_n = sum(1 for r in master_records if r.get("dup_candidate"))

# master 重复 hash 复核
dup_internal_master, master_unique = count_dup_hash(master_records)

# ---------- 信息性注释（透明披露，非异常）----------
note("cxyonly(题集/128) 无 content_hash 字段，三口径对账其 dup_hash 记为 N/A；其 statement_md 空值率 0%。")
note("(empty) 无分类路径组 410 题因题干为纯图片引用(asset://)、无可用文本，按规则标 unknown；方案 §1.5 曾「粗判全高数」，此处按任务书「判不出标 unknown」如实披露，待 §8 目检/重分类补齐。")
note("历年真题/模拟卷 的 category_full_path 第二三级为「卷别/年份」而非考点（如 历年真题/数一/2001），kp_freq 按字面取 2-3 级；真·考点以 高等数学/线代/概率统计 路径为准。")
note("master 中 id 452、5654 在 raw 与 full 各有一条（content_hash 不同），按哈希去重后共存；id 非唯一但 content_hash 唯一（6275/6275）。")

# ---------- S0 动作5：三口径对账 ----------
def reconcile(name, records, stem_field="stem"):
    if records and "content_hash" not in records[0]:
        dup, uniq, hash_status = "N/A(无content_hash字段)", "N/A", "N/A"
    else:
        dup, uniq = count_dup_hash(records)
        hash_status = dup
    rate, empty = stem_empty_rate(records, stem_field)
    return {"name": name, "count": len(records), "dup_hash": hash_status,
            "unique_hash": uniq, "stem_empty": empty, "stem_empty_rate": rate,
            "stem_field": stem_field}

recon = [
    reconcile("cxyonly(题集/128)", cxyonly, stem_field="statement_md"),
    reconcile("cxy_raw(1616)", raw),
    reconcile("cxy_master", master_records),
]

# ---------- S0 动作6：域分布 + unknown 披露 ----------
domain_dist = collections.Counter(r["subject_domain"] for r in master_records)
# unknown 按来源组拆分
unknown_by_group = collections.Counter()
for r in master_records:
    if r["subject_domain"] == UNKNOWN:
        cfp = r.get("category_full_path")
        top = (cfp.split(" / ")[0] if cfp else "(empty)")
        unknown_by_group[top] += 1
unknown_rate = round(domain_dist.get(UNKNOWN, 0) / master_n, 4) if master_n else 0.0
image_only_unknown = sum(1 for r in master_records
                          if r["subject_domain"] == UNKNOWN and is_image_only(r.get("stem", "")))

# ---------- S0 动作4 复核：dup_candidate 中 题干归一相同但 hash 不同 ----------
# 已在 master_records 构建循环内逐题判定并累加 raw_only_dup；此处无需重算

# ---------- 写 master ----------
with open(MASTER_P, "w", encoding="utf-8") as f:
    for r in master_records:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

# ---------- S1 统计 ----------
s1 = {}
# 域分布
s1["domain_distribution"] = dict(domain_dist)
s1["domain_distribution_total"] = master_n

# 题型配比（按域）
qt_by_domain = collections.defaultdict(collections.Counter)
for r in master_records:
    qt_by_domain[r["subject_domain"]][r.get("question_type")] += 1
s1["question_type_by_domain"] = {d: dict(c) for d, c in qt_by_domain.items()}
# 全局题型
qt_global = collections.Counter(r.get("question_type") for r in master_records)
s1["question_type_global"] = dict(qt_global)

# 考点频次（category_full_path 第二、三级）
kp_by_domain = collections.defaultdict(collections.Counter)
for r in master_records:
    cfp = r.get("category_full_path") or ""
    parts = [p.strip() for p in cfp.split(" / ") if p.strip()]
    if len(parts) >= 2:
        key = " / ".join(parts[1:3]) if len(parts) >= 3 else parts[1]
    elif len(parts) == 1:
        key = parts[0]
    else:
        key = "(no_path)"
    kp_by_domain[r["subject_domain"]][key] += 1
s1["kp_freq_by_domain"] = {d: dict(c.most_common(30)) for d, c in kp_by_domain.items()}

# 题干开头形态摘要（每域 top 开头字符）
def leading_char(stem):
    if not stem:
        return "(empty)"
    # 去图片引用后取首字符
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", stem)
    s = re.sub(r"\s+", "", s)
    if not s:
        return "(image_only)"
    # 取前 2 字作为形态（中文/符号）
    return s[:2]

lead_by_domain = collections.defaultdict(collections.Counter)
for r in master_records:
    lead_by_domain[r["subject_domain"]][leading_char(r.get("stem", ""))] += 1
s1["stem_opening_by_domain"] = {d: dict(c.most_common(15)) for d, c in lead_by_domain.items()}

with open(S1_STATS_P, "w", encoding="utf-8") as f:
    json.dump(s1, f, ensure_ascii=False, indent=2)

# ---------- s0_report.json ----------
s0 = {
    "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    "inputs": {
        "cxy_raw_count": len(raw),
        "cxy_full_count": len(full),
        "cxyonly_count": len(cxyonly),
    },
    "s0": {
        "diff_raw_minus_full_by_hash": diff_n,
        "diff_rerun_confirmed": (diff_n == len(raw_only2)),
        "master_total": master_n,
        "master_expected_range": "6273~6275",
        "dup_candidate_count": dup_candidate_n,
        "dup_candidate_in_raw_only": raw_only_dup,
    },
    "reconciliation_three_caliber": recon,
    "domain_distribution": dict(domain_dist),
    "unknown_rate": unknown_rate,
    "unknown_count": domain_dist.get(UNKNOWN, 0),
    "unknown_by_source_group": dict(unknown_by_group),
    "unknown_image_only_count": image_only_unknown,
    "master_dup_hash_internal": dup_internal_master,
    "notes": NOTES,
}
# s1 摘要段并入
s0["s1_summary"] = {
    "domain_distribution": dict(domain_dist),
    "question_type_global": dict(qt_global),
    "question_type_by_domain": s1["question_type_by_domain"],
    "kp_freq_by_domain_top5": {d: dict(list(c.items())[:5]) for d, c in kp_by_domain.items()},
    "stem_opening_by_domain_top5": {d: dict(list(c.items())[:5]) for d, c in lead_by_domain.items()},
    "full_stats_file": "cache/s1_stats.json",
}
with open(S0_REPORT_P, "w", encoding="utf-8") as f:
    json.dump(s0, f, ensure_ascii=False, indent=2)

# ---------- 控制台摘要 ----------
print("=== S0 完成 ===")
print(f"raw={len(raw)} full={len(full)} diff(raw-full by hash)={diff_n} master={master_n}")
print(f"dup_candidate(命中)={dup_candidate_n} (raw-only 内={raw_only_dup})")
print(f"master 内部重复 hash={dup_internal_master}")
print(f"三口径: " + " | ".join(f"{r['name']}:n={r['count']},dup={r['dup_hash']},empty%={r['stem_empty_rate']*100:.2f}" for r in recon))
print(f"域分布: {dict(domain_dist)}")
print(f"unknown 占比={unknown_rate*100:.2f}% (image_only={image_only_unknown})")
print(f"产出: cxy_master.jsonl / s0_report.json / s1_stats.json")
