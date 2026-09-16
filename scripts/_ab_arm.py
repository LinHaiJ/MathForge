"""A/B 单臂执行器（由 ab_skill_ablation.py 以子进程调用，stdout 末行输出 JSON 结果）。

env MATHFORGE_ABLATION=1 → B 臂（剥离 SKILL.md）；缺省/0 → A 臂。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "v1"))  # db/eval 迁入 v1/

from eval import m1_green_success  # noqa: E402

if __name__ == "__main__":
    m1 = m1_green_success("big40")
    print(json.dumps(m1, ensure_ascii=False))
