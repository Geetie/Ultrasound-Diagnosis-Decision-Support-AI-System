# progress.md — 当前状态（唯一「现在在哪」来源）

**Last Updated**: 2026-10-08（晚）
**HEAD**: 分支 `main` @ `54f1234`（本轮：refresh 旋转宽限期 + 剩余项收口 + 医生全流程最终摸排 13/13，详见 16 号附录 A.34）
**Owner of truth**: 本文件只记「当前状态/下一步/阻塞」；**功能与门控状态在 `feature_list.json`**，长期证据在 `16-开发进展汇报-20260928.md` 附录 A。

## Current Objective

把项目从「评测与模型能力攻关」推进到**生产化收口**：通用技术轨道（检索/推理/融合/安全网关/降级/
可观测）已达标；剩余为**门控口径待项目组决策项**、**外部数据依赖项**与**NMPA 交付物**。

## Current State

### What 已完成（有证据，详见 `feature_list.json` 与 16 号附录 A）

- 检索：Gate 0 94.5% ✅ / Gate 1 98% ✅（混合检索 RRF 已调优到顶）。
- 推理：官方 vLLM OpenAI server（780 例评测 3.25h→5min）；P99 定容 0.73~0.79s ✅。
- 融合/管线：DB 会话治理 + Layer2/Layer3 并行 + 阶段延迟预算 → 端到端 9/9 completed（1.5~9s）。
- 安全：SG 降级注入可注入场景全过（Redis 停→PG 降级；SG 停→E4005 拒绝输出）。
- 模型资产：T2a 方案 C（AUC 0.8751 ✅）+ T2b 切面/质量头（Top-1 0.9793 ✅），二者均已生产端点（8003 `/v1/classify`、`/v1/analyze_view`）。
- 知识：kb_v1.3.0（1257 chunks）生产切换；图谱查询 P2-KB-08 收口（默认门控关闭）。
- 前端：Cornerstone3D 5.11.3 迁移完成，`npm run build` 通过。
- 医学知识资产加固：vocab_v2 接入 UDAF、cardiac 危象词表 22 条、hallucination 词表资产化、禁忌规则 15 条补溯源。
- 可观测：Langfuse v3 自托管（127.0.0.1:30100）+ OTel 激活。
- **访问面合规（2026-10-05）**：主服务由 `0.0.0.0:18000`（临床 API 对校园网裸奔，违反硬规则 #3）
  改绑 `127.0.0.1:18000`；新增唯一权威启动脚本 `scripts/start_main.sh` 终结"7 个 deploy_*.sh
  各写死 `--host` 导致改绑必然漂回"的根因；6 例双向锁定测试（裸机必须 loopback / 容器必须
  `0.0.0.0`）。**本机浏览器验证通道已就绪**：`~\.uai\keep_tunnel.ps1` 守护 + 计划任务
  `UaiKeepTunnel`（登录自启，断了 5s 自动重连），浏览器开 `http://127.0.0.1:8000/ui/` 即可。
  实测直连 = `000`（已封）、经隧道 `/ui/` = 200（39ms）。详见 16 号附录 A.25。
- **医生主流程打通 +渲染守卫（2026-10-05 下午）**：勘察发现**上传端点与前端封装早已存在、
  只是从未被调用**（`uploadDicom` 零调用，`useWorkspaceFlow.start` 也零调用—— 判读主链没有
  起点）。已补 `useDicomUpload` + `WorkspaceImageUpload`，入口落在结论区 02 扫查态
  （影像区 600 与 900 骨架未动），工作列表带`session_id` 进工作区；导航出口放常驻条右侧。
  守卫分两层：`tests/unit/quality/test_frontend_render_guard.py`（17 例，含5 例守卫自测）
  + `scripts/frontend_browser_smoke.mjs`（Playwright + 真 Chrome，20 项判定）。
  真机：门禁 **PASS=23 FAIL=0**，冒烟 **20/20 PASS**（详见 16 号附录 A.28）。- **临床前端真机走查（2026-10-05 夜，首次真实浏览器渲染）**：此前所有验证都只到 HTTP 层
  （门禁 23 项 curl + 契约对账 18 路径字符串比对），**从未在浏览器里渲染过一行前端代码**。
  Playwright + 真 Chrome 首次点击即抓到 **4 个 P0 阻断缺陷 —— 临床前端此前完全不可用**：
  ①**白屏**（`Class extends value undefined`，`vendor-cornerstone` 跨 chunk 循环依赖 +
  Vite 8/Rolldown 求值顺序）；②**登录永远 422**（前端发 JSON 打到 `OAuth2PasswordRequestForm`
  端点）；③**登录后跳到后端根 JSON**（`href='/'`）；④**所有二级页面打不开**
  （`BrowserRouter` 缺 `basename`，9 条路由全回落成工作区）。**四个全部已修并复验**：
  修后登录成功落地工作区、9 条路由各渲染（textLen 各不相同）、全程 0 JS 错误 0 HTTP 错误。
  录像 `C:\Users\ghf\Videos\US-DX-医生视角走查-20261005.webm`（2.71MB）。详见 16 号附录 A.27。
- 并发与性能审查（2026-10-03）：**发现 26 项（P0×4 / P1×12 / P2×10），已修 17 项**。
  重点：①登录 bcrypt 与 HSM 签名、onnxruntime、pydicom 解析等**同步阻塞已全部 offload**
  （原为事件循环上的定时炸弹）；②**多器官联合诊断原先完全无限流**（已登记）；③**refresh token
  rotate 的 check-then-act 竞态**（可派生两个有效后继、绕过 reuse 检测）已用 per-token 锁
  串行化；④WS 下行不再持锁 `await send_json`（原会被一个慢客户端拖住同 session 全部消息）；
  ⑤诊断管线补上端到端总预算 180s（原 `pipeline_dispatch/fusion_budget_s=6.0` 是**从未被读取的
  孤儿配置**，最坏 ~240s 会占死全部 worker 槽位）；⑥停机链不再中断且会 dispose PG 连接池。
  报告：`reports/concurrency_perf_audit.md` / `.json`（16 号附录 A.22）。

### What 未完成 / 挂起

- `faithfulness` 官方 RAGAS 口径 68.96%（门控 95%）——判官侧已无空间，卡在**语料覆盖**（Top-5 缺依据）。
- 核心覆盖率 86.30%（门禁 90%，差 3.70pt），缺口集中在已登记的 🔬 真机项（SoftHSM 签名字节、真实 Redis 语义）。
- UDT Phase 1 词汇表 ≈1650/2000，**待医生审定草案**（AI 侧覆盖度审计与草案已交付）。
- 甲状腺注册数据方案（管理决策 + 数据授权）；NMPA 注册材料/部署手册/验收报告未交付。
- 镜像仓库的**文件级同步窗口**：服务器 `~/uai/src` 经 `git archive` 铺 HEAD，非实时（改动后需跑同步脚本）。
- ~~UI 无 DICOM 上传入口~~ **已闭合**（2026-10-05 A.28）：结论区扫查态有「选择 DICOM 影像」，
  装载后自动发起判读。真机 Playwright 实测 `file input accept=".dcm,.dicom"`，且**真传一个
  DICOM 后界面自动进入「分析中」** —— `useWorkspaceFlow.start()` 第一次真正被触发，
  此前判读主链没有起点。勘察发现端点与前端封装早已存在、只是从未被调用。
- ~~工作区无导航出口~~ **已闭合**：常驻条右侧三个入口（工作列表/新建会话/设置），真机 `btn=4`。
- **医生全流程已走通并真机验证**（2026-10-06，A.29）：登录 → 工作列表 → 选检查 →
  **装载 DICOM** → 判读出结论（33s）→ 报告 → 草稿 → **签发成功**（HSM 签名、
  签发人 smoke-doctor），全程 26/26 PASS。补这最后一跳时挖出**两个 P0**：
  ①**判读结果永远等不到** —— 前端轮询上限 30s 而真实管线 90~300s，
  `timeout` 又映射回 `progress`，界面永久停在「分析中」；A.24.1 只修了服务端那一侧；
  ②**报告与签名在 UI 上不可达** —— `request_id` 从不交给界面，医生只能手敲 URL。
  两者均已修并加前端守卫（6 例）。
- **报告签发门禁 + 补录已定稿并落地**（A.32，口径由 AI 拍板、依据记档）：
  段落按临床权重分两档——缺**超声所见/诊断意见**（证据段/结论段）服务端拒绝签发
  （E1030）；缺**测量摘要/处置建议**走知情确认（部分检查确实可为空）。
  补录入口同页（PATCH 重渲染重算 digest，出处记 doctor_supplemented）。
  真机全闭环：缺阻断段 → 签发被禁 → 补录 → 解禁 → 知情确认 → 已签发（28/28 PASS）。
- **外部字体已移除**（A.30）：删掉 fonts.googleapis.com 外联（CSP 一直拦着，从未生效），
  真机 12 条 CSP 违规全部消失、视觉零变化。要恢复设计字体走自托管。
- ~~影像不支持回取~~ **已闭合**（2026-10-06 A.31）：后端补 GET 端点（列表 + 按 id 取字节，
  带 Bearer 与审计），前端显示源改为服务端副本（同管线同源），刷新 / 「继续检查」时
  按会话自动恢复最近影像。`ui-no-wado-retrieve-endpoint` 转 done。
- ~~新建会话页未接线~~ **已闭合**（2026-10-08 A.33）：患者编号经 `GET /fhir/Patient/{id}`
  一次校验存在性 + 知情同意（未签署/撤回 → 引导找质控，如实告知医生无权补签；
  404 → 就地建档后重新校验），设备号录入并记住上次，「开始检查」带 `?session_id=`
  直达工作区。真机端到端 6/6：登录 → 校验 → 建会话 → 装载 DICOM → 自动「分析中」。
  勘察结论同前：端点早就在，缺的是接线（患者/设备列表端点确实不存在，用校验+录入补位）。

## Recommended Next Step

1. **交付收口（2026-10-08 更新，距 10-17 交付）**：**医生主流程含「开新检查」已全通**
   （A.29 全流程 + A.33 新建会话接线，均真机端到端验证）。**剩余**：
   ①**NMPA 验收材料尚未产出**（距交付最近的一块）；
   ②设备/患者列表端点不存在（现为校验+录入+记忆补位，可用但不如下拉；
   已登记 `ui-session-create-not-wired` 的遗留备注）；
   ③服务器 `~/uai/src` 的产物陈旧哈希待清（本地已清零，47→31 文件）。
2. **把真浏览器守卫接进门禁的第二层**：静态层已进服务器门禁（I 组，PASS=24 中的一组）；
   运行时层 `scripts/frontend_browser_smoke.mjs` 仍是人工跑。它需要 Playwright + 真
   Chrome（本机 agent-browser 那份）与从环境变量注入的凭据，服务器没有 Chrome，
   所以**不适合进服务器门禁**；建议在本机 `Makefile` 加一个 `fe-smoke` 目标，
   并在 `init.sh --full` 里提示可跑。
2. **并发审查残留项收口**（详见 `reports/concurrency_perf_audit.md` 第 4 节）：①refresh token rotate 跨进程互斥**已闭合**（3dab8f2 随 feature 分支并回 main，配 `test_refresh_token_rotate_cross_process.py` 122 行真机级测试）——放开 `uvicorn --workers > 1` 的前置条件已就绪；②按压测容量给网关加全局并发闸门。P2-10 已修复并订正表述（见 16 号附录 A.23：真实缺口是临床事实漏传，非"绕开 SG"）。
3. **口径决策收口**：`faithfulness ≥95%` 门控是否需要调整口径（NLI 允许同义概括）或走语料扩展——需项目组拍板（对应 `feature_list.json: faithfulness-gate`）。
4. **UDT 草案送审**：把 `data/udt/vocab_v2_final.json`（~1650 条，含逐词条溯源分层）交医生勾选背书。
5. **存量失败清收**：`tests/integration/int` 12 例（`_write_result_to_db(diagnosis_repo=...)` 与 `app/services/diagnosis_pipeline.py:687` 签名不匹配，**HEAD 即存在，非并发审查引入**）。

## Blockers

| 阻塞项 | 类型 | 解除条件 | 影响 feature |
|---|---|---|---|
| faithfulness 95% 门控 | 口径/语料 | 项目组口径决策 或 域内语料到位 | `faithfulness-gate` |
| 甲状腺注册数据 | 管理决策 + 授权 | 自有/合作医院或授权采购 | `thyroid-registry-data`、`nmpa-materials` |
| 核心覆盖率 90% | 真机依赖 | SoftHSM 签名字节、真实 Redis 语义可测 | `coverage-core` |
| T2b 真机泛化 | 数据 | 真实临床切面验证集（CACTUS 为体模） | `t2b-plane-quality` |

## 证据与报告索引（Verification Evidence）

| 报告 | 内容 |
|---|---|
| `tests/reports/t1_kb_retrieval_eval.json` | 检索 Gate 0/1 |
| `tests/reports/p99_capacity.json` | P99 并发定容 |
| `tests/reports/t2a_v2_official.json` | T2a 方案 C AUC |
| `reports/t2b_cactus.json` | T2b 切面/质量 |
| `reports/concurrency_perf_audit.md` / `.json` | 并发与性能审查（26 项发现/修复状态/压测） |
| `reports/concurrency_perf_report.json` | 阶梯并发压测（1/5/10/20/50，P50/P95/P99/429/队列积压） |
| `tests/reports/t2c_e2e_report_final2.json` | T2c 端到端 |
| `tests/reports/phase0a_retest_v2.json` | Gate 0A-T-P0 重测（口径 A） |
| `tests/reports/t1_faithfulness_v11.json` | faithfulness 基线 |
| `tests/reports/phase0a_report.json` | 早期 6 例口径（已作废，仅存档） |

## Files（本轮相关）

- `AGENTS.md`（harness 入口）、`progress.md`、`feature_list.json`、`session-handoff.md`、`init.sh`（harness 五子系统）
- `.codebuddy/skills/remote-gpu-server-devtest/SKILL.md`（服务器作业手册）
- `.codebuddy/skills/uai-harness-maintenance/SKILL.md`（文档/SKILL 维护规范）

## Next

见 [Recommended Next Step](#recommended-next-step)；下一位 Agent 从这里起步即可 clean restart。
