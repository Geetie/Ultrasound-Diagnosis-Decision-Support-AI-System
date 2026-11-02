# Agent 作业认证指引（给审计方 / 换人接手用）

> 这份文件回答两个问题：**"这个 Agent 到底做了什么"** 和 **"我怎么独立认证他做的"**。
> 它是流程文档，不含项目事实——所有事实按 `AGENTS.md` 的权威次序落在代码、`00` 号、`16` 号附录 A 里。

## 0. 为什么需要它

本项目同时有多方在动同一棵工作树（真机部署方 / 审查方 / 文档方）。历史上出现过三次"审计方看不懂对面做了什么"：

- A.96 提交信息写「18 项」，实际只覆盖 15 项（A.99.5 已更正）；
- A.96.3 的「前端 45/45」是**指定 4 个文件**，被当成全量（A.98 自证）；
- A.104 之后工作区还躺着未提交的 `app/services/diagnosis_pipeline.py`（A.105）。

共同点：**结论写在聊天/提交信息里，判据没有落到可复跑的地方**。所以下面三层认证法是硬要求，不是礼貌。

## 1. 三层认证法（任何一条断言都按这个顺序核）

### 第 1 层：东西在不在远端（30 秒）

```bash
git fetch origin
git rev-parse --short HEAD origin/main     # 两个 SHA 必须相同
git status --porcelain                     # 只允许出现 .codebuddy/rules/（已 ignore）或空
git log --oneline origin/main -10          # 提交清单
git ls-remote origin refs/heads/main       # 与本地 origin/main 一致
```

判据：**本地 HEAD == origin/main**。若不等，后面所有"已完成"都不成立。

### 第 2 层：断言 → `path:行号`（可反查，不接受口头）

任何"已接线/已删除/已接通"的说法，必须在代码里指得到：

```bash
git show <sha> --stat                      # 这个提交到底动了哪些文件
git show <sha> -- <path>                   # 具体改了什么
git log -S '被删的符号名' --oneline         # 证明"删净"：搜不到才是删净
grep -rn '符号名' app/ frontend/src/        # 双向核对：读写两侧都在才算接线
```

判据：**说得出的每个结论都有 `file:line`；`git log -S` 搜不到才算删净。**

### 第 3 层：复跑（能跑的都跑，跑不了的标 🔬）

```bash
make harness-check                                  # harness 结构自检（本轮新增，见 §3）
pytest tests/unit/... -q                            # 具体到文件，别用"全量绿"含糊过去
cd frontend && npx vitest run <file> --reporter=basic
```

判据：**命令 + 输出一起贴**。分三类如实标注：

| 类别 | 例子 | 认证方式 |
|---|---|---|
| 可复现 | 单元/组件测试、`harness-check`、`grep` | 审计方本机实跑，结果应一致 |
| 需环境 | 全量 `make test`（7500 例）、前端全量套件 | 需项目 venv + 依赖栈（见 `requirements.txt`） |
| 🔬 不可复现 | 真机部署、GPU 服务、生产 DB、外部随访数据 | **只认台账**：`16` 号 A.104 这类现场记录 + 部署方签字，Agent 不得自证 |

## 2. 2026-11-03 这一轮做了什么（逐条可核）

**远端已同步**（2026-11-03 01:2x Asia/Shanghai）：`HEAD == origin/main == 8f5ab31`，工作区干净。

| # | 动作 | 提交 | 认证命令 |
|---|---|---|---|
| 1 | `self_rag_score` 落进 `findings.fusion_result`（`diagnosis_pipeline.py:2738`），补两条守卫 | `fa4a72d` | `git show <sha> -- app/services/diagnosis_pipeline.py`；`pytest tests/unit/services/test_pipeline_pure_functions.py -q` |
| 2 | 新增仓库内 harness 自检 `scripts/audit/p78_harness_selfcheck.py` + 11 条守卫 | `e83e81f` | `python scripts/audit/p78_harness_selfcheck.py`；`pytest tests/unit/quality/test_harness_selfcheck.py -q` |
| 3 | 状态文件追平（`progress.md` / `session-handoff.md` / `feature_list.json` 三条过期 `in_progress`） | `8f5ab31` | `git diff <prev>..<sha> -- progress.md session-handoff.md feature_list.json`；`python -c "import json;json.load(open('feature_list.json'))"` |
| 4 | 文档漂移修正（`00b` 手册列了已删的孤儿开关；台账 §二 停在 A.96 前） | `8f5ab31` | `grep -n 'rag_use_redis_cache' "docs-for-guiding generating docs/超声诊疗决策支持系统/00b-统一配置参考手册.md"`（应为 0） |
| 5 | `.gitignore` 收本机 IDE 授权规则（`.codebuddy/rules/`） | `8f5ab31` | `git status --porcelain`（应为空） |

> 编号以 `16` 号附录 A 的实际条目为准：本表里的 `A.105/A.106` 就是那两节。

## 3. `make harness-check` 判什么（机器判据，不靠人记）

| 检查项 | 级别 | 判据 |
|---|---|---|
| `file:AGENTS.md` 等 5 个状态文件 | FAIL | 存在且非空 |
| `feature_list` | FAIL | 可 `json.load`，`features` 非空，每条有 `id/name/status` |
| `feature_list:one_in_progress` | **WARN** | `in_progress ≤ 1`（AGENTS「一次一个」）；超出要人拍板 |
| `progress:markers` / `handoff:markers` / `agents:markers` | FAIL | 关键节齐备（Last Updated / Blockers / Recommended Next Step / 六项 / DoD / 硬规则 …） |
| `appendix:A.N` | **WARN** | `16` 号条目编号单调、无同号同级重复、无缺号 |
| `git:no_pptx` | FAIL | 入库 `.pptx` 数为 0（硬规则 #16） |
| `agents:links` | FAIL | `AGENTS.md` 反引号里的仓库路径都能解析（纯文件名按 git 清单后缀匹配） |

**FAIL = 本轮必须修，不许拿这种状态对外汇报；WARN = 需人拍板，写进 `16` 号当轮条目即可放行。**
输出 `--json` 可直接给审计工具消费：`python scripts/audit/p78_harness_selfcheck.py --json`。

## 4. 我没做 / 做不到的（边界，先声明免得被当成隐瞒）

- **没改任何诊断核心逻辑**：本轮代码改动只有 `self_rag_score` 一行落库（+守卫），其余全是 harness/文档。
- **没跑全量后端 7500 例**：本机缺 torch/monai/open_clip 等重依赖，只能跑点名文件；全量结论引用 `16` 号 A.102.2，标"采信未复核"。
- **没碰真机**：服务器状态、`alembic 0019`、T2a 8003 实测数字全部来自 A.104（部署方现场记录），我无法二次验证。
- **没改 `.codebuddy/` 内已有的项目数据**（只把本机 IDE 的 `rules/` 加进 ignore，未删）。
- 本机为跑测试做的环境改动：`~/.local/bin/uv` + `~/tmp/uv312`（venv，fastapi 0.115.14 / starlette 0.41.3），**不落仓库**。

## 5. 五个常见误判（审计方容易判错的地方）

| 现象 | 容易得出的错误结论 | 正确读法 |
|---|---|---|
| `settings.py` 里某开关默认 `False` | "功能没做" | 查三处：是否部署 `.env` 打开（A.99.1）、是否有消费者（`grep`）、真机台账（A.104.3） |
| 函数里有 `if is_low_confidence` 分支 | "这个能力已接线" | 查**入参是否被生产路径赋过值**（`_assess_routing` 不传 `retrieval_confidence` → 分支恒假） |
| 提交信息写"N 项打通" | 直接采信 | 用 `git show --stat` 数**实际触碰的文件**，与清单对齐（A.99.5 教训） |
| 文档写"已完成" | 采信 | 按权威次序：代码/测试 > `00` 号 > `16` 号 > harness 状态文件 > 其它文档 |
| 测试"全绿" | 采信 | 先问**跑了哪些文件**、**在本机还是服务器**、**是否 `--dist=loadfile`** |

## 6. 认证记录模板（审计方填完可直接贴进 `16` 号）

```text
认证对象：<commit sha> / <16 号 A.N>
第 1 层 远端一致：HEAD=<sha> origin/main=<sha> status=<空/…>
第 2 层 断言反查：<断言> → <path:line>  ✅/❌
第 3 层 复跑：<命令> → <输出摘要>（可复现/需环境/🔬）
WARN 处置：<harness-check 的 WARN 项> → <谁拍板 / 指向哪条 A.N>
结论：通过 / 有条件通过（附条件） / 不通过（附反例）
```
