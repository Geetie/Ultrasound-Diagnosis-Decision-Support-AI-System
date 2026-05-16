# Phase 0A API接口轨道 — 系统性审查报告

**审查日期**：2026-05-13  
**审查范围**：Phase 0A API轨道（FastAPI主服务、诊断接口、WebSocket双连接、DICOM接口）  
**审查原则**：独立、客观、深入、批判性  
**参考文档**：03-接口规范.md(P0)、06-开发环境与进度规划.md(P0)、01-系统架构设计.md(P1)  

---

## 一、审查总结

### 总体评估

Phase 0A API轨道的核心框架已基本搭建完成，主要接口路径、数据模型、错误码体系、限流机制、WebSocket双连接架构均已实现。34个Phase 0A单元测试全部通过。但审查发现**4个严重规范偏离**、**10个中等规范偏离/缺失**和**5个轻微代码质量问题**，需要在进入下一阶段前修复。

### 问题统计

| 严重级别 | 数量 | 说明 |
|----------|------|------|
| 🔴 严重 | 4 | 规范明确偏离，影响安全性或合规性 |
| 🟡 中等 | 10 | 规范部分偏离或功能缺失 |
| 🟢 轻微 | 5 | 代码质量/风格问题 |
| **合计** | **19** | |

---

## 二、详细审查结果

### 维度一：设计规范一致性

#### 2.1 诊断请求接口（POST /api/v1/diagnosis/request）

| # | 检查项 | 结果 | 说明 |
|---|--------|------|------|
| 1 | 路径一致性 | ✅ | `/api/v1/diagnosis/request` 完全一致 |
| 2 | X-Priority Header | ✅ | 支持0/1/2三级，默认值1 |
| 3 | Request字段完整性 | ⚠️ | 缺少`voice_text`在创建请求时未传递给repository；`request_id`允许客户端传入（规范中由服务端生成） |
| 4 | Response字段完整性 | ✅ | request_id, session_id, status, estimated_time_ms, response_tier 均存在 |
| 5 | 幂等性实现 | ✅ | idempotency_key + 600s TTL（与规范10分钟一致） |
| 6 | retry跳过Self-RAG | ✅ | 通过Redis flag实现 |
| 7 | response_tier默认值 | ✅ | schemas.py中`ResponseTier.FULL`为默认值，与规范一致 |

**🔴 严重问题 S1**：[diagnosis.py:87](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/api/v1/diagnosis.py#L87) — `DiagnosisRequest`允许客户端传入`request_id`（schemas.py:99 `request_id: Optional[str] = None`），但规范§1.3中`request_id`仅出现在Response中，由服务端生成。客户端传入可能覆盖服务端生成的ID，存在ID冲突风险。

**修复建议**：从`DiagnosisRequest`模型中移除`request_id`字段，或在endpoint中忽略客户端传入的值。

#### 2.2 诊断结果接口（GET /api/v1/diagnosis/result/{request_id}）

| # | 检查项 | 结果 | 说明 |
|---|--------|------|------|
| 1 | 路径一致性 | ✅ | 完全一致 |
| 2 | Response字段完整性 | ✅ | 所有规范字段均存在于DiagnosisResult模型 |
| 3 | 轮询策略 | ⚠️ | 轮询策略为前端行为，后端仅提供接口；超时检测已实现（30s） |
| 4 | 超时处理 | ✅ | status="timeout" 已实现 |
| 5 | 结果缓存10分钟 | ✅ | RESULT_CACHE_TTL_SECONDS = 600 |
| 6 | SafetyGateway输出前校验 | ✅ | 已实现check_output调用 |
| 7 | hallucination_severity一期固定null | ✅ | DiagnosisResult模型中为Optional[str]，默认None |

**🔴 严重问题 S2**：[diagnosis.py:198-199](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/api/v1/diagnosis.py#L198) — SafetyGateway返回`allowed=false`时，直接抛出`SafetyGatewayUnavailableException(E4005)`。但规范§4明确定义E4005为"安全校验服务不可用"，而SG返回`allowed=false`可能是因为**内容不通过安全校验**（如检测到PII），并非服务不可用。两者语义完全不同：
- 服务不可用 → E4005，503，可重试
- 内容不通过 → 应返回200 + 降级标记，不可重试

**修复建议**：区分SG返回的`allowed=false`原因——若`degraded_mode=true`则走降级输出路径；若因内容安全校验不通过则返回200 + 相应标记。

#### 2.3 WebSocket双连接

| # | 检查项 | 结果 | 说明 |
|---|--------|------|------|
| 1 | 导航通道路径 | ✅ | `/api/v1/realtime/nav` 一致 |
| 2 | DICOM帧通道路径 | ✅ | `/api/v1/realtime/dicom` 一致 |
| 3 | 双连接认证流程 | ✅ | 导航通道认证→签发Token→DICOM帧通道用Token认证 |
| 4 | 认证超时5秒 | ✅ | `asyncio.wait_for(..., timeout=5.0)` |
| 5 | 关闭码4001/4002 | ✅ | WS_CLOSE_AUTH_FAILED=4001, WS_CLOSE_AUTH_TIMEOUT=4002 |
| 6 | 心跳间隔5s | ✅ | heartbeat_check每5s执行 |
| 7 | 二进制帧格式 | ✅ | 4B frame_index + 1B flags + 4B dicom_length + NB data + 4B CRC32 |
| 8 | 小端序 | ✅ | `struct.unpack("<I", ...)` |
| 9 | frame_index负值拒绝 | ⚠️ | 实现检查`frame_index >= 0x80000000`（有符号整数最高位），但规范要求`frame_index >= 0`，应直接检查`< 0` |
| 10 | 心跳超时15s | ⚠️ | 规范"连续3次无pong(5s间隔)判定断开"=15s，实现一致但语义表达不同 |
| 11 | 重连策略 | ❌ | 未实现指数退避重连（1s→2s→4s→8s→最大30s） |
| 12 | DICOM帧缓存90帧限制 | ❌ | 未实现重传范围限制 |
| 13 | zstd压缩协商 | ❌ | 未实现 |
| 14 | 禁止URL query传Token | ✅ | API Key通过消息体传递；session_id/device_id通过query传递但非Token |

**🟡 中等问题 M1**：[dicom.py:116](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/api/websocket/dicom.py#L116) — `frame_index`负值检查使用`>= 0x80000000`（无符号整数最高位），这是因为`struct.unpack("<I", ...)`返回无符号整数。虽然逻辑正确（无符号整数>=0x80000000对应有符号负数），但代码可读性差，且与规范"frame_index ≥ 0，负值拒绝"的表述不一致。

**🟡 中等问题 M2**：重连策略（指数退避）未实现。规范§1.2明确要求"指数退避（1s→2s→4s→8s→最大30s），重连后自动恢复导航"。当前实现中WebSocket断连后无自动重连机制，需依赖客户端（前端reconnecting-websocket库）实现。

**🟡 中等问题 M3**：DICOM帧缓存90帧重传限制未实现。规范§1.2要求"后端重传请求的帧索引范围不超过最近90帧，超出范围的帧索引返回E1001"。当前实现中重传请求无帧索引范围校验。

**🟡 中等问题 M4**：zstd压缩协商未实现。规范§1.2要求"边缘端与后端在WebSocket握手阶段协商是否启用zstd压缩（握手参数X-Compression: zstd）"。当前实现中无压缩协商机制。

#### 2.4 DICOM影像接入

| # | 检查项 | 结果 | 说明 |
|---|--------|------|------|
| 1 | 路径一致性 | ✅ | `/api/v1/imaging/dicom/upload` 一致 |
| 2 | multipart/form-data | ✅ | 使用`File(...)`和`Form(...)` |
| 3 | 文件大小限制 | ✅ | `settings.dicom_storage_max_file_size`默认524288000(500MB) |
| 4 | 必需DICOM Tag验证 | ✅ | 10个必需Tag全部验证 |
| 5 | 拒绝条件 | ✅ | Modality≠US、缺少Tag、BitsAllocated>16均已实现 |
| 6 | SOP Class/Transfer Syntax | ❌ | 未在代码中体现（边缘端pynetdicom SCP职责，非后端API） |
| 7 | patient_id脱敏 | ❌ | 返回原始PatientID，未脱敏为P******格式 |

**🔴 严重问题 S3**：[imaging.py:96](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/api/v1/imaging.py#L96) — `extract_metadata_from_dataset`中`patient_id`直接返回原始值`str(getattr(ds, "PatientID", ""))`，未做脱敏处理。规范§1.1 Response示例中`patient_id`为`"P******"`格式。同样，`patient_name`（imaging.py:97）也未脱敏。这构成**患者隐私泄露风险**，违反04-安全合规要求。

**修复建议**：在`extract_metadata_from_dataset`中对`patient_id`和`patient_name`执行脱敏处理（如`patient_id[:1] + "******"`）。

#### 2.5 错误码体系

| # | 检查项 | 结果 | 说明 |
|---|--------|------|------|
| 1 | E1xxx完整性 | ✅ | E1001, E1004, E1006, E1007, E1008, E1009, E1010, E1011, E1012 均有异常类 |
| 2 | E2xxx完整性 | ✅ | E2001, E2002, E2003, E2004, E2004A, E2004B, E2006, E2007 均在ERROR_CODE_MAP中 |
| 3 | E3xxx完整性 | ✅ | E3001-E3005 均有异常类，且status_code=200（降级场景） |
| 4 | E4xxx完整性 | ⚠️ | E4001-E4012在MAP中，但E4007实现与规范冲突 |
| 5 | 统一错误响应格式 | ✅ | ErrorResponse模型包含全部7个字段 |
| 6 | 降级场景HTTP 200 | ✅ | SafetyException基类status_code=200 |

**🔴 严重问题 S4**：[errors.py:366-374](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/api/v1/errors.py#L366) — `HSMUnavailableException`使用错误码`E4007`，但规范§4明确定义E4007为"Redis不可用导致新会话创建延迟增加（PostgreSQL替代Redis写入）"。HSM不可用在规范中无独立错误码定义，应使用其他错误码或新增。

同时，ERROR_CODE_MAP中E4007映射为"HSM不可用"，与规范定义冲突。

**修复建议**：
1. 将`HSMUnavailableException`的错误码改为未使用的编号（如E4013）
2. 新增`RedisUnavailableNewSessionDelayException`使用E4007，描述与规范一致
3. 更新ERROR_CODE_MAP

#### 2.6 限流规范

| # | 检查项 | 结果 | 说明 |
|---|--------|------|------|
| 1 | 令牌桶算法 | ✅ | TokenBucket类实现正确 |
| 2 | 限流规则 | ✅ | 诊断10/分、影像30/分、反馈20/分 |
| 3 | 超限响应 | ✅ | HTTP 429 + E1007 + Retry-After: 5 |
| 4 | 急诊豁免 | ✅ | compact=2x, full=5x |
| 5 | IP+用户ID组合限流 | ✅ | bucket_key = `{ip}:{user_id}:{path}` |
| 6 | FHIR接口限流 | ❌ | RATE_LIMITS中未包含FHIR路径 |
| 7 | 多器官计1次 | ❌ | 未实现 |
| 8 | retry不计限流 | ❌ | 未实现 |

**🟡 中等问题 M5**：FHIR接口限流未配置。规范§7要求"FHIR接口：60次/分钟/用户"。RATE_LIMITS字典中无FHIR相关路径。

**🟡 中等问题 M6**：规范要求"多器官联合诊断计为1次，不按器官数累加"，当前限流中间件无法区分单器官与多器官诊断请求。

**🟡 中等问题 M7**：规范要求"重试请求retry=true不计入限流"，当前限流中间件在请求到达前执行，无法读取请求体中的retry字段。

#### 2.7 知情同意接口

| # | 检查项 | 结果 | 说明 |
|---|--------|------|------|
| 1 | GET路径 | ✅ | `/api/v1/consent/{patient_id}` |
| 2 | POST路径 | ✅ | `/api/v1/consent` |
| 3 | PostgreSQL主存储 | ✅ | 无Redis双写 |
| 4 | 撤回时Redis Streams | ✅ | 发布到CONSENT_WITHDRAWAL_STREAM |
| 5 | 无记录时行为 | ⚠️ | 抛出403+E1008，规范未明确定义此场景 |

**说明**：实现中对无同意记录返回403+E1008（PatientNotConsentedException），代码注释中已标注"行为变更说明（前端需适配）"。此行为虽与规范§5.1 Response示例（返回consent_status字段）不完全一致，但从安全角度更严格（无记录=未同意），属于合理的防御性实现。

#### 2.8 枚举与数据模型

| # | 检查项 | 结果 | 说明 |
|---|--------|------|------|
| 1 | 会话状态枚举 | ✅ | 7个状态值完全一致 |
| 2 | 器官状态枚举 | ✅ | 6个状态值完全一致 |
| 3 | WebSocket路径 | ✅ | 与§8.3一致 |
| 4 | ConsentStatus扩展 | ⚠️ | 新增PENDING和DENIED，规范仅定义granted/denied/withdrawn |

**🟡 中等问题 M8**：`ConsentStatus`枚举包含`PENDING`和`DENIED`两个规范外扩展值。规范§5.1仅定义`granted/denied/withdrawn`三种状态。`PENDING`可能用于"同意书已提交但尚未审批"场景，`DENIED`可能用于"明确拒绝签署"场景。这些扩展虽合理但需在规范中补充定义，避免前后端理解不一致。

---

### 维度二：代码质量评估

#### 2.9 WebSocket连接管理健壮性

| # | 评估项 | 风险等级 | 说明 |
|---|--------|----------|------|
| 1 | asyncio.Lock粒度 | 🟢 低 | 全局锁保护所有Dict操作，简单但可能成为高并发瓶颈 |
| 2 | 心跳任务cancel | 🟢 低 | finally中正确cancel，异常路径安全 |
| 3 | auth_tokens内存存储 | 🟡 中 | 进程重启后Token丢失，所有已认证DICOM帧通道需重新认证。对于一期单进程部署可接受 |
| 4 | 单session单连接 | 🟡 中 | `active_connections[session_id]`仅存一个WebSocket，多设备同session会互相覆盖 |
| 5 | retransmit_counts内存泄漏 | 🟢 低 | 连接断开后Dict随函数退出被GC，无持久泄漏 |

**🟡 中等问题 M9**：[nav.py:49-50](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/api/websocket/nav.py#L49) — `active_connections`以`session_id`为key，同一session_id仅支持一个WebSocket连接。若多设备（如超声科多台设备）共享同一session，后连接的设备会覆盖前一个的连接。规范§1.2中`session_id`与`device_id`为独立参数，暗示可能多设备共享session。

**修复建议**：将key改为`(session_id, device_id)`元组，或使用`session_id`关联多个连接的列表。

#### 2.10 诊断接口代码质量

| # | 评估项 | 风险等级 | 说明 |
|---|--------|----------|------|
| 1 | Redis错误时继续执行 | 🟡 中 | 幂等性检查Redis失败时仅warning，可能导致重复创建诊断任务 |
| 2 | 硬编码占位数据 | 🟢 低 | Phase 0A阶段可接受，待核心推理链路集成后替换 |
| 3 | SG校验失败处理 | 🔴 高 | 详见严重问题S2 |
| 4 | Redis key不一致 | 🟢 低 | diagnosis.py:166使用手写key，其他地方用RedisKey类 |
| 5 | db.flush()无commit | 🟡 中 | [diagnosis.py:246](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/api/v1/diagnosis.py#L246) `result.result_pushed = True; await db.flush()` 后未commit，依赖FastAPI的依赖注入自动commit。若中间发生异常，flush的数据可能丢失 |

#### 2.11 DICOM处理代码质量

| # | 评估项 | 风险等级 | 说明 |
|---|--------|----------|------|
| 1 | pydicom force=True | 🟡 中 | 跳过DICM前缀验证，但imaging.py中已单独验证DICM magic word，因此force=True在此场景安全 |
| 2 | 存储失败仅日志 | 🟡 中 | DICOM文件存储失败后仍返回accepted，metadata中storage_path=None。调用方无法区分"已存储"和"存储失败" |
| 3 | 流式接口无DICOM验证 | 🟡 中 | base64解码后未验证DICOM格式，恶意数据可注入系统 |
| 4 | patient_name未脱敏 | 🔴 高 | 详见严重问题S3 |

#### 2.12 限流中间件代码质量

| # | 评估项 | 风险等级 | 说明 |
|---|--------|----------|------|
| 1 | 进程内存存储 | 🟡 中 | 多实例部署时限流不同步。一期单实例可接受，二期需迁移至Redis |
| 2 | 匿名用户共享桶 | 🟡 中 | 所有匿名用户共享"anonymous"桶，可能被恶意利用 |
| 3 | cleanup频率 | 🟢 低 | 每5分钟清理，性能影响可忽略 |
| 4 | WebSocket无限流 | 🟢 低 | WebSocket连接建立后为长连接，限流意义有限 |

---

### 维度三：代码风格合规性

| # | 检查项 | 结果 | 说明 |
|---|--------|------|------|
| 1 | 中英文混用 | ⚠️ | errors.py中文错误消息 + 英文log消息，风格不统一但功能正确 |
| 2 | 函数命名 | ✅ | 一致使用snake_case |
| 3 | 类型注解 | ✅ | 关键函数均有类型注解 |
| 4 | docstring | ⚠️ | 部分函数有docstring（如imaging.py），部分无（如diagnosis.py） |
| 5 | import组织 | ✅ | 标准库→第三方→项目内，组织一致 |
| 6 | 魔法数字 | ⚠️ | 心跳5s、超时15s、缓存600s等硬编码在代码中，部分已提取为常量（IDEMPOTENCY_TTL_SECONDS=600），部分未提取（heartbeat_check中的5和15） |

**🟢 轻微问题 L1**：[nav.py:246-247](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/api/websocket/nav.py#L246) — `asyncio.sleep(5)`和`check_heartbeat_timeout(15)`中的魔法数字应提取为配置常量，与06-开发环境与进度规划.md §5中`NAVIGATION_WS_HEARTBEAT_SECONDS=5`对齐。

**🟢 轻微问题 L2**：[diagnosis.py:166](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/api/v1/diagnosis.py#L166) — 结果缓存key `f"ultrasound:diagnosis:result:{request_id}"` 未使用`RedisKey`类统一管理，与项目中其他Redis key命名规范不一致。

**🟢 轻微问题 L3**：[dicom.py:42](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/api/websocket/dicom.py#L42) — WebSocket endpoint使用`Depends(get_db_session)`获取DB session，但WebSocket连接生命周期远长于HTTP请求，DB session可能在连接期间过期。nav.py中使用`AsyncSessionLocal()`手动管理更合适。

**🟢 轻微问题 L4**：[feedback.py:38](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/api/v1/feedback.py#L38) — 反馈幂等性key使用`f"feedback:idempotency:{request.idempotency_key}"`，未使用RedisKey类且缺少`ultrasound:`前缀，与其他key命名不一致。

**🟢 轻微问题 L5**：[errors.py:425](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/api/v1/errors.py#L425) — `general_exception_handler`中使用`E5000`错误码，但规范中错误码体系仅定义E1xxx-E4xxx范围，E5000超出规范范围。

---

### 维度四：功能正确性验证

#### 测试执行结果

```
tests/unit/api/test_api_phase0a.py: 34 passed, 1 warning
```

#### 测试覆盖度评估

| 测试类 | 覆盖场景 | 缺失场景 |
|--------|----------|----------|
| TestE4006Exception | E4006异常默认值和自定义消息 | 其他E4xxx异常类的构造测试 |
| TestRetryStrategyEnum | 3个枚举值 | — |
| TestDiagnosisRequestFields | request_id/retry_strategy可选性 | 字段验证（如target_organ非空）、边界值 |
| TestFeedbackIdempotency | 幂等键可选性、TTL=24h | 幂等键重复提交行为 |
| TestWebSocketNewMessageTypes | 3种新消息类型构造 | 消息序列化/反序列化 |
| TestConnectionManagerNewMethods | 3种新发送方法+无连接场景 | 并发连接/断开、认证流程 |
| TestSessionRenew | 正常/不存在session | TTL续期逻辑 |
| TestSessionTTLRemaining | 新鲜/None/过期 | 边界值（恰好等于TTL） |
| TestRequestIdGeneration | 格式/唯一性 | — |
| TestSessionResponseTTLField | 字段存在性 | — |
| TestWebSocketMessageTypeEnum | 3种新类型值 | — |

**关键缺失测试**：
1. **DICOM上传接口**：无测试覆盖（空文件、非DICOM、缺少Tag、Modality非US等）
2. **诊断请求端到端**：无集成测试（会话验证、同意校验、幂等性Redis交互）
3. **WebSocket认证流程**：无端到端测试（认证超时、Token验证、双连接认证链路）
4. **限流中间件**：无测试覆盖（超限、急诊豁免、匿名用户）
5. **错误码映射**：无测试验证ERROR_CODE_MAP与异常类的一致性

---

## 三、合规性矩阵

### P0文档：03-接口规范.md

| 规范条目 | 实现状态 | 偏离说明 |
|----------|----------|----------|
| §1.1 DICOM影像接入 | ⚠️ 部分符合 | patient_id/patient_name未脱敏 |
| §1.2 WebSocket双连接 | ⚠️ 部分符合 | 缺少zstd协商、90帧重传限制、指数退避重连 |
| §1.2 兼容方案base64+HTTP | ⚠️ 部分符合 | 未验证解码后DICOM格式 |
| §1.3 诊断请求接口 | ⚠️ 部分符合 | request_id允许客户端传入 |
| §1.4 诊断结果接口 | ⚠️ 部分符合 | SG校验失败处理不当 |
| §4 统一错误响应格式 | ⚠️ 部分符合 | E4007错误码冲突 |
| §5.1 知情同意接口 | ✅ 符合 | 无记录时行为更严格（合理偏离） |
| §7 API限流 | ⚠️ 部分符合 | 缺少FHIR限流、retry豁免、多器官计1次 |
| §8.1 会话状态枚举 | ✅ 符合 | — |
| §8.2 器官状态枚举 | ✅ 符合 | — |
| §8.3 WebSocket路径 | ✅ 符合 | — |

### P0文档：06-开发环境与进度规划.md

| 规范条目 | 实现状态 | 偏离说明 |
|----------|----------|----------|
| §3 FastAPI 0.110+ | ✅ 符合 | requirements.txt中已锁定 |
| §3 pydicom 2.4+ | ✅ 符合 | 已集成 |
| §3 pynetdicom 2.0+ | ✅ 符合 | 边缘端使用 |
| §5 API端口配置 | ✅ 符合 | settings.py中api_port=8000 |
| §5 WebSocket路径配置 | ✅ 符合 | 硬编码路径与规范一致 |
| §5 DICOM参数配置 | ✅ 符合 | dicom_scp_port/dicom_storage_max_file_size等 |
| §5 配置校验 | ⚠️ 部分符合 | validator.py校验项少于规范§5定义的完整清单 |
| §7 DICOM双方案压测 | ❌ 未实现 | 属于Gate 0A验证项，非API轨道代码 |

### P1文档：01-系统架构设计.md

| 规范条目 | 实现状态 | 偏离说明 |
|----------|----------|----------|
| §1.2 双WebSocket连接设计 | ✅ 符合 | 导航+DICOM双通道已实现 |
| §3.4 SafetyGateway进程隔离 | ⚠️ 部分符合 | SG客户端已集成，但校验结果处理不当（S2） |
| §5.3 内部接口协议 | ✅ 符合 | 一期进程内调用，接口定义预留 |

---

## 四、问题清单（按优先级排序）

### 🔴 严重问题（必须修复）

| # | 问题 | 文件 | 修复建议 |
|---|------|------|----------|
| S1 | DiagnosisRequest允许客户端传入request_id | schemas.py:99 | 移除request_id字段或endpoint中忽略客户端值 |
| S2 | SG校验allowed=false时误抛E4005 | diagnosis.py:198-199 | 区分"内容不通过"和"服务不可用"，内容不通过走降级路径 |
| S3 | DICOM上传返回未脱敏的patient_id/patient_name | imaging.py:96-97 | 实现脱敏函数，对PII字段脱敏后返回 |
| S4 | E4007错误码冲突（HSM vs Redis不可用） | errors.py:366-374, ERROR_CODE_MAP | HSM使用新错误码E4013，E4007留给Redis不可用 |

### 🟡 中等问题（建议修复）

| # | 问题 | 文件 | 修复建议 |
|---|------|------|----------|
| M1 | frame_index负值检查可读性差 | dicom.py:116 | 改为显式检查或添加注释说明无符号转换 |
| M2 | WebSocket重连策略未实现 | nav.py | 服务端无需实现（客户端职责），但需确认前端reconnecting-websocket配置 |
| M3 | DICOM帧缓存90帧重传限制未实现 | dicom.py | 添加frame_index范围校验（最近90帧） |
| M4 | zstd压缩协商未实现 | dicom.py | 一期可选，二期必须实现 |
| M5 | FHIR接口限流未配置 | rate_limit.py | 添加FHIR路径到RATE_LIMITS |
| M6 | 多器官诊断计1次限流未实现 | rate_limit.py | 需在请求体解析后判断，可能需调整中间件架构 |
| M7 | retry=true不计限流未实现 | rate_limit.py | 同M6，需调整中间件架构 |
| M8 | ConsentStatus枚举规范外扩展 | schemas.py:27-32 | 在规范中补充PENDING/DENIED定义 |
| M9 | 单session仅支持单WebSocket连接 | nav.py:49-50 | 改为(session_id, device_id)复合key |
| M10 | DICOM流式接口未验证解码后格式 | imaging.py:209-211 | 添加DICOM格式验证 |

### 🟢 轻微问题（可选修复）

| # | 问题 | 文件 | 修复建议 |
|---|------|------|----------|
| L1 | 心跳魔法数字未提取为配置 | nav.py:246-247 | 提取为常量或使用settings |
| L2 | 诊断结果缓存key未使用RedisKey类 | diagnosis.py:166 | 统一使用RedisKey类 |
| L3 | DICOM WebSocket使用Depends(get_db_session) | dicom.py:42 | 改为手动管理AsyncSessionLocal |
| L4 | 反馈幂等key缺少ultrasound:前缀 | feedback.py:38 | 统一使用RedisKey类 |
| L5 | E5000错误码超出规范范围 | errors.py:425 | 改为E2001或规范内错误码 |

---

## 五、测试覆盖度缺口

| 功能域 | 现有测试 | 缺失测试 | 优先级 |
|--------|----------|----------|--------|
| DICOM上传 | 0 | 格式验证、Tag缺失、Modality校验、大小限制 | P0 |
| 限流中间件 | 0 | 超限、急诊豁免、匿名用户、清理机制 | P0 |
| WebSocket认证 | 0 | 认证超时、Token验证、双连接链路 | P0 |
| 诊断请求集成 | 0 | 幂等性Redis交互、同意校验、session验证 | P1 |
| 错误码映射 | 0 | ERROR_CODE_MAP与异常类一致性 | P1 |
| DICOM流式接口 | 0 | base64解码、帧索引校验 | P2 |

---

## 六、结论与建议

### 总体结论

Phase 0A API轨道的**核心架构已正确搭建**，主要接口路径、数据模型、双WebSocket连接设计、错误码体系框架均与规范对齐。34个单元测试全部通过，基础功能可运行。

但存在**4个严重规范偏离**（E4007错误码冲突、PII未脱敏、SG校验结果处理不当、request_id客户端可传入），这些问题涉及**安全合规性和数据隐私**，必须在进入Gate 0A前修复。

### 修复优先级建议

1. **立即修复（Gate 0A前）**：S3(PII脱敏)、S4(E4007冲突)、S2(SG校验处理)
2. **短期修复（Phase 0A内）**：S1(request_id)、M3(90帧限制)、M5(FHIR限流)、M9(多设备连接)
3. **中期修复（Phase 0B前）**：M6/M7(限流架构调整)、M10(DICOM流式验证)、测试覆盖度补充
4. **长期跟进（二期）**：M4(zstd压缩)、M2(重连策略确认)
