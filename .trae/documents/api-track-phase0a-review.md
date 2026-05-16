# API接口轨道 Phase0A 实施情况系统性审查计划

## 审查范围

根据 `各阶段并发任务.md` Phase 0A 定义，API接口轨道任务为：**FastAPI主服务、诊断接口、WebSocket双连接、DICOM接口**。

## 审查方法

按四个核心维度逐模块审查，每个维度对照参考文档逐项检查，记录发现与偏离。

---

## 一、设计规范一致性审查

### 1.1 FastAPI主服务

| 检查项 | 参考文档 | 审查要点 |
|--------|----------|----------|
| FastAPI版本 | 06-§3: FastAPI 0.110+ | 检查 pyproject.toml/requirements.txt 中实际版本 |
| 端口配置 | 00b: APP_PORT=8000 | settings.py 中 api_port=8000 ✓ |
| CORS配置 | 01-§安全 | allow_origins 从配置读取 ✓；检查是否缺少 X-Priority 头 |
| 路由前缀一致性 | 03-§8.3 | 所有路由前缀是否与接口规范路径一致 |
| Lifespan启动校验 | 06-§5: 三层校验 | validate_config() 在 lifespan 中调用 ✓；检查是否覆盖所有必需校验 |
| 限流中间件注册 | 03-§7 | **RateLimitMiddleware 已实现但未在 main.py 注册** — 关键偏离 |
| 异常处理器 | 03-§4 | 3个全局异常处理器已注册 ✓；检查 E3xxx 是否返回 HTTP 200（规范要求） |

**已知偏离**：
- **P0**: `RateLimitMiddleware` 未在 `main.py` 中注册为中间件，限流功能完全不生效
- **P1**: CORS `allow_headers` 缺少 `X-Priority`，急诊优先级头可能被浏览器预检拒绝

### 1.2 诊断接口

| 检查项 | 参考文档 | 审查要点 |
|--------|----------|----------|
| POST /api/v1/diagnosis/request 路径 | 03-§1.3 | 路由前缀 /api/v1/diagnosis + /request ✓ |
| X-Priority 请求头 | 03-§1.3 | Header(default=1) ✓；0=急诊/1=常规/2=教学 ✓ |
| idempotency_key 幂等性 | 03-§1.3 | Redis缓存 TTL=300s ✓；10分钟 vs 5分钟差异 |
| retry + retry_strategy | 03-§1.3 | retry=true 时默认 FULL_RETRIEVAL ✓；规范要求"仅执行1次生成+质检，跳过Self-RAG重试"未实现 |
| response_tier | 03-§1.3 | 枚举 realtime/standard/full ✓ |
| estimated_time_ms | 03-§1.3 | FULL=3000ms, 其他=2000ms — 规范未指定具体值，需确认合理性 |
| GET /api/v1/diagnosis/result/{request_id} | 03-§1.4 | 路径 ✓ |
| 结果缓存10分钟 | 03-§1.4 | **未实现** — 无服务端结果缓存机制 |
| 轮询策略(1s→2s→max 30s) | 03-§1.4 | **未实现** — 无轮询指导逻辑 |
| 超时返回 status:timeout | 03-§1.4 | **未实现** — 无超时状态返回 |
| WebSocket推送诊断结果 | 03-§1.4 | nav_manager.send_diagnosis_result() ✓ |
| DiagnosisResult字段完整性 | 03-§1.4 | 检查所有字段是否与规范一致 |

**已知偏离**：
- **P0**: 幂等性TTL为300秒(5分钟)，规范要求10分钟
- **P1**: retry=true时的"跳过Self-RAG重试"逻辑未实现
- **P1**: 诊断结果无服务端缓存机制
- **P1**: 无超时状态(status:timeout)返回
- **P2**: patient_id 通过 `session_id.split("_")[0]` 获取，脆弱且不规范

### 1.3 WebSocket双连接

| 检查项 | 参考文档 | 审查要点 |
|--------|----------|----------|
| 导航通道路径 | 03-§8.3: /api/v1/realtime/nav | ✓ |
| DICOM帧通道路径 | 03-§8.3: /api/v1/realtime/dicom | ✓ |
| 双连接隔离消除HOL | 01-§1.2 | 两个独立WebSocket ✓ |
| 导航通道认证: device_id+api_key | 03-§1.2 | AuthMessage 支持 ✓ |
| DICOM通道交叉认证: 复用nav token | 03-§1.2 | validate_auth_token() ✓ |
| Token禁止通过URL query传递 | 03-§1.2 | **偏离**: session_id和device_id通过Query传递，但token通过首条消息传递 ✓ |
| 心跳间隔5s | 03-§1.2 | heartbeat_check 每5s检查 ✓；超时15s(3次) vs 规范"连续3次无pong" |
| 重连策略: 指数退避1s→30s | 03-§1.2 | **未实现** — 服务端无重连逻辑(应由客户端实现) |
| zstd压缩协商 | 03-§1.2 | **未实现** — 无压缩协商机制 |
| 帧大小分级(≤64KB/＞64KB) | 03-§1.2 | **未实现** — 无帧大小分级逻辑 |
| 位深动态适配(8bit/16bit) | 03-§1.2 | **未实现** |
| DICOM帧缓存90帧 | 03-§1.2 | **未实现** — 无帧缓存 |
| CRC32校验 | 03-§1.2 | calculate_crc32() ✓ |
| 重传机制 | 03-§1.2 | MAX_RETRANSMIT_PER_FRAME=3 ✓ |
| 二进制帧格式 | 03-§1.2 | [4B frame_index][1B flags][4B length][N data][4B CRC32] ✓ |
| 负数frame_index拒绝 | 03-§1.2 | >= 0x80000000 检测 ✓ |
| ReconnectAck含session_ttl_remaining_ms | 08-§5 | ✓ (从session.updated_at计算) |
| 导航标注经安全校验 | 01-§1.2 | **未实现** — 导航标注为硬编码数据，未经过SafetyGateway |

**已知偏离**：
- **P0**: 导航标注未经安全校验直接返回，违反一期"后端中转消除60ms安全窗口"设计
- **P1**: 无zstd压缩协商机制
- **P1**: 无帧大小分级和位深动态适配
- **P2**: 心跳超时15s(固定3次5s间隔) vs 规范"连续3次无pong判定断开"语义一致但实现方式不同
- **P2**: 无DICOM帧缓存(90帧)

### 1.4 DICOM接口

| 检查项 | 参考文档 | 审查要点 |
|--------|----------|----------|
| POST /api/v1/imaging/dicom/upload | 03-§1.1 | 路径 ✓ |
| multipart/form-data | 03-§1.1 | UploadFile + Form ✓ |
| 文件大小≤500MB | 03-§1.1 | dicom_storage_max_file_size=524288000 ✓ |
| DICOM magic word验证 | 03-§1.1 | validate_dicom_format() ✓ |
| 10个必需DICOM Tag | 03-§1.1 | REQUIRED_DICOM_TAGS 10项 ✓ |
| Modality=US校验 | 03-§1.1 | ✓ |
| BitsAllocated 1-16 | 03-§1.1 | ✓ |
| 返回image_id+session_id+metadata+status | 03-§1.1 | ✓ |
| metadata含patient_id/modality/body_part/acquisition_date | 03-§1.1 | ✓ |
| POST /api/v1/imaging/dicom/stream | 03-§1.2 | 备选方案 ✓ |
| DICOM Conformance Statement | 03-§1.1 | **未实现** — 无SOP Class/Transfer Syntax验证 |
| DICOM SCP角色 | 03-§1.1 | **未实现** — 无pynetdicom SCP服务(属于EDG轨道) |

**已知偏离**：
- **P1**: 无DICOM Conformance Statement验证(SOP Class/Transfer Syntax)
- **P2**: MinIO存储后端未实现(回退到local)

### 1.5 错误码体系

| 检查项 | 参考文档 | 审查要点 |
|--------|----------|----------|
| E1xxx客户端错误 | 03-§4 | E1001-E1013 ✓ (E1003/E1005缺失异常类但有映射) |
| E2xxx服务端错误 | 03-§4 | E2001-E2007 ✓ (E2006无异常类但有映射) |
| E3xxx医疗安全 | 03-§4 | E3001-E3005 ✓；SafetyException status_code=200 ✓ |
| E4xxx基础设施 | 03-§4 | E4001-E4012 ✓ (部分仅有映射无异常类) |
| 统一ErrorResponse格式 | 03-§4 | 7字段完整 ✓ |
| retryable+retry_strategy | 03-§4 | 三种策略 ✓ |
| E4005子码拆分(A/B/C) | 01-§3.4 | ✓ |

**已知偏离**：
- **P2**: E1003(未授权)、E1005(方法不允许)、E2006(检索无结果)仅有ERROR_CODE_MAP映射，无专用异常类
- **P2**: http_exception_handler中422→E1006映射不准确(422是验证错误，E1006是对抗性输入)

---

## 二、代码质量评估

### 2.1 健壮性问题

| 问题 | 位置 | 严重度 | 描述 |
|------|------|--------|------|
| 限流中间件未注册 | main.py | **P0** | RateLimitMiddleware代码完整但未add_middleware，所有限流规则不生效 |
| 导航标注硬编码 | nav.py L271-278 | **P0** | 返回固定硬编码标注数据，未经安全校验，违反核心安全设计 |
| patient_id解析脆弱 | diagnosis.py L154 | **P1** | `session_id.split("_")[0]` 获取patient_id，session_id格式无保证 |
| 诊断结果硬编码 | diagnosis.py L190-201 | **P1** | findings为"等待实际诊断系统完成"，processing_time_ms=2500固定值 |
| DICOM帧处理TODO | dicom.py L185-187 | **P1** | 帧数据接收后无实际处理逻辑 |
| 结果缓存缺失 | diagnosis.py | **P1** | 规范要求10分钟结果缓存，未实现 |
| 超时状态缺失 | diagnosis.py | **P1** | 规范要求status:timeout，未实现 |
| 无zstd压缩 | dicom.py | **P2** | 规范要求可选zstd压缩协商，未实现 |
| 无帧大小分级 | dicom.py | **P2** | 规范要求≤64KB/＞64KB分级处理 |

### 2.2 可维护性问题

| 问题 | 位置 | 描述 |
|------|------|------|
| ConnectionManager内存存储 | nav.py | auth_tokens/active_connections均为进程内Dict，多实例部署时状态不共享 |
| 审计日志内联创建 | nav.py/dicom.py | 每次WebSocket操作都创建新的AuditService+AsyncSessionLocal实例 |
| 重复的DB会话管理 | nav.py | 多处 `async with AsyncSessionLocal() as db` + try/commit/rollback 模式重复 |
| 配置项分散 | settings.py | 部分配置用getattr+默认值(sg_client相关)，未声明为Settings字段 |

### 2.3 可扩展性问题

| 问题 | 位置 | 描述 |
|------|------|------|
| WebSocket仅支持单实例 | nav.py | ConnectionManager为进程内单例，不支持多进程/多实例水平扩展 |
| 无WebSocket消息版本控制 | schemas.py | WebSocket消息类型无版本号，未来协议升级困难 |
| DICOM存储策略硬编码 | dicom_storage.py | MinIO回退到local而非抛出NotImplementedError，可能隐藏配置错误 |

### 2.4 性能问题

| 问题 | 位置 | 描述 |
|------|------|------|
| 全局asyncio.Lock | nav.py L44 | ConnectionManager使用单个Lock保护所有操作，高并发下成为瓶颈 |
| 限流桶内存存储 | rate_limit.py | TokenBucket存储在进程内存，多实例部署时限流失效 |
| DICOM文件同步写入 | dicom_storage.py L64 | 使用同步文件写入，可能阻塞事件循环 |

---

## 三、代码风格合规性

### 3.1 命名约定

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 路由函数命名 | ✓ | `submit_diagnosis_request`, `get_diagnosis_result` 等语义清晰 |
| 异常类命名 | ✓ | `XxxException` 统一后缀 |
| Schema模型命名 | ✓ | `XxxRequest`/`XxxResponse`/`XxxMessage` 统一模式 |
| 配置项命名 | ⚠ | 部分使用`sg_`前缀，部分使用`safety_gateway_`，不一致 |
| 常量命名 | ✓ | UPPER_SNAKE_CASE |

### 3.2 文档标准

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 函数docstring | ⚠ | 部分函数有docstring(如validate_dicom_format)，部分缺失 |
| API端点描述 | ✓ | router tags已设置 |
| 类型注解 | ✓ | 全面使用Python类型注解 |
| Pydantic Field描述 | ✓ | ErrorResponse等关键字段有description |

### 3.3 代码一致性

| 检查项 | 状态 | 说明 |
|--------|------|------|
| structlog使用 | ✓ | 统一使用structlog |
| 异步模式 | ✓ | 统一async/await |
| 依赖注入 | ⚠ | 部分端点使用Depends，WebSocket端点混用AsyncSessionLocal直接创建 |
| 错误处理模式 | ⚠ | HTTP端点统一抛异常，WebSocket端点内联处理错误 |

---

## 四、功能正确性验证

### 4.1 测试覆盖分析

| 模块 | 单元测试 | 集成测试 | E2E测试 | 覆盖评估 |
|------|----------|----------|---------|----------|
| FastAPI主服务 | -- | -- | -- | **缺失** |
| POST /diagnosis/request | Repository级 | 有(成功/无同意) | -- | **部分** |
| GET /diagnosis/result/{id} | -- | -- | -- | **缺失** |
| WS /realtime/nav | ConnectionManager方法 | 路由存在性 | -- | **不足** |
| WS /realtime/dicom | CRC32/帧标志 | 路由存在性 | -- | **不足** |
| POST /imaging/dicom/upload | 验证函数完整 | 有(成功/无效/缺失标签/错误Modality) | -- | **较好** |
| POST /imaging/dicom/stream | -- | -- | -- | **缺失** |
| 错误码体系 | 异常类构造 | -- | -- | **部分** |
| 限流中间件 | TokenBucket算法 | -- | -- | **不足**(未注册未生效) |
| Auth端点(11个) | JWT/密码/RoleChecker | -- | -- | **端点级缺失** |
| Health端点 | SG内部组件 | -- | -- | **端点级缺失** |
| Session端点 | Repository级 | 有(创建/404) | -- | **部分** |
| Consent端点 | -- | 有(查询/更新) | -- | **较好** |
| Feedback端点 | Schema级 | 有(成功/404) | -- | **部分** |
| Security端点(10个) | 脱敏引擎/RoleChecker | 路由认证检查 | -- | **端点级缺失** |
| Speech端点(3个) | 引擎/协议 | -- | -- | **端点级缺失** |

### 4.2 关键缺失测试场景

| 场景 | 端点 | 优先级 |
|------|------|--------|
| 诊断结果获取+安全网关校验 | GET /diagnosis/result/{id} | P0 |
| 幂等性缓存命中 | POST /diagnosis/request | P0 |
| 限流触发429 | 任意限流端点 | P0 |
| WebSocket完整认证流程 | WS /realtime/nav | P0 |
| WebSocket DICOM帧CRC校验失败重传 | WS /realtime/dicom | P1 |
| DICOM stream备选方案 | POST /imaging/dicom/stream | P1 |
| 急诊优先级限流倍率 | POST /diagnosis/request | P1 |
| Auth token刷新/撤销 | POST /auth/refresh-token等 | P1 |
| SafetyGateway不可用降级 | GET /diagnosis/result/{id} | P1 |
| 并发WebSocket连接 | WS /realtime/nav | P2 |

---

## 五、实施步骤

### Step 1: 设计规范一致性修复 (P0)

1. **注册限流中间件** — 在 main.py 中添加 `app.add_middleware(RateLimitMiddleware)`
2. **修复幂等性TTL** — 将 IDEMPOTENCY_TTL_SECONDS 从 300 改为 600 (10分钟)
3. **导航标注安全校验** — 在nav.py中集成SafetyGateway校验，替代硬编码数据
4. **CORS添加X-Priority头** — allow_headers 增加 "X-Priority"

### Step 2: 代码质量修复 (P1)

5. **修复patient_id获取方式** — 从session记录中获取patient_id，而非解析session_id
6. **实现诊断结果缓存** — 在GET /diagnosis/result中添加Redis缓存(10分钟TTL)
7. **实现超时状态** — 添加status:timeout返回逻辑
8. **实现retry=true跳过Self-RAG** — 在诊断请求处理中实现
9. **DICOM帧处理集成** — 将帧数据传递给导航推理服务

### Step 3: 测试补全 (P0-P1)

10. **添加GET /diagnosis/result/{id}测试** — 含安全网关校验、脱敏状态、同意状态
11. **添加幂等性缓存命中测试** — Redis mock验证缓存返回
12. **添加限流触发测试** — 验证429响应和Retry-After头
13. **添加WebSocket认证E2E测试** — 完整连接→认证→消息→断开流程
14. **添加DICOM帧CRC重传测试** — 验证重传请求和MAX_RETRANSMIT限制

### Step 4: 代码风格与可维护性改进 (P2)

15. **统一配置项命名** — sg_前缀统一为safety_gateway_
16. **WebSocket DB会话管理重构** — 提取公共方法减少重复
17. **ConnectionManager扩展性** — 评估Redis pub/sub替代方案(二期)
18. **DICOM文件异步写入** — 使用aiofiles替代同步写入

### Step 5: 验证与回归测试

19. **运行全量测试套件** — `pytest -m track_api`
20. **运行lint/typecheck** — 确保无新增问题
21. **限流端到端验证** — 手动或脚本验证限流规则生效

---

## 六、审查发现汇总

### 严重度分布

| 严重度 | 数量 | 典型问题 |
|--------|------|----------|
| **P0(阻断)** | 3 | 限流未注册、导航标注未安全校验、幂等性TTL不符 |
| **P1(重要)** | 8 | patient_id解析脆弱、结果缓存缺失、超时状态缺失、retry逻辑缺失、DICOM帧处理TODO、测试大面积缺失 |
| **P2(改进)** | 6 | 配置命名不一致、zstd未实现、帧大小分级未实现、MinIO回退、同步文件写入、ConnectionManager单实例 |

### Phase0A完成度评估

| 模块 | 设计规范符合度 | 代码质量 | 测试覆盖 | 综合评估 |
|------|---------------|----------|----------|----------|
| FastAPI主服务 | 85% | 80% | 10% | **需改进** |
| 诊断接口 | 70% | 65% | 30% | **需改进** |
| WebSocket双连接 | 65% | 70% | 20% | **需改进** |
| DICOM接口 | 80% | 75% | 50% | **基本达标** |
| 错误码体系 | 95% | 90% | 40% | **较好** |
| 限流 | 90%(代码) / 0%(生效) | 85% | 30% | **需改进** |
