"""pytest 全局配置：把作品A 目录加入 sys.path，使测试骨架的
`from mathforge.verify import ...` 包式导入在任意 cwd 下可解析。
（tests/test_verify.py 自带的 sys.path 插入少了一层目录，且该文件禁改，故在此兜底。）

模块 B（2026-09-28）：全局环境密封（autouse）——测试注入假依赖、不依赖真实环境：
- LLM 磁盘缓存指向 per-test 临时目录（llm._cache_path 每次读 MATHFORGE_CACHE_DIR）；
- 强制 MATHFORGE_DEMO=1（演示模式红线：只读缓存、未命中即报错，测试绝不出网）；
- 删除进程环境里的 DEEPSEEK_API_KEY（防 .env 真实 key 经 load_dotenv 泄漏进测试行为）。
干净检出（无 cache/llm 预热缓存、无 .env）也应全量跑绿——投递前质量门。
"""

import os
import sys

import pytest

_here = os.path.dirname(os.path.abspath(__file__))
_parent = os.path.dirname(_here)  # D:\腾讯冲刺\作品A
if _parent not in sys.path:
    sys.path.insert(0, _parent)
# 目录重组（2026-09-12）：v1/=纯v1 模块，v2/=纯v2 模块；共用链仍在仓库根
# v1 退役（2026-09-30）：v1/ 已删除，db.py 迁入 v2/，此处只注册 v2/
for _sub in ("v2",):
    _d = os.path.join(_here, _sub)
    if os.path.isdir(_d) and _d not in sys.path:
        sys.path.insert(0, _d)


# 模块级 fixture 以 os.environ 直设、跨测试存活的测试隔离变量（test_v2api client）：
# 清扫它们会破坏外层 fixture 的作用域语义——v2 请求会回落到真实 mathforge.db /
# 真实 profiles 目录，反而制造更严重的密封破口，故豁免、由相应 fixture 自管。
# 其余 MATHFORGE_*（含 .env 经 load_dotenv 灌进来的，如 MATHFORGE_ABLATION）全量清扫。
_SWEEP_EXEMPT = frozenset({"MATHFORGE_V2_DB", "MATHFORGE_PROFILES_DIR"})


@pytest.fixture(autouse=True)
def _sealed_test_env(monkeypatch, tmp_path):
    """环境密封（对全部测试生效；monkeypatch 在测试后自动还原原值）：

    - 全量清扫 MATHFORGE_* 运行期环境变量：.env 会被 load_dotenv（llm.py import 期
      执行）全量灌进 os.environ，除 import 期已固化的常量（generate._ABLATION、
      llm.BASE_URL）外，运行期一律不得残留仓库配置泄漏；
    - MATHFORGE_CACHE_DIR → per-test 临时目录：LLM 缓存读写与仓库 cache/llm 完全
      隔离（llm.py 的 _cache_path() 每次调用时读该变量，无模块级状态泄漏）；
    - MATHFORGE_DEMO=1：测试默认演示模式，缓存未命中直接报错、绝不静默出网；
      个别测试需非 demo 路径的，用 monkeypatch.setenv("MATHFORGE_DEMO", "0") 显式覆盖
      （如 test_v2api 的非 demo 降级用例），测完自动还原；
    - 删除 DEEPSEEK_API_KEY：v2/router、v2/v2api 等处用该变量判断「在线态」，
      测试内一律视为无 key（假依赖注入，真实 key 只存在于 .env / 开发者进程）。
    """
    # 1) 先清扫（setenv 必须放在清扫之后，否则自身也会被清掉）
    for _k in [k for k in os.environ
               if k.startswith("MATHFORGE_") and k not in _SWEEP_EXEMPT]:
        monkeypatch.delenv(_k, raising=False)
    # 2) 再落密封值
    monkeypatch.setenv("MATHFORGE_CACHE_DIR", str(tmp_path / "llm_cache"))
    monkeypatch.setenv("MATHFORGE_DEMO", "1")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
