# Changelog

## 2026-08-30 · 夜间全量构建（T0-T12，执行：Zcode）

### Added
- **T0** 环境自检：依赖就绪（fastapi/uvicorn/sympy/openai/httpx/pymupdf/pytest/mcp），`.env`+`.env.example`（key 隔离验证）
- **T1** `verify.py`：`check_answer`（simplify 差值判等，异常 False）/ `gen_parametric` / `is_valid_expr`；tests 10/10
- **T2** `skills/math-examiner/SKILL.md`：出题专家 skill（命题七步/难度三档硬约束/参数扰动/功能性干扰项/答案契约/输出 JSON 契约），双轨调研=128 题语料实证+命题文献
- **T3** cxyonly 题集：API 直连抓取 1616 题撒点 → 分层规范 128 题（高数60/线代40/概率25/真题3；主观83/单选45）；题集不入 git（D015 纪律）
- **T4** `ingest.py` + `llm.py`：讲义→知识点结构化 JSON（LaTeX 保真/前置链/sympy_checks 保留）；LLM 客户端全量磁盘缓存=演示零 API 基础
- **T5** `vlm.py`：deepseek-v4-flash-vision-exp 扫描讲义转写（两页实测合格，公式还原~100%/旁注~2/3），`ingest_image` 前置通道
- **T6** `generate.py` 出题引擎：skill+真题 few-shot+双级验证（绿：SymPy 构造+盲解对账+前提审查+渲染守卫 / 黄：双盲自洽）+解析实例化；中值定理 5 题 DoD
- **T7** `policy.py`+`db.py`：P1-P6 命中即执行（P6 兜底 LLM 解释）；记忆三表（半衰期 7 天）；场景 A 断言（P4 降档确定性+P5 反向对照）
- **T8** `app.py`+`attribute.py`：FastAPI 四端点+策略台决策时间线；错题归因四类 few-shot；API 实况证据（P6/P3 记忆驱动序列）
- **T9** `eval.py`：四指标评测器（可复跑）；R1 报告（构成矩阵+失败归因）
- **T10** R2 修订：对账公平性（canon 保留括号）+黄标毒缓存绕行+排序序列校验；R2 对比报告
- **T11** `mcp_server.py`：MCP 三工具（generate_quiz/get_mastery/diagnose），真实 stdio 客户端实测
- **T12** README/CHANGELOG/终报 status-mf.md

### Fixed
- tests 骨架包导入路径 off-by-one（conftest.py 兜底）
- `_canon` 剥括号误杀 `Rational(1,3)`（R2-1）；sqrt3 记法修复；毒缓存投毒（命名空间带版本+attempt）
- 零参数绿标冒充"构造即正确"→ 强制绿标参数化（架构缺口修补）
- MCP SDK 2.x API 适配（FastMCP→MCPServer）

### Known Issues
见 KNOWN_ISSUES.md（K1-K8），核心：拉格朗日 ξ 参数化模板为生成弱区（靠拦截链防流出）。
