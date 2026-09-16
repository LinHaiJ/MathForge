"""确定性参数化模板族 · 三阶行列式计算（kp: la.det.calc）。

缓存命名空间：family-la.det.calc:v1

迁移来源（D8-6）：v1 repo 根 families.py 族 det_3x3 的「包内化」落地。
本文件零改动 v1（families.py / generate.py / v2api.py / app.py 等一律未动），
仅新增此包内族模块，委托 repo 根的 families 模块完成参数化枚举，再把每一项
重写成 v2 契约字段（kp_id / verify_level 等）。

v1 族语义：A=[[1,2,a],[b,1,1],[0,2,3]]，求 det(A)（填入数值）；
det 由 SymPy Matrix.det 计算，构造即正确。题干与参数结构全部自造，不引用任何
真题原文；沿用 v1 缓存命名空间 family-<kp>:v1。

运行上下文：同其他包内族，模块顶层 import families 依赖 repo 根在 sys.path。
"""

from __future__ import annotations

import os
import sys

# packs/linear/families/det3x3.py → 上溯 4 级到 repo 根
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import families  # noqa: E402  —— v1 repo 根确定性族模块（只读委托，不改动）

KP_ID = "la.det.calc"
KP_NAME = "行列式计算"
PACK_ID = "linear"
CACHE_NS = "family-la.det.calc:v1"
FAMILY = "det_3x3"

# 镜像 v1 参数格（仅作契约文档；实际枚举委托 families.enumerate_family）
GRID: dict[str, list[int]] = {"a": [1, 2], "b": [1, 2, 3]}
SPEC: dict[str, object] = {FAMILY: None}


def _reshape(item: dict) -> dict:
    item = dict(item)
    item.setdefault("kp_id", KP_ID)
    item.setdefault("verify_level", "green")
    item.setdefault("analysis", "三阶行列式按对角线法则（沙路法）展开：正对角线三项之和减去副对角线三项之和；本题由 SymPy 按该定义直接计算并校验（构造即正确）。")
    return item


def enumerate_family(family: str = FAMILY, limit: int = 24) -> list[dict]:
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
