"""确定性参数化模板族 · 罗尔定理（kp: calc.rolle）。

缓存命名空间：family-calc.rolle:v1

迁移来源（D8-6）：v1 repo 根 families.py 族 rolle_xi 的「包内化」落地。
本文件零改动 v1（families.py / generate.py / v2api.py / app.py 等一律未动），
仅新增此包内族模块，委托 repo 根的 families 模块完成参数化枚举，再把每一项
重写成 v2 契约字段（kp_id / verify_level 等）。

v1 族语义：f(x)=a(x-p)(x-q) 在 [p,q]，f(p)=f(q)=0 → f'(ξ)=0 有 ξ=(p+q)/2；
ξ 由 SymPy solve 从结构推导，构造即正确。本题干与参数结构全部自造，不引用任何
真题原文；沿用 v1 缓存命名空间 family-<kp>:v1（与 v1 缓存语义一致）。

运行上下文：模块在顶层 `import families` 依赖 repo 根在 sys.path。app 从根启动 /
pytest 从根跑均满足；动态加载（pack_loader）时本文件也会把 repo 根插入 sys.path，
故 import 失败路径已被兜底。
"""

from __future__ import annotations

import os
import sys

# 把 repo 根（packs/calculus/families → 上溯 4 级）补进 sys.path，保证 `import families`
# 可用（pack_loader 以独立 module name 动态加载本文件，顶层 import 需根在 path）。
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import families  # noqa: E402  —— v1 repo 根确定性族模块（只读委托，不改动）

KP_ID = "calc.rolle"
KP_NAME = "罗尔定理"
PACK_ID = "calculus"
CACHE_NS = "family-calc.rolle:v1"
FAMILY = "rolle_xi"

# 镜像 v1 参数格（仅作契约文档；实际枚举委托 families.enumerate_family）
GRID: dict[str, list[int]] = {"a": [1, 2, 3], "p": [-2, 1], "q": [2, 4]}
SPEC: dict[str, object] = {FAMILY: None}


def _reshape(item: dict) -> dict:
    """把 v1 枚举项改写成 v2 契约字段（宁多勿缺）：补 kp_id / verify_level，其余透传。"""
    item = dict(item)
    item.setdefault("kp_id", KP_ID)
    item.setdefault("verify_level", "green")
    return item


def enumerate_family(family: str = FAMILY, limit: int = 24) -> list[dict]:
    """委托 v1 root families.enumerate_family(FAMILY, limit)，逐项归一成 v2 契约。

    v1 已对每项断言全过（_clean + 结构合法性），这里再过滤一次确保不漏退化项。
    """
    items = families.enumerate_family(FAMILY, limit)
    out = []
    for it in items:
        if it and all(bool(v) for _, v in it.get("assert", [])):
            out.append(_reshape(it))
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    items = enumerate_family(limit=24)
    print(f"{FAMILY}: {len(items)} 合法实例（委托 v1 root families）")
    for it in items[:3]:
        print("  ", it["statement_md"][:70], "| ans:", it["answer_sympy"])
