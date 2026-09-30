"""MathForge FastAPI 服务：v2 记忆驱动训练闭环（/v2/*）+ 学生端 ui2 静态界面。

启动：uvicorn app:app --port 8141（部署入口 start_server.py 读 PORT 环境变量）。
MCP 工具入口在仓库根 mcp_server.py（stdio：python mcp_server.py）。
v1 已退役（2026-09-30，docs/2026-09-12_夜间优化/10 号计划）：v1 端点与 /ui 静态挂载移除，
db.py 迁入 v2/（v2/policy2 等 flat import 口径不变）。
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _PkgPath

# v2/ 目录注册进 sys.path：模块内保持 flat import（import db / import mem2），
# 物理目录分离与导入兼容解耦（v1 退役后本循环只注册 v2）。
for _d in ("v2",):
    _p = _PkgPath(__file__).resolve().parent / _d
    if _p.is_dir() and str(_p) not in _sys.path:
        _sys.path.insert(0, str(_p))

from pathlib import Path

from fastapi import FastAPI

app = FastAPI(title="MathForge", description="记忆驱动的数学训练 Agent")

# CORS（file:// 打开页面时也能调 API）——纯接入层，不改端点逻辑
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

from v2api import router as v2_router  # noqa: E402 —— v2 闭环接线层（填空/自评/归因/复盘）
# 每人一库：X-MF-Profile 档案路由在 v2api._ProfileRoute 内处理（路由级 contextvar，
# 不走 app 中间件——BaseHTTPMiddleware 的下游任务孵化时序会吞掉 contextvar）
app.include_router(v2_router, prefix="/v2")

# v2 自检端点（模块 D，借鉴 Claude Code /doctor 诊断屏）：配置来源/环境路径/警告一键汇总。
# doctor.py 是纯函数模块（不依赖 FastAPI，便于测试与脚本直调），路由在此直接 @app 注册
# （与 v2api 的 _ProfileRoute 档案路由无关——系统级诊断不随学生档案切库）。
# 红线：零 LLM 调用、零网络、只读；HTTP 恒 200，健康状态放 JSON 字段供监控脚本消费。
import doctor as _doctor  # noqa: E402


@app.get("/v2/doctor", tags=["v2"])
def api_v2_doctor():
    """一键体检：env / cache / database(含迁移状态) / packs / llm_entry / intents。

    run_doctor 内部任何意外异常都兜底为 {ok:false, error}——诊断端点绝不 500。
    """
    try:
        return _doctor.run_doctor()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"自检失败：{type(e).__name__}: {e}"}

# ui2/ 静态服务（v2 四视图界面 + 填空表达式输入面板）
# 前端改版后必须让浏览器拿到新资源：静态响应统一 no-cache，避免启发式缓存长期吃旧版
class _NoCacheStatic(StaticFiles):
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if resp.status_code == 200:
            resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp


_UI2_DIR = Path(__file__).resolve().parent / "ui2"
if _UI2_DIR.exists():
    app.mount("/ui2", _NoCacheStatic(directory=str(_UI2_DIR), html=True), name="ui2")


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def _root():
    """根路径直达学生端 ui2（此前 / 无路由 → 分享链接打开即 404）。"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/ui2/")
