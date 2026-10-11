# progress.md — 当前状态（唯一「现在在哪」来源）

**Last Updated**: 2026-10-12（A.51 远期第一梯队四项收口）
**HEAD**: 分支 `main`（本轮：检索口径闭合（服务腿 vs eval diff +0.00pp）+ 多帧分期③择优帧 + 设备/患者列表端点 + fusion-validate 降载（P99 2296→885ms），四项均真机探针复验，详见 16 号附录 A.51）
**Owner of truth**: 本文件只记「当前状态/下一步/阻塞」；**功能与门控状态在 `feature_list.json`**，长期证据在 `16-开发进展汇报-20260928.md` 附录 A。

## Current Objective

把项目从「评测与模型能力攻关」推进到**生产化收口**：通用技术轨道（检索/推理/融合/安全网关/降级/
可观测）已达标；剩余为**门控口径待项目组决策项**、**外部数据依赖项**与**NMPA 交付物**。

## Current State

### What 已完成（有证据，详见 `feature_list.json` 与 16 号附录 A）

- **远期第一梯队四项收口（2026-10-12，A.51）**：
  ①**检索口径闭合**——p15 同进程双跑（服务器真机）：eval 腿 hit@10 **0.9853**（完美
  复现 Gate 2）、服务腿（Retriever→qdrant）**0.9853，diff +0.00pp**；过程中抓到
  **两个真缺陷**（qdrant-client ≥1.14 移除 `search()` 致 dense 腿静默全空 + kb_v1.3.0
  向量从未灌 qdrant），已修并重灌（qdrant 现纯 kb_v1.3.0/1257 点）。
  ②**多帧分期③帧级归属**——分割旁路由「rows[0]+中间帧」升级为**候选择优帧**
  （多影像取前 2 张代表帧 / 单文件多帧取 mid±2，按 mean_prob 择优），报告写「第 X/Y 帧
  （多帧候选择优）」，前端按帧门控渲染；真机 p17 **15/15**（多影像）、p18 **12/12**
  （单文件 7 帧 → 采样 [3,1,5]、chosen=5==argmax、报告含「第 6/7 帧」）。
  ③**设备/患者列表端点**——`GET /api/v1/admin/device`（分页/过滤、**零 api_key**）+
  `GET /api/v1/patients`（模糊检索 + 同意状态 + 最近会话），前端建会话页接患者候选
  下拉（手工录入路径不变）；真机 p16 **14/14**。**踩坑**：新增 schema 时插入边界吃掉
  相邻 `expires_at` → 运行期 ValidationError（静态检查发现不了），已加字段集守卫测试。
  ④**fusion-validate 降载**——基线 P50 1854/P99 2296ms（NOT_MET，双峰）→ 消 Self-RAG
  评估重试重叠 + 接线 validate 预算（超时优雅降级 needs_review）+ 非 FULL 档跳过
  Self-RAG LLM → 复测 **P50 717.5 / P99 884.6ms PASS**；降级路径真机实测
  `validate_degraded=1` 且结果正常返回（非 5xx）。

- **E11/E13 真 HAPI 闭环（2026-10-12，A.50.10）**：本机 maven 构建
  jpaserver-starter 8.12（370MB war）→ 服务器 java21 起**真 HAPI**（8080/fhir）
  → E13 报告 push 真进 HAPI（LOINC/presentedForm 读回 7/7）+ E11 我方 Patient
  被 HAPI 裁决 201（7/7）+ fhir.resources 合规 5/5。**清单 14/15 销号**，
  真外部仅剩 E5/E10。

- **E13/E11 攻坚（2026-10-11，A.50.9）**：**E13 DICOM 侧真实闭环销号**——
  pynetdicom C-STORE SCP（UAI_PACS@127.0.0.1:11112）+ CAMUS 真实帧推送→
  归档→读回一致 7/7；fhir_bridge 产出标准 R4 资源。E11：HAPI cli 三版本
  run-server 全坏（batch2 缺 bean/AIOOBE，官方已弃）——等官方 docker 镜像
  窗口（p13_fhir_hapi.py 已备好）。

- **假外部依赖清理（2026-10-11，A.50.8）**：E6 **qdrant 上线**（gh-proxy 拉
  二进制、pgvector→qdrant 迁移 3269 点、qdrant_client uuid5 id 修复、p11 走
  qdrant 9/9）+ E7 **OTA 真分发落地**（ota_package_dir 配置 + 1MiB 包 +
  p6 14/14）。真外部依赖仅剩 E5/E10/E11/E13（理由见 21 号甄别结论）。
  **新铁律**：git archive 同步=HEAD 镜像，会覆盖服务器上未提交文件——
  先 commit 再同步。

- **GPU 误判更正 + 清单收官（2026-10-11，A.50.7.1）**：用户指出 GPU 可用——
  实测证实是**我方环境污染**（CUDA_VISIBLE_DEVICES=2 + LD_LIBRARY_PATH 旧
  toolkit），`env -u LD_LIBRARY_PATH CVD=0` 即恢复（nvidia-smi 的 NVML 版本号
  瑕疵不影响 CUDA，误判教训入 SKILL）；E8 GPU 复跑与 CPU 逐帧一致；**E14
  dispatcher P50=1.34s < 2.5s 达标销号**（端到端 wall P50 5.25s）。清单
  E1~E4/E8/E9/E12/E14/E15 全销号，剩余 E5/E6/E7/E10/E11/E13 均外部依赖。

- **清单第二批：E8/E9/E12（2026-10-11，A.50.7）**：①GPU 实情=NVML 驱动
  mismatch（新进程 CUDA 不可用，登记 E15 待管理员），E8 改 CPU 全量闭环：
  **500 例 MYO mean 0.7388 / 95.2% ≥0.6 达标销号**（A.42 的 0.58 是 3 帧抽样
  偏差）；②E9/E12 并发核实单/双 worker 各 **8/8**——抓到**幂等键竞态真缺陷**
  （check-then-set → SET NX 原子占位修复，真机同 request_id）+ refresh rotate
  跨进程互斥真机验证（[200,401]）。

- **待摸排核实清单建立 + 第一批核实（2026-10-11，A.50.6）**：21 号路线图新增
  滚动清单（E1~E14「绿但未真机核」项）。第一批 E1~E4 核实：E1 帧级归属
  **抓到 2 处真缺陷**（过滤锚错位 + metadata.toolName 漏检 → 翻回原帧永不恢复）
  已修 ALL PASS；E2 RAG 证据链 9/9（pgvector 降级库真实参与）；E3 打印渲染
  ALL PASS（161KB PDF）；E4 非标采样率**医疗安全隐患**修复（8k 拒绝 400）。

- **远期第一梯队（2026-10-11，A.50）**：①告警联系人 PUT 语义定案（空表清空/
  无 webhook 放行/有 webhook 保持验证，p7 7/7）；②`make fe-smoke` 真浏览器
  守卫入 Makefile + init.sh 提示（本机隧道实测 20/20）；③**语音引擎部署**
  （paraformer-large CTC ONNX fp32+int8、适配器补齐特征管线 fbank→LFR→CMVN→
  CTC、启动预热、短输入 1s 填充）——validate_asr.py 3/3 条真实语音产出连贯
  中文（97~176ms/条），health engine_ready=true，p9 14/14。

- **端到端管线全量摸排（2026-10-10，A.49）**：10 条管线（走查/影像交互/测量标注/
  认证/同意撤回/设备/告警/报告分发/辅助/FHIR）逐一真机实测，探针套件入库
  `scripts/audit/` 可复跑；**探针 71 PASS / 1 P2 观察**，走查 21/21（录屏 A49）。
  挖出并修复 **3 个真缺陷**（全部真机复验+单测钉住）：①撤回 cancelled 终态
  读路径被同意门挡死（不可观测）；②语音触发判读 100% 500（签名漂移+绕开同意门+
  不入队）；③设备同名注册 500（name 无唯一约束→scalar_one_or_none 炸）。
  甄别 4 个探针误判（refresh 宽限窗/attributes 语言护栏/FHIR 同意前置/设备同名语义），
  登记 2 个 P2（speech 引擎未部署、告警联系人空表 PUT 语义）。

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

1. **交付收口（2026-10-12 A.51 更新，距 10-17 交付）**：P2 登记两项已销号
   （speech 引擎已部署、告警联系人语义已定案）；本轮第一梯队四项（检索口径、多帧③、
   设备/患者列表端点、validate 降载）**全部闭环**。**剩余**：①NMPA 验收材料
   （用户拍板：留到性能数据提升、远期目标攻克之后）；②远期第二梯队候选：
   分割 MYO GPU 全量稳定度、ULC graph-fusion 拼装、EchoCare 分割评测
   （见 21 号路线图）。
2. **语音功能验收待真实医生语音样本**：转写链路已通（模型自带语音 3/3），
   医疗术语标注的实效需医生真实口述样本验证（A.50.3 如实登记）。
3. **把真浏览器守卫接进门禁的第二层**：`make fe-smoke` 已就绪（本机 20/20），
   静态层已在服务器门禁（I 组）；
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
| ruff 棘轮 2 项红灯 | 基线口径 | 统一重定基线（A.51 附三已实证 HEAD~1 即红，非本轮引入；本轮不改基线） | `quality-debt` |
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
| `scripts/audit/p15_retrieval_parity.py` | A.51 检索口径（服务腿 vs eval，真机可复跑） |
| `scripts/audit/p16_list_endpoints.py` | A.51 设备/患者列表端点（14 项判定） |
| `scripts/audit/p17_multiframe_segmentation.py` | A.51 多影像择优帧（15 项判定） |
| `scripts/audit/p18_multiframe_singlefile.py` | A.51 单文件多帧择优帧 + 报告帧号（12 项判定） |
| `scripts/audit/p14b_validate_bench.py` / `p14c` | A.51 validate 时延基准（改前/改后 N=8 热态） |
| `tests/reports/t1_faithfulness_v11.json` | faithfulness 基线 |
| `tests/reports/phase0a_report.json` | 早期 6 例口径（已作废，仅存档） |

## Files（本轮相关）

- `AGENTS.md`（harness 入口）、`progress.md`、`feature_list.json`、`session-handoff.md`、`init.sh`（harness 五子系统）
- `.codebuddy/skills/remote-gpu-server-devtest/SKILL.md`（服务器作业手册）
- `.codebuddy/skills/uai-harness-maintenance/SKILL.md`（文档/SKILL 维护规范）

## Next

见 [Recommended Next Step](#recommended-next-step)；下一位 Agent 从这里起步即可 clean restart。
