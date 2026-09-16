"""assembler 上下文组装器单测。

覆盖：build_context 预算结构 / build_context 在 mem2 缺失时降级(_degraded) /
prepare_decision_state 含 prereq_mastery（P1 死代码修复证据：decide 真能命中 P1）/
与 policy2.decide 集成验证 P1 触发。
mem2 已实现，测试通过注入 stub 验证真实路径；纯函数级、独立 tmp db。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import assembler
import pack_loader
import policy2


class _MemStub:
    """模拟 mem2 接口（对齐 mem2.py 源码）：
    project_mastery -> {decayed_value}；project_all -> {kp: {...}}；
    project_mistakes -> list；last_attribution 为 {type} 或 None。"""

    def __init__(self, allp=None, mistakes=None):
        self._all = allp or {}
        self._mistakes = mistakes or []

    def project_mastery(self, conn, kp, at=None, strategy=None):
        d = self._all.get(kp)
        if isinstance(d, dict):
            return {"decayed_value": d.get("decayed_value")}
        return None

    def project_all(self, conn, strategy=None):
        return self._all

    def project_mistakes(self, conn, pack=None, kp=None, limit=20):
        return self._mistakes[:limit]


def _calc_pack():
    return pack_loader.load_pack("calculus")


def test_build_context_structure_and_filled_with_mem():
    pack = _calc_pack()
    mem = _MemStub(
        allp={"calc.rolle": {"decayed_value": 0.6}},
        mistakes=[{"kp": "calc.rolle", "attribution": {"type": "计算失误"}}],
    )
    ctx = assembler.build_context(None, pack, "calc.rolle", mem=mem)
    assert ctx["kp"]["id"] == "calc.rolle"
    assert set(ctx["kp"].keys()) >= {"id", "name", "section", "difficulty_floor", "typical_forms"}
    assert ctx["mastery_current"] == 0.6
    assert len(ctx["recent_mistakes"]) == 1
    assert ctx["_degraded"] is False


def test_build_context_degraded_when_mem_missing(monkeypatch):
    # mem2 缺失（模块级 _MEM2=None）→ 降级标注，不崩
    monkeypatch.setattr(assembler, "_MEM2", None)
    pack = _calc_pack()
    ctx = assembler.build_context(None, pack, "calc.rolle")  # 不传 mem
    assert ctx["mastery_current"] is None
    assert ctx["recent_mistakes"] == []
    assert ctx["_degraded"] is True


def test_prepare_state_contains_prereq_mastery():
    # 关键证据：state 含 prereq_mastery（v1 死代码修复）
    pack = _calc_pack()
    mem = _MemStub(allp={
        "calc.rolle": {"decayed_value": 0.5},
        "calc.continuity": {"decayed_value": 0.3},
        "calc.derivative.basic": {"decayed_value": 0.7},
    })
    st = assembler.prepare_decision_state(None, pack, "calc.rolle", "基础", mem=mem)
    assert "prereq_mastery" in st
    # calc.rolle 在 kp_graph 中 parents=[calc.continuity, calc.derivative.basic]
    assert set(st["prereq_mastery"].keys()) == {"calc.continuity", "calc.derivative.basic"}
    assert st["prereq_mastery"]["calc.continuity"] == 0.3


def test_p1_deadcode_fixed_via_assembler(monkeypatch):
    # 集成证据：assembler 提供真实 prerequisites → decide 真命中 P1（v1 死代码已修复）
    # （demo 可达性门控放行：本测试聚焦 P1 机制本身）
    monkeypatch.setattr(policy2, "_kp_reachable", lambda kp: True)
    pack = _calc_pack()
    mem = _MemStub(allp={
        "calc.rolle": {"decayed_value": 0.3},       # 当前掌握度 <0.4
        "calc.continuity": {"decayed_value": 0.3},  # 前置未达 0.6
        "calc.derivative.basic": {"decayed_value": 0.8},
    })
    st = assembler.prepare_decision_state(None, pack, "calc.rolle", "基础", mem=mem)
    d = policy2.decide(st, pack_loader.DEFAULT_STRATEGY)
    assert d["rule"] == "P1"
    assert d["action"]["kp_target"] == "calc.continuity"


def test_prepare_state_degraded_when_mem_none(monkeypatch):
    monkeypatch.setattr(assembler, "_MEM2", None)
    pack = _calc_pack()
    st = assembler.prepare_decision_state(None, pack, "calc.rolle", "基础")  # 不传 mem
    assert st["_degraded"] is True
    assert st["first_contact"] is True
    assert st["mastery_decayed"] == 0.5
    # prereq_mastery 全 None（降解）
    assert all(v is None for v in st["prereq_mastery"].values())
