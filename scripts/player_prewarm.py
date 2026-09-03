"""player 演示归因预热：为 auto-player 的两处 LLM 归因步骤写入缓存（一次真实调用，永久缓存）。

预热后 `MATHFORGE_DEMO=1` 下 player 全链路零 API。幂等：重复运行命中缓存零 API。
预热项（与 player.html 的 fetch payload 逐字一致）：
  1) 罗尔族实例#1 计算题 + 错答 "999" → LLM 归因
  2) 罗尔黄标概念题 + 错答 "错误" → LLM 归因
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from attribute import attribute_error  # noqa: E402
from generate import generate_question  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    rolle = next(k for k in json.load(open(ROOT / "cache" / "ingest_zwdl.json", encoding="utf-8"))["kp_list"]
                 if "罗尔" in k["kp"])

    # 家族实例 #1-6 全部预热（CTX 复用后每次完整重放消耗 2 发：c04/c06；
    # 同进程 3 次完整重放 = 6 发。重放前重启服务更稳，见 demo_checklist）
    for i in range(6):
        q1 = generate_question({"kp": "罗尔定理"}, difficulty="基础", qtype="calculation")
        assert q1.get("statement_md"), f"罗尔实例#{i+1} 生成失败"
        d1 = attribute_error(q1["statement_md"], "999", q1["answer_sympy"], q1.get("analysis"))
        print(f"[{i+1}] 计算题错答 999 → {d1['attribution']} (conf={d1['confidence']})")

    # 与 player 的 /generate 端点完全同形：kp 仅传名字（缓存键与完整 kp 上下文不同）
    c1 = generate_question({"kp": "罗尔定理"}, difficulty="基础", qtype="concept")
    assert c1.get("statement_md"), "罗尔概念题生成失败"
    std = c1.get("answer_md") or c1.get("correct") or ""
    d2 = attribute_error(c1["statement_md"], "错误", std, c1.get("analysis"))
    print(f"[2] 概念题错答 错误 → {d2['attribution']} (conf={d2['confidence']})")
    print(f"    概念题标准答案={std!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
