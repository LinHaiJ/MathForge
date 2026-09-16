# 真题库统计（AI 变式引擎任务书 P0-a，2026-09-15）
#
# 数据源：cache/cxy_master.jsonl（蒸馏 §1 产出，主源）；降级 cache/cxy_raw.jsonl。
# 产出：cache/bank_stats.json
#   total / source_file / empty_path_rate
#   by_domain  {高数|线代|概率|unknown: n}
#   by_category [{path, domain, count, years{yyyy:n}, tracks{}, types{}, first/last_year}]
#   sources_span {min_year, max_year}
# 口径：
#   - category_full_path = 站点三级分类（学科 / 章 / 考点）；空值计入 unknown 桶并披露空值率
#   - source 形如「1988数三」「2023数一」→ year=前 4 位数字，track=剩余中文
#   - subject_domain 优先取记录自带字段（蒸馏 §1.6 写入），缺失时从分类路径首段推断

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter, defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
CACHE = os.path.join(_ROOT, "cache")

sys.stdout.reconfigure(encoding="utf-8")

_MASTER = os.path.join(CACHE, "cxy_master.jsonl")
_RAW = os.path.join(CACHE, "cxy_raw.jsonl")

_DOMAIN_KEYS = ("高数", "线代", "概率")
_SRC_RE = re.compile(r"(\d{4})\s*(数[一二三])?")


def pick_source() -> tuple[str, str]:
    if os.path.exists(_MASTER):
        return _MASTER, "cxy_master.jsonl"
    if os.path.exists(_RAW):
        return _RAW, "cxy_raw.jsonl"
    raise FileNotFoundError("cxy_master.jsonl 与 cxy_raw.jsonl 均不存在")


def infer_domain(rec: dict, path: str) -> str:
    for key in ("subject_domain",):
        v = rec.get(key)
        if v in _DOMAIN_KEYS:
            return v
    if path:
        head = path.split("/")[0]
        for d in _DOMAIN_KEYS:
            if d in head:
                return d
        if "高等数学" in head:
            return "高数"
        if "线性代数" in head:
            return "线代"
        if "概率" in head or "数理统计" in head:
            return "概率"
    return "unknown"


def main() -> int:
    src_path, src_name = pick_source()
    total = 0
    empty_path = 0
    bad_source = 0
    by_domain: Counter = Counter()
    cats: dict[str, dict] = defaultdict(lambda: {
        "count": 0, "years": Counter(), "tracks": Counter(), "types": Counter(),
    })
    min_year, max_year = None, None

    with open(src_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            total += 1
            path = (rec.get("category_full_path") or "").strip()
            if not path:
                path = "unknown"
                empty_path += 1
            domain = infer_domain(rec, path if path != "unknown" else "")
            by_domain[domain] += 1

            c = cats[path]
            c["count"] += 1
            c.setdefault("domain", domain)
            qtype = rec.get("question_type") or "unknown"
            c["types"][qtype] += 1

            src = str(rec.get("source") or "")
            m = _SRC_RE.search(src)
            year = int(m.group(1)) if m else None
            # 真题年份合理范围：1987（考研数学统考起始）至当前年份；范围外多为「1000题」类字符串误匹配
            if year is not None and not (1987 <= year <= 2026):
                year = None
            if year is not None:
                track = m.group(2) or ""
                c["years"][year] += 1
                if track:
                    c["tracks"][track] += 1
                min_year = year if min_year is None else min(min_year, year)
                max_year = year if max_year is None else max(max_year, year)
            else:
                bad_source += 1

    by_category = []
    for path, c in cats.items():
        years = {str(k): v for k, v in sorted(c["years"].items())}
        ylist = [int(y) for y in years]
        by_category.append({
            "path": path,
            "domain": c.get("domain", "unknown"),
            "count": c["count"],
            "years": years,
            "tracks": dict(c["tracks"].most_common()),
            "types": dict(c["types"].most_common()),
            "first_year": min(ylist) if ylist else None,
            "last_year": max(ylist) if ylist else None,
        })
    by_category.sort(key=lambda r: -r["count"])

    out = {
        "total": total,
        "source_file": src_name,
        "empty_path_rate": round(empty_path / total, 4) if total else None,
        "bad_source_count": bad_source,
        "by_domain": dict(by_domain.most_common()),
        "by_category": by_category,
        "sources_span": {"min_year": min_year, "max_year": max_year},
    }
    dst = os.path.join(CACHE, "bank_stats.json")
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print(f"source={src_name} total={total} empty_path_rate={out['empty_path_rate']}")
    print(f"by_domain={out['by_domain']}")
    print(f"categories={len(by_category)} span={min_year}-{max_year}")
    print("top10:")
    for r in by_category[:10]:
        print(f"  {r['count']:>5}  {r['path']}")
    print(f"-> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
