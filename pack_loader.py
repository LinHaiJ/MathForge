# MathForge V2 Pack Loader（D024/D025/D026 契约）
#
# packs/ = 科目级功能包（纯内容：数据 + skill + families），准入硬门槛=可符号验证
#   packs/<id>/pack.json            manifest（契约见 packs/CONTRACT.md）
#   packs/<id>/kp_graph.json        知识点 DAG（parents 前置依赖）
#   packs/<id>/qtypes.json          题型规格蒸馏产物（蒸馏暂缓 → 允许缺失/空）
#   packs/<id>/examiner/SKILL.md    出题专家·本域段（≤40 行，与 core 拼接）
#   packs/<id>/families/*.py        确定性模板族（可选，动态加载）
#   packs/<id>/macros.json          输入宏表（可选）
#   packs/<id>/strategy.json        策略覆写（可选，与全局默认合并）
# skills/_core/examiner-core.md     全局契约单点（D026-DP1，所有调用共享）
# exams/<exam>.json                 考试组合档案（包组合+权重+分值结构）
#
# 纪律：本模块只做加载/校验/拼接，不含业务逻辑；不 import 业务层。

import importlib.util
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.abspath(__file__))
PACKS_DIR = os.path.join(ROOT, "packs")
CORE_SKILL = os.path.join(ROOT, "skills", "_core", "examiner-core.md")
EXAMS_DIR = os.path.join(ROOT, "exams")

# 全局默认策略（P1-P6 数据化，D024/D025）：pack strategy.json 深合并覆写
DEFAULT_STRATEGY = {
    "decay_half_life_days": 7.0,
    "mastery": {
        "window": 5,
        "recency_halflife_days": 7.0,
        "difficulty_weight": 1.2,
    },
    "policy": {
        # P1-P6 阈值（对齐 v1 policy.py 语义）
        "p1_mastery_floor": 0.4,
        "p1_prereq_floor": 0.6,
        "p2_dup_basic": True,
        "p3_concept_variant": True,
        "p4_penalty": True,
        "p4_drop_floor": "basic",
        "p5_streak": 2,
        "p6_llm_explain": True,
    },
    "review_sort": "default",
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def list_packs() -> list[str]:
    """返回按 id 排序的可用科目包列表（含 pack.json 的目录）。"""
    packs = []
    for d in sorted(os.listdir(PACKS_DIR)):
        if os.path.isfile(os.path.join(PACKS_DIR, d, "pack.json")):
            packs.append(d)
    return packs


def _load_json(path: str, required: bool = False, default=None):
    if not os.path.exists(path):
        if required:
            raise FileNotFoundError(f"required file missing: {path}")
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_pack(pack_id: str) -> dict:
    """加载科目包，返回标准化结构（manifest/kp 图/策略/宏/域段/家族/契约提示词）。"""
    pack_dir = os.path.join(PACKS_DIR, pack_id)
    manifest_path = os.path.join(pack_dir, "pack.json")
    if not os.path.isfile(manifest_path):
        raise FileNotFoundError(f"pack not found: {pack_id} ({manifest_path})")
    manifest = json.load(open(manifest_path, encoding="utf-8"))
    entry = manifest.get("entry", {})

    kp_graph = _load_json(
        os.path.join(pack_dir, entry.get("syllabus", "kp_graph.json")), default=[]
    )
    qtypes = _load_json(
        os.path.join(pack_dir, entry.get("qtypes", "qtypes.json")), default=[]
    )
    domain_skill = None
    exam_path = entry.get("examiner", "examiner/SKILL.md")
    if os.path.isfile(os.path.join(pack_dir, exam_path)):
        domain_skill = open(os.path.join(pack_dir, exam_path), encoding="utf-8").read()
    strategy = _deep_merge(
        DEFAULT_STRATEGY,
        _load_json(
            os.path.join(pack_dir, entry.get("strategy", "strategy.json")), default={}
        ),
    )
    macros = _load_json(
        os.path.join(pack_dir, entry.get("macros", "macros.json")), default=[]
    )
    families = _load_families(pack_dir, entry.get("families"))

    # 出题提示词 = core 全局契约（单点）+ 本域段；core 缺失时报错（契约不可缺）
    core_skill = None
    if os.path.isfile(CORE_SKILL):
        core_skill = open(CORE_SKILL, encoding="utf-8").read()
    else:
        raise FileNotFoundError(f"global contract missing: {CORE_SKILL}")
    examiner_prompt = core_skill
    if domain_skill:
        examiner_prompt += "\n\n--- 本域约束（域段） ---\n" + domain_skill

    return {
        "manifest": manifest,
        "pack_dir": pack_dir,
        "kp_graph": kp_graph,
        "kp_ids": [k["id"] for k in kp_graph],
        "qtypes": qtypes,
        "strategy": strategy,
        "macros": macros,
        "families": families,
        "domain_skill": domain_skill,
        "examiner_prompt": examiner_prompt,
        "maturity": manifest.get("maturity", "confirmed"),
    }


def _load_families(pack_dir: str, fam_ref) -> dict:
    """动态加载 packs/<id>/families/*.py（每个 py 一个模板族模块）。
    fam_ref 兼容 manifest 的 'families' 目录引用；返回 {module_name: module}。"""
    if not fam_ref:
        return {}
    fam_dir = os.path.join(pack_dir, fam_ref)
    if not os.path.isdir(fam_dir):
        return {}
    mods = {}
    for fn in sorted(os.listdir(fam_dir)):
        if not fn.endswith(".py") or fn.startswith("_"):
            continue
        name = fn[:-3]
        spec = importlib.util.spec_from_file_location(
            f"pack_{os.path.basename(pack_dir)}_{name}", os.path.join(fam_dir, fn)
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mods[name] = mod
    return mods


def get_kp(pack: dict, kp_id: str) -> dict | None:
    """按 kp_id 查包内知识点；带 parents 前置解析校验。"""
    for k in pack["kp_graph"]:
        if k["id"] == kp_id:
            return k
    return None


def find_pack_for_kp(kp_id: str) -> str | None:
    """kp_id → 所属科目包（两段式路由 stage1 的确定性索引）。"""
    kp_id = (kp_id or "").strip()
    for pid in list_packs():
        pack = load_pack(pid)
        if kp_id in pack["kp_ids"]:
            return pid
    return None


def resolve_prereqs(pack: dict, kp_id: str) -> list[str]:
    """返回 kp 的直接前置知识点 id（策略层 P1 用；缺失静默返回空）。"""
    kp = get_kp(pack, kp_id)
    if not kp:
        return []
    return kp.get("parents", [])


def load_exam(exam_id: str) -> dict:
    """考试组合档案（packs 组合 + 权重 + 真题分值结构）。"""
    path = os.path.join(EXAMS_DIR, f"{exam_id}.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"exam profile not found: {exam_id} ({path})")
    return json.load(open(path, encoding="utf-8"))
