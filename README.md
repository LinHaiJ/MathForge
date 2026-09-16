# MathForge · 记忆驱动的数学训练 Agent

> **🌐 在线体验（V2 学生端，免部署直开）：<https://mathforge.app.workbuddy.host/ui2/>**
> 确定性模板族 23 个 kp 零 API 零等待（SymPy 构造即正确）；其余考点由 DeepSeek 实时出题（首次约 3-10s）。无 key 自动退回演示模式，只读缓存。

> 做错一道题，先判出你为什么错（概念混淆/计算失误/方法选错/审题错误），再生成一道**答案经数学验证**的变式题，盯着这个错因练到会。
> 不是题库检索，不是搜题答案机——每道 AI 题过 SymPy 验证才外推，调度规则全落盘可回放。

三层分工（回答"为什么是 Agent"）：**规则管调度 / LLM 管生成理解 / SymPy 管验证**。
策略由记忆状态驱动（非随机）；每道题过对应等级验证才外推；策略决策全部落盘可回放。

## 架构（2026-09-12 目录重组后）

```
讲义(MD/扫描页) ──► v1/ingest.py ──► 知识点JSON(前置DAG)
   ▲(VLM: v1/vlm.py)        │
                            ▼
┌─────────── 记忆系统 mathforge.db (SQLite 五表) ───────────┐
│ v1 三表: mastery / mistakes / policy_log                  │
│ v2 两表: attempt_events / patterns（上线主线）             │
└────────────────────┬──────────────────────────┘
                     ▼
        v2/policy2.py 决策 P1-P6（命中即执行，理由落盘）
     P1前置切换 │ P2首触基础×2 │ P3概念混淆 │ P4计算失误降档 │ P5连对升档 │ P6兜底+LLM解释
                     ▼
     共用出题链 generate.py（skills/math-examiner + 真题few-shot锚）
     🟢绿标: 参数化模板族→SymPy构造答案→盲解对账+前提审查+渲染守卫
     🟡黄标: 独立双盲自洽(两次盲做一致才通过)
                     ▼
     attribute.py 归因(四类+未归因) ──► 记忆回写 ──► 下一轮决策
```

## 目录（2026-09-12 重组：纯v1 / 纯v2 / 共用 三分）

| 路径 | 内容 |
|---|---|
| v2/ | **上线主线**：v2api（/v2 全端点）、mem2（事件流记忆投影）、policy2（P1-P6）、assembler/router/v2intent/pack_loader |
| packs/ | 科目包（calculus·linear·probability：kp_graph/strategy/families/SKILL），**确定性族 23 个 kp 零 API**（16 族，详见各族 docstring） |
| ui2/ | v2 学生界面（今日练习/足迹统计 两页 + 拍照识别入口） |
| 共用（根） | app.py（唯一装配点）、generate/families/verify/attribute/llm（出题·判分·归因·LLM） |
| v1/ | 演示/叙事线：db/policy/review/bank/ingest/vlm、ui/（练习界面+auto-demo 播放器）、mcp_server、eval、demo_scenarios |
| tests/ + conftest.py | pytest（756 用例全绿，2026-09-17 实测） |
| scripts/ | 评测/蒸馏/预演辅助（S0-S6 产出在 cache/，gitignored） |
| docs/ | 变更说明与工作日志（docs/2026-09-12_夜间优化/ 最新） |
| 证据/ | 全部实测证据（ingest/出题/评测/API/MCP/VLM） |

## 启动

```bash
pip install fastapi uvicorn sympy openai httpx pymupdf pytest python-dotenv mcp python-multipart
cp .env.example .env          # 填入 DEEPSEEK_API_KEY（仅发往 api.deepseek.com）
uvicorn app:app --port 8127   # FastAPI：v1 端点 + /v2 全端点；/ui（v1 演示）与 /ui2（v2 学生端）
python v1/mcp_server.py       # MCP stdio 服务端（可选）
python v1/demo_scenarios/scene_a.py   # 演示场景A：连错2次→P4降难度（纯本地断言）
python v1/eval.py R1          # 四指标评测（可复跑；缓存已预热则零 API）
```

**学生端入口：`http://127.0.0.1:8127/ui2/`**（今日练习 + 足迹统计）；线上版见顶部链接。
**演示模式零 API**：所有 LLM 调用全量落 `cache/llm/`；设 `MATHFORGE_DEMO=1` 后只读缓存、
缓存未命中直接报错（不静默降级）——录制演示与 API 状态解耦。
**拍照识别（可选）**：另起识别服务（WorkBuddy `mathforge_ingest.service`，端口 8600）后，
练习卡内「拍照识别答案」可用；未启动时优雅降级为键盘输入。

## 口径（TEAM.md §四 v3.2）

评测集 20 道评测用例 │ **六轮评测演化（R1-R6）：最终 M1 出题成功率 90%（CI 0.70-0.97，超额达标）**
│ M2 黄标自洽 100%（12/12）│ M3 归因：目标 ≥70%，**实测真人 GT 68%**（R3 修订后；边界题本质模糊，
产品以 attribution_override 人工修正闭环兜底，LLM 不可用时记「未归因」不硬猜）│ R5 数学语义终审 17/18
│ R6 扩样轨道 n=40=77.5% 另披露 │ 讲义扫描样本 n=3 待扩样

## 差异化（竞品基线 · 详见 证据/竞品能力摸底.md，含 [未验证] 纪律）

- **横向空白**：主流产品核心均无「生成式 AI 出题」——夸克扫描王/小猿搜题/作业帮三条主线锚定 K12，
  其"举一反三"为题库检索与同类题推荐；唯一覆盖考研的粉笔，"个性出题"实为题库智能组卷。
- **纵向空白**：K12 三强不覆盖考研成人生自学；「考研数学 × 生成式变式训练闭环」在受检产品中明确空缺。
- **护城河判断**：单点生成能力会商品化；**验证链（SymPy 构造背书+双盲自洽+拦截链零泄漏）、
  事件流架构（append-only 事实源+投影可重算+策略可回放）、记忆×策略联动**做进架构而非功能点缀。
- 口径纪律：商业产品能力边界以官方口径从严处理，自媒体文案与官方冲突一律 [未验证]。

## 诚实边界

- M1 出题成功率 90% 部分来自确定性模板族路由（族覆盖率口径在各轮报告显式披露）；概率域已有 2 个确定性族（rv_func/mle_exp），弱区 K12（柯西/泰勒/多事件概率）登记待扩族
- 归因真人 GT 口径 68% < 70% 验收线：差异全在本质边界题，LLM 不可用时记「未归因」不硬猜，attribution_override 修正闭环兜底，标注扩样 50+ 登记 roadmap
- 证明题不出（无验证手段）；数二数三不做；无用户系统/在线部署（多用户需每人一库，机制已备 MATHFORGE_V2_DB）；半衰期 7 天为经验参数 [待校准]
- 拍照识别依赖可选 sidecar，手写识别是全链路最弱一环（错误必被标记，不保证全对）
