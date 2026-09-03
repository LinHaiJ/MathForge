# MathForge v2 · 四视图 UI（ui2）

v2 填空闭环（`/v2` API）的用户界面，四视图 + 填空表达式输入面板。纸感简洁、移动端友好（390px 无横向溢出），**仅依赖本地 KaTeX，无任何 CDN 外链**。

## 页面结构

顶栏：MathForge v2 标识 + 四视图 Tab（今日练习 / 今日复习 / 足迹 / 统计）+ 当日打卡徽标（连续天数由 localStorage 诚实本地统计；今日提交任一作答即记当日，并会回填服务端已有的今日作答）。

1. **今日练习**（默认）
   - `GET /v2/packs` → 科目包 chips（实验包标「·实验」）
   - 选包 → `GET /v2/packs/{id}` → kp chips（显示难度；在复习清单中者标「待复习」，由 `GET /v2/review` 提供）
   - 点 kp → `POST /v2/turn {kp_id, qtype:"fill", difficulty}` → 题卡（KaTeX 渲染 `statement_md`，**标准答案绝不显示**）
   - **填空表达式输入面板** → `POST /v2/answer {pack_id, kp, qtype, student_answer, standard_answer(= turn 的 answer_sympy), statement_md, analysis, difficulty}`
   - 结果区：对/错 + 归因卡 + 四个修正按钮（概念混淆/计算失误/方法选错/审题错误，带 `attribution_override` 重发）+ 决策区（P 规则与理由）+「下一题」
   - **解答题模式（大题）**：仅当 kp.qtypes 含 `solution` 时出现「解答题」切换；步骤编辑器（行号 + 逐行 KaTeX 预览），机器不判步骤分 → 学生对照折叠解析三档自评 `POST /v2/selfassess {pack_id, kp, qtype:"solution", student_steps, grade:会|部分会|不会, attribution?, statement_md, analysis, standard_answer}`，并可点「AI 分步归因（供参考）」`POST /v2/steps-attribute`（DEMO 无 key 时优雅降级，advisory 红线 D022）
2. **今日复习**：`GET /v2/review` → 列表（kp/掌握度条/最近错因/建议），每条「出题」切回练习视图并预填该 kp 出题。
3. **足迹**：`GET /v2/heatmap?days=180` → 52×7 手写网格热力图（色阶 count 0–5+；悬停/点击 tooltip；今日格描边高亮）。
4. **统计**：`GET /v2/stats` → 摘要框 + 掌握度条（decayed_value 0–1 色阶）+ 错因分布（计数自 `/v2/review` 最近错因）。

## 填空表达式输入面板（核心组件，见 `app.js`）

- a) 文本输入框（LaTeX 源）
- b) 符号面板：积分 ∫∬∭∮、算子 ∂∑∏lim∞、关系 =≠≤≥≈、逻辑 ∀∃∈⊂、希腊 αβγδελμπσφω、箭头 →⇒↔（点击在光标处插入 LaTeX 记法）
- c) 结构模板：`\frac{a}{b} \sqrt{x} x^{2} x_{1} \sum_{i=1}^{n} \int_{0}^{1} \lim_{x \to 0} \begin{pmatrix} \end{pmatrix} \begin{vmatrix} \end{vmatrix}` 等（插入后光标落可填位）
- d) 矩阵/行列式尺寸选择器：弹层设行 n、列 m（各 1–6，快速预设 2×2/3×3/2×3 等），类型切换 [矩阵 pmatrix｜行列式 vmatrix｜找规律省略号]；省略号模式生成含 `\vdots \ddots \cdots` 的抽象形式；确定后插入 LaTeX
- e) KaTeX 实时预览：输入 debounce 300ms 渲染（出错显示原文不崩）
- f) `student_answer` 提交即输入框 LaTeX 文本

## 依赖

- 仅本地 KaTeX：`/ui/vendor/katex/`（字体 woff2 本地）。`ui2/index.html` 通过绝对路径 `/ui/vendor/katex/...` 引用，**不联网**。
- 后端端点：`v2api.py`（已含 `GET /v2/heatmap`）。前端不引入任何第三方 JS 库。

## 如何起服务验收

```bash
# 1) 起服务（演示模式强制零 API；端口随意，示例 8191）
set MATHFORGE_DEMO=1
D:/Python/Python312/python.exe -m uvicorn app:app --port 8191

# 2) 冒烟（浏览器或 urllib）
#   GET  http://127.0.0.1:8191/ui2             → 200，含四 tab（#tab-practice 等）
#   GET  http://127.0.0.1:8191/v2/heatmap?days=7   → JSON 数组
#   POST http://127.0.0.1:8191/v2/turn  {"kp_id":"calc.rolle","qtype":"fill","difficulty":"基础"} → 拿题
```

## 文件清单

- `ui2/index.html` — 页面骨架（四视图 + 面板容器 + 本地 KaTeX 引用）
- `ui2/style.css` — 纸感主题、响应式（390 无横向溢出）
- `ui2/app.js` — 全部交互逻辑 + 填空表达式输入面板组件（含注释）
- `ui2/README.md` — 本文档

## 改动边界（红线）

- 后端仅新增 `v2api.py` 的 `GET /v2/heatmap` 端点，`app.py` 末尾新增一行 `/ui2` 挂载；v1 端点零改动。
- 真实库 `mathforge.db` 经测试隔离（`MATHFORGE_V2_DB` 临时库）全程不写。
