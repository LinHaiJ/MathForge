# 盲评工具（模块 K）· 三步用法

> 目的：验证「AI 变式是否像出题团队出的题」。每道真题配两个变式——**AI 变式**（生产绿标验证链生成）
> 与**同源换数变式**（确定性族对照，无族考点如实降级为第二道 AI 变式并在 manifest 标注），
> 左右位置构建期随机。你做三件事：①指认哪边是 AI（2AFC）；②五维 rubric 盲打；③揭盲后写破绽备注。

## 第 1 步 · dry-run（零 API，看计划）

```bash
python scripts/build_blind_eval.py --kps "参数方程求导,罗尔定理,二重积分" --pairs 12
# 或从全部有锚考点里选：python scripts/build_blind_eval.py --all --pairs 30
```

打印：kp 清单（锚池真题数 / 可用原题数 / 对照类型 / 计划对数）、**预计 LLM 调用次数**与成本口径。
不调任何 API。`--all` 小额 pairs 优先排给锚多的考点。

## 第 2 步 · build（在线态，本人执行）

```bash
python scripts/build_blind_eval.py --kps "..." --pairs 12 --build
```

- **必须在线态**：检测到 `MATHFORGE_DEMO=1` 或无 `DEEPSEEK_API_KEY` 直接拒绝启动（exit 2），
  不静默降级——AI 变式需要真实调 LLM 走绿标链（LLM 参数化模板 → SymPy 构造 → 盲解对账 → 解析实例化）。
- 幂等：每次 build 生成新目录 `blind_eval/session_<时间戳>/`，绝不覆盖旧 session。
- 产物（全部在 session 目录内，**不写任何 db**）：

| 文件 | 内容 | 谁能看 |
|---|---|---|
| `blind.json` | 盲态题面：pair_id / kp / 母本+左右两变式的题干与选项 | 页面前端数据，**无答案/来源/左右归属** |
| `key.json` | 揭盲数据：每题 provenance(AI/family/ori)/答案/解析/ai_side/对照类型 | 整对提交后页面才拉取；评分复算用 |
| `manifest.json` | 构建档案：种子、逐 kp 配额、control_kind（含 ai_second 如实标注）、跳过清单 | 构建 | 
| `index.html` | 盲评页副本（与 `blind_eval/index.html` 同一文件） | 浏览器 |

## 第 3 步 · 打开页面评完导出

```bash
cd blind_eval/session_<时间戳>
python -m http.server 8139
# 浏览器打开 http://127.0.0.1:8139/index.html
```

（`file://` 双击打开会被浏览器拦截 fetch——页面会提示上面的命令；也可自行改端口。）

页面流程（进度存 localStorage，可中断续评，「重来」清空本 session 进度）：

1. **任务一（2AFC）**：读左/右两道变式（+可折叠的母本真题），指认「左边是 AI / 右边是 AI / 分不出」。
   （无族考点的对两道都可能是 AI——按直觉区分，结果页分桶统计。）
2. **任务二（rubric）**：对两道变式各打五维 1-5 分：题干表述规范 / 条件自洽 / 难度与考点匹配 /
   解析质量 / 命题专业度。**均为盲打**——此时看不到答案与解析。
3. **提交本对** → 打分锁定，揭盲对照展开（答案/解析/来源/指认对错）。标注：对照用，打分已在展开前完成。
   揭盲后写**破绽备注**（哪里像 AI / 露馅点），它直接进破绽清单。
4. 最后一对提交后点「查看结果」：即时区分率（识破正确数 / 决断对数，「分不出」单列不入分母）+
   **Wilson 95% 置信区间** + 五维均分对比（被指认为 AI vs 被指认为原题 vs 分不出）。
5. 导出：**结果 JSON**（全量原始作答）与**破绽清单 MD**（每道被识破的 AI 题：题目摘要/指认/备注）。

### 成本口径（build 前先看 dry-run 的报量）

每条 AI 变式约 2-4 次调用（出题 1 + 盲解对账 1±复核 1 + 解析实例化 1；失败重生成至多再翻一倍）；
包族对照 0 次；根族对照约 2-3 次；无族考点的第二道 AI 对照约 2-4 次。dry-run 给出总区间，
按你账号现价折算；缓存命中的调用零费用。

## 结果怎么读

- **区分率 vs 50% 基线**：左右随机下瞎猜=50%。结果页按 `control_kind` **分三桶**展示：
  **全量**（主口径）、**family**（AI vs 族换数，最干净的口径）、**ai_second**（双 AI，仅参考——
  区分的是两次生成的风格差异）。Wilson 95% 区间**下界 > 50%** → AI 变式可被稳定识破
  （有破绽，看破绽清单定位）；区间**覆盖 50%** → 暂无区分信号；上界 < 50% → AI 变式反而更像人出的题。
  决断对数 < 10 时区间很宽，只作方向性参考。
- **五维均分对比**：被指认为 AI 的题哪几个维度显著低，就是风格破绽所在（辅助读数，主指标是区分率）。
- **破绽清单**：被识破的 AI 题 + 你的备注 → 直接成为 SKILL/提示词下一批修改项。
- **无族考点注意**：manifest 里 `control_kind=ai_second` 的对，对照也是 AI 变式（2AFC 是 AI vs AI），
  解读时按 `key.json` 的 `control_kind` 分桶，不要混入主口径。
- 评完后可离线复算核对（零 API）：`python scripts/build_blind_eval.py --score <session>/results.json`

## 定案与边界

- **盲态分离**：`blind.json`（前端）绝不含 provenance/答案/解析/左右归属；`key.json` 由页面在整对
  提交后才拉取。本地工具的盲评是自觉盲（key.json 就在目录里，别提前打开）。
- **左右随机**：构建期用种子随机（manifest 记录 seed），页面不重排；记录只进 key.json。
- 题干按 LaTeX 源码展示（离线无渲染库）；git 纪律：session 数据不入 git
  （`.gitignore: blind_eval/*`），页面 `blind_eval/index.html` 与本 README 进 git。
- 测试零出网：`tests/test_blind_eval.py` 用假 LLM/monkeypatch 全链路验证组装与统计；
  真实 `--build` 永远由你本人执行。
