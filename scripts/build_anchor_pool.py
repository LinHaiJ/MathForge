# -*- coding: utf-8 -*-
"""模块 H（真题锚池扩容）：从 cxy_raw 全量真题构建 LLM 出题的风格锚池。

只读三个数据源，产出 cache/anchor_pool.jsonl（gitignored——S0-S6 惯例：产出落 cache/）：
  - cache/cxy_raw.jsonl      1616 道带题干/答案/解析/来源年份的全量真题（cxyonly 的 12 倍）
  - cache/cxy_categories.json category_id → 类目路径（如 "高等数学/极限/函数"）
  - packs/{calculus,linear,probability}/kp_graph.json  kp 名称/typical_forms/aliases（锚点匹配词表）

清洗定案（本 docstring 即规格）：
  1. 题型过滤：question_type ∈ {single_choice, subjective} 保留，type 词汇原样映射
     （与 题集/cxyonly.jsonl 对齐，generate.pick_few_shot 的 want_type 直接可判）；
     multiple_choice 等其余题型丢弃。
  2. 空值丢弃：题干（stem）为空丢弃；答案取 answer.reference_answer_md，为空回退
     correct_answer（单选题的参考答案字段为空、只存字母列，实测 388/388），仍为空丢弃。
  3. 去重：按 content_hash 去重（保留文件序首个；content_hash 为空的行跳过去重，
     防两道无 hash 的不同题被并掉——抗审 minor-4）；输出按 (content_hash, id) 排序，
     固定序列化 → 同名重跑产出逐字节一致。
  4. 字段裁剪为锚条目 schema（与 cxyonly 对齐，pick_few_shot 直接消费）：
     id/type/statement_md/options/answer/analysis/kp/subject/科目/年份/source_raw/is_core/
     content_hash。id 保持 "cxy-{raw_id}" 形式（cxyonly 的 128 个 id 全部 ⊆ cxy_raw，
     同一身份空间）；answer 一律字符串；options 为空列表时置 null（与 cxyonly 的主观题一致）。
  5. 原子落盘（抗审 major-1）：先写同名 .tmp 再 os.replace 到目标——构建中断/磁盘满
     不留残缺池；读取侧 generate._load_jsonl 另有逐行容错双保险。

kp 标注定案（可多值取最具体）：
  - 匹配词表 = 三包 kp_graph 的 name + typical_forms + aliases（v2/router.build_index 的
    文本摊平思路；当前包数据无 aliases 键，读取时防御性取空列表）；pattern 长度 ≥2
    （与 router 自由文本切词下限一致）；对每题在 stem + "\\n" + answer_explanation
    文本里做包含匹配，命中任一 pattern 即该 kp 命中。
  - 命中多个 kp 时按「最长命中 pattern → kp 名长度 → kp id」排序取最具体，kp 字段记录
    top3 命中 kp 的 name 以 " / " 连接（首位 = 最具体）。多值记录的理由：pick_few_shot 的
    kp 命中判据是「输入 kp 切词后任一 token 是否为锚条目 kp 字段的子串」，输入侧是
    kp_graph 中文名（kp_context["kp"]），把每个真实命中题文的 kp 名都留进字段可扩大
    命中面；且入列名字都真实出现在题文里，不引入虚假关联。
  - 未命中 kp 但类目可映射科目时记 subject（calculus/linear/probability）留作降级匹配；
    类目映射取 cxy_categories.json 类目路径首段：高等数学→calculus、线性代数→linear、
    概率统计→probability；类目 id 在类目表缺失时回退记录内 category_full_path；
    "历年真题/…" 类目（仅年份卷种层级）不映射科目，subject 置 null。

脚本本身零网络零 LLM。统计打印：总数/题型过滤/空值丢弃/去重剔除/有效数/
有解析数/有 kp 标注数/分科目分布。
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
PACKS = ("calculus", "linear", "probability")

# 类目路径首段 → 科目（subject 机器码 / 科目 中文名，与三包 pack_id 对齐）
_SUBJECT_BY_PREFIX = {"高等数学": "calculus", "线性代数": "linear", "概率统计": "probability"}
_MIN_PATTERN_LEN = 2  # 与 v2/router 自由文本切词下限一致，滤掉单字符噪音命中
_KP_TOP_N = 3         # kp 字段最多记录的命中 kp 名个数（首位最具体）


def load_kp_vocab(packs_dir: Path) -> list[dict]:
    """摊平三包 kp_graph 为锚点匹配词表（router.build_index 同思路）。

    返回按 kp_id 排序的 [{kp_id, name, patterns}]，patterns = name + typical_forms
    + aliases（当前包数据无 aliases 键，防御性取空）去重保序。
    """
    vocab = []
    for pid in PACKS:
        path = packs_dir / pid / "kp_graph.json"
        graph = json.loads(path.read_text(encoding="utf-8"))
        for k in graph:
            forms = [str(x) for x in (k.get("typical_forms", []) or [])]
            aliases = [str(x) for x in (k.get("aliases", []) or [])]
            patterns = []
            for p in [str(k.get("name", "")), *forms, *aliases]:
                if p and p not in patterns:
                    patterns.append(p)
            vocab.append({"kp_id": str(k["id"]), "name": str(k.get("name", "")), "patterns": patterns})
    vocab.sort(key=lambda e: e["kp_id"])  # 固定顺序 → 匹配与 tie-break 确定性
    return vocab


def match_kps(text: str, vocab: list[dict]) -> list[str]:
    """在题文里做包含匹配，返回最具体的 kp 名列表（首位最具体，至多 _KP_TOP_N 个）。

    最具体排序键：该 kp 最长命中 pattern 长度降序 → kp 名长度降序 → kp_id 升序
    （字典序，保证字节级确定）。
    """
    hits = []
    for e in vocab:
        matched = [p for p in e["patterns"] if len(p) >= _MIN_PATTERN_LEN and p in text]
        if matched:
            hits.append((max(len(p) for p in matched), len(e["name"]), e["kp_id"], e["name"]))
    hits.sort(key=lambda t: (-t[0], -t[1], t[2]))
    names, seen = [], set()
    for _, _, _, name in hits:
        if name and name not in seen:
            seen.add(name)
            names.append(name)
        if len(names) >= _KP_TOP_N:
            break
    return names


def map_subject(category_id, cats: dict, record: dict) -> str | None:
    """类目 → 科目机器码；不可映射（含 "历年真题/…" 层级）返回 None。"""
    path = cats.get(str(category_id)) or ""
    if not path:
        path = (record.get("category_full_path") or "").replace(" / ", "/")
    prefix = (path or "").split("/")[0].strip()
    return _SUBJECT_BY_PREFIX.get(prefix)


def build(raw_path: Path, cats_path: Path, packs_dir: Path, out_path: Path) -> dict:
    """清洗 + kp 标注 + 写出锚池；返回统计 dict（打印交给 main）。"""
    cats = json.loads(cats_path.read_text(encoding="utf-8"))
    vocab = load_kp_vocab(packs_dir)

    raw_total = 0
    dropped_type = 0
    dropped_empty = 0
    dedup_removed = 0
    seen_hash = set()
    entries = []
    for line in raw_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw_total += 1
        r = json.loads(line)
        if r.get("question_type") not in ("single_choice", "subjective"):
            dropped_type += 1
            continue
        stem = (r.get("stem") or "").strip()
        answer = ((r.get("answer") or {}).get("reference_answer_md") or "").strip() \
            or (r.get("correct_answer") or "").strip()
        if not stem or not answer:
            dropped_empty += 1
            continue
        chash = r.get("content_hash") or ""
        if chash:  # 空 hash 跳过去重（minor-4）：无 hash 的不同题不得被并掉
            if chash in seen_hash:
                dedup_removed += 1
                continue
            seen_hash.add(chash)

        analysis = (r.get("answer_explanation") or "").strip()
        text = stem + "\n" + analysis
        kp_names = match_kps(text, vocab)
        subject = map_subject(r.get("category_id"), cats, r)
        source = (r.get("source") or "").strip()
        year_m = re.match(r"(\d{4})", source)
        options = r.get("options") or None
        entries.append({
            "id": f"cxy-{r.get('id')}",
            "年份": int(year_m.group(1)) if year_m else None,
            "科目": next((cn for cn, code in _SUBJECT_BY_PREFIX.items() if code == subject),
                         None),
            "kp": " / ".join(kp_names) if kp_names else None,
            "难度": None,
            "type": r["question_type"],
            "statement_md": stem,
            "options": options,
            "answer": answer,
            "analysis": analysis,
            "subject": subject,
            "source_raw": source,
            "is_core": bool(r.get("is_core")),
            "content_hash": chash,
        })

    entries.sort(key=lambda e: (e["content_hash"], e["id"]))  # 确定性排序（与输入行序无关）
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_name(out_path.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8", newline="\n") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    os.replace(str(tmp_path), str(out_path))  # 原子落盘（major-1）：中断/磁盘满不留残缺池

    subject_dist = {"calculus": 0, "linear": 0, "probability": 0}
    none_subject = 0
    for e in entries:
        if e["subject"] in subject_dist:
            subject_dist[e["subject"]] += 1
        else:
            none_subject += 1
    return {
        "raw_total": raw_total,
        "dropped_type": dropped_type,
        "dropped_empty": dropped_empty,
        "dedup_removed": dedup_removed,
        "kept": len(entries),
        "with_analysis": sum(1 for e in entries if e["analysis"]),
        "with_kp": sum(1 for e in entries if e["kp"]),
        "subject_dist": subject_dist,
        "subject_none": none_subject,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="构建 cache/anchor_pool.jsonl（模块 H 锚池扩容）")
    ap.add_argument("--raw", type=Path, default=_ROOT / "cache" / "cxy_raw.jsonl")
    ap.add_argument("--cats", type=Path, default=_ROOT / "cache" / "cxy_categories.json")
    ap.add_argument("--packs-dir", type=Path, default=_ROOT / "packs")
    ap.add_argument("--out", type=Path, default=_ROOT / "cache" / "anchor_pool.jsonl")
    args = ap.parse_args()

    st = build(args.raw, args.cats, args.packs_dir, args.out)
    print(f"锚池构建完成 → {args.out}")
    print(f"  原始行数: {st['raw_total']}")
    print(f"  题型过滤丢弃: {st['dropped_type']}（非 single_choice/subjective）")
    print(f"  空题干/空答案丢弃: {st['dropped_empty']}")
    print(f"  content_hash 去重剔除: {st['dedup_removed']}")
    print(f"  有效锚条目: {st['kept']}")
    print(f"  有解析: {st['with_analysis']}  有 kp 标注: {st['with_kp']}")
    print(f"  分科目: calculus={st['subject_dist']['calculus']}, "
          f"linear={st['subject_dist']['linear']}, "
          f"probability={st['subject_dist']['probability']}, "
          f"未映射={st['subject_none']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
