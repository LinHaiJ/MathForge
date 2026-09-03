"""S2 全量批跑吞吐探针（只读，不改业务代码）。

目的：实测「6268 题结构标签批跑」的真实墙钟时间与 token 成本，
替换蒸馏方案 §3 中的估计值。结果写入 cache/s2_probe.json。

探针不做真实标注质量评估，只测吞吐。
"""
from __future__ import annotations

import json
import random
import statistics
import time
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import llm  # noqa: E402
CORPUS = HERE / "cache" / "cxy_full.jsonl"
OUT = HERE / "cache" / "s2_probe.json"

NS = "s2-probe:v1"  # 独立命名空间，确保缓存未命中

SYS = """你是考研数学题库结构分析器。对给定题目，只输出 JSON，不要任何解释文字。
字段：
- kp: 所属知识点路径（如"高等数学/一元微分/导数计算/参数方程求导"）
- struct: 题目结构标签（如"隐函数+变限积分求二阶偏导"），不超过 20 字
- slots: 参数槽位列表（可替换成变量的数学对象），数组，最多 5 项
- steps: 解题步数估计（整数 1-6）
- qform: 问法，取值之一：求值/证明/讨论/判断/计算/其他
只输出 JSON。"""

TPL = "题目：\n{stem}\n\n答案：{ans}"


def load_sample(n: int = 18, seed: int = 7):
    rows = [json.loads(l) for l in CORPUS.open(encoding="utf-8") if l.strip()]
    kp = [r for r in rows
          if (r.get("category_full_path") or "").strip()
          and not (r.get("category_full_path") or "").startswith("历年真题")]
    random.Random(seed).shuffle(kp)
    return kp[:n]


def one(r: dict) -> dict:
    stem = (r.get("stem") or "")[:700]
    ans = (r.get("correct_answer") or r.get("answer") or "")[:120]
    t0 = time.perf_counter()
    txt = llm.chat(
        [{"role": "system", "content": SYS},
         {"role": "user", "content": TPL.format(stem=stem, ans=ans)}],
        temperature=0.0, response_json=True, namespace=NS,
    )
    dt = time.perf_counter() - t0
    return {"sec": round(dt, 2), "chars": len(txt),
            "ok": txt.strip().startswith("{")}


def main() -> None:
    sample = load_sample(18)
    print(f"样本 {len(sample)} 题，模型 {llm.DEFAULT_MODEL}")

    # 阶段 1：串行 6 题
    seq = [one(r) for r in sample[:6]]
    seq_t = [x["sec"] for x in seq]
    print(f"串行 n=6  中位 {statistics.median(seq_t):.2f}s  均值 {statistics.mean(seq_t):.2f}s  "
          f"max {max(seq_t):.2f}s  JSON合规 {sum(x['ok'] for x in seq)}/6")

    # 阶段 2：并发 12 题（并发度 6）
    rest = sample[6:]
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=6) as ex:
        par = list(ex.map(one, rest))
    wall = time.perf_counter() - t0
    par_t = [x["sec"] for x in par]
    print(f"并发6 n=12 墙钟 {wall:.2f}s  单请求中位 {statistics.median(par_t):.2f}s  "
          f"max {max(par_t):.2f}s  JSON合规 {sum(x['ok'] for x in par)}/12")

    allt = seq_t + par_t
    med_seq = statistics.median(seq_t)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "model": llm.DEFAULT_MODEL,
        "seq_median_s": round(med_seq, 2),
        "seq_mean_s": round(statistics.mean(seq_t), 2),
        "par6_wall_s": round(wall, 2),
        "par6_n": len(par),
        "json_ok_rate": round(sum(x["ok"] for x in seq + par) / len(allt), 3),
        "raw": seq + par,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    N = 6268
    print("\n--- 外推到全量 %d 题 ---" % N)
    print(f"  串行 (1 并发):      {N * med_seq / 3600:5.1f} 小时")
    for c in (4, 6, 8, 16):
        eff = 0.75 if c > 1 else 1.0
        print(f"  并发 {c:2d} (效率 {eff:.2f}):   {N * med_seq / c / eff / 3600:5.2f} 小时")
    print(f"\n明细: {OUT}")


if __name__ == "__main__":
    main()
