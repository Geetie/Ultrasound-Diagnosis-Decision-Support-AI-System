# API接口轨道开发计划 — Phase 0A (Gate 0A)

## 任务来源

`各阶段并发任务.md#L34`: **API接口** — FastAPI主服务、诊断接口、WebSocket双连接、DICOM接口 — 多任务并行

对应任务ID:
- T-GA-API-01: FastAPI主服务骨架+lifespan事件+依赖注入+错误码体系(E1xxx-E4xxx)
- T-GA-API-02: 诊断请求/结果接口(POST/GET+幂等性+结果缓存)
- T-GA-API-03: WebSocket双连接(导航通道+DICOM帧通道)
- T-GA-API-04: DICOM影像接入接口(upload+stream)
- T-GA-API-05: 反馈提交接口(L2/L3分级+幂等性)

## 现有代码分析

### 已实现的功能（需增强/修复）

| 模块 | 文件 | 现状 | 差距 |
|------|------|------|------|
| FastAPI主服务 | `app/main.py` | 基本骨架+lifespan，4个router已注册 | **缺少6个router注册**（diagnosis/session/consent/feedback/imaging/websocket），缺少异常处理器注册 |
| 诊断接口 | `app/api/v1/diagnosis.py` | POST/GET骨架，幂等性，SafetyGateway调用 | 幂等性TTL应为5分钟(300s)非10分钟，缺少retry_strategy字段，缺少request_id自动生成 |
| WebSocket导航 | `app/api/websocket/nav.py` | 双连接之一，认证+心跳+导航标注 | 缺少session_terminated事件推送，缺少navigation_revocation消息，缺少reconnect_ack携带TTL |
| WebSocket DICOM | `app/api/websocket/dicom.py` | 双连接之二，认证+CRC32+帧解析 | 基本完整，缺少帧存储集成 |
| DICOM接口 | `app/api/v1/imaging.py` | upload+stream | 基本完整，缺少instance_uid返回 |
| 反馈接口 | `app/api/v1/feedback.py` | L2/L3提交 | 缺少幂等性保障，缺少衰减触发逻辑 |
| 会话接口 | `app/api/v1/session.py` | CRUD | 缺少会话TTL续期逻辑，缺少KB版本锁定 |
| 同意接口 | `app/api/v1/consent.py` | 查询+更新 | 基本完整 |
| 错误码体系 | `app/api/v1/errors.py` | E1xxx-E4xxx覆盖 | 缺少E4006知识库安全更新异常 |
| Schemas | `app/models/schemas.py` | 核心模型已定义 | 缺少request_id自动生成，缺少retry_strategy字段 |

### 关键差距清单

1. **main.py未注册所有router** — diagnosis/session/consent/feedback/imaging/websocket均未注册
2. **异常处理器未注册** — app_exception_handler/http_exception_handler/general_exception_handler未挂载
3. **诊断接口幂等性TTL错误** — 600s应为300s（5分钟，与03-接口规范对齐）
4. **诊断请求缺少request_id自动生成** — 前端应自动生成唯一request_id，后端也应有兜底
5. **反馈接口缺少幂等性** — 03-接口规范要求feedback_id幂等键
6. **WebSocket缺少关键消息类型** — session_terminated/navigation_revocation/reconnect_ack
7. **会话接口缺少TTL续期** — 诊断请求/反馈提交应触发会话续期
8. **缺少E4006异常类** — 知识库安全更新导致会话终止

## 实施步骤

### Step 1: 注册所有router和异常处理器到main.py

**目标**: 让所有已开发的API端点可达

**修改文件**: `app/main.py`

**具体操作**:
- 导入并注册6个缺失router: diagnosis, session, consent, feedback, imaging, websocket(nav+dicom)
- 注册3个异常处理器: app_exception_handler, http_exception_handler, general_exception_handler
- 确保lifespan中已有的初始化逻辑不变

**验证**: `python -c "from app.main import app; print([r.path for r in app.routes])"` 列出所有路由

### Step 2: 补充E4006异常类和缺失的错误码

**目标**: 完善错误码体系覆盖

**修改文件**: `app/api/v1/errors.py`

**具体操作**:
- 新增 `KnowledgeBaseSafetyUpdateException` (E4006) — 知识库安全更新，在途诊断已取消
- 在ERROR_CODE_MAP中确认E4006条目存在

**验证**: 单元测试验证E4006异常类

### Step 3: 修复诊断接口 — 幂等性TTL + request_id自动生成 + retry_strategy

**目标**: 与03-接口规范对齐

**修改文件**: `app/api/v1/diagnosis.py`, `app/models/schemas.py`

**具体操作**:
- 幂等性TTL从600s改为300s（5分钟）
- DiagnosisRequest增加request_id字段（前端生成，后端兜底）
- DiagnosisRequest增加retry_strategy字段（full_retrieval/generation_only/quality_check_only）
- 诊断结果获取时，若ai_diagnosis_failed=true且retry=true，根据retry_strategy设置重试逻辑

**验证**: 单元测试验证幂等性TTL、request_id生成、retry_strategy传递

### Step 4: 增强反馈接口 — 幂等性保障

**目标**: 反馈提交满足幂等性要求

**修改文件**: `app/api/v1/feedback.py`, `app/models/schemas.py`

**具体操作**:
- FeedbackSubmission增加idempotency_key字段
- 提交前检查Redis幂等性缓存（24小时TTL，与03-接口规范对齐）
- 缓存命中时返回已存在的反馈结果

**验证**: 单元测试验证幂等性去重

### Step 5: 增强WebSocket导航通道 — session_terminated/navigation_revocation/reconnect_ack

**目标**: 与01-系统架构设计和03-接口规范对齐

**修改文件**: `app/api/websocket/nav.py`, `app/models/schemas.py`

**具体操作**:
- schemas.py增加WebSocketMessageType枚举: SESSION_TERMINATED, NAVIGATION_REVOCATION, RECONNECT_ACK
- schemas.py增加对应消息模型: SessionTerminatedMessage, NavigationRevocationMessage, ReconnectAckMessage
- nav.py增加send_session_terminated方法（推送知识库安全更新事件）
- nav.py增加send_navigation_revocation方法（推送导航标注撤销通知）
- WebSocket连接成功时发送reconnect_ack消息（携带session_ttl_remaining_ms）

**验证**: 单元测试验证新消息类型和方法

### Step 6: 增强会话接口 — TTL续期

**目标**: 诊断请求和反馈提交触发会话续期

**修改文件**: `app/api/v1/session.py`, `app/repositories/session_repository.py`

**具体操作**:
- session_repository增加renew_session方法（更新updated_at，延长TTL）
- diagnosis.py的submit_diagnosis_request中调用renew_session
- feedback.py的submit_feedback中调用renew_session
- 会话查询接口返回ttl_remaining字段

**验证**: 单元测试验证续期逻辑

### Step 7: 编写API轨道单元测试

**目标**: 覆盖所有新增/修改功能

**修改文件**: `tests/unit/api/test_api_phase0a.py`（新建）

**具体操作**:
- 测试所有router注册
- 测试异常处理器
- 测试E4006异常
- 测试诊断接口幂等性TTL=300s
- 测试request_id自动生成
- 测试retry_strategy传递
- 测试反馈接口幂等性
- 测试WebSocket新消息类型
- 测试会话续期

**验证**: `pytest tests/unit/api/test_api_phase0a.py -v` 全部通过

### Step 8: 运行lint和现有测试确认无回归

**目标**: 确保修改不破坏现有功能

**具体操作**:
- 运行ruff检查
- 运行现有API测试: `pytest tests/unit/api/ -v`
- 运行全量测试确认无回归

## 依赖关系

```
Step 1 (main.py注册) → 无依赖，最先执行
Step 2 (E4006异常) → 无依赖，可与Step 1并行
Step 3 (诊断接口修复) → 依赖Step 1（router需注册）
Step 4 (反馈接口幂等性) → 依赖Step 1
Step 5 (WebSocket增强) → 依赖Step 1
Step 6 (会话续期) → 依赖Step 1
Step 7 (单元测试) → 依赖Step 2-6全部完成
Step 8 (回归测试) → 依赖Step 7
```

## 验收标准

| 任务ID | 验收标准 |
|--------|---------|
| T-GA-API-01 | 服务启动+健康检查通过+错误码覆盖E1xxx-E4xxx+所有router注册+异常处理器挂载 |
| T-GA-API-02 | 端到端诊断P99<5s+幂等性TTL=300s+request_id自动生成+retry_strategy支持 |
| T-GA-API-03 | 双连接建立+心跳+重连+session_terminated+navigation_revocation+reconnect_ack |
| T-GA-API-04 | 帧接收+元数据解析+CRC32校验+重传（已有，验证即可） |
| T-GA-API-05 | 反馈提交+幂等性+L2/L3衰减触发 |

## 风险与注意事项

1. **不修改数据库Schema** — 本轮不涉及Alembic迁移，所有改动在API层和逻辑层
2. **保持向后兼容** — 新增字段均为Optional，不破坏现有前端调用
3. **不引入新依赖** — 所有改动使用现有依赖（FastAPI/Redis/SQLAlchemy/structlog）
4. **WebSocket消息类型扩展** — 仅新增枚举值和消息模型，不修改现有消息格式
