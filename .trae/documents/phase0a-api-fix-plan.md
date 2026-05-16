# Phase 0A API接口轨道 — 审查问题修复计划

**基于**：`phase0a-api-track-audit-report.md` 审查报告  
**原则**：Simplicity First + Surgical Changes — 最小化修改，仅修复审查发现的问题，不做额外重构  

---

## 修复批次与依赖关系

```
批次1（无依赖，可并行）: S3, S4, S1, L5
批次2（依赖批次1）: S2, M1, M3, M5
批次3（依赖批次2）: M9, M10, L1-L4
批次4（测试补充）: 测试覆盖度
```

---

## 批次1：严重问题 + 独立修复项

### Fix S3：DICOM上传返回未脱敏的patient_id/patient_name

**文件**：`app/api/v1/imaging.py`  
**行号**：93-109（`extract_metadata_from_dataset`函数）

**问题**：`patient_id`和`patient_name`直接返回原始值，规范要求`P******`格式脱敏。

**修复方案**：

1. 在`imaging.py`中新增脱敏辅助函数：
```python
def _desensitize_patient_id(value: str) -> str:
    if not value:
        return ""
    return value[:1] + "******"

def _desensitize_patient_name(value: str) -> str:
    if not value:
        return ""
    return value[:1] + "******"
```

2. 修改`extract_metadata_from_dataset`函数第96-97行：
```python
# 修改前
"patient_id": str(getattr(ds, "PatientID", "")),
"patient_name": str(getattr(ds, "PatientName", "")),

# 修改后
"patient_id": _desensitize_patient_id(str(getattr(ds, "PatientID", ""))),
"patient_name": _desensitize_patient_name(str(getattr(ds, "PatientName", ""))),
```

**注意**：`patient_id`的原始值仍需在存储路径中使用（imaging.py:154 `patient_id = metadata.get("patient_id", "unknown")`），因此需要在脱敏前保存原始值。修改方案：

```python
def extract_metadata_from_dataset(ds: pydicom.Dataset, content: bytes) -> dict:
    raw_patient_id = str(getattr(ds, "PatientID", ""))
    raw_patient_name = str(getattr(ds, "PatientName", ""))
    return {
        "patient_id": _desensitize_patient_id(raw_patient_id),
        "patient_name": _desensitize_patient_name(raw_patient_name),
        "raw_patient_id": raw_patient_id,  # 仅用于内部存储路径，不返回给客户端
        ...
    }
```

3. 修改`upload_dicom`函数第154行，使用`raw_patient_id`：
```python
# 修改前
patient_id = metadata.get("patient_id", "unknown")

# 修改后
patient_id = metadata.get("raw_patient_id", "unknown")
```

4. 在返回给客户端前，从metadata中移除`raw_patient_id`：
```python
metadata.pop("raw_patient_id", None)
```

**验证**：上传DICOM文件后，响应中`patient_id`应为`P******`格式，存储路径仍使用原始PatientID。

---

### Fix S4：E4007错误码冲突

**文件**：`app/api/v1/errors.py`  
**行号**：365-374, 470

**问题**：`HSMUnavailableException`使用E4007，但规范定义E4007为"Redis不可用导致新会话创建延迟增加"。

**修复方案**：

1. 修改`HSMUnavailableException`错误码为E4013（行365-374）：
```python
class HSMUnavailableException(InfrastructureException):
    def __init__(self, error_message: str = "HSM不可用", **kwargs):
        super().__init__(
            error_code="E4013",
            error_message=error_message,
            status_code=503,
            retryable=True,
            retry_strategy="retry_after_5s",
            **kwargs
        )
```

2. 新增`RedisUnavailableNewSessionDelayException`（在HSMUnavailableException之后）：
```python
class RedisUnavailableNewSessionDelayException(InfrastructureException):
    def __init__(self, error_message: str = "Redis不可用导致新会话创建延迟增加", **kwargs):
        super().__init__(
            error_code="E4007",
            error_message=error_message,
            status_code=503,
            retryable=True,
            retry_strategy="retry_after_5s",
            suggestion="新会话将写入PostgreSQL替代Redis",
            **kwargs
        )
```

3. 更新ERROR_CODE_MAP（行470和新增）：
```python
"E4007": "Redis不可用导致新会话创建延迟增加",
"E4013": "HSM不可用",
```

**验证**：搜索项目中所有使用`HSMUnavailableException`的地方，确认E4013生效；E4007映射与规范一致。

---

### Fix S1：DiagnosisRequest允许客户端传入request_id

**文件**：`app/models/schemas.py`（行99）、`app/api/v1/diagnosis.py`（行87）

**问题**：`request_id`出现在Request模型中，但规范中由服务端生成。

**修复方案**：

方案A（推荐）：从`DiagnosisRequest`中移除`request_id`字段，在endpoint中始终使用服务端生成的值。

1. 修改`schemas.py`第98-99行，删除`request_id`字段：
```python
class DiagnosisRequest(BaseModel):
    session_id: str
    image_ids: Optional[List[str]] = None
    ...
```

2. 修改`diagnosis.py`第87行，始终使用服务端生成：
```python
# 修改前
request_id = request.request_id or _generate_request_id()

# 修改后
request_id = _generate_request_id()
```

**验证**：客户端POST请求中即使包含`request_id`字段也会被Pydantic忽略（若使用`model_config = {"extra": "ignore"}`）或报422验证错误。需确认前端未依赖传入`request_id`。

---

### Fix L5：E5000错误码超出规范范围

**文件**：`app/api/v1/errors.py`（行425）

**问题**：`general_exception_handler`使用E5000，规范仅定义E1xxx-E4xxx。

**修复方案**：

修改`general_exception_handler`中的错误码为E2001（服务端错误）：
```python
# 修改前
error_code="E5000",

# 修改后
error_code="E2001",
```

**验证**：未处理异常返回的错误码为E2001而非E5000。

---

## 批次2：严重问题S2 + 中等问题M1/M3/M5

### Fix S2：SafetyGateway校验allowed=false时误抛E4005

**文件**：`app/api/v1/diagnosis.py`（行198-199）

**问题**：SG返回`allowed=false`时直接抛`SafetyGatewayUnavailableException(E4005)`，但`allowed=false`可能是因为内容不通过安全校验（非服务不可用）。

**修复方案**：

1. 在`errors.py`中新增异常类（用于区分"内容安全校验不通过"）：
```python
class SafetyOutputBlockedException(SafetyException):
    def __init__(self, error_message: str = "安全校验未通过，输出被阻止", **kwargs):
        super().__init__(
            error_code="E3006",
            error_message=error_message,
            suggestion="请人工复核",
            **kwargs
        )
```

2. 在ERROR_CODE_MAP中新增：
```python
"E3006": "安全校验未通过，输出被阻止",
```

3. 修改`diagnosis.py`第191-210行，区分SG返回结果：
```python
sg_result = await safety_gateway_client.check_output(
    session_id=result.session_id,
    patient_id=patient_id,
    output_data=result.findings or {},
    consent_status_at_input=consent_status.value,
)

if not sg_result.get("allowed", False):
    if sg_result.get("service_unavailable", False):
        raise SafetyGatewayUnavailableException()
    desensitization_status = DesensitizationStatus.DEGRADED
    consent_status_at_output = ConsentStatus.DENIED
    requires_manual_review = True
    review_reason = sg_result.get("message", "安全校验未通过")
else:
    consent_status_at_output = ConsentStatus.GRANTED
    sg_consent = sg_result.get("consent_status_at_output", "granted")
    if sg_consent == "withdrawn":
        consent_status_at_output = ConsentStatus.WITHDRAWN
    elif sg_consent in ("unknown_degraded", "unknown_blocked"):
        consent_status_at_output = ConsentStatus.DENIED

    desensitization_status = DesensitizationStatus.FULL
    if sg_result.get("degraded_mode"):
        desensitization_status = DesensitizationStatus.DEGRADED
```

4. 在`DiagnosisResult`构造中添加`requires_manual_review`和`review_reason`的动态值：
```python
diagnosis_result = DiagnosisResult(
    ...
    requires_manual_review=requires_manual_review if not sg_result.get("allowed", False) else False,
    review_reason=review_reason if not sg_result.get("allowed", False) else None,
    ...
)
```

**验证**：SG返回`allowed=false`且非服务不可用时，返回HTTP 200 + 降级标记而非503。

---

### Fix M1：frame_index负值检查可读性差

**文件**：`app/api/websocket/dicom.py`（行116）

**修复方案**：

添加注释说明无符号转换逻辑，并将检查改为更直观的写法：
```python
# 修改前
if frame_index >= 0x80000000:

# 修改后
if frame_index >= 0x80000000:  # struct.unpack("<I") returns unsigned; >=0x80000000 means negative in signed int
```

或改为更直观的写法：
```python
# 将无符号整数转为有符号整数检查
signed_frame_index = frame_index if frame_index < 0x80000000 else frame_index - 0x100000000
if signed_frame_index < 0:
```

选择方案1（仅添加注释），因为逻辑正确且性能更优。

---

### Fix M3：DICOM帧缓存90帧重传限制

**文件**：`app/api/websocket/dicom.py`

**问题**：规范要求重传请求的帧索引范围不超过最近90帧。

**修复方案**：

1. 在dicom.py中新增常量：
```python
MAX_RETRANSMIT_FRAME_RANGE = 90
```

2. 在主循环中跟踪最新帧索引：
```python
# 在websocket_dicom_endpoint函数中新增变量
latest_frame_index = -1
```

3. 在帧处理成功后更新最新帧索引：
```python
# 在CRC32校验通过后、frame_count递增后
if frame_index > latest_frame_index:
    latest_frame_index = frame_index
```

4. 在重传请求处理中添加范围校验（当前实现中重传请求通过CRC32失败自动触发，无需客户端主动请求重传。但规范要求"超出范围的帧索引返回E1001"，因此需要在接收到重传请求时校验）。

**注意**：当前实现中重传由服务端在CRC32失败时主动请求，客户端被动重发。规范中"重传请求的帧索引范围不超过最近90帧"主要约束的是客户端主动请求重传的场景。当前实现无需修改重传逻辑，但应在重传请求中添加帧索引范围校验作为防御性编程。

在CRC32校验失败请求重传前添加校验：
```python
if received_crc != computed_crc:
    if latest_frame_index >= 0 and frame_index < latest_frame_index - MAX_RETRANSMIT_FRAME_RANGE:
        logger.warning(
            "Retransmit request out of range",
            session_id=session_id,
            frame_index=frame_index,
            latest=latest_frame_index,
        )
        await websocket.send_json({
            "type": "error",
            "error_code": "E1001",
            "error_message": f"帧索引{frame_index}超出重传范围（最近{MAX_RETRANSMIT_FRAME_RANGE}帧）",
        })
        continue
    # ... 原有重传逻辑
```

---

### Fix M5：FHIR接口限流未配置

**文件**：`app/config/rate_limit.py`（行39-47）

**修复方案**：

在RATE_LIMITS字典中添加FHIR路径：
```python
RATE_LIMITS = {
    "/api/v1/diagnosis/request": {"capacity": 10, "refill_rate": 10 / 60},
    "/api/v1/imaging/dicom/upload": {"capacity": 30, "refill_rate": 30 / 60},
    "/api/v1/imaging/dicom/stream": {"capacity": 30, "refill_rate": 30 / 60},
    "/api/v1/feedback": {"capacity": 20, "refill_rate": 20 / 60},
    "/api/v1/auth/token": {"capacity": 5, "refill_rate": 5 / 60},
    "/api/v1/auth/login": {"capacity": 5, "refill_rate": 5 / 60},
    "/api/v1/consent": {"capacity": 10, "refill_rate": 10 / 60},
    "/api/v1/fhir": {"capacity": 60, "refill_rate": 60 / 60},
}
```

**注意**：FHIR接口尚未实现，此配置为预留。限流中间件仅匹配精确路径前缀，需确认FHIR路由的路径前缀。

---

## 批次3：中等问题M9/M10 + 轻微问题L1-L4

### Fix M9：单session仅支持单WebSocket连接

**文件**：`app/api/websocket/nav.py`

**问题**：`active_connections`以`session_id`为key，同一session仅支持一个连接。

**修复方案**：

将key改为`(session_id, device_id)`复合key：

1. 修改ConnectionManager的Dict定义（行41-44）：
```python
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[tuple, WebSocket] = {}
        self.auth_tokens: Dict[str, str] = {}
        self.last_heartbeat: Dict[tuple, float] = {}
        self.device_sessions: Dict[str, str] = {}
        self._lock = asyncio.Lock()
```

2. 修改`connect`方法：
```python
async def connect(self, websocket: WebSocket, session_id: str, device_id: str):
    await websocket.accept()
    key = (session_id, device_id)
    async with self._lock:
        self.active_connections[key] = websocket
        self.last_heartbeat[key] = time.time()
    logger.info("WebSocket connected", session_id=session_id, device_id=device_id)
```

3. 修改`disconnect`方法：
```python
async def disconnect(self, session_id: str, device_id: str):
    key = (session_id, device_id)
    async with self._lock:
        if key in self.active_connections:
            del self.active_connections[key]
        if key in self.last_heartbeat:
            del self.last_heartbeat[key]
    # auth_tokens仍以session_id为key（同一session共享token）
    async with self._lock:
        for dev_id, sess_id in list(self.device_sessions.items()):
            if sess_id == session_id and dev_id == device_id:
                del self.device_sessions[dev_id]
    logger.info("WebSocket disconnected", session_id=session_id, device_id=device_id)
```

4. 修改`send_personal_message`方法，支持按session_id广播：
```python
async def send_personal_message(self, message: dict, session_id: str):
    async with self._lock:
        for (sid, did), ws in self.active_connections.items():
            if sid == session_id:
                try:
                    await ws.send_json(message)
                except Exception as e:
                    logger.error("Failed to send message", session_id=sid, device_id=did, error=str(e))
```

5. 修改`check_heartbeat_timeout`方法：
```python
async def check_heartbeat_timeout(self, timeout_seconds: int = 15) -> list:
    current_time = time.time()
    timeout_keys = []
    async with self._lock:
        for key, last_time in list(self.last_heartbeat.items()):
            if current_time - last_time > timeout_seconds:
                timeout_keys.append(key)
    return timeout_keys
```

6. 修改`websocket_nav_endpoint`中的调用：
```python
await manager.connect(websocket, session_id, device_id)
# ...
timeout_keys = await manager.check_heartbeat_timeout(15)
for timeout_key in timeout_keys:
    if timeout_key[0] == session_id:
        # ...
await manager.disconnect(session_id, device_id)
```

**影响范围**：nav.py中所有调用ConnectionManager方法的地方需同步更新。`send_diagnosis_result`、`send_session_terminated`、`send_navigation_revocation`、`send_reconnect_ack`等方法使用`session_id`参数，通过`send_personal_message`广播，无需修改签名。

---

### Fix M10：DICOM流式接口未验证解码后格式

**文件**：`app/api/v1/imaging.py`（行208-212）

**修复方案**：

在base64解码后添加DICOM格式验证：
```python
try:
    frame_data = base64.b64decode(request.frame_data)
except Exception:
    raise BadRequestException(error_message="Invalid base64 data")

if len(frame_data) >= 132 and frame_data[128:132] == b"DICM":
    pass
elif len(frame_data) > 0:
    logger.warning(
        "Streamed frame is not valid DICOM format",
        session_id=request.session_id,
        frame_index=request.frame_index,
    )
```

**注意**：流式接口的帧数据可能不是完整DICOM文件（可能是裸像素帧），因此仅做warning而非拒绝。这与upload接口不同——upload要求完整DICOM文件，stream可能传输部分帧数据。

---

### Fix L1：心跳魔法数字未提取为配置

**文件**：`app/api/websocket/nav.py`（行246-247）

**修复方案**：

1. 在`app/config/settings.py`中新增配置项：
```python
navigation_ws_heartbeat_interval_seconds: int = 5
navigation_ws_heartbeat_timeout_seconds: int = 15
```

2. 在nav.py中导入settings并使用：
```python
from app.config.settings import settings

# 修改行246-247
await asyncio.sleep(settings.navigation_ws_heartbeat_interval_seconds)
timeout_sessions = await manager.check_heartbeat_timeout(settings.navigation_ws_heartbeat_timeout_seconds)
```

---

### Fix L2：诊断结果缓存key未使用RedisKey类

**文件**：`app/api/v1/diagnosis.py`（行166）

**修复方案**：

1. 在`app/config/redis_keys.py`的`RedisKey`类中新增方法：
```python
@staticmethod
def diagnosis_result(request_id: str) -> str:
    return f"{_PREFIX}:diagnosis:result:{request_id}"
```

2. 修改`diagnosis.py`第166行：
```python
# 修改前
cache_key = f"ultrasound:diagnosis:result:{request_id}"

# 修改后
cache_key = RedisKey.diagnosis_result(request_id)
```

---

### Fix L3：DICOM WebSocket使用Depends(get_db_session)

**文件**：`app/api/websocket/dicom.py`（行42）

**修复方案**：

将`Depends(get_db_session)`改为手动管理`AsyncSessionLocal`，与nav.py保持一致：

```python
# 修改前
@router.websocket("/api/v1/realtime/dicom")
async def websocket_dicom_endpoint(
    websocket: WebSocket,
    session_id: str = Query(...),
    device_id: str = Query(...),
    db: AsyncSession = Depends(get_db_session),
):

# 修改后
@router.websocket("/api/v1/realtime/dicom")
async def websocket_dicom_endpoint(
    websocket: WebSocket,
    session_id: str = Query(...),
    device_id: str = Query(...),
):
```

在函数内部手动创建DB session：
```python
async with AsyncSessionLocal() as db:
    audit_service = AuditService(db)
    # ... 原有逻辑 ...
    try:
        await db.commit()
    except Exception:
        await db.rollback()
```

需要导入`AsyncSessionLocal`：
```python
from app.config.database import AsyncSessionLocal
```

移除不再需要的导入：
```python
# 移除
from app.config.database import get_db_session
```

---

### Fix L4：反馈幂等key缺少ultrasound:前缀

**文件**：`app/api/v1/feedback.py`（行38, 76）

**修复方案**：

1. 在`app/config/redis_keys.py`的`RedisKey`类中新增方法：
```python
@staticmethod
def feedback_idempotency(key: str) -> str:
    return f"{_PREFIX}:feedback:idempotency:{key}"
```

2. 修改`feedback.py`第38行和第76行：
```python
# 修改前
cache_key = f"feedback:idempotency:{request.idempotency_key}"

# 修改后
cache_key = RedisKey.feedback_idempotency(request.idempotency_key)
```

3. 在feedback.py中导入RedisKey：
```python
from app.config.redis_keys import RedisKey
```

---

## 批次4：测试覆盖度补充

### 新增测试：DICOM上传接口

**文件**：`tests/unit/api/test_dicom_upload.py`（新建）

测试场景：
1. 有效DICOM文件上传成功
2. 空文件上传失败
3. 非DICOM文件上传失败（无DICM magic word）
4. 缺少必需DICOM Tag上传失败
5. Modality非US上传失败
6. BitsAllocated>16上传失败
7. 文件大小超限上传失败
8. patient_id脱敏验证（P******格式）
9. patient_name脱敏验证
10. 不存在的session_id上传失败

### 新增测试：限流中间件

**文件**：`tests/unit/api/test_rate_limit.py`（新建）

测试场景：
1. 正常请求通过限流
2. 超限请求返回429 + E1007 + Retry-After
3. 急诊优先级豁免（X-Priority: 0）
4. 匿名用户限流
5. 不同路径独立限流
6. FHIR路径限流配置存在

### 新增测试：错误码映射一致性

**文件**：`tests/unit/api/test_error_code_consistency.py`（新建）

测试场景：
1. 每个异常类的error_code在ERROR_CODE_MAP中有对应条目
2. ERROR_CODE_MAP中每个E4xxx条目有对应异常类
3. E4007映射为"Redis不可用导致新会话创建延迟增加"（而非HSM不可用）
4. E4013映射为"HSM不可用"

### 新增测试：SafetyGateway校验结果处理

**文件**：`tests/unit/api/test_safety_gateway_handling.py`（新建）

测试场景：
1. SG返回allowed=true → 正常输出
2. SG返回allowed=false + service_unavailable=true → 抛SafetyGatewayUnavailableException(E4005)
3. SG返回allowed=false + service_unavailable=false → 返回200 + 降级标记
4. SG返回allowed=true + degraded_mode=true → desensitization_status=DEGRADED

---

## 不修复项（记录原因）

| 问题 | 原因 |
|------|------|
| M2 重连策略未实现 | 服务端无需实现，客户端（前端reconnecting-websocket库）负责重连 |
| M4 zstd压缩协商 | 二期必须实现，一期标记为TODO |
| M6 多器官计1次限流 | 需调整中间件架构，Phase 0B前实现 |
| M7 retry不计限流 | 同M6，需调整中间件架构 |
| M8 ConsentStatus枚举扩展 | 需在规范中补充定义，代码不改 |

---

## 修改文件清单

| 文件 | 修改类型 | 涉及问题 |
|------|----------|----------|
| `app/api/v1/imaging.py` | 修改 | S3, M10 |
| `app/api/v1/errors.py` | 修改 | S4, S2, L5 |
| `app/models/schemas.py` | 修改 | S1 |
| `app/api/v1/diagnosis.py` | 修改 | S1, S2, L2 |
| `app/api/websocket/nav.py` | 修改 | M9, L1 |
| `app/api/websocket/dicom.py` | 修改 | M1, M3, L3 |
| `app/config/rate_limit.py` | 修改 | M5 |
| `app/config/redis_keys.py` | 修改 | L2, L4 |
| `app/config/settings.py` | 修改 | L1 |
| `app/api/v1/feedback.py` | 修改 | L4 |
| `tests/unit/api/test_dicom_upload.py` | 新建 | 测试 |
| `tests/unit/api/test_rate_limit.py` | 新建 | 测试 |
| `tests/unit/api/test_error_code_consistency.py` | 新建 | 测试 |
| `tests/unit/api/test_safety_gateway_handling.py` | 新建 | 测试 |

---

## 执行顺序

```
1. Fix S3 → imaging.py脱敏
2. Fix S4 → errors.py E4007冲突
3. Fix S1 → schemas.py + diagnosis.py request_id
4. Fix L5 → errors.py E5000→E2001
5. Fix S2 → errors.py新增SafetyOutputBlockedException + diagnosis.py SG处理
6. Fix M1 → dicom.py注释
7. Fix M3 → dicom.py 90帧限制
8. Fix M5 → rate_limit.py FHIR配置
9. Fix M9 → nav.py多设备连接
10. Fix M10 → imaging.py流式验证
11. Fix L1 → settings.py + nav.py心跳配置
12. Fix L2 → redis_keys.py + diagnosis.py缓存key
13. Fix L3 → dicom.py DB session管理
14. Fix L4 → redis_keys.py + feedback.py幂等key
15. 运行现有测试确认无回归
16. 新增测试文件
17. 运行全部测试
```
