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
