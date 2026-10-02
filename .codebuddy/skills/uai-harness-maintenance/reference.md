# Harness 文档与 SKILL 维护规范（详细档案）

> 本文件是 SKILL `uai-harness-maintenance` 的详细参考。**日常操作以 SKILL.md 为准**；
> 本文件提供逐项通过条件、数据结构、变更日志模板与常见反模式。
> 最后核对：2026-10-02。

---

## 1. 五子系统逐项通过条件（对应 validate-harness 的检查项）

结构审计工具只认「结构化行」（标题 / 列表 / 表格 / 加粗引导语 / 代码块），
不认散落在自然段里的关键词——这是为了防止「堆关键词刷分」。据此，各文件必须**在结构位置**出现下列要素：

### instructions（`AGENTS.md`）

| 检查项 | 通过条件 | 本项目落点 |
|---|---|---|
| 指令文件存在 | 根有 `AGENTS.md` 或 `CLAUDE.md` | ✅ `AGENTS.md` |
| 启动流程已记录 | 结构中出现 `Startup Workflow` 或 `Before writing code` | ✅ 两个标题都有 |
| 完成定义已记录 | 结构中出现 `Definition of Done` 或 `done only when` | ✅ `## Definition of Done`（含 `done only when`） |
| 验证命令可发现 | 结构中出现 `./init.sh` / `test` / `verify` | ✅ `## Verification Commands` |
| 状态文件已路由 | 结构中出现 `feature_list.json` 或 `progress.md` | ✅ `## State Artifacts` 表 |

### state

| 检查项 | 通过条件 | 落点 |
|---|---|---|
| feature 追踪存在且合法 | `feature_list.json` 为 JSON，且 `features[]` 每项含 string 型 `id/name/description/status` | ✅ 22 项 |
| 进度日志存在 | 根有 `progress.md` | ✅ |
| 进度日志支持重启 | `progress.md` 结构含 `Current State` / `What` / `Next` | ✅ |
| 交接含阻塞/文件/下一步 | `session-handoff.md` 结构含 `Blockers` / `Files` / `Next Session` | ✅ |

### verification

| 检查项 | 通过条件 | 落点 |
|---|---|---|
| 验证入口存在 | 根有 `init.sh` | ✅ |
| 失败即停 | `init.sh` 含 `set -e` | ✅ `set -euo pipefail` |
| 测试命令已记录 | `init.sh` 或 `AGENTS.md` 含 `pytest` 等 | ✅ |
| 静态/构建检查已记录 | 含 `build` / `lint` / `compile` | ✅ `ruff` |
| 验证证据被记录 | 任一 harness 文件含 `Evidence` / `command and output` | ✅ `Verification Evidence` |

### scope

| 检查项 | 通过条件 | 落点 |
|---|---|---|
| 一次一个 feature | 结构含 `One feature at a time` / `one-feature-at-a-time` | ✅ |
| 依赖被追踪 | `feature_list.json` 含 `dependencies` | ✅ 每项都有 |
| 状态显式 | `AGENTS.md` 或 feature 列表含 `status` | ✅ `status_legend` |
| 范围边界已记录 | 结构含 `Stay in scope` / `scope` | ✅ |
| 完成门限限制收口 | 结构含 `Definition of Done` | ✅ |

### lifecycle

| 检查项 | 通过条件 | 落点 |
|---|---|---|
| 启动脚本存在 | 根有 `init.sh` | ✅ |
| 收尾流程存在 | `AGENTS.md` 含 `End of Session` / `Before ending` | ✅ |
| 交接模板存在 | 根有 `session-handoff.md` | ✅ |
| 重启标记存在 | `progress.md` 或 `session-handoff.md` 结构含 `Last Updated` / `Current Objective` / `Recommended Next Step` | ✅ 两处都有 |
| 干净重启路径已记录 | `AGENTS.md` 或 `init.sh` 含 `restartable` / `clean` / `Next steps` | ✅ |

> **计分**：每个子系统 5 项，满分 25；`overall = total/25*100`。低于 `--min-score`（默认 70）时退出码非零。
> 本项目基线（2026-10-02 改造前）为 **24/100**，瓶颈为 `state`。

---

## 2. 状态文件数据结构

### `feature_list.json`

```jsonc
{
  "schema_version": "1.0",
  "project": "…",
  "updated_at": "YYYY-MM-DD",
  "head_commit": "short-sha",          // 记录写入时的 HEAD，便于比对漂移
  "branch": "…",
  "status_legend": { "done": "…", "in_progress": "…", "blocked": "…", "planned": "…", "deferred": "…" },
  "authoritative_sources": { "metrics": "…", "evidence": "…", "current_state": "progress.md" },
  "features": [
    {
      "id": "kebab-case-唯一",          // 必填 string
      "name": "中文名",                 // 必填 string
      "description": "一句话说明",       // 必填 string
      "status": "done|in_progress|blocked|planned|deferred", // 必填 string
      "gate": "门控值（可选）",
      "result": "实测值（可选）",
      "dependencies": ["其它 feature id"],  // 必填（可为空数组）
      "evidence": "报告路径 / commit / 测试路径"
    }
  ]
}
```

**约束**：`in_progress` 至多一个；`done` 必须有 `evidence`；`blocked` 必须能一句话说清解除条件。

### `progress.md` 必备小节

`## Last Updated`、`## Current Objective`、`## Current State`（分 `What 已完成` / `What 未完成`）、
`## Recommended Next Step`、`## Blockers`、`## 证据与报告索引`、`## Files`、`## Next`。

### `session-handoff.md` 必备小节

`## Last Updated`、`## Current Objective`、`## Snapshot`、`## Blockers`、`## Files`、`## Next Session`、`## Restart Markers`。

---

## 3. 更新纪律（Do / Don't）

| Do | Don't |
|---|---|
| 用 `path:行号` 或报告文件名引用事实 | 凭记忆写数字 |
| 只追加 `16` 号附录 A（A.N 递增） | 重写/删除历史结论 |
| 覆盖旧结论时新增更正条目并标「已作废」 | 静默改数字 |
| 一个事实写一处，别处写指针 | 同一事实多处各写一份 |
| 状态变更立即落 `feature_list.json` | 把状态留在聊天里 |
| SKILL 写流程 | SKILL 里堆项目事实/数字 |
| 改 harness 后跑审计 | 改完不验证 |

---

## 4. 变更日志模板（追加到 `16` 号附录 A）

```markdown
### A.<N> <一句话主题>（YYYY-MM-DD）

**背景 / 触发**：为什么做这件事（用户质询 / 门控未达标 / 立项推进）。

**做了什么**：改动清单（文件:行号 或 commit）。

**实测结果**：
| 指标 | 实测 | 门控 | 判定 |
|---|---|---|---|

**归因与结论**：诚实写清失败/负结果；禁止粉饰。

**本轮新钉子**：新踩的坑（若无可省略）。
```

---

## 5. 常见反模式（本项目真实教训）

| 反模式 | 后果 | 正确做法 |
|---|---|---|
| 状态只在聊天里 | 下会话从零开始 / 重复劳动 | 落 `progress.md` + `feature_list.json` |
| 「已完成」无证据 | 文档方向性错误（旧文档曾断言 `app/rag/` 为空） | 给 `path:行号` 或标「待验证」 |
| 记忆与检测并存 | 同一个数字在两处漂移 | 单一事实源 + 指针 |
| SKILL 里写满数字 | 数字一变 SKILL 就过期 | 数字指向 `16` 号，SKILL 只写流程 |
| 全量重写指令文件 | 丢失既有约定、Agent 反复适应 | 定点 `replace_in_file` 增量修订 |
| 未跑审计就收尾 | harness 腐烂（改造前 24/100） | 收尾跑 `validate-harness`，≥70 |
| 删 `.codebuddy` / 历史归档 | 破坏项目数据与可追溯性 | 只读不改；确需清理先向用户说明 |

---

## 6. 与其它文档目录的关系

| 目录 | 内容 | 维护方 |
|---|---|---|
| `.codebuddy/skills/` | 可复用 Agent 流程（本技能 + `remote-gpu-server-devtest`） | 本规范 |
| `docs-for-guiding generating docs/` | 项目设计/计划/规范/进展文档集（含 00 口径、16 证据） | 项目文档纪律 |
| `docs/` | 技术文档(SRS/SAD)、合规、运维、注册材料 | 对应领域 |
| `.codebuddy/plans/` | 历史会话计划（只读归档） | 不改 |
| `.trae/documents/`、`archive_of_achivement/` | 更早期历史归档 | 只读，勿删 |
| `harness_engineering_guide-main/` | 外部参考书（Harness 工程指南），非本项目产物 | 只读参考 |
