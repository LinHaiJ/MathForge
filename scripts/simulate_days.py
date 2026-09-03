#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""MathForge V2 模拟卷模拟器（设计共识 §4「数据方案：模拟卷（唯一数据源）」）。

用户当前不备考 → 无真实刷题数据 → 模拟数据是记忆层/复习/蒸馏的唯一数据源
（DECISIONS D024/D025）。本脚本按日历日推进「一天一套模拟卷 + 当晚复盘」，
把作答表现写成 attempt_events 事件流（全部 sim=1），产物 = 事件流 + 每日本报。

**所有输出均带「模拟」标识（§4.3 诚实口径）。模拟的是作答表现数据，不是题干。**

事件写入耦合方式（packs/CONTRACT.md「事件流/投影契约」）：
  优先 `import mem2` 并调用 `mem2.append_event(conn, ...)`（T2 交付物）；
  mem2 缺失 / 接口不符 / 首次写入抛错时，自动降级为本模块内建的最小
  append 实现（同表结构 attempt_events，append-only），并在报告里注明后端。
  → 本脚本对 mem2 零硬依赖，集成时无需改动即可自动切到 mem2。

用法（设计共识写法 `simulate` 子命令可省略）：
  python scripts/simulate_days.py --days 3 --seed 1
  python scripts/simulate_days.py simulate --days 7 --score 100 --report
  python scripts/simulate_days.py --days 5 --weak-kps calc.rolle,la.eigen --db /tmp/s.db

红线：只写独立 sim 库（默认 mathforge_sim.db），拒绝写 mathforge.db。
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
from datetime import date, datetime, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PACKS_DIR = os.path.join(ROOT, "packs")
EXAMS_DIR = os.path.join(ROOT, "exams")
SIM_LABEL = "模拟"
REAL_DB_NAME = "mathforge.db"  # 红线：绝不写入

# 四类错因（v1 attribute.py 同口径）
ATTRIBUTIONS = ["概念混淆", "计算失误", "方法选错", "审题错误"]
DEFAULT_ATTR_WEIGHTS = [0.4, 0.3, 0.2, 0.1]

# 难度对正确率的乘子 / 抽题时各题型的难度分布
DIFFICULTY_MULT = {"basic": 1.15, "mid": 1.0, "advanced": 0.80}
DIFFICULTY_MIX = {
    "choice": [("basic", 0.40), ("mid", 0.45), ("advanced", 0.15)],
    "fill": [("basic", 0.33), ("mid", 0.50), ("advanced", 0.17)],
    "solution": [("basic", 0.17), ("mid", 0.50), ("advanced", 0.33)],
}
DIFFICULTY_ORDER = ["basic", "mid", "advanced"]
FREQ_WEIGHT = {"high": 3.0, "mid": 2.0, "low": 1.0}

# 解答题分值结构（真题口径：6 题共 70 分）
SOLUTION_POINTS = [10, 12, 12, 12, 12, 12]
PARTIAL_SHARE = 0.55  # 解答题未做对时，其中 55% 拿部分分（≈半分）
DEFAULT_WEAK_KPS = ["calc.rolle", "calc.lagrange", "la.eigen", "prob.total_bayes"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS attempt_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    day TEXT NOT NULL,
    sim INTEGER NOT NULL DEFAULT 0,
    pack TEXT,
    kp TEXT,
    qtype TEXT,
    mode TEXT,
    result TEXT,
    attribution TEXT,
    user_override INTEGER DEFAULT 0,
    policy_snapshot TEXT,
    meta TEXT
);
CREATE INDEX IF NOT EXISTS idx_ae_day ON attempt_events(day);
CREATE INDEX IF NOT EXISTS idx_ae_kp ON attempt_events(sim, kp, ts);
"""


# --------------------------------------------------------------------------- #
# 数据加载（只读 json，不 import 业务层，也不动态加载 families）
# --------------------------------------------------------------------------- #
def load_exam(exam_id: str = "math1", exams_dir: str = EXAMS_DIR) -> dict:
    """考试组合档案 exams/<id>.json（包组合+权重+真题分值结构）。"""
    path = os.path.join(exams_dir, f"{exam_id}.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_kp_pool(exam: dict, packs_dir: str = PACKS_DIR) -> list[dict]:
    """按 exam.packs 读各包 kp_graph.json，摊平成候选题源池。

    只读 kp_graph.json（不走 pack_loader，避免动态执行 families/*.py 引入 sympy 依赖）。
    """
    pool: list[dict] = []
    for p in exam.get("packs", []):
        pid = p["id"]
        path = os.path.join(packs_dir, pid, "kp_graph.json")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            graph = json.load(f)
        for kp in graph:
            pool.append(
                {
                    "pack": pid,
                    "pack_weight": float(p.get("weight", 0.0)),
                    "kp": kp["id"],
                    "kp_name": kp.get("name", kp["id"]),
                    "section": kp.get("section"),
                    "qtypes": kp.get("qtypes") or ["fill", "solution"],
                    "difficulty_floor": kp.get("difficulty_floor", "basic"),
                    "exam_freq": kp.get("exam_freq", "mid"),
                }
            )
    if not pool:
        raise RuntimeError("kp 池为空：检查 packs/<id>/kp_graph.json")
    return pool


# --------------------------------------------------------------------------- #
# 纯函数：试卷生成
# --------------------------------------------------------------------------- #
def allocate_by_weight(total: int, weights: list[float]) -> list[int]:
    """按权重把 total 道题分配到各包（最大余额法，确定性）。"""
    if total <= 0:
        return [0] * len(weights)
    s = sum(weights) or 1.0
    raw = [total * w / s for w in weights]
    base = [int(x) for x in raw]
    rest = total - sum(base)
    order = sorted(range(len(weights)), key=lambda i: (-(raw[i] - base[i]), i))
    for i in order[:rest]:
        base[i] += 1
    return base


def _pick_weighted(rng: random.Random, items: list, weights: list[float]):
    total = sum(weights)
    r = rng.random() * total
    acc = 0.0
    for it, w in zip(items, weights):
        acc += w
        if r <= acc:
            return it
    return items[-1]


def _pick_difficulty(rng: random.Random, qtype: str, floor: str) -> str:
    mix = DIFFICULTY_MIX[qtype]
    d = _pick_weighted(rng, [m[0] for m in mix], [m[1] for m in mix])
    # 不得低于 kp 的 difficulty_floor（kp schema 契约）
    if DIFFICULTY_ORDER.index(d) < DIFFICULTY_ORDER.index(floor):
        d = floor
    return d


def paper_sections(exam: dict) -> list[tuple[str, int, list[int]]]:
    """(题型, 题数, 每题分值) —— 读 exam.paper_structure，解答题按真题分值拆分。"""
    st = exam["paper_structure"]
    out = []
    c = st["single_choice"]
    out.append(("choice", int(c["count"]), [int(c["points"])] * int(c["count"])))
    f = st["fill"]
    out.append(("fill", int(f["count"]), [int(f["points"])] * int(f["count"])))
    sol = st["solution"]
    n = int(sol["count"])
    total = int(sol.get("points_total", sum(SOLUTION_POINTS)))
    pts = SOLUTION_POINTS[:n] if n <= len(SOLUTION_POINTS) else SOLUTION_POINTS + [12] * (n - len(SOLUTION_POINTS))
    diff = total - sum(pts)
    if diff:  # 分值结构与档案不一致时，把差额落到最后一题
        pts = pts[:-1] + [pts[-1] + diff]
    out.append(("solution", n, pts))
    return out


def generate_paper(
    exam: dict,
    pool: list[dict],
    weak_kps: list[str],
    seed,
    weak_boost: float = 2.5,
) -> list[dict]:
    """生成一套模拟卷的结构化题目清单（纯函数：同 seed → 同试卷）。

    题干不生成（模拟对象是作答表现数据）；每题携带 kp/qtype/difficulty/points/卷内题号。
    抽题权重 = 包权重（math1: 0.56/0.22/0.22）× kp 考频 × 薄弱(目标) kp 加权。
    """
    rng = random.Random(f"paper|{seed}")
    weak = set(weak_kps)
    pack_ids = [p["id"] for p in exam.get("packs", [])]
    pack_weights = [float(p.get("weight", 0.0)) for p in exam.get("packs", [])]
    by_pack = {pid: [k for k in pool if k["pack"] == pid] for pid in pack_ids}

    paper: list[dict] = []
    no = 0
    for qtype, count, points in paper_sections(exam):
        alloc = allocate_by_weight(count, pack_weights)
        slots: list[str] = []
        for pid, n in zip(pack_ids, alloc):
            slots.extend([pid] * n)
        rng.shuffle(slots)
        for i, pid in enumerate(slots):
            cands = [k for k in by_pack.get(pid, []) if qtype == "choice" or qtype in k["qtypes"]]
            if not cands:
                cands = by_pack.get(pid) or pool
            w = [
                FREQ_WEIGHT.get(k["exam_freq"], 2.0) * (weak_boost if k["kp"] in weak else 1.0)
                for k in cands
            ]
            kp = _pick_weighted(rng, cands, w)
            no += 1
            paper.append(
                {
                    "no": no,
                    "qtype": qtype,
                    "points": points[i],
                    "pack": kp["pack"],
                    "kp": kp["kp"],
                    "kp_name": kp["kp_name"],
                    "difficulty": _pick_difficulty(rng, qtype, kp["difficulty_floor"]),
                    "weak": kp["kp"] in weak,
                }
            )
    return paper


# --------------------------------------------------------------------------- #
# 纯函数：作答模拟 / 判分 / 锚点校准
# --------------------------------------------------------------------------- #
def question_accuracy(q: dict, base_acc: float, weak_acc: float) -> float:
    p = (weak_acc if q["weak"] else base_acc) * DIFFICULTY_MULT[q["difficulty"]]
    return min(0.98, max(0.02, p))


def half(points: int) -> int:
    return round(points * 0.5)


def expected_score(paper: list[dict], base_acc: float, weak_acc: float) -> float:
    tot = 0.0
    for q in paper:
        p = question_accuracy(q, base_acc, weak_acc)
        tot += q["points"] * p
        if q["qtype"] == "solution":
            tot += half(q["points"]) * (1.0 - p) * PARTIAL_SHARE
    return tot


def fit_accuracy_scale(paper: list[dict], anchor: float, base_acc: float, weak_acc: float) -> float:
    """求正确率整体缩放系数，使期望总分 ≈ 得分锚（保持薄弱/非薄弱的相对差）。

    这样默认参数跑出来的分数天然贴锚（100±5），校准只需微调少数题 → 视察可信。
    """
    lo, hi = 0.02, 1.6
    for _ in range(60):
        mid = (lo + hi) / 2
        if expected_score(paper, base_acc * mid, weak_acc * mid) < anchor:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def simulate_answers(
    paper: list[dict], rng: random.Random, base_acc: float, weak_acc: float
) -> list[dict]:
    """按每题正确率抽样作答结果（解答题可得部分分）。"""
    results = []
    for q in paper:
        p = question_accuracy(q, base_acc, weak_acc)
        r = rng.random()
        if r < p:
            res, earned = "correct", q["points"]
        elif q["qtype"] == "solution" and r < p + (1 - p) * PARTIAL_SHARE:
            res, earned = "partial", half(q["points"])
        else:
            res, earned = "wrong", 0
        results.append(
            {"no": q["no"], "result": res, "earned": earned, "p": round(p, 4), "calibrated": False}
        )
    return results


def grade_paper(paper: list[dict], results: list[dict]) -> int:
    """总分（分值结构见 exams/math1.json：10×5 + 6×5 + 70 = 150）。"""
    return int(sum(r["earned"] for r in results))


def calibrate_to_anchor(
    paper: list[dict], results: list[dict], anchor: float, band: float = 5.0, max_steps: int = 8
) -> tuple[list[dict], list[dict]]:
    """总分偏离锚点超过 band 时，改判少数题使其落回锚带内。

    候选动作 = 每题可切换到的其它判定（解答题含 partial → 粒度更细，易命中锚带）。
    可信度优先：降分优先改薄弱/难题，升分优先改非薄弱/基础题。返回 (结果, 校准动作列表)。
    """
    results = [dict(r) for r in results]
    qmap = {q["no"]: q for q in paper}
    actions: list[dict] = []
    for _ in range(max_steps):
        total = grade_paper(paper, results)
        dev = abs(total - anchor)
        if dev <= band:
            break
        cands = []
        for r in results:
            q = qmap[r["no"]]
            opts = [("correct", q["points"]), ("wrong", 0)]
            if q["qtype"] == "solution":
                opts.append(("partial", half(q["points"])))
            for target, earned in opts:
                if target == r["result"]:
                    continue
                delta = earned - r["earned"]
                after = abs(total + delta - anchor)
                if after >= dev:
                    continue
                # 可信度：降分改薄弱/高难，升分改非薄弱/低难
                plaus = 0
                if delta < 0:
                    plaus = (2 if q["weak"] else 0) + DIFFICULTY_ORDER.index(q["difficulty"])
                else:
                    plaus = (0 if q["weak"] else 2) + (2 - DIFFICULTY_ORDER.index(q["difficulty"]))
                cands.append((after, -plaus, r["no"], target, delta, earned))
        if not cands:
            break
        cands.sort()
        _, _, no, target, delta, earned = cands[0]
        for r in results:
            if r["no"] == no:
                actions.append(
                    {"no": no, "from": r["result"], "to": target, "delta": delta,
                     "kp": qmap[no]["kp"]}
                )
                r["result"], r["earned"], r["calibrated"] = target, earned, True
                break
    return results, actions


def assign_attributions(
    paper: list[dict], results: list[dict], rng: random.Random, attr_weights: list[float]
) -> list[dict]:
    """给错题/半对题按四类归因比例抽一个错因（复盘产物：写入事件 attribution）。"""
    results = [dict(r) for r in results]
    qmap = {q["no"]: q for q in paper}
    for r in results:
        if r["result"] in ("wrong", "partial"):
            t = _pick_weighted(rng, ATTRIBUTIONS, attr_weights)
            # 薄弱 kp 的概念混淆置信度更高（规则兜底口径，非 LLM）
            conf = 0.60 + 0.30 * rng.random() + (0.05 if qmap[r["no"]]["weak"] else 0.0)
            r["attribution"] = {"type": t, "conf": round(min(0.98, conf), 3), "source": "sim-rule"}
        else:
            r["attribution"] = None
    return results


# --------------------------------------------------------------------------- #
# 事件构建（契约：attempt_events，sim=1）
# --------------------------------------------------------------------------- #
def day_ts(day: str, hour: int, minute: int = 0) -> int:
    d = datetime.strptime(day, "%Y-%m-%d")
    return int(datetime(d.year, d.month, d.day, hour, minute).timestamp())


def build_events(
    day: str,
    day_index: int,
    exam_id: str,
    paper: list[dict],
    results: list[dict],
    rng: random.Random,
    seed,
    unresolved_rate: float = 0.25,
    answer_hour: int = 21,
    review_hour: int = 22,
) -> list[dict]:
    """一天的事件流：每题一条 mode='sim' 作答事件（错题带 attribution）
    + 当晚复盘后仍不会的题一条 mode='selfassess' partial 事件。全部 sim=1。"""
    qmap = {q["no"]: q for q in paper}
    events: list[dict] = []
    base_ts = day_ts(day, answer_hour)
    for i, r in enumerate(results):
        q = qmap[r["no"]]
        events.append(
            {
                "ts": base_ts + i * 60,
                "day": day,
                "sim": 1,
                "pack": q["pack"],
                "kp": q["kp"],
                "qtype": q["qtype"],
                "mode": "sim",
                "result": r["result"],
                "attribution": r.get("attribution"),
                "user_override": 0,
                "policy_snapshot": None,
                "meta": {
                    "label": SIM_LABEL,
                    "simulated": True,
                    "exam": exam_id,
                    "paper_id": f"sim-{exam_id}-{day}",
                    "no": q["no"],
                    "section": q["qtype"],
                    "points": q["points"],
                    "earned": r["earned"],
                    "difficulty": q["difficulty"],
                    "weak_kp": q["weak"],
                    "p_correct": r["p"],
                    "calibrated": bool(r["calibrated"]),
                    "day_index": day_index,
                    "seed": str(seed),
                },
            }
        )
    # 当晚复盘：错题里仍未消化的部分（比例可配）
    review_ts = day_ts(day, review_hour, 30)
    k = 0
    for r in results:
        if r["result"] not in ("wrong", "partial"):
            continue
        if rng.random() >= unresolved_rate:
            continue
        q = qmap[r["no"]]
        events.append(
            {
                "ts": review_ts + k * 60,
                "day": day,
                "sim": 1,
                "pack": q["pack"],
                "kp": q["kp"],
                "qtype": q["qtype"],
                "mode": "selfassess",
                "result": "partial",
                "attribution": r.get("attribution"),
                "user_override": 0,
                "policy_snapshot": None,
                "meta": {
                    "label": SIM_LABEL,
                    "simulated": True,
                    "review_of": q["no"],
                    "unresolved": True,
                    "paper_id": f"sim-{exam_id}-{day}",
                    "day_index": day_index,
                },
            }
        )
        k += 1
    return events


# --------------------------------------------------------------------------- #
# 事件落库（优先 mem2，缺失则内建最小 append）
# --------------------------------------------------------------------------- #
def _try_import_mem2():
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    try:
        import mem2  # type: ignore
    except Exception:
        return None
    return mem2 if callable(getattr(mem2, "append_event", None)) else None


class EventSink:
    """attempt_events 写入端。backend='mem2' 或 'builtin'（降级，同表结构）。"""

    def __init__(self, db_path: str, prefer_mem2: bool = True):
        db_path = str(db_path)
        if os.path.basename(db_path) == REAL_DB_NAME:
            raise SystemExit(f"红线：模拟数据禁止写入真实库 {REAL_DB_NAME}（--db 请用独立 sim 库）")
        self.db_path = db_path
        parent = os.path.dirname(os.path.abspath(db_path))
        os.makedirs(parent, exist_ok=True)
        self.fallback_reason = None
        self._mem2 = _try_import_mem2() if prefer_mem2 else None
        self.backend = "mem2" if self._mem2 else "builtin"
        self.conn = self._connect()
        self.count = 0

    def _connect(self) -> sqlite3.Connection:
        if self._mem2 is not None:
            for fn in ("connect", "open_db", "get_conn"):
                f = getattr(self._mem2, fn, None)
                if callable(f):
                    try:
                        conn = f(self.db_path)
                        conn.row_factory = sqlite3.Row
                        return conn
                    except Exception:
                        break
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        if self._mem2 is not None:
            init = getattr(self._mem2, "init_schema", None)
            if callable(init):
                try:
                    init(conn)
                    return conn
                except Exception:
                    pass
        conn.executescript(_SCHEMA)
        return conn

    def _demote(self, exc: Exception) -> None:
        self.fallback_reason = f"{type(exc).__name__}: {exc}"
        self.backend = "builtin"
        self._mem2 = None
        self.conn.executescript(_SCHEMA)

    def append(self, ev: dict) -> None:
        if self._mem2 is not None:
            try:
                self._mem2.append_event(self.conn, **ev)
                self.count += 1
                return
            except Exception as exc:  # 接口不符/约束拒绝 → 永久降级到内建实现
                self._demote(exc)
        self.conn.execute(
            "INSERT INTO attempt_events (ts, day, sim, pack, kp, qtype, mode, result, "
            "attribution, user_override, policy_snapshot, meta) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                ev["ts"], ev["day"], ev["sim"], ev["pack"], ev["kp"], ev["qtype"], ev["mode"],
                ev["result"],
                json.dumps(ev["attribution"], ensure_ascii=False) if ev.get("attribution") else None,
                ev.get("user_override", 0),
                json.dumps(ev["policy_snapshot"], ensure_ascii=False) if ev.get("policy_snapshot") else None,
                json.dumps(ev.get("meta") or {}, ensure_ascii=False),
            ),
        )
        self.count += 1

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()


# --------------------------------------------------------------------------- #
# 一天一套：编排
# --------------------------------------------------------------------------- #
def simulate_day(
    day: str,
    day_index: int,
    exam: dict,
    pool: list[dict],
    seed,
    weak_kps: list[str],
    anchor: float,
    band: float = 5.0,
    base_acc: float = 0.85,
    weak_acc: float = 0.40,
    anchor_fit: bool = True,
    attr_weights: list[float] | None = None,
    unresolved_rate: float = 0.25,
    weak_boost: float = 2.5,
    resample: int = 12,
) -> dict:
    """生成第 day_index 天（day）的一套模拟卷 + 作答 + 校准 + 归因 + 事件流。"""
    attr_weights = attr_weights or list(DEFAULT_ATTR_WEIGHTS)
    day_seed = f"{seed}|{day}"
    paper = generate_paper(exam, pool, weak_kps, day_seed, weak_boost=weak_boost)
    rng = random.Random(f"answer|{day_seed}")
    # 跨天随机微调（薄弱/非薄弱差距逐日波动），再按锚点求整体缩放
    b = base_acc * rng.uniform(0.94, 1.06)
    w = weak_acc * rng.uniform(0.88, 1.12)
    scale = fit_accuracy_scale(paper, anchor, b, w) if anchor_fit else 1.0
    b, w = b * scale, w * scale
    # 全卷概率抽样：抽最多 resample 次，取总分最贴锚的一次（等价于条件在"目标水平"上），
    # 剩余偏差再交给校准改判少数题 → 默认跑出来贴 100，且人工改判痕迹尽量少。
    best = None
    tries = 0
    for _ in range(max(1, resample)):
        tries += 1
        cand = simulate_answers(paper, rng, b, w)
        dev = abs(grade_paper(paper, cand) - anchor)
        if best is None or dev < best[0]:
            best = (dev, cand)
        if dev <= band:
            break
    results = best[1]
    raw_score = grade_paper(paper, results)
    results, actions = calibrate_to_anchor(paper, results, anchor, band)
    results = assign_attributions(paper, results, rng, attr_weights)
    events = build_events(day, day_index, exam["id"], paper, results, rng, seed,
                          unresolved_rate=unresolved_rate)
    return {
        "day": day,
        "day_index": day_index,
        "paper": paper,
        "results": results,
        "raw_score": raw_score,
        "score": grade_paper(paper, results),
        "total": exam["paper_structure"]["total"],
        "actions": actions,
        "events": events,
        "eff_base_acc": round(b, 3),
        "eff_weak_acc": round(w, 3),
        "draws": tries,
    }


def _counts(pairs) -> list[tuple[str, int]]:
    d: dict[str, int] = {}
    for k in pairs:
        d[k] = d.get(k, 0) + 1
    return sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))


def day_stats(day_res: dict) -> dict:
    qmap = {q["no"]: q for q in day_res["paper"]}
    by_section = {"choice": [0, 0], "fill": [0, 0], "solution": [0, 0]}
    wrong_kps, attrs = [], []
    weak_hit = [0, 0]  # [薄弱题数, 薄弱错题数]
    nonweak_hit = [0, 0]
    for r in day_res["results"]:
        q = qmap[r["no"]]
        sec = by_section[q["qtype"]]
        sec[1] += 1
        if r["result"] == "correct":
            sec[0] += 1
        bucket = weak_hit if q["weak"] else nonweak_hit
        bucket[0] += 1
        if r["result"] != "correct":
            bucket[1] += 1
            wrong_kps.append(q["kp"])
            if r.get("attribution"):
                attrs.append(r["attribution"]["type"])
    return {
        "by_section": by_section,
        "correct": sum(v[0] for v in by_section.values()),
        "n": sum(v[1] for v in by_section.values()),
        "wrong_kps": _counts(wrong_kps),
        "attrs": _counts(attrs),
        "weak": weak_hit,
        "nonweak": nonweak_hit,
        "n_review": sum(1 for e in day_res["events"] if e["mode"] == "selfassess"),
    }


def day_report_lines(day_res: dict, anchor: float, band: float) -> list[str]:
    st = day_stats(day_res)
    sec = st["by_section"]
    L = [
        f"【{SIM_LABEL}数据】Day {day_res['day_index'] + 1} · {day_res['day']} · "
        f"{'数一模拟卷'} · 得分 {day_res['score']}/{day_res['total']}（锚 {anchor:g}±{band:g}）",
        f"  正确 {st['correct']}/{st['n']}"
        f"（选择 {sec['choice'][0]}/{sec['choice'][1]}"
        f" 填空 {sec['fill'][0]}/{sec['fill'][1]}"
        f" 解答 {sec['solution'][0]}/{sec['solution'][1]}）"
        f"｜有效正确率 非薄弱 {day_res['eff_base_acc']} / 薄弱 {day_res['eff_weak_acc']}",
    ]
    if day_res["actions"]:
        acts = "；".join(
            f"Q{a['no']} {a['from']}→{a['to']} {a['delta']:+d}" for a in day_res["actions"]
        )
        L.append(
            f"  校准 {len(day_res['actions'])} 处（抽样 {day_res['draws']} 次，最优原始分 "
            f"{day_res['raw_score']}）：{acts}"
        )
    else:
        L.append(f"  校准 0 处（抽样 {day_res['draws']} 次，原始分已在锚带内：{day_res['raw_score']}）")
    L.append("  错题 kp：" + ("  ".join(f"{k}×{v}" for k, v in st["wrong_kps"]) or "无"))
    L.append("  归因：" + ("  ".join(f"{k} {v}" for k, v in st["attrs"]) or "无"))
    L.append(
        f"  事件：{st['n']} 作答（mode=sim）+ {st['n_review']} 复盘（mode=selfassess，复盘后仍不会）"
    )
    return L


def summary_lines(days: list[dict], anchor: float, band: float, sink_info: dict) -> list[str]:
    scores = [d["score"] for d in days]
    in_band = sum(1 for s in scores if abs(s - anchor) <= band)
    weak = [0, 0]
    nonweak = [0, 0]
    attrs: list[str] = []
    wrong_kps: list[str] = []
    n_events = 0
    for d in days:
        st = day_stats(d)
        weak[0] += st["weak"][0]
        weak[1] += st["weak"][1]
        nonweak[0] += st["nonweak"][0]
        nonweak[1] += st["nonweak"][1]
        attrs.extend([k for k, v in st["attrs"] for _ in range(v)])
        wrong_kps.extend([k for k, v in st["wrong_kps"] for _ in range(v)])
        n_events += len(d["events"])
    wr_w = weak[1] / weak[0] if weak[0] else 0.0
    wr_n = nonweak[1] / nonweak[0] if nonweak[0] else 0.0
    total = days[0]["total"] if days else 150
    L = [
        f"【{SIM_LABEL}数据】{len(days)} 日汇总（{days[0]['day']} → {days[-1]['day']}）",
        f"  平均分 {sum(scores) / len(scores):.1f}/{total}"
        f"（min {min(scores)} / max {max(scores)}）｜落在锚带 {anchor:g}±{band:g} 内 {in_band}/{len(days)}",
        f"  薄弱 kp 错误率 {wr_w:.2f}（{weak[1]}/{weak[0]} 题）"
        f" vs 非薄弱 {wr_n:.2f}（{nonweak[1]}/{nonweak[0]} 题）"
        f"｜倍数 {(wr_w / wr_n if wr_n else 0):.2f}×",
        "  错题 kp Top：" + ("  ".join(f"{k}×{v}" for k, v in _counts(wrong_kps)[:6]) or "无"),
        "  归因总分布：" + ("  ".join(f"{k} {v}" for k, v in _counts(attrs)) or "无"),
        f"  总事件数 {n_events}（全部 sim=1）｜库 {sink_info['db']}｜写入后端 {sink_info['backend']}",
    ]
    if sink_info.get("fallback_reason"):
        L.append(f"  注：mem2 写入失败已降级内建实现 → {sink_info['fallback_reason']}")
    L.append(f"  口径：以上全部为{SIM_LABEL}数据（设计共识 §4.3），非真实作答记录。")
    return L


def write_report_md(path: str, days: list[dict], anchor: float, band: float, sink_info: dict,
                    argv_note: str) -> str:
    lines = [
        f"# MathForge 模拟卷日报（{SIM_LABEL}数据）",
        "",
        f"> **本文件全部数据为程序{SIM_LABEL}生成**，用于驱动 v2 记忆层/复习/蒸馏链路，"
        f"非真实作答记录（设计共识 §4.3 诚实口径）。",
        "",
        f"- 命令：`{argv_note}`",
        f"- 库：`{sink_info['db']}`（独立 sim 库，事件 sim=1）｜写入后端：{sink_info['backend']}",
        f"- 得分锚：{anchor:g} ± {band:g}（150 分制，试卷结构见 exams/math1.json）",
        "",
        "## 每日明细",
        "",
    ]
    for d in days:
        st = day_stats(d)
        lines.append(f"### Day {d['day_index'] + 1} · {d['day']} · {d['score']}/{d['total']}")
        lines.append("")
        lines.append(f"- 正确 {st['correct']}/{st['n']}；校准 {len(d['actions'])} 处（原始 {d['raw_score']}）")
        lines.append("- 错题 kp：" + ("，".join(f"{k}×{v}" for k, v in st["wrong_kps"]) or "无"))
        lines.append("- 归因：" + ("，".join(f"{k} {v}" for k, v in st["attrs"]) or "无"))
        lines.append("")
        lines.append("| 题号 | 题型 | 分值 | kp | 难度 | 薄弱 | 判定 | 得分 | 归因 |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        qmap = {q["no"]: q for q in d["paper"]}
        for r in d["results"]:
            q = qmap[r["no"]]
            attr = r["attribution"]["type"] if r.get("attribution") else "-"
            lines.append(
                f"| {q['no']} | {q['qtype']} | {q['points']} | {q['kp']} | {q['difficulty']} | "
                f"{'是' if q['weak'] else ''} | {r['result']} | {r['earned']} | {attr} |"
            )
        lines.append("")
    lines.append("## 汇总")
    lines.append("")
    for s in summary_lines(days, anchor, band, sink_info):
        lines.append(f"- {s.strip()}")
    lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def run(
    days: int = 3,
    seed=42,
    anchor: float | None = None,
    band: float = 5.0,
    exam_id: str = "math1",
    db: str | None = None,
    start_day: str | None = None,
    weak_kps: list[str] | None = None,
    base_acc: float = 0.85,
    weak_acc: float = 0.40,
    anchor_fit: bool = True,
    attr_weights: list[float] | None = None,
    unresolved_rate: float = 0.25,
    weak_boost: float = 2.5,
    resample: int = 12,
    prefer_mem2: bool = True,
    report: str | None = None,
    out=None,
    argv_note: str = "",
) -> dict:
    """生成 N 天模拟数据并落库；返回 {days, sink, score...}。stdout 打印日报+汇总。"""
    out = out or sys.stdout
    exam = load_exam(exam_id)
    pool = load_kp_pool(exam)
    anchor = float(exam.get("score_anchor", 100) if anchor is None else anchor)
    weak_kps = weak_kps or list(DEFAULT_WEAK_KPS)
    db = db or os.path.join(ROOT, "mathforge_sim.db")
    if start_day:
        d0 = datetime.strptime(start_day, "%Y-%m-%d").date()
    else:
        d0 = date.today() - timedelta(days=days - 1)

    sink = EventSink(db, prefer_mem2=prefer_mem2)
    day_list: list[dict] = []
    print(
        f"【{SIM_LABEL}】MathForge 模拟卷生成：{days} 天 · exam={exam_id} · seed={seed} · "
        f"锚 {anchor:g}±{band:g} · 薄弱 kp {','.join(weak_kps)}",
        file=out,
    )
    print(f"       库 {db}（独立 sim 库）｜写入后端 {sink.backend}", file=out)
    print("", file=out)
    for i in range(days):
        day = (d0 + timedelta(days=i)).isoformat()
        res = simulate_day(
            day, i, exam, pool, seed, weak_kps, anchor, band, base_acc, weak_acc,
            anchor_fit, attr_weights, unresolved_rate, weak_boost, resample,
        )
        for ev in res["events"]:
            sink.append(ev)
        sink.commit()
        day_list.append(res)
        for line in day_report_lines(res, anchor, band):
            print(line, file=out)
        print("", file=out)

    sink_info = {"db": db, "backend": sink.backend, "fallback_reason": sink.fallback_reason,
                 "n_events": sink.count}
    for line in summary_lines(day_list, anchor, band, sink_info):
        print(line, file=out)
    report_path = None
    if report:
        report_path = report if report != "AUTO" else os.path.join(
            SCRIPT_DIR, f"sim_report_{day_list[0]['day']}_{days}d.md"
        )
        write_report_md(report_path, day_list, anchor, band, sink_info, argv_note)
        print(f"  报告已写出：{report_path}", file=out)
    sink.close()
    return {"days": day_list, "sink": sink_info, "report": report_path,
            "scores": [d["score"] for d in day_list], "anchor": anchor}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="simulate_days.py",
        description=f"MathForge V2 模拟卷模拟器（一天一套+当晚复盘；产出{SIM_LABEL}事件流，sim=1）",
    )
    p.add_argument("action", nargs="?", default="simulate", choices=["simulate"],
                   help="子命令（可省略，设计共识写法：simulate）")
    p.add_argument("--days", type=int, default=3, help="生成天数（默认 3）")
    p.add_argument("--seed", default="42", help="随机种子（可复现，默认 42）")
    p.add_argument("--score", type=float, default=None, help="得分锚，默认取 exam.score_anchor=100")
    p.add_argument("--band", type=float, default=5.0, help="锚带半宽（默认 ±5）")
    p.add_argument("--exam", default="math1", help="考试档案 id（默认 math1）")
    p.add_argument("--db", default=None, help="sqlite 库路径（默认 mathforge_sim.db；禁止 mathforge.db）")
    p.add_argument("--start-day", default=None, help="起始日期 YYYY-MM-DD（默认使末日=今天）")
    p.add_argument("--weak-kps", default=",".join(DEFAULT_WEAK_KPS), help="薄弱(目标) kp，逗号分隔")
    p.add_argument("--base-acc", type=float, default=0.85, help="非薄弱 kp 正确率基线（默认 0.85）")
    p.add_argument("--weak-acc", type=float, default=0.40, help="薄弱 kp 正确率（默认 0.40）")
    p.add_argument("--no-anchor-fit", action="store_true",
                   help="关闭「按锚点整体缩放正确率」（此时分数由基线决定，校准承担更多）")
    p.add_argument("--attribution-weights", default=",".join(str(x) for x in DEFAULT_ATTR_WEIGHTS),
                   help="四类归因比例 概念混淆,计算失误,方法选错,审题错误")
    p.add_argument("--unresolved-rate", type=float, default=0.25,
                   help="复盘后仍不会的错题比例（写 selfassess partial 事件，默认 0.25）")
    p.add_argument("--weak-boost", type=float, default=2.5, help="薄弱 kp 抽题加权（默认 2.5）")
    p.add_argument("--resample", type=int, default=12,
                   help="每天最多抽样几次全卷作答、取最贴锚者（默认 12，1=不重抽）")
    p.add_argument("--no-mem2", action="store_true", help="强制使用内建 append（不尝试 import mem2）")
    p.add_argument("--report", nargs="?", const="AUTO", default=None,
                   help="落一份 markdown 报告（不带值 → 写到脚本旁 scripts/sim_report_*.md）")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    a = build_parser().parse_args(argv)
    weights = [float(x) for x in a.attribution_weights.split(",") if x.strip()]
    if len(weights) != 4 or sum(weights) <= 0:
        raise SystemExit("--attribution-weights 需为 4 个正数（概念混淆,计算失误,方法选错,审题错误）")
    run(
        days=a.days,
        seed=a.seed,
        anchor=a.score,
        band=a.band,
        exam_id=a.exam,
        db=a.db,
        start_day=a.start_day,
        weak_kps=[s.strip() for s in a.weak_kps.split(",") if s.strip()],
        base_acc=a.base_acc,
        weak_acc=a.weak_acc,
        anchor_fit=not a.no_anchor_fit,
        attr_weights=weights,
        unresolved_rate=a.unresolved_rate,
        weak_boost=a.weak_boost,
        resample=a.resample,
        prefer_mem2=not a.no_mem2,
        report=a.report,
        argv_note="python scripts/simulate_days.py " + " ".join(argv),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
