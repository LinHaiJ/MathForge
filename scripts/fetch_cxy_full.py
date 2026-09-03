"""D025: cxyonly 全量补抓（63 页 × 100，本地私有 D015；产物不入 git）。

原料扩展：概率域 52 条 → 全量补齐；蒸馏语料从 1616（17 页撒点）扩到全站。
原始落 cache/cxy_full.jsonl（gitignored）；规范化留待蒸馏管线。
API 无需鉴权（既有 fetch_cxy.py 已实证），服务端无过滤参数 → 全范围分页抓全。
"""

import json
import time
import urllib.request
from collections import Counter

BASE = "https://cxyonly.fans/api/questions"
OUT = "cache/cxy_full.jsonl"
PER_PAGE = 100
TOTAL_PAGES = 63


def fetch_page(page: int, retry: int = 3) -> dict:
    url = f"{BASE}?page={page}&per_page={PER_PAGE}"
    for i in range(retry + 1):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.load(r)["data"]
        except Exception as e:
            if i == retry:
                raise
            time.sleep(2 * (i + 1))
    return {}


def main():
    seen, raw = set(), []
    for pg in range(1, TOTAL_PAGES + 1):
        try:
            d = fetch_page(pg)
        except Exception as e:
            print(f"page {pg}: FAILED {e}")
            continue
        items = d.get("items") or []
        for it in items:
            if it["id"] in seen:
                continue
            seen.add(it["id"])
            raw.append(it)
        print(f"page {pg}: +{len(items)} (累计 {len(raw)})")
        time.sleep(0.3)

    with open(OUT, "w", encoding="utf-8") as f:
        for it in raw:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")

    subj = Counter((it.get("category_full_path") or "?").split(" / ")[0] for it in raw)
    qt = Counter(it.get("question_type") for it in raw)
    print(f"saved: {len(raw)} -> {OUT}")
    print("subject:", dict(subj))
    print("qtype:", dict(qt))


if __name__ == "__main__":
    main()
