"""T3: cxyonly 题集抓取脚本（本地私有使用，D015 版权已解除；产物不入 git）。

策略：服务端无过滤参数 → 全范围分页撒点抓取 → 本地按科目/题型分层筛选。
原始落 cache/cxy_raw.jsonl（gitignored），规范化落 题集/cxyonly.jsonl（gitignored）。
"""

import json
import re
import time
import urllib.request

BASE = "https://cxyonly.fans/api/questions"
OUT_RAW = "cache/cxy_raw.jsonl"


def fetch_page(page: int, per_page: int = 100, retry: int = 2) -> dict:
    url = f"{BASE}?page={page}&per_page={per_page}"
    for i in range(retry + 1):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.load(r)["data"]
        except Exception:
            if i == retry:
                raise
            time.sleep(2 * (i + 1))


def main():
    # 1) 全量撒点：per_page=100 共 63 页，取 17 页覆盖全科目区段
    pages = [1, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48, 52, 56, 60, 63]
    seen, raw = set(), []
    for pg in pages:
        d = fetch_page(pg)
        for it in d["items"]:
            if it["id"] in seen:
                continue
            seen.add(it["id"])
            raw.append(it)
        print(f"page {pg}: +{len(d['items'])} (累计 {len(raw)})")
        time.sleep(0.5)

    with open(OUT_RAW, "w", encoding="utf-8") as f:
        for it in raw:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    print(f"raw saved: {len(raw)} -> {OUT_RAW}")

    # 2) 科目分布概览
    from collections import Counter
    subj = Counter((it.get("category_full_path") or "?").split(" / ")[0] for it in raw)
    print("科目分布:", dict(subj))
    qt = Counter(it.get("question_type") for it in raw)
    print("题型分布:", dict(qt))


if __name__ == "__main__":
    main()
