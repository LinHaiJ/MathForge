"""三包 kp_graph 一致性校验（T10 内容工程验收）。

覆盖：
- 三包 kp 数落入目标区间（calculus 40-50 / linear 19-25 / probability 16-20）
- id 唯一、parents 均指向本包已存在 id、无自环
- 轻量无环检查（拓扑排序可行）
- resolve_prereqs 单层前置可用
- 字段完整性 + 枚举合法（difficulty_floor∈{basic,mid}、exam_freq∈{high,mid,low}、qtypes⊆{fill,solution}）
- 旧 kp id 全部保留
- 冒烟 load_pack 三包正常、find_pack_for_kp 抽查 6 个新 kp 归属正确
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pack_loader as pl

SUBJECTS = {
    "calculus": (40, 50),
    "linear": (19, 25),
    "probability": (16, 20),
}

# 既有 id（别处引用锚）必须全部保留
PRESERVED_OLD_IDS = [
    "calc.rolle", "calc.lagrange", "calc.limit.basic", "calc.derivative.basic",
    "calc.derivative.chain", "calc.derivative.param", "calc.integral.basic",
    "calc.integral.byparts", "calc.definite.eval", "calc.continuity",
    "calc.limit.lhopital",
    "la.det.calc", "la.matrix.basic", "la.matrix.rank", "la.eigen",
    "prob.classical", "prob.total_bayes", "prob.expectation",
]

# 抽查归属的新 kp（每段至少 2 个）
NEW_KP_SAMPLES = {
    "calculus": ["calc.taylor", "calc.double.int", "calc.ode.first"],
    "linear": ["la.vector.group", "la.quadratic.form", "la.lineq.judge"],
    "probability": ["prob.rv.continuous", "prob.cov.corr", "prob.estimate"],
}

REQUIRED_FIELDS = [
    "id", "name", "section", "level", "parents", "verifiable",
    "qtypes", "difficulty_floor", "exam_freq", "typical_forms", "pitfalls",
]


def _load_graph(subject):
    path = os.path.join(ROOT, "packs", subject, "kp_graph.json")
    import json
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _topo_ok(nodes):
    ids = [k["id"] for k in nodes]
    idset = set(ids)
    indeg = {i: 0 for i in ids}
    adj = {i: [] for i in ids}
    for k in nodes:
        for p in k.get("parents", []):
            if p in idset:
                adj[p].append(k["id"])
                indeg[k["id"]] += 1
    from collections import deque
    q = deque([i for i in ids if indeg[i] == 0])
    seen = 0
    while q:
        u = q.popleft()
        seen += 1
        for v in adj[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                q.append(v)
    return seen == len(ids)


def test_kp_counts_in_target_range():
    for subject, (lo, hi) in SUBJECTS.items():
        g = _load_graph(subject)
        assert lo <= len(g) <= hi, f"{subject} kp 数 {len(g)} 不在 [{lo},{hi}]"


def test_kp_ids_unique():
    for subject in SUBJECTS:
        g = _load_graph(subject)
        ids = [k["id"] for k in g]
        assert len(ids) == len(set(ids)), f"{subject} 存在重复 id"


def test_kp_parents_exist_and_no_self_loop():
    for subject in SUBJECTS:
        g = _load_graph(subject)
        idset = {k["id"] for k in g}
        for k in g:
            assert k["id"] not in k.get("parents", []), f"{k['id']} 自环"
            for p in k.get("parents", []):
                assert p in idset, f"{k['id']} 的 parent {p} 不存在于 {subject}"


def test_kp_graph_acyclic():
    for subject in SUBJECTS:
        g = _load_graph(subject)
        assert _topo_ok(g), f"{subject} 存在环"


def test_kp_fields_complete_and_enums_valid():
    for subject in SUBJECTS:
        g = _load_graph(subject)
        for k in g:
            for f in REQUIRED_FIELDS:
                assert f in k, f"{k.get('id')} 缺字段 {f}"
            assert k["difficulty_floor"] in ("basic", "mid"), \
                f"{k['id']} difficulty_floor 非法: {k['difficulty_floor']}"
            assert k["exam_freq"] in ("high", "mid", "low"), \
                f"{k['id']} exam_freq 非法: {k['exam_freq']}"
            for q in k["qtypes"]:
                assert q in ("fill", "solution"), f"{k['id']} qtype 非法: {q}"
            assert isinstance(k["verifiable"], bool)
            assert isinstance(k["typical_forms"], list) and len(k["typical_forms"]) >= 1
            assert isinstance(k["pitfalls"], list) and len(k["pitfalls"]) >= 1


def test_resolve_prereqs_single_layer():
    for subject in SUBJECTS:
        pack = pl.load_pack(subject)
        for k in pack["kp_graph"]:
            pre = pl.resolve_prereqs(pack, k["id"])
            assert set(pre) == set(k.get("parents", [])), \
                f"{subject}/{k['id']} resolve_prereqs 与 parents 不一致"
            for p in pre:
                assert pl.get_kp(pack, p) is not None


def test_old_kp_ids_preserved():
    all_ids = set()
    for subject in SUBJECTS:
        all_ids |= {k["id"] for k in _load_graph(subject)}
    for oid in PRESERVED_OLD_IDS:
        assert oid in all_ids, f"旧 kp id 丢失: {oid}"


def test_load_pack_smoke_three_subjects():
    for subject in SUBJECTS:
        pack = pl.load_pack(subject)
        assert pack["kp_ids"]
        assert pack["examiner_prompt"]
        assert pack["manifest"]["id"] == subject


def test_find_pack_for_kp_samples():
    for subject, samples in NEW_KP_SAMPLES.items():
        for kp in samples:
            assert pl.find_pack_for_kp(kp) == subject, \
                f"{kp} 应归属 {subject}"
