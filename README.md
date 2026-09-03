# MathForge · 考研数学练习 Agent

> 生成式变式出题 + 事件流记忆 + 数据化复习策略——一个能自己出题、记住你会什么、安排你练什么的考研数学练习系统。
> A self-contained math practice agent for China's postgraduate entrance exam (考研数学一): generates concept-variant exercises, remembers what you know via an append-only event stream, and schedules review with data-driven policies.

[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

**在线演示 Demo**: https://cf118363b3114626802bb31405a83f4e.app.workbuddy.link/ui2/ （部署形态：确定性模板族零 API + DeepSeek 出题）

## 简介 / Introduction

MathForge 是作者求职作品集中的"从零构建的垂直 Agent"：把"考研数学出题 → 作答判分 → 错因归因 → 记忆 → 复习安排"做成一条**可解释、可评测**的闭环。核心思路是不依赖大模型"碰运气"——**确定性模板族用 SymPy 构造即正确**，LLM 只承担无法符号化的结构选择，每道题都过符号验证链。

## 特性 / Features

| Feature | Description |
|:--------|:------------|
| 确定性模板族变式 | SymPy 参数化构造，答案"构造即正确"；同族换参即变式，全部带溯源区 |
| 事件流记忆层 | append-only 事件流为唯一事实源；掌握度 = 最近 N=5 事件加权投影，可离线重算 |
| 数据化复习策略 | P1-P6 规则阈值化（全局默认 + 科目包覆写），每步决策落日志可回放 |
| 两段式路由 | 意图/知识点 → 科目包 → 题型族；包即插拔（高数/线代/概率） |
| 符号验证链 | 填空 SymPy 判分；解答题三档自评（机器不判步骤分）；错误归因四分类 + 人工修正 |
| 完整评测纪律 | 每轮指标带样本量与 Wilson CI；口径变更显式声明，禁止跨版本拼数字 |

## 快速开始 / Quick Start

```bash
# 1) 依赖（Windows/Linux 均可，Python ≥3.10）
pip install fastapi uvicorn sympy openai python-multipart

# 2) 起服务（演示模式：确定性族出题，零 API）
MATHFORGE_DEMO=1 python -m uvicorn app:app --host 0.0.0.0 --port 8127

# 3) 浏览器打开
#    http://127.0.0.1:8127/ui2/   ← 两页 UI（今日练习 / 足迹统计）
#    http://127.0.0.1:8127/ui/    ← v1 演示页
```

带 DeepSeek key 的真实出题：在项目根放 `.env`（`DEEPSEEK_API_KEY=sk-...`），去掉 `MATHFORGE_DEMO=1` 重启即可（应用自带磁盘缓存，`cache/llm/`）。

## 为什么做这个 / Why

面试叙事落点：主流 Agent 的"记忆 / 计划 / 评测"能力在通用 Agent 上是口号，在封闭垂直场景里可以被**符号系统 + 事件流 + 规则策略**真正落地并量化。MathForge 用考研数学这个封闭领域把整套机制做实：评测不是"感觉变好了"，而是每一轮都有 n、CI、口径声明（详见 [ARCHITECTURE.md](ARCHITECTURE.md) 的评测章节）。

## 文档 / Documentation

- [架构设计 ARCHITECTURE.md](ARCHITECTURE.md)
- [科目包契约 packs/CONTRACT.md](packs/CONTRACT.md)

## 贡献 / Contributing

PRs and ideas are welcome. 数学模板族（`packs/*/families/*.py`）是最容易入手的入口：看懂一个 `integral_byparts.py` 就能照着加新族。

## 合规说明

- 原始题库（cxyonly 语料）仅存本地、不进本仓库（版权红线）；仓库内只有结构规格、原创参数与样例。
- 交互形态参考 MIT 项目 [summer-checkin](https://github.com/gdut4140/summer-checkin)（信息架构层面借鉴，未使用其代码）。

## 许可证 / License

MIT © 2026 LinHaiJ. See [LICENSE](LICENSE).
