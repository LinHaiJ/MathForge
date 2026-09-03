# MathForge V2 Packs 契约（v0.1 · D024/D025/D026 定案）
# 设计共识附录 A-E 为本契约源头；本文件是 loader 实现契约，两者冲突以本文件+loader 为准。

## 目录结构
packs/<subject_id>/          科目级功能包（subject_id: calculus|linear|probability|...）
  pack.json                  manifest（必填）
  kp_graph.json              知识点 DAG（数组，必填；空数组=空包）
  qtypes.json                题型规格蒸馏产物（数组；蒸馏暂缓 → 可缺失/空）
  examiner/SKILL.md          出题专家·本域段（≤40 行，可缺失 → 只用 core）
  families/*.py              确定性模板族（可选，动态加载，构造即正确）
  macros.json                输入宏表（可选）
  strategy.json              策略覆写（可选，与 loader.DEFAULT_STRATEGY 深合并）
skills/_core/examiner-core.md  全局契约单点（必填；所有包共用，≤60 行）
exams/<exam>.json            考试组合档案（包组合+权重+真题分值结构）

## pack.json 必填字段
id / name / version / entry{syllabus, examiner} ；可选 entry{qtypes,families,macros,strategy}；
verify{engine:"sympy",pack_validators:[]}；maturity:"confirmed"|"experimental"（概率包=experimental）。
准入硬门槛：包内每个 kp 必须可符号验证（kp.verifiable=true），否则只能黄标/人工。

## kp 节点字段（kp_graph.json 数组元素）
id(命名空间 subject.prefix.*) / name / section / level(大纲层级) / parents[](前置依赖 DAG)
/ verifiable(bool) / qtypes[](fill|solution) / difficulty_floor(basic|mid)
/ exam_freq(low|mid|high) / typical_forms[] / pitfalls[]

## 事件流/投影契约（mem2 层，T2 交付）
attempt_events 表（append-only）：id, ts(unix), day(YYYY-MM-DD), sim(0/1), pack, kp, qtype,
  mode(fill|solution|selfassess|sim), result(correct|wrong|partial), attribution(json: type/conf),
  user_override, policy_snapshot(json), meta(json)
投影（离线可重算，不落真相）：mastery(pack,kp)=Σw_i·s_i/Σw_i，w_i=0.5^(age_days/7)，
  取最近 N=5 事件（strategy.mastery.window），s: correct=1/partial=0.5/wrong=0；
  mistakes(pack,kp) 最近错题+归因+修正；patterns 表占位（蒸馏器 TBD）。
sim 数据写独立库 mathforge_sim.db（绝不污染 mathforge.db）。

## 策略契约（policy2 层，T3 交付）
loader.DEFAULT_STRATEGY（全局默认）+ pack strategy.json 深合并 → decide(state, strategy)。
P1-P6 语义继承 v1 policy.py；阈值全部参数化；decide 输出落 policy_log（可回放）。
上下文组装器 ≤4k 预算；decide 必须收到 prerequisites/first_contact（修 v1 P1 死代码）。

## 红线（执行期全程有效）
1. 不动 cache/、题集/（D015）；不动 .env；真题原文永不入公开仓库。
2. v1 文件（db.py/policy.py/app.py/generate.py/verify.py 等）只读不改；
   v2 新增模块 mem2.py/policy2.py/assembler.py/router.py；接口切换在 T5 集成时统一做。
3. 蒸馏线（S0-S6、qtypes 填充）冻结中，不执行。
4. 每次 commit 绿色（pytest 不回归已测模块）；测试补进 tests/。
