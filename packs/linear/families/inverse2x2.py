"""确定性参数化模板族 · 二阶逆矩阵（kp: la.matrix.basic）。

缓存命名空间：family-la.matrix.basic:v1

迁移来源（D8-6）：v1 repo 根 families.py 族 inverse_2x2 的「包内化」落地。
本文件零改动 v1（families.py / generate.py / v2api.py / app.py 等一律未动），
仅新增此包内族模块，委托 repo 根的 families 模块完成参数化枚举，再把每一项
重写成 v2 契约字段（kp_id / verify_level 等）。

v1 族语义：A=[[a,b],[c,d]]（det≠0），求逆矩阵 A⁻¹（Matrix([[...],[...]] 格式）；
答案由 SymPy Matrix.inv 计算并校验 A·A⁻¹=I，构造即正确。
题干与参数结构全部自造，不引用任何真题原文；沿用 v1 缓存命名空间 family-<kp>:v1。

注意：la.matrix.basic 同时被 matmul_entry 族（packs/linear/families/matmul.py）覆盖，
与 inverse_2x2 同为 KP_ID=la.matrix.basic（v2api._module_covers_kp 按 KP_ID 命中，
两模块均匹配；_try_pack_family 取首个命中模块的首题，均为确定性合法题）。

运行上下文：同其他包内族，模块顶层 import families 依赖 repo 根在 sys.path。
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import families  # noqa: E402  —— v1 repo 根确定性族模块（只读委托，不改动）

KP_ID = "la.matrix.basic"
KP_NAME = "逆矩阵"
PACK_ID = "linear"
CACHE_NS = "family-la.matrix.basic:v1"
FAMILY = "inverse_2x2"

# 镜像 v1 参数格（仅作契约文档；实际枚举委托 families.enumerate_family）
GRID: dict[str, list[int]] = {"a": [1, 2, 3], "b": [0, 1, 2], "c": [1, 2], "d": [1, 2, 3]}
SPEC: dict[str, object] = {FAMILY: None}


def _reshape(item: dict) -> dict:
    item = dict(item)
    item.setdefault("kp_id", KP_ID)
    item.setdefault("verify_level", "green")
    item.setdefault("analysis", "二阶矩阵求逆：A⁻¹ = (1/det A)·[[d,−b],[−c,a]]（伴随矩阵除以行列式）；本题由 SymPy 按此公式直接计算并校验 A·A⁻¹ = I（构造即正确）。")
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
