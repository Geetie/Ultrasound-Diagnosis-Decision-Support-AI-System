# 核查B — 核心业务链路（HTTP 请求 → 结论落库）

> 核查时间：2026-10-28
> 代码基线：`git HEAD = c65da99`
> 方法：**只读代码，不运行**（本地跑不起来）
> 范围：`app/services/diagnosis_pipeline.py` / `diagnosis_worker.py` / `app/fusion/*` / `safety_gateway/*` / `app/api/v1/diagnosis.py` / `app/repositories/*`
> 不碰：前端、评测指标、架构图（属其他核查线）

---

## 0. 结论摘要（先看这 5 条）

| # | 结论 | 证据 |
|---|---|---|
| **1** | **真实链路是 13 步**，其中 5 步是旁路、4 步有硬开关、**只有 4 步在主判定路径上** | §1 |
| **2** | **融合层名义 4 层，单器官链路上"无条件生效"只有 1 层（Self-RAG）**；一致性仅"指南比对"一条子路径可能跑（正则级，非 LLM）；**冲突检测 4 类规则结构上恒空** | §2 |
| **3** | **安全网关 6 个阶段里有 3 个在生产上是空转**（阈值 / 一致性 / 边缘连接）—— 载荷里根本没有它们要的键 | §3 |
| **4** | **SG 判 `passed=False` 不改变落库状态**：`_write_result_to_db` 只看 `sg_ok`（传输层是否拿到响应），不看 `passed`（校验结论） | §4.1 |
| **5** | **"重试 / DLQ"只计数、不重跑**：`_handle_failure` 递增计数并写 DLQ，但**没有任何 re-enqueue**；而管线内部失败返回 `False` 不抛异常，连计数都不会发生 | §4.4 |

---

## 1. 完整调用链

### 1.1 入口与队列

```
POST /api/v1/diagnosis/request                      app/api/v1/diagnosis.py:232
  ├─ PermissionChecker("submit_diagnosis")          diagnosis.py:243
  ├─ check_rate_limit(...)                          diagnosis.py:249
  ├─ 影像归属校验（A.78 病人隔离）                    diagnosis.py:302-316
  ├─ check_diagnosis_admission()  → 队列深 ≥24 即 503  diagnosis.py:320 / admission_control.py:89-105
  ├─ request_id = _generate_request_id()            diagnosis.py:322
  ├─ 幂等键 SET NX 原子占位                          diagnosis.py:325-345
  ├─ diagnosis_repo.create_request(...)  status="processing"  diagnosis.py:352 / repository:88
  ├─ await diagnosis_repo.db.commit()   ← 必须先提交，否则 worker 读不到  diagnosis.py:368
  └─ enqueue_diagnosis(request_id)                  diagnosis.py:376
       ├─ Redis: RPUSH diagnosis:pending (先 LLEN 查上限 1000)  worker.py:93 / settings.py:335
       └─ 降级: 进程内 asyncio.Queue(maxsize=同上限).put_nowait  worker.py:120
```

**队列形态**：Redis list（`RPUSH` / `BLPOP`），**不是** asyncio 队列为主；进程内 `asyncio.Queue` 只是 Redis 异常时的降级（`worker.py:74-137`）。

**worker 数量**：`int_worker_pool_size = 4`（`settings.py:267`，仓库内无 `.env`、compose 未覆盖 → 生效值 4）。
`start_worker_pool` 在 lifespan 启动（`main.py:356-357`），起 4 个 `_worker_loop` 协程（`worker.py:336-343`）。

**并发模型**：单进程 4 协程 + 每 worker 顺序消费（`worker.py:192-257`）。**不是** 多进程多队列。

**每个任务一个端到端预算**：`asyncio.wait_for(..., timeout=pipeline_total_budget_s=180.0)`（`worker.py:232-235` / `settings.py:302`）。

### 1.2 管线 13 步（`run_diagnosis_pipeline`，`pipeline.py:241-648`）

| # | 步骤 | 行号 | 同步/阻塞 | 默认开 | 失败后果 |
|---|---|---|---|---|---|
| 1 | **读段**：读 request + 解析 patient_id + 登记在途 + 路由判定 + 灰度判定 + **取既往诊断** | 265-321 | 同步阻塞（DB） | 必跑 | 读不到 → `return False`（`pipeline.py:275-281`） |
| — | **DB session 在此关闭**（关键设计） | 322-328 | — | — | 避免跨 LLM 持有连接 |
| 2 | 读重试标志 → 决定是否跳过 Self-RAG | 332-344 | 同步（Redis） | 必跑 | 失败按"未设置"处理 |
| 3 | **分割旁路** `_safe_nodule_contour` | 351 / 1835 | **阻塞**（读盘 + UNet 推理 `asyncio.to_thread`，`pipeline.py:1945`） | **开**（`nodule_contour_in_pipeline_enabled=True`，`settings.py:481`） | `available=False` + 真实 reason，不影响判定 |
| 4 | **相似病例旁路** `_safe_similar_cases` | 357 / 1762 | 阻塞（读盘 + 向量检索） | **开**（`similar_cases_enabled=True`，`settings.py:424`）；无 `image_ids` 直接 skipped | `status="unavailable"` |
| 5 | 启动流式预览 watcher（段落级 SG） | 371 | **旁路协程** | **关**（需 `UAI_LLM_STREAM_PROGRESS=1`，`pipeline.py:1068`） | 仅日志 |
| 6 | **影像腿作用域** `image_leg_scope_for_request` | 401 | 旁路（永不抛） | 看 `rag_use_multimodal`（A.68 后默认关） | 写 `skip_reason` |
| 7 | **调度** `dispatcher.invoke`（RAG → prompt → LLM → parse） | 421 / `dispatcher.py:176-189` | **阻塞**（分钟级 LLM） | 必跑 | 返回 1 条 `confidence=0.0` 占位 + `needs_review=True` |
| 8 | **融合** `fusion_engine.fuse` | 437 / `fusion/service.py:110` | **阻塞**（含 Self-RAG LLM 与 CRAG） | 必跑 | 见 §2 |
| 9 | **T2a 影像分类旁路** | 464 / 2154 | 阻塞（HTTP 到 8003） | **关**（`t2a_classifier_in_pipeline_enabled=False`，`settings.py:487`） | `available=False` |
| 10 | **幻觉检测** | 473 / 2043 | 同步（纯正则/词表） | **开**（FULL 档触发，`pipeline.py:480` + `2081`） | `checked=False` + 真实 reason |
| 11 | **安全网关** `_safe_safety_check` | 495 / 1071 | 阻塞（HTTP，`pipeline_sg_budget_s=2.0`，`settings.py:292`） | 必跑 | `(None, False)` → 终态 `failed` |
| 12 | **三层脱敏汇总** | 509 / 1434 | 同步（只汇总台账） | 必跑 | `status="unavailable"` + reason |
| 13 | **写终态 + WS 推送** | 515 / 1183；538 | 阻塞（独立短会话） | 必跑 | 写失败 → `return False` |

**关键结构事实**：
- 管线**不抛异常**（除 `CancelledError` 外全捕获，`pipeline.py:571`），调用方只看到 `bool`（docstring `pipeline.py:251-255`）。
- 第 3/4/9 步是**旁路**：它们的结果只进 `findings`，**不改 status / confidence**（`pipeline.py:1230-1235` 只读 `fusion_result.needs_review`）。
- 第 3 步（分割）虽是"旁路"，但**在 dispatch 之前**且**阻塞**——它会把量化特征注入 LLM prompt（`pipeline.py:691-706`），所以是**关键路径上的耗时**，不是真旁路。

### 1.3 专科判读内部（第 7 步展开）

`RAGSpecialistAgent.invoke`（`app/agents/specialist/base.py:299-400`）：
1. RAG 检索（异常 → 空列表，`base.py:324-334`）
2. **空证据 → 直接返回 `confidence=0.0` + 可读中文 + `needs_review=True`**（`base.py:336-359`）
3. 拼 prompt → LLM（**有界重试 1 次，0.5s 退避**，`base.py:352-383`）
4. parse → `_post_process`（器官阈值：低于阈值置 `needs_review`）

**"1+N 多专科"在单器官端点上不成立**：`dispatcher.invoke()` 只调 `context.organ` 一个专科并把它包成**单元素 list**（`dispatcher.py:185-186`）。`dispatch_all()` 全仓**无生产调用方**（只被 `multi_organ_orchestrator.py:232` 的 `dispatch_organs_parallel` 调，而后者门控默认关 `multi_organ_parallel_dispatch_enabled=False`，`settings.py:357`，且**没有接进主循环**，见 `multi_organ_orchestrator.py:66-90` 的自述）。

---

## 2. 融合层：跑了几层

### 2.1 编排骨架

`FusionEngineImpl.fuse`（`app/fusion/service.py:110-262`）：

```
fuse()
 ├─ 1) 空输入 → 占位 FusionResult(needs_review=True)          service.py:127-150
 ├─ 2) LayeredValidator.validate(deadline)                    service.py:158-191
 │      （外层 wait_for，预算 pipeline_validate_budget_s=12.0 settings.py:321）
 │      超预算 → FusionResult(validate_degraded=True)          service.py:173-181
 │      异常   → FusionResult(validation_failed)               service.py:182-190
 ├─ 3) 选 final_response（confidence 最高）                    service.py:197
 └─ 4) Self-RAG 失败 → CRAG                                   service.py:201-208
```

### 2.2 四层逐层裁决

| 层 | 代码位置 | 生产单器官链路是否执行 | 判定 |
|---|---|---|---|
| **L1 冲突检测** | `layered_validator.py:102` → `conflict.py:153` | ❌ **恒不产生冲突** | **结构上不可达** |
| **L2 一致性** | `layered_validator.py:127` → `consistency.py:604` | ⚠️ 局部（见下） | 条件生效 |
| **L3 Self-RAG** | `layered_validator.py:128-131` → `self_rag.py:192` | ✅ **默认执行**（FULL 档） | 真生效 |
| **CRAG 兜底** | `service.py:201-208` → `crag.py:265` | ⚠️ 仅 Self-RAG 失败且 RAG 可用 | 条件生效 |

#### L1 冲突检测：**4 类规则全部要求 ≥2 份响应**

- 分级冲突：`if organ_value == "__none__" or len(group) < 2: continue`（`conflict.py:287`）
- 置信度冲突：`if len(responses) < 2: return []`（`conflict.py:313`）
- 建议冲突：`if len(responses) < 2: return []`（`conflict.py:335`）
- 证据冲突：`if len(responses) < 2: return []`（`conflict.py:384`）

而 `dispatcher.invoke()` 只返回 **1** 条（`dispatcher.py:185-186`）。
→ **单器官请求下 `ConflictReport.has_conflict` 恒为 `False`，且不是"检测后没冲突"，是"根本没比较对象"**。
→ 这四类规则唯一可能被喂饱的场景是**多器官会话的事后跨器官融合**（`cross_organ_fusion.py:70` 用**库里已落库**的结论重建 `AgentResponse` 再跑一次），且结果只写 `findings["cross_organ_fusion"]`，**不改任何器官的 status/confidence/正文**（`cross_organ_fusion.py:26-33`）。

#### L2 一致性：三条子路径，一条死、一条半活

`ConsistencyChecker.check`（`consistency.py:604-679`）：

| 子路径 | 触发条件 | 生产状态 |
|---|---|---|
| 与**既往诊断**比 | `request.prior_diagnosis is not None`（`:623`） | ✅ **已接线**（A.88 A2）：管线读段才取（`pipeline.py:315` → `_safe_load_prior_diagnosis:841`），`_safe_fuse` 传入（`pipeline.py:786`）。**但**：必须"同患者 + 同器官 + 已终态 + 非本次"（`repository:177-217`），**首次诊断必然取不到** |
| 与**指南**比 | `self._rag_service is not None and responses`（`:634`） | ⚠️ **半活**：`rag_service` 存在才跑；内部是**关键字/正则级语义比对**（`consistency.py:797-905`，`_semantic_compare`），不是 LLM |
| **跨器官**互斥矩阵 | `len(responses) >= 2`（`:656`） | ❌ **单器官恒不可达** |

**"与既往比"实际能查出什么**：只比 `grade`（分级码数字差）与 `confidence`（差值阈值），见 `_compare_with_prior:681`。
**查不了**：同一份正文的语义变化、"上次说观察这次说穿刺"这类临床转向（那要靠指南分支）。
**A.88 A1 修复过一处量纲错配**：`min_score_threshold=0.5` 是按 rerank 分（0~1）定的，而 standard 档是 RRF 分（0.016~0.033），原来恒把整批 chunk 剔空 → 指南分支拿不到证据（`rag/service.py:341-410` 已改为"有 rerank 分用绝对阈值、无则用相对阈值"）。

#### L3 Self-RAG：真跑，但**只跑 1 次**

- 触发：`use_self_rag = (not skip_self_rag) and (_effective_tier == FULL)`（`pipeline.py:344`）——默认 `response_tier=FULL`（`schemas.py:241`）→ **默认开**
- 重试次数：`self_rag_max_retries=0`（`settings.py:309` 传入 `main.py:679`）→ `max_attempts = 0 + 1 = 1`（`self_rag.py:242`）→ **实质无重试**
- 评分：三维取 min，`pass_threshold = 3.0`（`self_rag.py:82`）
- LLM 失败 / 解析失败 → **单次回退启发式**（`self_rag.py:247-262`）：要求 `confidence ≥ 0.7` **且** `evidence>0` **且** 正文非空（`self_rag.py:394-398`）
- 失败 → `review_reason="self_rag_failed"`（`layered_validator.py:160-170`）

#### CRAG：三重前置条件，缺一不触发

`service.py:201-208` 要求**同时**满足：
1. `request.use_crag` —— 管线恒传 `True`（`pipeline.py:781`）
2. `result.self_rag_passed is False`
3. `not result.validate_degraded`
4. `self._crag_handler is not None`
5. **`self._rag_service is not None`** ← 注意这条：RAG 初始化失败时 `app.state.rag_service=None`（`main.py:527`），此时即使 `crag_handler` 建出来了（`main.py:701-710` 允许任一后端存在即建），CRAG **也不会触发**

**CRAG 的"恢复成功"有一处隐患**：`_verify_recovery_quality` 在**占位路径**（无 `specialist_invoker`）下返回 `(True, "unverified_placeholder_evidence_only…")`（`crag.py:483-494`）——即"仅合并证据"也算 recovered；而 `service.py:216-222` 一看到 `recovered` 就**清掉 `self_rag_failed` 标记**（`needs_review=False; review_reason=None`）。生产上 `specialist_invoker=dispatcher.invoke` 已接线（`main.py:697`），所以这条路径是**测试/降级形态**，但它存在且无二次封堵。

### 2.3 "代码在但永远不执行"清单（融合层）

| 代码 | 为什么永不执行 |
|---|---|
| `ConflictDetector` 4 类规则 | 单器官只给 1 条响应（`dispatcher.py:186`） |
| `ConsistencyChecker._check_cross_organ` | 需 ≥2（`consistency.py:656`） |
| `consistency._compare_with_prior` 的首次诊断 | 库里没有既往行（`repository:177`） |
| `dispatch_all()` 真并发 | 门控默认关 + 未接主循环（`multi_organ_orchestrator.py:66-90`） |
| `SelfRAGEvaluator` 的"重试"分支 | `max_retries=0`（`settings.py:309`） |
| `FusionEngineImpl._validate_with_deadline` 的 `accepts_deadline=False` 分支 | 自家 `LayeredValidator` 签名恒有 `deadline`（`layered_validator.py:87`） |

---

## 3. 安全网关：6 个阶段与它们的实际状态

### 3.1 阶段顺序（`safety_gateway/checker/orchestrator.py:56-57, 152-191`）

```
同意校验 → 边缘连接 → 脱敏校验 → 阈值 → 禁忌(含组合判定) → 一致性
ConsentPhase → EdgeConnectivityPhase → DesensitizationPhase →
ThresholdPhase → ContraindicationThresholdPhase → ConsistencyPhase
任一阶段返回非 None 即短路（第一个非 None 直接 return）
```

### 3.2 逐阶段：查什么 / fail-closed 怎么实现 / 生产是否空转

| 阶段 | 查什么 | fail-closed 实现 | **生产状态** |
|---|---|---|---|
| **① 同意** | 入口快速拒绝（denied/withdrawn/pending）+ PG 直查最新状态 | 无状态 → `CheckResult.failed`（`consent_phase.py:83-93`）；超时 → failed（`:74-82`）；DB 错 → 抛 `DatabaseUnavailableError`（`:63-70`） | ✅ **真跑**。`consent_status_at_input` 由 `safety_gateway_client.get_consent_status()` 反查得到（`pipeline.py:1118`），拿不到就 `return None, False` → 终态 failed（`pipeline.py:1119-1125`） |
| **② 边缘连接** | `edge_connected` 为 False → 降级 | `CheckResult.degraded(EDGE_DISCONNECTED)`（`edge_connectivity_phase.py:23-31`） | ❌ **恒空转**：`SafetyCheckRequest.edge_connected` 默认 `True`（`main.py:86`），而**客户端根本不发这个字段**（`safety_gateway_client.py:192-202` 的 json 只有 6 个键，`edge_connected` 出现次数 = 0） |
| **③ 脱敏** | 输出载荷里有没有残留 PHI | 有敏感数据 + 融合不可用 → `failed(E4005A1)`（`desensitization_phase.py:41-51`） | ✅ 真跑（白名单字段见 `orchestrator.py:73-97`） |
| **④ 阈值** | 各条结论的 confidence 是否低于病种阈值 | `CheckResult.degraded(CONFIDENCE_BELOW_THRESHOLD)`（`threshold_phase.py:70-78`） | ❌ **恒空转**：读 `output_data.get("conclusions", output_data.get("diagnoses", []))`（`threshold_phase.py:30`），而生产载荷**只有** `organ/findings/recommendation/diagnosis_data` 四个键（`sg_output_data.py:105-115`）→ `conclusions` 恒空 → `if not conclusions: return None`（`:31-32`） |
| **⑤ 禁忌** | `diagnosis_data.patient_conditions` 是否命中规则 | **缺条件数据 → `return True`（判命中）**（`contraindication.py:216-229`） | ⚠️ **半活**：只有客户端显式传 `patient_context.conditions`（list）才会转发（`sg_output_data.py:113-115`）；`None` → 不转发 → 走"缺数据即命中" → 恒 `degraded(CONTRAINDICATION_HIT)`（`contraindication_threshold_phase.py:75-88`） |
| **⑥ 一致性** | 专科结论 vs 融合输出 | `CONTRADICTORY` → `degraded`（`consistency_phase.py:41-49`） | ❌ **恒空转**：读 `output_data.get("fusion_output", {})` 与 `output_data.get("specialist_conclusions", [])`（`consistency_phase.py:25-26`），**生产载荷无这两个键** → `len(specialist_conclusions)==0` → 走"自洽性检查"，而 `fusion_output={}` → 无 `diagnostic_conclusion` → 恒判 `CONSISTENT`（`consistency_phase.py:90-103`） |

> **净结论**：6 个阶段里 **3 个（②边缘 / ④阈值 / ⑥一致性）在生产上结构性空转**；⑤禁忌在"没传 conditions"时**恒判命中**（保守但等于把自动结论全挡掉）；真正在工作的只有 **①同意 + ③脱敏**。

### 3.3 有没有"某条件下直接 return True"的绕过路径

**有，两处，都是有意的保守设计，但方向相反**：

1. **`contraindication._match_condition` 末尾 `return True`**（`contraindication.py:229`）—— 缺条件数据时**判命中**（fail-closed，宁可多拦）。副作用：如果客户端不传 `conditions`，**每一例都命中禁忌** → 恒 `requires_manual_review`。
2. **`LayeredValidator._check_consistency_safe` 异常时返回 `is_consistent=True`**（`layered_validator.py:180-192`）—— 一致性检查崩了当"一致"处理。这是 **fail-open**，与 SG 的方向相反。虽然它只影响 `needs_review`，不阻断输出，但语义上是"探测器坏了 = 没检出问题"。
   同理 `conflict` 检测异常时 `has_conflict=False`（`layered_validator.py:103-112`）—— 也是 fail-open。

3. **`SafetyGatewayChecker.perform_full_check` 空载荷直接 `failed`**（`orchestrator.py:127-134`）—— 这是 fail-closed，正常。

### 3.4 SG 客户端的三处"看起来失败、实际算成功"

`app/services/safety_gateway_client.py::_attempt_check`（`:182-244`）：

| 返回 | 含义 | 后果 |
|---|---|---|
| `(json, False, None)` for HTTP 200（`:203`） | 成功 | `check_output` 返回结果，`sg_ok=True` |
| `(e4005, False, sub_code)` for HTTP 503（`:224`） | **SG 明确拒绝**（E4005A1/A2/B/C） | `check_output` 视为"拿到响应"→ **`sg_ok=True`**，`passed=False` |
| `(None, True, None)` for 超时/连接错（`:231,234,241`） | 传输失败 | 重试 → 最终 `_e4005_response()`，`sg_ok=True` |
| `(e4005, False, None)` for 其它异常（`:244`） | 未知异常 | 同上 |

**唯一让 `sg_ok=False` 的路径**：`get_consent_status()` 返回 `None`（`pipeline.py:1119-1125`）或 `patient_id` 为空（`pipeline.py:1112-1117`）。
→ **"SG 判你不许输出"这件事，在管线里根本不算失败。**

---

## 4. 错误处理、降级与数据一致性

### 4.1 【严重】SG 的 `passed=False` 不改变落库状态

`_write_result_to_db`（`pipeline.py:1183-1375`）的状态判定**只看 `sg_ok`**：

```python
1216  if not sg_ok:
1217      status = "failed"; confidence = 0.0; review_reason = "safety_gateway_unavailable"
1220  else:
1221      status = "completed"
...
1231      if fusion_result.needs_review:
1235          status = "needs_review"
```

`sg_result` 之后**只被塞进 findings**（`pipeline.py:1250`：`"sg_result": sg_result or {...}`），**没有任何 `if not sg_result["passed"]` 分支**（全仓 grep `"passed"` 在 pipeline 里只命中 `:1047` 的流式预览）。

**后果链**：
- SG 因"同意已撤回 / 敏感数据 / 禁忌命中 / 组合故障"判 `passed=False` → 落库 `status="completed"`，`confidence` 保留原值，正文完整。
- 读路径会补一道：`if not sg_result.get("passed", ...)` → `requires_manual_review=True` + `desensitization_status=DEGRADED` + `consent_status_at_output=DENIED`（`diagnosis.py:775-781`）。**但 `diagnosis=diagnosis_payload`（`diagnosis.py:917`）照样把全文结论返回**。
- `degraded_output`（SG 算好的"只留白名单字段"的降级版本，`safety_gateway/main.py:466-467`）**全仓无消费者**：只有 `three_layer_desensitization.py:158-159,189-190` 读过一次并塞进 `layer_result["final"]`，而那个 `final` 又被 `pipeline.py:1480-1493` 的汇总函数**丢掉**（只回 `status/all_passed/security_check_failed/degraded_mode/layers/elapsed_ms`，没有 `final`）。
- `conclusion_suppressed` 参数在 SG 内部全链有实现（`orchestrator.py:115,167-188`），但**生产无任何赋值点**（grep `conclusion_suppressed=True` 只命中测试）。

→ **"SG 拒绝输出"目前只体现为读接口上的一个 `requires_manual_review=True` 标志，结论正文、落库状态、签发门禁都不受影响。**

**再确认一次读路径**：`diagnosis=diagnosis_payload`（`diagnosis.py:917`）是**无条件赋值**——`requires_manual_review` 的分支（`diagnosis.py:775-781`）只改 `desensitization_status` / `consent_status_at_output` / `requires_manual_review` / `review_reason` 四个字段，**没有任何"清空 diagnosis"或"替换成 degraded_output"的代码**。

### 4.2 【严重】三层脱敏的合规判定不进任何门禁

- `_run_desens_full_pipeline`（`pipeline.py:1434-1493`）确实产出了 `all_passed` / `security_check_failed` / `degraded_mode`，写进 `findings["desensitization"]`（`pipeline.py:1325-1326`）。
- **但没有任何代码读它**：全仓 grep `all_passed` 在 `app/` 下只有 `knowledge/baseline_annotation.py:528`（另一套东西）+ 本模块自身。
- 也就是说：**"三层全过"这个结论产出后被写进 JSONB，然后没人用它做任何判断**。

### 4.3 【严重】报告签发门禁不看"是否需人工复核"

`_assert_signable`（`report.py:90-109`）只检查 `SIGN_BLOCKING_SECTIONS = ("findings","diagnosis")`（`report_service.py:94`）是否空缺。
`requires_manual_review` / `ai_diagnosis_failed` / `sg_result.passed` **都不在门禁里**。
→ **一份 SG 判"检测到禁忌、需人工核验"的诊断，只要正文有字，就能直接签发。**
（唯一间接拦截：结论为空时 `build_report_content` 写 `"（模型未产出结论，请人工评估）"`（`report_service.py:518`），而 `_is_missing` 会把以 `（模型未产出` 开头的串判为缺失（`report_service.py:125,234-244`）→ 该段算缺 → 挡住签发。）

### 4.4 【严重】"重试 / DLQ"只计数，不重跑

`_handle_failure`（`worker.py:273-300`）：
```
incr diagnosis:retry_count:{id}  → expire 86400
if new_count >= int_dlq_max_retries(3): RPUSH diagnosis:dlq
```
**它不 re-enqueue。** 全仓 `enqueue_diagnosis` 的调用点只有：`diagnosis.py:376`（首次提交）、`diagnosis.py:534`（医生确认升级后重跑）、`voice.py:84`、`navigation_upgrade.py:383`。**worker 失败路径里没有任何一次重新入队。**

而且**只有抛异常才会进 `_handle_failure`**（`worker.py:246-257`）。但 `run_diagnosis_pipeline` **内部把一切都 catch 了**（`pipeline.py:571`），返回 `False`。所以：
- 写库失败（`pipeline.py:1367-1374` 返回 `False`）→ `success=False` 只被 `logger.info` 记一笔（`worker.py:237-243`），紧接着**照样 `_clear_retry_count`**（`worker.py:245`）→ **一次都不计、更不进 DLQ**。
- 只有 `asyncio.wait_for` 超时（180s）或 worker 自身异常才走计数。

→ **状态语义**：`status="failed"` 的行**没有任何自动重试机制**；DLQ 里的条目也**不会被自动消费**（只有人工）。

### 4.5 降级清单：设计内（有留痕）vs 静默

**全部 31 处 `except Exception` 都有 `logger.*`**（逐块扫描 `pipeline.py`，无一处空 except / `pass`）。这是本仓做得最好的一点。

| 降级点 | 类型 | 留痕质量 |
|---|---|---|
| RAG 检索失败 → 空列表（`base.py:324-334`） | 设计内 | ✅ `rag_failed: {type}` 进 trace |
| LLM 失败 → 重试 1 次 → 占位（`base.py:352-400`） | 设计内 | ✅ `llm_retry: 1` / `llm_unavailable` 进 trace |
| dispatch 失败 → 1 条 `confidence=0`（`pipeline.py:731-748`） | 设计内 | ✅ `needs_review=True` + 可读中文 |
| fusion 失败 → 取最高 conf（`pipeline.py:750-797`） | 设计内 | ✅ `review_reason="fusion_layer_failed"` |
| validate 超预算 → `validate_degraded=True`（`service.py:173-181`） | 设计内 | ✅ 但**冲突/一致性已算出的结论一起丢弃**（注释自承，`service.py:155-163`） |
| SG 不可达 → `(None, False)` → failed（`pipeline.py:1140-1155`） | 设计内 | ✅ |
| 一致性检查异常 → `is_consistent=True`（`layered_validator.py:186-192`） | **fail-open** | ✅ 有日志，但语义是"坏了=一致" |
| 冲突检查异常 → `has_conflict=False`（`layered_validator.py:103-112`） | **fail-open** | 同上 |
| 监控上报失败（`pipeline.py:2363-2376`） | 旁路 | ✅ |
| WS 推送失败（`pipeline.py:542-547`） | 旁路 | ✅ |
| 脱敏 L1/L2 引擎异常 → **原文/原样透传**（`pipeline.py:1533-1550, 1571-1588`） | 设计内（L3 兜底） | ✅ `desens_l1_failed_passthrough` |
| 三层汇总异常 → `status="unavailable"`（`pipeline.py:1467-1479`） | 设计内 | ✅ 真实原因 |

**唯一称得上"静默"的**：`_commit_if_possible` 在 commit 调用本身抛错时只 `warning` 后 `return`（`pipeline.py:116-120`）——事务可能没提交，但调用方按成功继续。

### 4.6 结论为空时会发生什么

分三条路：

1. **RAG 空 / LLM 失败** → 专科返回可读中文占位（`readable.py:112-151`，如"本次未检索到…建议转人工"），`confidence=0.0`，`needs_review=True` → 落库 `status="needs_review"`，正文非空 → **报告可生成、可签发**。
2. **dispatch/fusion 异常** → `_make_failed_response` / `_make_failed_fusion_result` 给普通话术，同样 `needs_review=True`。
3. **管线级异常** → `findings={"fusion_result":{"content": pipeline_failed_text(...), "degraded":True}, ...}`（`pipeline.py:596-630`），**也留可读正文**（A.73 明确要求"失败也必须留下可读正文"）。

→ 因此"结论为空"在**写路径**上基本不会发生；真正会空的是**历史遗留行**（`findings={"status":"processing"}` 或 NULL），由运维脚本 `scripts/ops/sweep_stale_processing.py` 清扫（默认 dry-run，`--minutes 30`）。

### 4.7 DB session 与跨 LLM 持有连接

`run_diagnosis_pipeline` 里共 **7 个独立 session 上下文**（`pipeline.py:265, 598, 1163, 1336, 1788, 1871, 2200`）：

| 行 | 用途 | 是否跨 LLM |
|---|---|---|
| 265 | 读 request / patient / prior | ❌ **在读段就关**（`:322`）✅ 设计正确 |
| 598 | 异常路径写 failed | ❌ 独立短会话 |
| 1163 | 取消路径写 cancelled | ❌ 独立短会话 |
| 1336 | 写终态 | ❌ 独立短会话 + `db.commit()`（`:1354`） |
| 1788 | 相似病例取 storage_path | ✅ **先关再 IO**（`:1803` 才 `storage.retrieve`） |
| 1871 | 分割取候选帧 | ✅ **先关再解码**（`:1908` 才 import 解码器） |
| 2200 | T2a 取 storage_path | ✅ **先关再 IO**（`:2217` 才 retrieve） |

→ **单器官管线在这一点上是干净的**，符合 AGENTS.md 硬规则 #6。

**但多器官编排器有一条风险**：`run_multi_organ_diagnosis` 在 `async with AsyncSessionLocal() as db:`（`multi_organ_orchestrator.py:383`）里包住**整个 3 器官串行循环**（到 `:490`），期间每次 `_run_single_organ` 会跑一条完整管线（含分钟级 LLM）。虽然每次 `_persist_progress` 会 commit（`:829`）把事务结束，但**外层 session 在整个 90s 预算内一直占着一个连接**（`db_pool_size=20`，`settings.py:44`）。3 个并发多器官会话 = 3 个连接被占满整个 LLM 时长。这与"跨 LLM 持有连接"的告警是同一族问题，只是强度略低（commit 后非 idle-in-transaction）。

### 4.8 状态机：定义了 5 个终态，实际可达 4 个

`TERMINAL_RESULT_STATUSES = ("completed","needs_review","cancelled","failed","timeout")`（`schemas.py:69-75`）

| 状态 | 谁写 | 可达 |
|---|---|---|
| `processing` | `create_request`（`repository:88`）、确认升级重跑（`diagnosis.py:528`） | ✅ |
| `completed` | `_write_result_to_db`（`pipeline.py:1221`） | ✅ |
| `needs_review` | `_write_result_to_db`（`pipeline.py:1235`）、多器官单器官超时（`orchestrator:628`） | ✅ |
| `failed` | 管线异常（`pipeline.py:602`）、入队失败（`diagnosis.py:213`）、清扫脚本 | ✅ |
| `cancelled` | 取消处理（`pipeline.py:1167`） | ✅ |
| **`timeout`** | **无人写入** —— 只是读路径的**派生值**（`diagnosis.py:795-798`） | ❌ **从不落库**（本仓 `scripts/audit/p64_degraded_cause_attribution.py:32,656` 明确记录"全库 0 行"） |

**`OrganStatus` 是另一套 6 值枚举**（`schemas.py:19-25`，`pending/in_progress/completed/failed/skipped/needs_review`），只用于多器官会话的 `organ_statuses` 进度，与上面的 `diagnosis_results.status` **不是同一个状态机**。

---

## 5. 最严重的 3 个问题（按危险度排序）

### ① SG 的否决权只到"标志位"，不到"结论"

- 证据：`pipeline.py:1216`（只判 `sg_ok`）、`pipeline.py:1250`（`sg_result` 只入 findings）、`diagnosis.py:917`（结论正文照返）、`report_service.py:94`（签发门禁不看复核标志）
- 后果：**同意撤回 / 敏感数据 / 禁忌命中 → 结论仍然写出、写全、可签发**。系统对外表达的合规语义（"安全网关是最终防线"）与代码实际行为不一致。
- 唯一残留的约束：读接口的 `requires_manual_review=True`（前端可能据此高亮，但**不影响** `diagnosis` 字段内容）。

### ② 融合层的"冲突检测"是一个永远不会响的警报器

- 证据：`conflict.py:287,313,335,384` 四处 `len < 2` 判据；`dispatcher.py:185-186` 只产 1 条
- 后果：**任何"融合检测到 N 处冲突"的表述在单器官链路上都不成立**。`conflict_detected` 字段永远 `False`，而它代表的是"没有可比对象"，不是"比过且一致"。
- 同理：`ConsistencyChecker._check_cross_organ`、`dispatch_all` 真并发，都是"代码在、生产不跑"。

### ③ 失败没有重试，状态却是"终态"

- 证据：`worker.py:245`（`success=False` 也清计数）、`worker.py:246-257`（只有异常才计数）、`worker.py:273-300`（`_handle_failure` 无 re-enqueue）
- 后果：一次瞬时故障（Redis 抖、PG 短断、写库超时）→ 行停在 `failed`，**永不自动恢复**；DLQ 只是"墓碑"，没有消费者。医生侧表现为"这次判读失败了，只能医生自己在界面上重发"。

---

## 6. 与"已核实/未核实"的边界说明

本核查**全部为静态代码阅读**，以下事项**本核查无法证明**（需真机）：

| 事项 | 为什么不能从代码断定 |
|---|---|
| 生产 `rag_service` 是否为 `None` | 取决于 RAG 初始化是否成功（`main.py:490-528`），需真机日志 |
| 生产 `response_tier` 实际取值 | 默认 FULL，但前端可能传 standard（`schemas.py:241`） |
| `patient_context.conditions` 医生侧是否真的传 | 取决于前端表单，需真机抓包 |
| Self-RAG 实际通过率 | 需真机样本 |
| 180s 端到端预算是否够 | 需真机 P99 |

**已确认"不需要真机也能断定"的**：上述 ① ② ③ 三条——它们是**控制流结构**决定的，与运行环境无关。
