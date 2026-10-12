# session-handoff.md — 会话交接

> 多会话/长任务专用。**Last Updated / Current Objective / Recommended Next Step / Blockers / Files / Next Session** 六项必填。
> 下一位 Agent 先读本节，再读 `progress.md` 与 `feature_list.json`。

## Last Updated

2026-10-12（分支 `main`；**A.51 远期第一梯队四项收口**——①检索口径闭合（服务腿
hit@10 0.9853 = eval 腿，diff +0.00pp；顺带修 qdrant-client `search()` 移除 + kb 向量
未灌 两个真缺陷）；②多帧分期③择优帧 + 帧级归属（p17 15/15、p18 12/12，报告含「第
X/Y 帧」）；③设备/患者列表端点（p16 14/14，零 api_key）；④fusion-validate 降载
（P99 2296→885ms PASS，降级路径实测 `validate_degraded=1` 且结果正常返回）。
详见 16 号附录 A.51。上一轮 A.50.10 E11/E13 真 HAPI 闭环仍成立）

### A.51 复跑入口（下一位直接用）

- 探针（服务器）：`cd ~/uai/src/scripts/audit && ~/uai/venv/bin/python p15_retrieval_parity.py`
  （`p16_list_endpoints.py` / `p17_multiframe_segmentation.py` / `p18_multiframe_singlefile.py`
  / `p14b_validate_bench.py` 同目录，全部可复跑）
- 单测：`tests/unit/services/test_multiframe_frame_selection.py`、
  `tests/unit/api/test_list_endpoint_contracts.py`、`tests/unit/fus/test_validate_budget_degrade.py`
- 真多帧样本：`python scripts/camus_frame_to_dicom.py --out /tmp/x.dcm --count 7 --multiframe`
  （A.51 新增 `--multiframe`：N 帧写进**一个** DICOM，供择优帧复验）
- **踩坑（会再犯）**：用 `replace_in_file` 在类定义处插入新类时，**插入边界会吃掉相邻类的
  尾部字段**（本轮把 `DeviceRemoteRecoveryResponse.expires_at` 吞进了新类 → 运行期
  pydantic ValidationError，静态检查与 `import app.main` 都发现不了）。改 schema 后
  **必须**跑 `tests/unit/api/test_list_endpoint_contracts.py` 这类字段集守卫。
- **踩坑**：`tsc -b` 的增量缓存会掩盖 HEAD 既有类型错误；清缓存（改前端文件即失效）
  才会暴露。前端改动后务必真跑 `tsc -b + vite build`，不要只看 vitest。
- 前端产物部署：`cd frontend && VITE_BASE=/ui/ npm run build` → 拷贝 `dist/*` 到
  **`app/static/ui/`**（该目录入库，随之提交）。

### ⏳ 遗留同步项（服务器失联，下一位先补）

- **服务器层已是应用代码 `4fefdb5`**（A.51 四项功能全在，门禁 PASS=24 在该版本通过）；
  `1e3a00f` 之后只有**文档层**（16 号 A.51 附二/附三/附四、`feature_list.json`、
  `progress.md`、本文件）与 1 处探针注释（p15 去掉多余 noqa）**未同步**。
- 原因：2026-10-12 03:0x 起 `10.20.28.21` **ping 与 TCP 22 均不通**（3 次重试 + 60s 等待），
  ssh `Connection timed out`；与本机网络/隧道无关（隧道日志无新条目）。
- 恢复后一条命令补齐（先确认 HEAD）：`cd <repo> && git archive --format=tar.gz -o /tmp/uai_head2.tar.gz HEAD && scp /tmp/uai_head2.tar.gz student03@10.20.28.21:/tmp/ && ssh student03@10.20.28.21 "cd ~/uai/src && tar -xzf /tmp/uai_head2.tar.gz --overwrite && rm -f /tmp/uai_head2.tar.gz"`
  随后核验：`grep -c '附四' <16 号>` 与 `curl -s -o /dev/null -w '%{http_code}' 127.0.0.1:18000/api/v1/health`。
  **应用代码未变，无需重启主服务**。

### 常驻服务新增（复跑 p13 前确认在跑）
- HAPI：`cd ~/uai/hapi && nohup java -jar ROOT.war --server.address=127.0.0.1 --server.port=8080 > ~/uai/logs/hapi_war.log 2>&1 &`（30s 就绪，`/fhir/metadata`）
- bridge：`cd ~/uai/src && FHIR_BRIDGE_HAPI_URL=http://127.0.0.1:8080/fhir nohup ~/uai/venv/bin/python scripts/fhir_bridge.py > ~/uai/logs/fhir_bridge.log 2>&1 &`（8091）
- PACS SCP：`cd ~/uai/src && nohup ~/uai/venv/bin/python scripts/pacs_scp.py > ~/uai/logs/pacs_scp.log 2>&1 &`（11112）
- **~/uai/.env 已设 PACS_ADAPTER=http / PACS_ENDPOINT=http://127.0.0.1:8091/reports**（报告 push 真出进程）

## Current Objective

**10-17 交付**：医生主流程**含「开新检查」与「多帧翻阅」已全通**（登录 →
新建会话/工作列表 → 多帧装载 → 帧指示器翻帧 → 判读 → 报告 → 签发 → 打印/导出，
全部真机端到端验证）。判读性能已测量+预热（重启首判分割 59.8ms，端到端 8.4s）。
**NMPA 验收材料：用户拍板留到性能数据提升、远期目标攻克之后**——当前无阻塞性剩余项，
改进方向见 feature_list 未闭合条目（测量工具 P2、标注持久化/历史对比远期、
LLM 模型侧提速）。

## 医生视角走查结论（2026-10-05 夜，下一位 Agent 先读这段）

此前所有验证只到 HTTP 层（门禁 23 项 curl + 契约对账 18 路径字符串比对），
**从未在真实浏览器里渲染过一行前端代码**。首次真实点击即发现
**临床前端此前完全不可用**，4 个 P0 全部已修并复验（commit `1ac92d5`）：

| # | 缺陷 | 根因 | 修法 |
|---|---|---|---|
| 1 | 白屏（`#root` 子节点 0） | `vendor-cornerstone` 跨 chunk 循环依赖 + Vite 8/Rolldown 求值顺序 | 从 `manualChunks` 移除 cornerstone |
| 2 | 登录永远 422 | 前端发 JSON 打到 `OAuth2PasswordRequestForm` 端点 | 改打 `/auth/login` |
| 3 | 登录后跳后端根 JSON | `window.location.href='/'` | 改用 `import.meta.env.BASE_URL` |
| 4 | 9 条二级路由全打不开 | `BrowserRouter` 缺 `basename` | `basename={BASE_URL 去尾斜杠}` |

**A.28 已闭合这2 个**（2026-10-05 下午，详见 16 号附录 A.28）：DICOM 上传入口（结论区
扫查态「选择 DICOM 影像」，装载后自动发起判读）+ 工作区导航出口（常驻条右侧三项）。
真机 Playwright 20/20 PASS，含**真传一个 DICOM 后界面自动进入「分析中」**——
`useWorkspaceFlow.start()` 第一次真正有调用方，此前判读主链没有起点。

**勘察结论值得记住**：上传端点（`POST /api/v1/imaging/dicom/upload`）与前端封装
（`imagingService.uploadDicom`）**早就写好了，只是从未被调用**。所以这类"功能缺失"
要先查是不是接线问题，而不是急着加接口。

**新增三层守卫**（下一位直接用）：
- 静态：`tests/unit/quality/test_frontend_render_guard.py`（pytest，17 例，含 5 例自测）
- 运行时：`scripts/frontend_browser_smoke.mjs`（Playwright + 真 Chrome，20 项）
  密码只能从环境变量注入：`UAI_DOCTOR_USER` / `UAI_DOCTOR_PASSWORD` / `UAI_BASE_URL`
  加 `--upload <dicom>` 可真传一份并验证自动发起判读（需 GPU 在线）
- 走查样本：`python scripts/make_test_dicom.py <路径>` 现造合规 DICOM
  （**不能取自 `dicom_storage/`**：落盘副本已被 PHI 剥离，回传必然 400）

**A.29（2026-10-06）**：补走「报告签名」这一跳时挖出**两个 P0**，均已修并加守卫 ——
①**判读结果永远等不到**：前端 `pollDiagnosisResult` 的 `maxWaitMs` 默认 30s，
而真实管线 90~300s；`timeout` 又被 `PHASE_TO_STATE` 映射回 `progress`，
于是界面**永久停在「分析中」**。A.24.1 只把服务端阈值抬到 180s，前端没跟着改。
②**报告与签名在 UI 上不可达**：`request_id` 从不交给界面，医生只能手敲 URL。
现两处都通：真机 26/26 PASS，直到**签发成功**（HSM 签名、签发人 smoke-doctor）。

**下一位先读这段**：医生主流程已全通，别再重复走查；直接看
`feature_list.json` 里 `planned` 的 4 项（NMPA 材料最急）。

**A.30（深夜）**：报告缺段落做了**知情确认**（完整性未过 → 签发先弹缺失清单，
确认后才能签；刻意不做硬禁用，口径需项目组定）；**外部字体已移除**（12 条 CSP 违规
清零，视觉零变化；要恢复设计字体走自托管）。真机走查 27/27 PASS。

**A.31（同夜）**：**影像回取已闭合** —— 后端补 GET 端点（列表 + 字节），前端显示源
改服务端副本，刷新 / 「继续检查」自动恢复最近影像（真机 27/27 PASS，审计
`dicom_retrieve` 留痕）。已登记 `ui-no-wado-retrieve-endpoint` 转 done。

**A.32（同夜）**：报告签发门禁 + 补录定稿并落地。口径（AI 拍板、依据记档于
`report_service.SIGN_BLOCKING_SECTIONS`）：缺 **findings / diagnosis**（证据段/结论段）
服务端拒签（400 E1030）；缺 **measurement_summary / clinical_actions** 走知情确认。
补录 PATCH 重渲染 + 出处记录（doctor_supplemented）。真机全闭环 28/28 PASS：
缺阻断段 → 签发被禁 → 补录 → 解禁 → 知情确认 → 已签发。

**A.33（2026-10-08）已闭合新建会话接线**：患者编号 → `GET /fhir/Patient/{id}` 一次校验
存在性 + 同意（**未签署/撤回 → 引导找质控，医生无权补签是事实**；404 → 就地建档后重新
校验，建档≠同意）；设备号录入 + localStorage 记住上次；「开始检查」带 `?session_id=`
直达工作区（旧实现 `navigate('/')` 把新会话 id 丢了）。真机端到端 6/6 PASS。
**关键坑（已写进 fhirService 注释与契约对账脚本）**：FHIR 挂在 `/fhir` 不带 `/api/v1`，
每个请求必须 `baseURL: ''` 覆盖全局前缀，否则永远 404。

**A.34 已收口可修项，剩余全部为「等外部条件」，无代码侧欠账**：
①**NMPA 材料**（项目决策 2026-10-08：延期至性能数据提升、远期目标攻克后；可借鉴 cmde.org.cn
  公开的 AI 三类证技术审评报告，系统证据映射见 A.33/A.34）；②consent 排程 handler（排程系统
  本体不存在，handler 是安全的 no-op 占位）；③UDT/faithfulness/覆盖率/甲状腺数据（语料与
  项目组决策）；④患者/设备列表端点（依赖登记数据，现为校验+录入+记忆补位）。
**医生全流程最终摸排 13/13 PASS**：`scripts/doctor_flow_walkthrough.mjs`（登录→新建会话→
装载→判读→报告→生成草稿→补录→签发闭环；报告草稿需医生点「生成草稿」，无草稿时 404+引导是正常态）。

录像：`C:\Users\ghf\Videos\US-DX-医生视角走查-20261005.webm`（2.71MB）。
走查脚本用 Playwright + 复用 `~\.agent-browser\browsers\chrome-154\chrome.exe`，
录屏需 `npx playwright install ffmpeg`。**下一位做真浏览器验证时直接复用这套**。

## A.49 端到端管线摸排（2026-10-10，下一位先读这段）

**用户指令**：「走端到端摸排，列出要摸排的管线，一一排查过去！」10 条管线
（走查/影像交互/测量标注/认证/同意撤回/设备/告警/报告分发/辅助/FHIR）逐一
真机实测，探针套件**入库 `scripts/audit/`（p0_admin_bootstrap + p4~p10，
可复跑）**：探针 71 PASS / 1 P2 观察。修复 3 真缺陷（详见 16 号 A.49.2），
甄别 4 个探针误判（别再重查：refresh 宽限窗 30s / attributes 中文 503 是
语言护栏 / FHIR 读端点要 consent 前置 / 设备同名注册已被拒绝）。

**本轮踩坑（都会再犯的那种）**：
- **build 丢 `VITE_BASE=/ui/` → 全站白屏**：每条命令都是新 shell，`$env:VITE_BASE`
  必须与 build 同一条命令；build 后先查 `dist/index.html` 资源前缀再部署。
- **登录限流**：探针登录几十次/天必撞 E1007 429——`scripts/audit/_audit_common.py`
  login 已带退避重试；写新探针直接复用它（别再裸 curl login）。
- **探针断言先对齐设计再跑**：8 个 FAIL 里 4 个是误判，先查代码定性。
- 服务器 /tmp 历史探针剩 25 个（清理命令需审批窗口未执行，见硬规则 #11）。
- smoke-admin（SYSTEM_ADMIN）账号已建（密码 ~/uai/.smoke_admin_pw，600），
  管理面探针靠它；p0_admin_bootstrap.py 可重建。

## A.50 远期第一梯队（2026-10-11）

- **语音引擎**：~/uai/models/funasr（fp32+int8 ONNX + tokens.txt 逐行 token 格式
  + test_wavs 真实语音），~/uai/src/models/funasr 是符号链接；适配器
  app/speech/features.py（numpy 特征管线）+ funasr.py（paraformer 路径）；
  功能验证 `scripts/audit/validate_asr.py`。**勿动共享 venv**（torch 被 vLLM 钉死）。
- **钉子**：多文件改动按文件 scp 容易漏（manager.py 漏发 → 修了还 400，探针抓到）；
  `awk f > f` 自截断；pydantic v2 bytes 字段吃 str 按 utf-8 编码非 base64 解码；
  CTC 连续重复折叠是正确语义。
- /tmp 自己的历史 _ 探针已清零（A.50.5）；root 所属 1 个不动（铁律 #3）。
  fish 对无匹配通配符整行中止——含通配符远端命令必须包 bash -c。
- **qdrant 未启动**（RAG 跑 pgvector 降级链路，4526 行正常返回）——设计内降级，
  feature_list `qdrant-deployment` 登记：上线后复测检索质量口径。
- **本机无 make**（WSL 有）——make 系列目标本机用 WSL 或 CI 跑。

## 凭据泄漏：已决定不轮换（A.28.10 更正 A.27.7 的建议）

`smoke-doctor` 密码明文进入过A.27 会话记录。**本轮决定不轮换**，理由与适用边界见
16 号附录 A.28.10：它是测试账号、只访问合成病例、主服务只绑回环、轮换会打断正在进行的
交付走查。**若该账号将来用于任何真实数据，必须立即轮换。**

该文件是纯密码、无 `=` 结构，别再用 `cut -d= -f1`「只看键名」—— 那等于打印密码。
本轮所有脚本已改为从环境变量注入，脚本与命令行均无密码字面值。

## 本机如何做浏览器验证（2026-10-05 新增，下一位 Agent 直接用）

主服务**只绑服务器 loopback**（硬规则 #3），所以本机必须走 SSH 隧道：

- 访问地址：**`http://127.0.0.1:8000/ui/`**
- 隧道已常驻：`~\.uai\keep_tunnel.ps1` + 计划任务 `UaiKeepTunnel`（登录自启；断了 5s 自动重连）
- 手动重连：`ssh -N -L 8000:127.0.0.1:18000 student03@10.20.28.21`
- 日志：`~\.uai\keep_tunnel.log`；删计划任务：`schtasks /Delete /TN UaiKeepTunnel /F`
- 改前端代码时：另起 `cd frontend && npm run dev`（3000 端口，vite 默认代理到 `127.0.0.1:8000`）
- 服务器重启主服务：走权威脚本 `bash ~/uai/src/scripts/start_main.sh`（**别再手写启动命令**）

## Snapshot（交接摘要）

- **交付三大阻塞已修**（A.23）：①8002 缺 `/v1/chat/completions` → LLM 全链路 404，已换回
  官方 OpenAI server（Qwen2.5-7B-Instruct，gpu-mem 0.46）；②临床前端**从未部署**——已构建
  （VITE_BASE=/ui/）并挂载 `/ui` + SPA 回落，契约对账 18 路径全命中；③8006 判官被误改为
  vLLM，已恢复 llama_cpp_server + GGUF（**启动必须串行**，并发互踩 memory probe）。
- **P2-10 订正并修复**：多器官并未"绕开 SG"（逐器官跑完整管线），真实缺口是
  `create_request` 漏传 clinical_text/patient_context/image_ids → SG 无事实运行；
  已透传 + 从同会话继承，5 条单测钉住。
- **新守卫**：`test_deploy_script_refs.py`（部署脚本悬空引用——llama_cpp_server.py 与
  vllm_server.py 先后被当探针删除但脚本仍引用，同款事故两次）；`check_frontend_api_contract.py`
  （前后端契约对账——**不能用 ast.parse**，TSX 必炸且静默 continue 会产出假通过）。
- **验收门禁**：`scripts/deploy_acceptance_gate.sh` 8 组 23 项，当前 PASS=23 FAIL=0；
  助手脚本已正式入库（drill_token/drill_pick_report/drill_tamper_check），/tmp 探针已清零。
- 分割旁路默认**关闭**（`nodule_contour_in_pipeline_enabled=False`），演练时用
  `NODULE_CONTOUR_IN_PIPELINE_ENABLED=1` 环境变量开启；**必须在重启主服务前 export**。
- 多器官分割矩阵：thyroid 0.7961 / cardiac 0.8395（CAMUS，按患者划分）/ liver 无公开数据。
- 未跟踪文件待处置：`scripts/_verify_services.sh`、`scripts/server_restart_api.sh`、`scripts/_smoke_graph.py`。

## 并发与性能审查（2026-10-03，本轮）

- **发现 26 项（P0×4 / P1×12 / P2×10），已修 17 项**，全部零语义风险项（加锁/信号量/上界/
  超时/回收/offload）；9 项未修并写明理由（其中 2 项需产品决策、1 项是 HEAD 既有 INT 失败）。
- 关键 P0：①多器官联合诊断**原先完全无限流**；②**refresh token rotate 的 check-then-act
  竞态**（同一 token 可派生两个有效后继 → reuse 检测被绕过）；③WS 下行**持锁 await
  send_json**；④登录 bcrypt + HSM 签名、onnxruntime、pydicom 解析在事件循环里同步执行。
- 关键 P1：诊断管线**无端到端超时**（`pipeline_dispatch/fusion_budget_s=6.0` 是从未被读取的
  孤儿配置，最坏 ~240s 占死全部 worker 槽位）→ 新增 `pipeline_total_budget_s=180`。
- 报告：`reports/concurrency_perf_audit.md` / `.json`；压测：`reports/concurrency_perf_report.json`
  （脚本 `scripts/bench_concurrency_staircase.py`，1/5/10 走 HTTP 多医生、20/50 走 direct_enqueue）。
- 回归：新增 46 例（4 个 `test_concurrency_audit_fixes.py`）；核心四包覆盖率 **91%**（上轮 90.04%）。
- **真机遗留**：接手时 8002 未监听（被并行工作线开 GPU 窗口时 `pkill VLLM::EngineCore` 连杀），
  本轮以 `--gpu-memory-utilization 0.40` 拉起 Qwen2.5-7B-Instruct（与判官 8006 共卡）。

## Blockers

| 阻塞 | 谁解 | 备注 |
|---|---|---|
| faithfulness 95% 门控口径 | 项目组 | 判官侧已穷尽（双口径 + 思考对照三方验证） |
| 甲状腺注册数据 | 管理决策 + 授权 | 无商用公开集，须自有/合作医院 |
| 核心覆盖率 90% | 真机依赖 | SoftHSM / 真实 Redis 语义 |
| 服务器代码与仓库同步 | 本机可做 | `git archive` 铺 HEAD + 重启主服务 |

## Files（交接涉及的路径）

- Harness：`AGENTS.md`、`progress.md`、`feature_list.json`、`session-handoff.md`、`init.sh`
- Skills：`.codebuddy/skills/remote-gpu-server-devtest/{SKILL.md,reference.md}`、`.codebuddy/skills/uai-harness-maintenance/{SKILL.md,reference.md}`
- 事实源：`docs-for-guiding generating docs/超声诊疗决策支持系统/{00-项目总览与核心指标.md,16-开发进展汇报-20260928.md}`
- 验证：`Makefile`、`pyproject.toml`、`.github/workflows/{ci.yml,security-scan.yml}`

## Next Session

1. 读 `progress.md` → 执行 `Recommended Next Step`（口径决策 / UDT 送审 / 清收未跟踪文件）。
2. 若触服务器：加载 SKILL `remote-gpu-server-devtest`，先读「铁律（先读）」5 条。
3. 收尾按 `AGENTS.md` 的 `End of Session` 走，并跑 harness 审计（分数 ≥70）。

## Restart Markers（clean restart 检查）

- [ ] `./init.sh` 通过（或说明为何跳过）。
- [ ] `progress.md` 的 Last Updated / Current Objective / Recommended Next Step 已刷新。
- [ ] `feature_list.json` 状态迁移已记录（含 evidence）。
- [ ] 新结论已追加 `16` 号附录 A（如涉及实测数字/决策）。
- [ ] 远程 `/tmp/_*` 已清、GPU 进程清单核对（嵌入×3 + vLLM 8002）。
