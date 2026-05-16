# Phase 0A API接口轨道系统性审查计划

## 审查概述

对文档 `各阶段并发任务.md` 第34行定义的 **API接口轨道** 在 Phase 0A 的实施情况进行系统性审查。Phase 0A API轨道任务为：**FastAPI主服务、诊断接口、WebSocket双连接、DICOM接口**（多任务并行）。

审查严格遵循独立、客观、深入且批判性原则，对照核心参考文档进行一致性审查。

---

## 审查范围与实现文件清单

| # | 功能域 | 实现文件 | 规范文档 |
|---|--------|----------|----------|
| 1 | FastAPI主服务 | `app/main.py` | 03-接口规范.md §6-8, 06-开发环境与进度规划.md §5 |
| 2 | 诊断请求接口 | `app/api/v1/diagnosis.py` | 03-接口规范.md §1.3 |
| 3 | 诊断结果接口 | `app/api/v1/diagnosis.py` | 03-接口规范.md §1.4 |
| 4 | WebSocket导航通道 | `app/api/websocket/nav.py` | 03-接口规范.md §1.2, §8.3 |
| 5 | WebSocket DICOM帧通道 | `app/api/websocket/dicom.py` | 03-接口规范.md §1.2, §8.3 |
| 6 | DICOM上传接口 | `app/api/v1/imaging.py` | 03-接口规范.md §1.1 |
| 7 | DICOM流式接口 | `app/api/v1/imaging.py` | 03-接口规范.md §1.2 兼容方案 |
| 8 | 错误响应体系 | `app/api/v1/errors.py` | 03-接口规范.md §4 |
| 9 | 限流中间件 | `app/config/rate_limit.py` | 03-接口规范.md §7 |
| 10 | 知情同意接口 | `app/api/v1/consent.py` | 03-接口规范.md §5.1 |
| 11 | 数据模型定义 | `app/models/schemas.py` | 03-接口规范.md §8 |
| 12 | 配置管理 | `app/config/settings.py` | 06-开发环境与进度规划.md §5 |
| 13 | 单元测试 | `tests/unit/api/test_api_phase0a.py` | — |

---

## 审查维度与实施步骤

### 维度一：设计规范一致性（P0优先级）

**目标**：全面评估实施内容是否严格符合既定设计标准，识别偏离或未实现的设计要素。

#### 步骤1：诊断请求接口规范一致性审查
- **对照**：03-接口规范.md §1.3
- **检查项**：
  1. ✅/❌ `POST /api/v1/diagnosis/request` 路径是否一致
  2. ✅/❌ `X-Priority` Header 是否支持 0/1/2 三级优先级
  3. ✅/❌ Request字段完整性：session_id, image_ids, clinical_text, voice_text, target_organ, response_tier, patient_context, retry, idempotency_key
  4. ✅/❌ Response字段完整性：request_id, session_id, status, estimated_time_ms, response_tier
  5. ✅/❌ 幂等性实现：idempotency_key + 10分钟缓存窗口
  6. ✅/❌ retry=true时跳过Self-RAG重试逻辑
  7. ⚠️ 规范要求`response_tier`默认`full`，实现中默认值是否一致
  8. ⚠️ 规范未定义`request_id`为Request字段（由服务端生成），实现中允许客户端传入

#### 步骤2：诊断结果接口规范一致性审查
- **对照**：03-接口规范.md §1.4
- **检查项**：
  1. ✅/❌ `GET /api/v1/diagnosis/result/{request_id}` 路径是否一致
  2. ✅/❌ Response字段完整性：request_id, status, consistency_flag, desensitization_status, consent_status_at_output, fusion_status, diagnosis, evidence, quality_control, requires_manual_review, review_reason, ai_diagnosis_failed, hallucination_severity, version_compatibility, processing_time_ms, model_version, knowledge_base_version
  3. ✅/❌ 轮询策略：首次1s后轮询，后续每2s，最大等待30s
  4. ✅/❌ 超时处理：status="timeout"
  5. ✅/❌ 结果缓存10分钟
  6. ✅/❌ SafetyGateway输出前同意状态校验
  7. ⚠️ `hallucination_severity`一期固定为null，实现中是否遵循

#### 步骤3：WebSocket双连接规范一致性审查
- **对照**：03-接口规范.md §1.2, §8.3
- **检查项**：
  1. ✅/❌ 导航通道路径 `/api/v1/realtime/nav` 是否一致
  2. ✅/❌ DICOM帧通道路径 `/api/v1/realtime/dicom` 是否一致
  3. ✅/❌ 双连接认证流程：导航通道先认证→签发Token→DICOM帧通道用Token认证
  4. ✅/❌ 认证超时5秒，关闭码4001/4002
  5. ✅/❌ 心跳间隔5s，连续3次无pong判定断开
  6. ✅/❌ DICOM二进制帧格式：4B frame_index + 1B flags + 4B dicom_length + NB data + 4B CRC32
  7. ✅/❌ 小端序字节序约定
  8. ✅/❌ frame_index >= 0 防误校验（负值拒绝E1001）
  9. ⚠️ 规范要求心跳间隔5s，实现中心跳检查间隔5s但超时15s（3次×5s），是否与"连续3次无pong响应判定连接断开"一致
  10. ⚠️ 规范要求重连策略为指数退避（1s→2s→4s→8s→最大30s），实现中是否支持
  11. ⚠️ 规范要求DICOM帧缓存90帧重传限制，实现中是否有限制
  12. ⚠️ 规范要求zstd压缩协商（握手参数X-Compression: zstd），实现中是否支持
  13. ⚠️ 规范禁止URL query参数传递API Key/Token，实现中session_id/device_id通过query传递但API Key通过消息体传递——需确认是否合规

#### 步骤4：DICOM影像接入规范一致性审查
- **对照**：03-接口规范.md §1.1
- **检查项**：
  1. ✅/❌ `POST /api/v1/imaging/dicom/upload` 路径是否一致
  2. ✅/❌ multipart/form-data格式
  3. ✅/❌ 文件大小限制≤500MB
  4. ✅/❌ 必需DICOM Tag验证：PatientID, PatientName, StudyInstanceUID, SeriesInstanceUID, SOPInstanceUID, Modality(=US), Rows, Columns, BitsAllocated, PixelData
  5. ✅/❌ 拒绝条件：Modality≠US、缺少PatientID/StudyInstanceUID/SOPInstanceUID、BitsAllocated>16
  6. ✅/❌ DICOM一致性声明中的SOP Class和Transfer Syntax是否在代码中体现
  7. ⚠️ DICOM文件上传后元数据中的patient_id是否做了脱敏处理（规范要求返回P******格式）

#### 步骤5：错误码体系规范一致性审查
- **对照**：03-接口规范.md §4
- **检查项**：
  1. ✅/❌ E1xxx客户端错误码完整性：E1001, E1004, E1006, E1007, E1008, E1009, E1010, E1011, E1012
  2. ✅/❌ E2xxx服务端错误码完整性：E2001, E2002, E2003, E2004, E2004A, E2004B, E2006, E2007
  3. ✅/❌ E3xxx医疗安全错误码完整性：E3001, E3002, E3003, E3004, E3005
  4. ✅/❌ E4xxx基础设施错误码完整性：E4001-E4012
  5. ✅/❌ 统一错误响应格式字段：error_code, error_message, detail, request_id, suggestion, retryable, retry_strategy
  6. ✅/❌ 降级场景HTTP 200返回
  7. ⚠️ ERROR_CODE_MAP中E4007映射为"HSM不可用"，但规范中E4007为"Redis不可用导致新会话创建延迟增加"——存在不一致
  8. ⚠️ HSMUnavailableException使用E4007，与规范E4007含义冲突

#### 步骤6：限流规范一致性审查
- **对照**：03-接口规范.md §7
- **检查项**：
  1. ✅/❌ 令牌桶算法实现
  2. ✅/❌ 限流规则：诊断10次/分、影像30次/分、反馈20次/分
  3. ✅/❌ 超限响应HTTP 429 + E1007 + Retry-After头
  4. ✅/❌ 急诊优先级豁免：compact 2倍、full 5倍
  5. ⚠️ 规范要求"按客户端IP+用户ID组合限流"，实现中bucket_key为`{ip}:{user_id}:{path}`——是否完全一致
  6. ⚠️ 规范要求FHIR接口60次/分限流，RATE_LIMITS中未包含FHIR路径
  7. ⚠️ 规范要求"多器官联合诊断计为1次，不按器官数累加"，实现中是否体现
  8. ⚠️ 规范要求"retry=true不计入限流"，实现中是否体现

#### 步骤7：知情同意接口规范一致性审查
- **对照**：03-接口规范.md §5.1
- **检查项**：
  1. ✅/❌ `GET /api/v1/consent/{patient_id}` 路径是否一致
  2. ✅/❌ `POST /api/v1/consent` 路径是否一致
  3. ✅/❌ 同意状态以PostgreSQL为主存储（无Redis双写）
  4. ✅/❌ 撤回同意时发布Redis Streams事件
  5. ⚠️ 规范要求GET返回consent_status为granted/denied/withdrawn，实现中无同意记录时抛出403异常而非返回denied状态——行为变更需确认前端适配

#### 步骤8：枚举与数据模型规范一致性审查
- **对照**：03-接口规范.md §8
- **检查项**：
  1. ✅/❌ 会话状态枚举：pending, active, partial, completed, needs_review, failed, terminated
  2. ✅/❌ 器官状态枚举：pending, in_progress, completed, failed, skipped, needs_review
  3. ✅/❌ WebSocket路径统一定义与§8.3一致
  4. ⚠️ ConsentStatus枚举包含PENDING和DENIED，但规范§5.1仅定义granted/denied/withdrawn——PENDING是否为规范外扩展

---

### 维度二：代码质量评估

**目标**：深入分析代码健壮性、可维护性、可扩展性和性能表现。

#### 步骤9：WebSocket连接管理健壮性评估
- **检查项**：
  1. ConnectionManager使用asyncio.Lock保护共享状态——评估锁粒度是否合理
  2. 心跳检查任务在finally中cancel——评估异常路径下资源泄漏风险
  3. auth_tokens存储在内存Dict中——评估进程重启后Token丢失的影响
  4. 单session_id仅支持一个WebSocket连接——评估多设备同session场景
  5. DICOM帧通道重传计数存储在内存Dict中——评估内存泄漏风险（断连后未清理）

#### 步骤10：诊断接口代码质量评估
- **检查项**：
  1. 幂等性检查Redis错误时仅warning继续执行——评估是否应拒绝请求
  2. 诊断结果中硬编码占位数据（"等待实际诊断系统完成"）——评估Phase 0A是否可接受
  3. SafetyGateway校验失败直接抛SafetyGatewayUnavailableException——评估是否应区分"不允许输出"和"服务不可用"
  4. 结果缓存使用手写Redis key而非RedisKey类——评估一致性
  5. db.flush()调用后未commit——评估数据持久化风险

#### 步骤11：DICOM处理代码质量评估
- **检查项**：
  1. pydicom.dcmread使用force=True——评估是否跳过必要的格式验证
  2. DICOM文件上传后存储失败仅记录日志不抛异常——评估数据一致性风险
  3. DICOM流式接口未验证frame_data解码后的DICOM格式——评估安全风险
  4. extract_metadata_from_dataset中patient_name未脱敏——评估隐私泄露风险

#### 步骤12：限流中间件代码质量评估
- **检查项**：
  1. 令牌桶存储在进程内存中——评估多实例部署下限流失效风险
  2. _get_user_id对匿名用户返回"anonymous"——评估匿名用户共享限流桶的风险
  3. cleanup在每次请求时检查——评估高并发下性能影响
  4. WebSocket路径未纳入限流——评估是否需要

---

### 维度三：代码风格合规性

**目标**：检查编码规范、命名约定和文档标准的一致性。

#### 步骤13：代码风格审查
- **检查项**：
  1. 中文注释与英文代码混用的一致性（errors.py中文错误消息 vs 英文log消息）
  2. 函数命名一致性（snake_case）
  3. 类型注解完整性
  4. docstring覆盖度
  5. import组织一致性
  6. 魔法数字处理（如心跳5s、超时15s、缓存600s是否提取为常量/配置项）

---

### 维度四：功能正确性验证

**目标**：验证所有功能按需求规格正确实现，重点测试边界条件和异常处理。

#### 步骤14：边界条件与异常处理验证
- **检查项**：
  1. DICOM上传：空文件、超大文件、非DICOM文件、缺少必需Tag、Modality非US、BitsAllocated>16
  2. 诊断请求：不存在的session_id、未同意患者、已撤回同意、重复idempotency_key
  3. 诊断结果：不存在的request_id、处理中超时、SafetyGateway不可用
  4. WebSocket：认证超时、认证失败、心跳超时、CRC32校验失败、frame_index负值
  5. 限流：超限请求、急诊优先级豁免、匿名用户限流

#### 步骤15：测试覆盖度评估
- **检查项**：
  1. test_api_phase0a.py覆盖的测试场景清单
  2. 未覆盖的关键场景识别
  3. 集成测试覆盖情况
  4. WebSocket端到端测试覆盖情况

---

## 已识别的关键问题（预审发现）

基于对代码和规范文档的详细比对，以下问题已在预审中识别，将在正式审查中深入确认：

### 🔴 严重问题（规范偏离）

| # | 问题 | 涉及文件 | 规范依据 |
|---|------|----------|----------|
| 1 | **E4007错误码冲突**：实现中`HSMUnavailableException`使用E4007，但规范定义E4007为"Redis不可用导致新会话创建延迟增加" | errors.py:366-374 | 03-接口规范.md §4 |
| 2 | **DICOM上传返回patient_id未脱敏**：规范要求返回`P******`格式，实现中直接返回原始PatientID | imaging.py:96 | 03-接口规范.md §1.1 |
| 3 | **DICOM帧通道认证Token通过URL query传递session_id**：规范禁止通过URL query传递Token，但session_id通过query传递 | dicom.py:40-41 | 03-接口规范.md §1.2 |
| 4 | **SafetyGateway校验结果处理不当**：SG返回allowed=false时抛SafetyGatewayUnavailableException(E4005)，但规范要求区分"不允许输出"和"服务不可用" | diagnosis.py:198-199 | 03-接口规范.md §4 |

### 🟡 中等问题（规范部分偏离/缺失）

| # | 问题 | 涉及文件 | 规范依据 |
|---|------|----------|----------|
| 5 | **zstd压缩协商未实现**：规范要求WebSocket握手阶段协商zstd压缩 | dicom.py | 03-接口规范.md §1.2 |
| 6 | **DICOM帧缓存90帧重传限制未实现**：规范要求重传范围不超过最近90帧 | dicom.py | 03-接口规范.md §1.2 |
| 7 | **重连策略未实现**：规范要求指数退避（1s→2s→4s→8s→最大30s） | nav.py | 03-接口规范.md §1.2 |
| 8 | **FHIR接口限流未配置**：规范要求60次/分/用户 | rate_limit.py | 03-接口规范.md §7 |
| 9 | **retry=true不计入限流未实现** | rate_limit.py | 03-接口规范.md §7 |
| 10 | **多器官联合诊断计为1次限流未实现** | rate_limit.py | 03-接口规范.md §7 |
| 11 | **DICOM流式接口未验证解码后DICOM格式** | imaging.py:209-211 | 03-接口规范.md §1.2 |
| 12 | **ConsentStatus枚举包含PENDING**：规范§5.1仅定义granted/denied/withdrawn | schemas.py:31 | 03-接口规范.md §5.1 |
| 13 | **DiagnosisRequest允许客户端传入request_id**：规范中request_id由服务端生成 | schemas.py:99 | 03-接口规范.md §1.3 |

### 🟢 轻微问题（代码质量/风格）

| # | 问题 | 涉及文件 |
|---|------|----------|
| 14 | 诊断结果缓存key未使用RedisKey统一类 | diagnosis.py:166 |
| 15 | DICOM帧通道retransmit_counts在连接断开后未清理 | dicom.py:47 |
| 16 | 导航通道心跳超时15s与规范"连续3次无pong(5s间隔)"的语义需确认 | nav.py:247 |
| 17 | DICOM帧通道使用Depends(get_db_session)但WebSocket中DB依赖注入可能有问题 | dicom.py:42 |
| 18 | patient_name在元数据中未脱敏 | imaging.py:97 |

---

## 审查执行计划

### 执行顺序（按优先级）

1. **P0-步骤1-5**：设计规范一致性审查（诊断接口→结果接口→WebSocket→DICOM→错误码）
2. **P0-步骤6-7**：限流与知情同意规范一致性审查
3. **P0-步骤8**：枚举与数据模型规范一致性审查
4. **P1-步骤9-12**：代码质量评估
5. **P2-步骤13**：代码风格合规性
6. **P1-步骤14-15**：功能正确性验证与测试覆盖度

### 产出物

1. **审查报告**：包含每个步骤的详细审查结果、问题分级（🔴严重/🟡中等/🟢轻微）、规范偏离记录
2. **问题清单**：按优先级排序的修复建议
3. **合规性矩阵**：规范条目 vs 实现状态的映射表

### 验证方式

- 静态代码分析：逐文件逐函数对照规范文档
- 规范偏离标记：每个检查项标注✅符合/❌不符合/⚠️需确认
- 代码走查：关键路径代码逐行审查
- 测试执行：运行现有测试验证功能正确性
