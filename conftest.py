"""pytest 全局配置：把作品A 目录加入 sys.path，使测试骨架的
`from mathforge.verify import ...` 包式导入在任意 cwd 下可解析。
（tests/test_verify.py 自带的 sys.path 插入少了一层目录，且该文件禁改，故在此兜底。）
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_parent = os.path.dirname(_here)  # D:\腾讯冲刺\作品A
if _parent not in sys.path:
    sys.path.insert(0, _parent)
# 目录重组（2026-09-12）：v1/=纯v1 模块，v2/=纯v2 模块；共用链仍在仓库根
for _sub in ("v1", "v2"):
    _d = os.path.join(_here, _sub)
    if os.path.isdir(_d) and _d not in sys.path:
        sys.path.insert(0, _d)
