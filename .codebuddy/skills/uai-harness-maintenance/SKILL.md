---
name: uai-harness-maintenance
description: 维护本项目（超声诊疗决策支持 AI 系统）的 harness 文档与 SKILL——即 AGENTS.md、progress.md、feature_list.json、session-handoff.md、init.sh，以及 .codebuddy/skills/** 下的技能。当需要更新项目进展/状态、沉淀踩坑与禁则、新增或修订技能、执行 harness 审计（validate-harness）、或不确定「这个事实该写进哪个文件」时使用。含五子系统检查表、单一事实源规则、证据纪律、更新触发条件与审计命令。
---

# Harness 文档与 SKILL 维护规范

## 何时用本技能

- 一轮工作收尾，需要同步 `progress.md` / `feature_list.json` / `session-handoff.md`。
- 出现新踩坑、新的「禁止再犯」条目、新的可复用流程，需要沉淀。
- 新增/修订 `.codebuddy/skills/**` 技能。
- 需要跑 harness 结构审计，或排查「Agent 为何总跑偏/忘上下文」。

## 本项目的 harness 结构（五子系统）

| 子系统 | 文件 | 职责 | 唯一性规则 |
|---|---|---|---|
| Instructions | `AGENTS.md` | 路由 + 不变量 + DoD + 硬规则 | 保持短；细节一律外链 |
| State | `feature_list.json` / `progress.md` | 功能与门控状态 / 当前状态与下一步 | 状态不写进聊天，写进这里 |
| Verification | `init.sh` + `Makefile` | fail-fast 验证入口 | 命令必须可直接复制运行 |
| Scope | `feature_list.json.dependencies` + AGENTS.md 的 DoD | 防越界、防半成品 | 同类 feature 同一时刻只允许一个 `in_progress` |
| Lifecycle | `session-handoff.md` + AGENTS.md 的 End of Session | 让下会话 clean restart | 六项（Last Updated/Current Objective/Recommended Next Step/Blockers/Files/Next Session）必填 |

## 维护工作流（可复用）

1. **取事实**：只从代码/测试/实测报告取事实（`path:行号` 或报告文件名 + 数字）。**不从旧文档或记忆里复制数字**。
2. **落位**：按「事实该写在哪」表决定目标文件（见下），一次只写一处。
3. **校验**：跑 `./init.sh`（或 `--skip-tests`）+ harness 审计脚本；两项都过才算完成。
4. **留痕**：`progress.md` 刷新 Last Updated / Current State / Recommended Next Step；必要时追加 `16` 号附录 A。

## 事实该写在哪（避免重复与漂移）

| 事实类型 | 唯一落点 |
|---|---|
| 指标口径（门控值、定义） | `00-项目总览与核心指标.md` |
| 实测数字、评测报告、口径决策 | `16-开发进展汇报-20260928.md` 附录 A（**只追加**） |
| 当前目标 / 下一步 / 阻塞 | `progress.md` |
| 功能与门控状态 / 依赖 / 证据指针 | `feature_list.json` |
| 服务器/真机操作与踩坑 | SKILL `remote-gpu-server-devtest` |
| harness 与 SKILL 维护规则 | 本技能 |
| 应遵守的硬规则（禁则） | `AGENTS.md`「硬规则」表 |

> 冲突时以靠前者为准：**代码与测试 > 00 号口径 > 16 号证据 > harness 状态文件 > 其它文档**。

## 硬性原则

- **单一事实源**：同一事实不在两处各写一份；要引用就写指针。
- **只追加不重写**：`16` 号附录按 A.N 递增；结论被推翻时新增更正条目 + 就地标「已作废」，不删历史。
- **证据纪律**：无 `path:行号` 或实测数字的断言，一律标「待验证」；禁止「已完成/已优化/基本可用」。
- **技能只装流程，不装事实**：SKILL 说「怎么查/怎么做」，数字指向 `16` 号。
- **禁止目录**：不得删 `.codebuddy/`（项目数据）、`.trae/`、`archive_of_achivement/`（历史归档）。
- **小步**：`AGENTS.md` 只增不改结构；改状态文件用 `replace_in_file` 定点更新，别整文件重写。

## 更新触发条件（什么时候必须动哪个文件）

| 触发 | 必须更新 |
|---|---|
| 完成一个 feature（DoD 满足） | `feature_list.json`（→done + evidence）、`progress.md` |
| 出现实测数字 / 口径决策 | `16` 号附录 A（追加）+ `progress.md` 证据索引 |
| 新增门控或改口径 | `00` 号（唯一口径源）+ `feature_list.json` |
| 发现新踩坑 / 新禁则 | 真机类 → `remote-gpu-server-devtest`；通用硬规则 → `AGENTS.md` 硬规则表 |
| 新增可复用流程 | 新建/更新 SKILL（`.codebuddy/skills/<name>/SKILL.md`） |
| 会话结束 / 上下文将满 | `session-handoff.md` + `progress.md` |

## 审计与自检命令

```bash
# harness 五子系统结构审计（分数不得低于 70；满分 100）
node ~/.codebuddy/skills/harness-creator/scripts/validate-harness.mjs --target .

# 生成 HTML 体检报告（便于人审）
node ~/.codebuddy/skills/harness-creator/scripts/validate-harness.mjs --target . --html harness_assessment.html

# 统一验证入口
./init.sh            # 快检（状态文件自检 + 文档质量守卫 + ruff）
./init.sh --full     # 全量 make test
```

## SKILL 编写规范（新建/修订技能时）

- 目录：`.codebuddy/skills/<kebab-name>/{SKILL.md, reference.md}`。
- `SKILL.md` frontmatter 必含 `name`（= 目录名）与 `description`（写清**何时用**与**触发词**）。
- 正文：先「何时用」，再「铁律/先读」，再「标准流程」，最后「细节指向 reference.md」。
- 单文件不超过 ~200 行；长档案放 `reference.md`（历史叙事、逐条踩坑）。
- 修订 SKILL 时**只更新受影响条目**；已过时条目就地标「已过期（日期）+ 现行做法」。

## 详细规范

- 五子系统逐项通过条件、状态文件 schema、变更日志模板、常见反模式：
  [reference.md](reference.md)
- 项目现状与进展总览：`progress.md` 与 `docs-for-guiding generating docs/超声诊疗决策支持系统/16-开发进展汇报-20260928.md`
