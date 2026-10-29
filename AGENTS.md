# AGENTS.md — 超声诊疗决策支持 AI 系统 · Agent 作业规范（harness 入口）

> 本文件是 Agent 的**唯一入口**（harness `instructions` 层）。目标：任何 Agent 在 5 分钟内拿到
> 「项目现在在哪、什么绝对不能做、怎么验证、状态写在哪、去哪查细节」。
> **细节不在本文件展开**——按 [Startup Workflow](#startup-workflow开工必读按序) 的顺序去取。

## 项目一句话

多模态（超声图像 + 文本）1+N 多智能体 RAG **辅助诊断**系统（甲状腺 / 肝脏 / 心脏），
含融合层（冲突检测 / Self-RAG / CRAG / 分层校验）与独立 SafetyGateway。
**定位是辅助工具：所有输出必须经医生确认，系统不替代医生诊断。**

## Startup Workflow（开工必读，按序）

1. `progress.md` —— **当前状态 / 当前目标 / 下一步**（「现在在哪」的唯一来源）。
2. `feature_list.json` —— 功能与门控状态、依赖、证据（「做到哪了」的唯一来源）。
3. `session-handoff.md` —— 上一会话的阻塞点、涉及文件、下会话第一动作。
4. `docs-for-guiding generating docs/超声诊疗决策支持系统/00-项目总览与核心指标.md` —— **指标口径唯一来源**。
5. `docs-for-guiding generating docs/超声诊疗决策支持系统/16-开发进展汇报-20260928.md` —— 进展与**证据台账**（附录 A，最新到 A.16）。
6. 任务落到**远程 GPU 服务器 / 真机** → 加载 SKILL `remote-gpu-server-devtest`（先读其 SKILL.md 的铁律）。
7. 涉及改 `AGENTS.md` / `feature_list.json` / `progress.md` / `.codebuddy/skills/**` → 加载 SKILL `uai-harness-maintenance`。

## Before writing code（动手前）

- 先声明假设；有多种解释就摆出来，**不要静默选一种**。
- 最小改动优先：不写没被要求的功能 / 抽象 / 配置；200 行能压到 50 行就压。
- 只碰必须碰的：不改相邻代码与格式，不重构没坏的东西，不删既有死代码（要删先说明）。
- 每一条改动都要能追溯到用户请求；改动产生的孤儿 import / 变量由你清理。

## 权威次序（冲突时以靠前者为准）

| 顺位 | 来源 | 说明 |
|---|---|---|
| 1 | **代码与测试**（`app/`、`tests/`、`pyproject.toml`、`.github/workflows/`） | 唯一事实源 |
| 2 | `00-项目总览与核心指标.md` | 指标口径唯一来源，其它文档不得改口径 |
| 3 | `16-开发进展汇报-20260928.md` 附录 A | 进展、实测数字、证据链台账 |
| 4 | `AGENTS.md` + `progress.md` + `feature_list.json` | 作业规范与当前状态（可覆盖、随会话更新） |
| 5 | `docs-for-guiding generating docs/**`、`docs/**` | 解释与导读 |

> 文档与代码冲突 → **以代码为准并立即修正文档**。无法在代码/实测中验证的承诺，一律标「待验证」，**不得写成已完成**。

## Stay in scope · One feature at a time

- 同一时间**只推进一个** feature（`feature_list.json` 里 `status: in_progress` 至多一个）。
- 开工前读该 feature 的 `dependencies`；依赖未 `done` 就先做依赖或明确降级方案。
- 不顺手做没立项的事；发现了就记进 `feature_list.json` 的 `planned`，不要当下扩展。
- 范围边界：**不改他人目录/服务**；不动 `.codebuddy/` 目录（项目数据，非缓存）；不删 `.trae/`、`archive_of_achivement/` 历史归档。

## Definition of Done（什么才算做完）

一个 feature 只有在**同时满足**下列条件时才可把状态改为 `done`（done only when）：

- [ ] 代码已落地，且 `Verification Commands` 全绿（贴命令 + 关键输出，即 **Verification Evidence**）。
- [ ] 新增/修改行为有测试覆盖；修 bug 时先有能复现的测试。
- [ ] 真机/外部依赖无法本地验证的，标 🔬 并登记到 `docs/合规/覆盖率豁免清单.json`。
- [ ] 文档/状态同步：口径变了改 `00` 号；进展与实测数字写进 `16` 号附录 A 与 `progress.md`。
- [ ] **有证据才敢写「已完成」**——禁止「已优化 / 基本可用」等无证据措辞。

## Verification Commands（验证入口，先跑这个）

```bash
./init.sh                     # harness 统一入口：环境自检 + 后端快测 + ruff + 前端 lint/build
make test                     # 后端单元 + INT 集成（-n 8 --dist=loadfile --timeout=300）
make cov                      # 覆盖率门禁：整体 ≥82% / 核心 ≥86%（与 CI 同口径）
make lint                     # ruff 全量（含历史债，仅参考）
make frontend                 # 前端 lint + tsc + vitest + build（阻断）
make test-perf                # 性能用例必须串行：-m perf -n0
```

- pytest **必须带 `--dist=loadfile`**：本仓库多个测试类有链式依赖，按用例随机分发会假失败。
- 前端构建基线：`npm run build`（tsc -b + vite，通过约 3.5s）。
- 真机评测/压测命令见 SKILL `remote-gpu-server-devtest`。

## State Artifacts（状态写在哪，别只写进聊天）

| 文件 | 作用 | 何时更新 |
|---|---|---|
| `progress.md` | 当前状态、当前目标、下一步、阻塞点 | 每轮收尾（End of Session）必须更新 |
| `feature_list.json` | feature / 门控状态 + 依赖 + 证据 | 状态迁移时（`in_progress` → `done` / `blocked`）立即更新 |
| `session-handoff.md` | Last Updated / Blockers / Files / Next Session | 会话结束或上下文将满时更新 |
| `docs-for-guiding generating docs/超声诊疗决策支持系统/16-开发进展汇报-20260928.md` | 长期证据台账（附录 A 只追加） | 每轮实测/决策后追加一节 |

## Skills 索引（可复用流程）

| Skill | 何时用 |
|---|---|
| `remote-gpu-server-devtest` | 一切远程 GPU 服务器 / 真机部署、重启、评测、压测、踩坑速查 |
| `uai-harness-maintenance` | 维护 `AGENTS.md` / 状态文件 / SKILL，或需要 harness 审计与更新准则时 |

## Harness 文档与 SKILL 维护准则（要点）

完整规范见 SKILL `uai-harness-maintenance`。最低要求：

- **单一事实源**：进度只写 `progress.md`，功能状态只写 `feature_list.json`，口径只写 `00` 号，证据只追加 `16` 号附录 A——**同一事实不在两处各写一份**。
- **只追加不重写**：`16` 号附录按 A.N 递增追加；历史结论被推翻时**新增更正条目**并就地标「已作废」，不删旧文。
- **分层**：本文件只放路由 + 不变量（保持短）；项目事实进项目文档；可复用流程进 SKILL。
- **技能只装流程，不装事实**：SKILL 里写「怎么查/怎么做」，具体数字指向 `16` 号。
- **每次收尾跑审计**：`node ~/.codebuddy/skills/harness-creator/scripts/validate-harness.mjs --target .`，分数不得低于 70。
- **失效即修**：任何文档/技能与代码或实测不符，当轮修掉或标「待验证」，不得留错。

## 硬规则（禁止再犯 · 违反即返工）

| # | 禁止 | 原因（真实踩坑） |
|---|---|---|
| 1 | **禁止把复杂命令内联进 `ssh "..."`**（远端 shell 是 fish）；**禁止 PowerShell 内联多行 `python -c`** | `fish: $? is not the exit status`；PowerShell 引号/换行转义必炸。一律本地落盘 → `scp` → `bash x.sh` |
| 2 | **禁止 `pkill` 与重启写在同一条 ssh 命令** | 命令行含模式串会自杀，重启没执行却「探活通过」（旧进程假活）。重启后核对 `ps -o lstart` 晚于文件 mtime |
| 3 | **禁止碰共享服务器上他人的东西**：`6379` 上的 `redis-server` 是别人的、不碰他人目录、服务只绑 `127.0.0.1` | 共享机；本项目中间件是容器 `uai-redis`(16379) / `uai-postgres`(15432) |
| 4 | **禁止回切 llama.cpp 作主生成引擎**（仅保留 compact 档链路验证） | 预编译包是 CPU-only 构建，7B 全靠 CPU（40s/请求）；vLLM 官方 server 快约 20x |
| 5 | **禁止用 `extra_body.guided_json`**（新版 vLLM 静默失效） | 无约束也不报错。结构化输出用 `response_format: {"type":"json_schema", ...}` |
| 6 | **禁止 DB 会话跨越 LLM 调用** | 事务空闲连接被 `idle_in_transaction_session_timeout` 掐断 → 写库抛 `underlying connection is closed` → 请求永挂 processing。session 只包读写段，写终态用独立短会话 |
| 7 | **禁止跨文件改动只部署一部分** | 多文件改动只 `scp` 部分 → 部署半成品 NameError；单测 mock 不触发真实 settings，全绿 ≠ 部署完整。整组部署后再跑 1 例端到端 |
| 8 | **禁止在评测里只看 `completed` 数** | `_pick_conclusion_text` 缩进错位致 `UnboundLocalError` 被吞 → 结论静默为空。跑完必须抽查 report 的 `error` 字段 + 抽样结论非空 |
| 9 | **禁止让 `.gitignore` 吞掉测试/资产文件** | 无锚点 `data/` 模式曾整目录忽略 `tests/unit/data` → 3 个测试文件从未进仓库。新增路径加例外并跑 `test_test_files_are_tracked` |
| 10 | **禁止在命令/脚本/聊天里出现密码、PHI** | 凭证只存 `~/uai/.smoke_pw`(600) 或 `.env`；不落患者数据到共享服务器 |
| 11 | **禁止 `/tmp` 探针累积** | 历次会话探针累积上百个。收尾 `ls /tmp/_*.py /tmp/_*.sh \| wc -l` 全量清空（正式脚本都在 `~/uai/src/scripts/`） |
| 12 | **禁止无证据宣称「已完成/已优化」** | 文档纪律：给 `path:行号` 或实测数字，否则标「待验证」 |
| 13 | **改 `feature_list.json` 禁止用英文双引号做中文引号；改完必须 `json.load` 通过才 `git add`；命令链用 `&&` 不用 `;`** | 2026-10-20 连犯 3 次：`"科室"` 把 JSON 字符串截断 → 提交了不可解析的 `feature_list.json`（`697f26c`/`c0000fa`/`b152a5c`），每次都要补一个 fix 提交。用 `;` 时校验失败也照样提交 |
| 14 | **判「脏数据」必须用生产同一把尺子（`app.inference.output_guard`）；删除历史数据必须先备份、只删已打标的、默认 dry-run** | A.70：98.7% 的历史结论是提示词示例照抄。删 608 条前用 `p50` 备份到 `~/uai/data/eval/deleted_dirty_20261020.jsonl`（1.9 MB，含完整 findings），再按 `findings.data_quality.prompt_echo` 标记删；若按"重新判定"删，规则一漂移就删错一批 |
| 15 | **禁止在 `~/.gitconfig` 里设静态 `http.proxy` / `https.proxy`**（git 只认单一地址、**无回退**，代理一挂所有 HTTPS 立等失败）；**要用代理走 `include.path=~/.gitconfig-proxy` 或 `http_proxy` 环境变量按需启用** | 2026-10-28：全局 `http.proxy=http://127.0.0.1:6696` 失效后，`git push` 报 `Failed to connect ... over proxy 127.0.0.1`，而 GitHub 实际走 SSH（`insteadOf` 规则）**根本不读 http.proxy**，是 LFS 的 HTTPS 请求触发的。实测：Clash 真实端口是 `mixed-port: 7890`（`randomMixedPort: false`）；不设代理时 GitHub SSH 与 gitee/pypi 直连均正常，仅 huggingface 不通。 |
| 16 | **禁止 pptx 入库**（`.gitignore` 已固化 `*.pptx`；大文件交付物用网盘/本地，不进 git） | 2026-10-30：两份 ~140MB 答辩 PPT 被加进 commit → GitHub pre-receive 拒收（GH001 Large files），被迫重写未推送历史。同类风险：>50MB 的任何产物（模型/缓存/导出包）入库前先查体积。 |

## 数据质量事故归档（2026-10-20，详见 16 号 A.70.x）

**一句话**：全库 1071 条判读里，**608 条（真结论的 98.1%）是提示词示例照抄**（`证据: chunk_a1b2, chunk_c3d4` / `患者PATIENT_001`），
另 **451 条根本没有结论**（9/28–9/30 模型服务故障 328 条 + 10/12 输出缺分级 66 条）。

**已处理**：①生产侧解析前拒收（`output_guard.detect_prompt_echo`）②提示词示例脱敏（`[1], [2]` 取代假 ID，真机验证不再照抄）
③采集端拒收并计数 ④608 条打标后**经用户决定删除**（备份见上）⑤残留 463 条（含 12 条干净、451 条无结论）。

**坑（会再犯）**：
1. **指标先"看原文"再信数字**：我曾把评分算式 `(3)/(0)` 当引用，算出"覆盖率 58.7%、正确率 50.9%"两个假数；去掉圆括号后真引用是 **0**。
2. **自证腿救命**：复算旧口径时对不上原数字（0.0304 vs 0.0906）才发现 `context` 是列表、没 join 就喂判官。
3. **重启用仓库脚本**：自写的 Python 重启"没报错却没生效"（PID 未变）；`scripts/_restart_main_std.sh` 一次成功。
4. **清理历史数据要留备份**：即便用户说"直接删"，也先写 JSONL（含完整 findings）再删，删后可复核。
5. **判"回显"要排在判"降级"之前**：`grade_unparseable: …; manual review required` 既是解析失败又含占位符正文，
   首版按降级跳过 → 漏标 52 条（事后 dry-run 才发现）。
6. **时区**：库里 `created_at` 是 aware(UTC)，`datetime.now()` 是 naive → 直接比较抛 `TypeError`；按行时区对齐。
7. **单一标记不得定罪**：删/拒收前的判据必须有**两条以上独立证据**。A.77 教训：把"答案里出现 `PATIENT_001`"当模板回显铁证 →
   **误删 415 条真结论**（它们有分级、有鉴别诊断，只是把语料脱敏标记抄进了患者解释；prompt 里根本没有该串，KB 里倒有 22 块）。
   现在规则：`chunk_a1b2`/重复 `# 输出` 才算回显；`PATIENT_` 单独出现只算"语料标记外泄"，处置是**清洗**而非拒收。
8. **聚合失败原因时不许写死默认值**：每个被跳过的分支必须留下自己的原因（`skips`），聚合处取**真实原因**。
   A.86 教训：`best is None` 一律报 `image_bytes_unavailable` → "这是屏幕截图、本来不该勾画"
   被显示成"系统取不到影像"，医生据此白跑一趟（真机：文件在盘上 122134 B、`retrieve` 正常返回）。

## End of Session（收尾必做，保证可 restartable / clean）

1. 跑 `./init.sh`（或对应精简验证）并记录 **Verification Evidence**（命令 + 输出）。
2. 更新 `progress.md`：Current State / Current Objective / Recommended Next Step / Blockers。
3. 更新 `feature_list.json`：状态迁移 + `evidence`/`dependencies`。
4. 更新 `session-handoff.md`：Blockers / Files / Next Session。
5. 有实测数字或决策 → 追加 `16` 号附录 A 一节（A.N 递增）。
6. 远程侧：清理 `/tmp/_*` 探针、核对 GPU 进程清单（嵌入×3 + vLLM 8002）。
7. 跑 harness 审计（见「维护准则」）；分数不得低于 70，否则本轮不结束。

> **Next steps 与 clean restart**：下一位 Agent 从 `progress.md` 的 Recommended Next Step 起步即可无缝续接；任何「只存在于聊天历史」的结论都视为已丢失。
