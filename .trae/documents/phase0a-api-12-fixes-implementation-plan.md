# Phase0A API接口轨道 — 12项待修复项系统性修复实施计划

> 审查基线：2026-05-14 | 计划版本：v2（基于代码现状更新）

---

## 当前状态总览

| # | 编号 | 优先级 | 修复项 | 状态 | 涉及文件 |
|---|------|--------|--------|------|---------|
| 1 | F-01 | P0 | DICOM帧缓存缺失（90帧环形缓冲区） | ❌ 未实施 | `dicom.py`, `settings.py` |
| 2 | F-05 | P0 | consent状态语义矛盾 | ✅ 已完成 | `session.py` |
| 3 | Q-03/Q-04 | P0 | dicom_storage异步化+Windows兼容 | ✅ 已完成 | `dicom_storage.py`, `imaging.py` |
| 4 | F-02 | P1 | 设备恢复token持久化存储+验证 | ❌ 未实施 | `device.py`, `device_repository.py`, `redis_keys.py` |
| 5 | F-04 | P1 | FHIR资源引用错误(session_id→patient_id) | ❌ 未实施 | `fhir.py` |
| 6 | Q-02 | P1 | SafetyGatewayClient.check_output重试逻辑拆分 | ❌ 未实施 | `safety_gateway_client.py` |
| 7 | F-03 | P2 | 语音确认后业务执行缺失 | ❌ 未实施 | `voice.py`, `schemas.py` |
| 8 | F-06 | P2 | Phase0A Mock硬编码替换为推理管线接入点 | ❌ 未实施 | `diagnosis.py`, `nav.py`, `settings.py` |
| 9 | S-01 | P2 | 路由前缀不一致(health.py) | ❌ 未实施 | `health.py`, `main.py` |
| 10 | Q-06 | P2 | 生产依赖混入测试包 | ❌ 未实施 | `requirements.txt`, `requirements-dev.txt` |
| 11 | Q-05 | P2 | SessionResponse构造代码重复3次 | ❌ 未实施 | `session.py` |
| 12 | D-12 | P2 | MinIO存储后端未实现 | ❌ 未实施 | `dicom_storage.py`, `settings.py` |

---

## 实施顺序与依赖关系

```
Phase 1 (P0 — 必须立即修复，仅剩1项):
  #1 F-01 DICOM帧缓存 ← 无依赖

Phase 2 (P1 — 本迭代内修复):
  #5 F-04 FHIR引用修正 ← 无依赖
  #4 F-02 设备token持久化 ← 无依赖，可与#5并行
  #6 Q-02 SafetyGateway拆分 ← 无依赖，可独立进行

Phase 3 (P2 — 可排期修复):
  #11 Q-05 SessionResponse去重 ← 无依赖，小改动
  #10 Q-06 依赖分离 ← 无依赖，小改动
  #9 S-01 路由前缀统一 ← 无依赖，小改动
  #7 F-03 语音确认业务执行 ← 依赖#2(已完成)
  #8 F-06 Mock替换 ← 依赖推理管线轨道进度
  #12 D-12 MinIO实现 ← 工作量大，可推迟至Phase0B
```

---

## 修复项 #1: F-01 — DICOM帧缓存缺失

### 问题描述
03-§1.2规范要求"边缘端缓存最近90帧已上传的DICOM数据"。当前 `dicom.py:264-276` 在接收帧后仅记录日志，未缓存帧数据。当CRC校验失败发送`retransmit_request`时，后端无缓存可提供，重传机制形同虚设。

### 影响范围
- 医疗影像传输完整性无法保障
- CRC校验失败的帧永久丢失
- 重传请求无实际意义

### 实施步骤

**步骤1：在 `app/api/websocket/dicom.py` 中新增 `FrameBuffer` 类**

在文件顶部（`MAX_DICOM_FRAME_PAYLOAD_BYTES` 定义之后）添加：

```python
from collections import OrderedDict

class FrameBuffer:
    def __init__(self, capacity: int = 90):
        self._capacity = capacity
        self._buffer: OrderedDict[int, bytes] = OrderedDict()

    def put(self, frame_index: int, data: bytes) -> None:
        if frame_index in self._buffer:
            del self._buffer[frame_index]
        self._buffer[frame_index] = data
        while len(self._buffer) > self._capacity:
            self._buffer.popitem(last=False)

    def get(self, frame_index: int) -> Optional[bytes]:
        return self._buffer.get(frame_index)

    def get_range(self, start: int, end: int) -> Dict[int, bytes]:
        return {idx: data for idx, data in self._buffer.items() if start <= idx <= end}

    def clear(self) -> None:
        self._buffer.clear()
```

**步骤2：在 `websocket_dicom_endpoint` 函数中集成帧缓存**

- 在连接初始化变量区域（约L50附近）添加：`frame_buffer = FrameBuffer(capacity=settings.dicom_frame_buffer_capacity)`
- 在帧CRC校验通过后（当前L264附近，`frame_count += 1` 之前），添加：`frame_buffer.put(frame_index, dicom_data)`
- 在发送`retransmit_request`之前（当前L258附近），先检查缓存：若有该帧数据则直接重发二进制帧而非请求客户端重传

**步骤3：在 `app/config/settings.py` 中添加配置项**

```python
dicom_frame_buffer_capacity: int = 90
```

### 测试验证
- 新增 `test_frame_buffer_put_get` — 验证帧存入和读取
- 新增 `test_frame_buffer_eviction` — 验证超过90帧时自动驱逐最旧帧
- 新增 `test_frame_buffer_get_range` — 验证范围查询
- 新增 `test_dicom_retransmit_from_cache` — 集成测试：CRC失败时从缓存重发

---

## 修复项 #4: F-02 — 设备恢复token持久化存储+验证

### 问题描述
`device.py:126` 生成的 `recovery_token` 和 `device.py:170` 生成的 `new_api_key` 仅返回客户端，未持久化存储，无法后续验证token有效性。

### 影响范围
- 任何人可伪造recovery_token执行恢复操作
- remote_recovery的new_api_key无法被后续认证流程识别

### 实施步骤

**步骤1：在 `app/config/redis_keys.py` 的 `RedisKey` 类中添加设备相关键名**

```python
@staticmethod
def device_recovery_token(device_id: str) -> str:
    return f"{_PREFIX}:device:recovery_token:{device_id}"
```

**步骤2：在 `app/repositories/device_repository.py` 中添加token存储方法**

```python
from app.config.redis import RedisPool

async def store_recovery_token(self, device_id: str, token: str, expires_at: datetime) -> None:
    redis_client = await RedisPool.get_client()
    key = RedisKey.device_recovery_token(device_id)
    ttl = int((expires_at - datetime.now(timezone.utc)).total_seconds())
    if ttl > 0:
        await redis_client.setex(key, ttl, token)

async def verify_recovery_token(self, device_id: str, token: str) -> bool:
    redis_client = await RedisPool.get_client()
    key = RedisKey.device_recovery_token(device_id)
    stored = await redis_client.get(key)
    if stored and stored.decode() == token:
        await redis_client.delete(key)
        return True
    return False

async def store_new_api_key(self, device_id: str, api_key: str) -> None:
    hashed = _hash_api_key(api_key)
    device = await self.get_device(device_id)
    if device:
        device.api_key = hashed
        await self.db.flush()
```

**步骤3：修改 `app/api/v1/device.py` 中的 `recovery_device`**

在 `recovery_token = secrets.token_urlsafe(32)` 之后添加：
```python
expires_at_dt = datetime.now(timezone.utc) + timedelta(hours=1)
await device_repo.store_recovery_token(device_id, recovery_token, expires_at_dt)
expires_at = expires_at_dt.isoformat()
```

**步骤4：修改 `app/api/v1/device.py` 中的 `remote_recovery_device`**

在 `new_api_key = secrets.token_urlsafe(32)` 之后添加：
```python
await device_repo.store_new_api_key(device_id, new_api_key)
```

**步骤5：在 `device_repository.py` 中添加 `from app.config.redis_keys import RedisKey` import**

### 测试验证
- 新增 `test_recovery_token_stored_in_redis` — 验证token存储
- 新增 `test_recovery_token_verification` — 验证token验证流程
- 新增 `test_recovery_token_expiry` — 验证token过期自动清理
- 新增 `test_remote_recovery_api_key_hashed` — 验证新api_key已哈希存储

---

## 修复项 #5: F-04 — FHIR资源引用错误

### 问题描述
`fhir.py:83` 中 `DiagnosticReport` 的 `subject` 引用 `session_id` 而非 `patient_id`，`fhir.py:119` 中 `Observation` 同样错误。

### 影响范围
- FHIR R4标准合规性失败
- 前端/外部系统无法通过FHIR接口正确关联患者

### 实施步骤

**步骤1：修正 `get_diagnostic_report` 的subject引用**

将 `fhir.py:83` 的 `"subject": {"reference": f"Patient/{diagnosis.session_id}"}` 改为：
```python
session = await db.get(DiagnosisSession, diagnosis.session_id)
patient_id = session.patient_id if session else "unknown"
```
然后 `"subject": {"reference": f"Patient/{patient_id}"}`

**步骤2：修正 `list_observations` 的subject引用**

将 `fhir.py:119` 的 `"subject": {"reference": f"Patient/{diagnosis.session_id}"}` 同样通过session获取patient_id

**步骤3：修正 `create_imaging_study` 的输入验证**

将 `request: Request` 替换为Pydantic模型，在 `fhir.py` 中添加：
```python
from pydantic import BaseModel

class ImagingStudyCreateRequest(BaseModel):
    session_id: str
```
修改函数签名为 `request: ImagingStudyCreateRequest`，将 `body = await request.json()` 替换为直接使用 `request.session_id`

### 测试验证
- 新增 `test_fhir_diagnostic_report_subject_is_patient` — 验证subject引用patient_id
- 新增 `test_fhir_observation_subject_is_patient` — 验证subject引用patient_id
- 新增 `test_fhir_imaging_study_uses_pydantic_model` — 验证输入校验

---

## 修复项 #6: Q-02 — SafetyGatewayClient.check_output重试逻辑拆分

### 问题描述
`safety_gateway_client.py:97-218` 的 `check_output` 方法约120行，混合了快速失败判定、指数退避、超时判断、多种异常处理，可读性差，锁竞争风险高。

### 影响范围
- 高并发诊断请求时延迟增加
- 代码维护困难

### 实施步骤

**步骤1：提取快速失败检查方法**

```python
def _should_fast_fail(self) -> bool:
    return self._consecutive_failures >= self.FAST_FAIL_THRESHOLD
```

**步骤2：提取失败/成功计数方法**

```python
async def _record_failure(self) -> int:
    async with self._lock:
        self._consecutive_failures += 1
        return self._consecutive_failures

async def _record_success(self) -> None:
    async with self._lock:
        self._consecutive_failures = 0
```

**步骤3：提取单次请求尝试方法 `_attempt_check`**

返回 `(result: dict | None, should_retry: bool, error_code: str | None)` 三元组，封装HTTP请求和异常处理逻辑。

**步骤4：简化 `check_output` 主方法**

```python
async def check_output(self, ...):
    if self._should_fast_fail():
        return self._e4005_response()
    start_time = asyncio.get_running_loop().time()
    retry_attempt = 0
    while True:
        result, should_retry, error_code = await self._attempt_check(...)
        if result is not None and not should_retry:
            if error_code is None:
                await self._record_success()
            return result
        failures = await self._record_failure()
        if failures >= self.FAST_FAIL_THRESHOLD:
            return self._e4005_response(sub_code=error_code)
        elapsed = asyncio.get_running_loop().time() - start_time
        if elapsed >= self._queue_timeout:
            return self._e4005_response()
        retry_attempt += 1
        backoff_delay = min(self._retry_interval * (2 ** (retry_attempt - 1)), 4.0)
        await asyncio.sleep(min(backoff_delay, self._queue_timeout - elapsed))
```

### 测试验证
- 确保现有 `test_safety_gateway_handling.py` 全部通过
- 新增 `test_safety_gateway_fast_fail_check` — 验证快速失败判定
- 新增 `test_safety_gateway_retry_backoff` — 验证退避策略

---

## 修复项 #7: F-03 — 语音确认后业务执行缺失

### 问题描述
`voice.py:54-63` 中 `confirmed=True` 时仅设置 `action_taken` 字符串，未实际执行任何业务逻辑，`request_id` 始终为 `None`。

### 影响范围
- 语音指令功能完全不可用

### 实施步骤

**步骤1：在 `app/models/schemas.py` 的 `VoiceConfirmRequest` 中添加可选字段**

```python
organ: Optional[str] = None
grade_target: Optional[str] = None
nav_action: Optional[str] = None
```

**步骤2：在 `voice.py` 中实现 `diagnosis_trigger` 命令**

提取诊断请求核心逻辑为独立service函数（或在voice中直接调用repository），避免与diagnosis路由函数耦合：
```python
if request.command_type == "diagnosis_trigger":
    if not request.organ:
        raise BadRequestException(error_code="E1001", error_message="organ is required for diagnosis_trigger")
    from app.repositories import DiagnosisRepository
    diag_repo = DiagnosisRepository(db)
    diag_request = await diag_repo.create_request(
        session_id=request.session_id,
        patient_id=session.patient_id,
        organ=request.organ,
    )
    action_taken = "diagnosis_initiated"
    request_id = diag_request.request_id
```

**步骤3：实现 `grade_change` 命令**

```python
elif request.command_type == "grade_change":
    if not request.grade_target:
        raise BadRequestException(error_code="E1001", error_message="grade_target is required for grade_change")
    from app.models.schemas import ResponseTier
    await session_repo.update_session(request.session_id, status=None, organ_statuses=None)
    action_taken = f"grade_changed_to_{request.grade_target}"
```

**步骤4：实现 `navigation_control` 命令**

```python
elif request.command_type == "navigation_control":
    if not request.nav_action:
        raise BadRequestException(error_code="E1001", error_message="nav_action is required for navigation_control")
    from app.api.websocket.nav import manager as nav_manager
    await nav_manager.send_personal_message(
        {"type": "navigation_control", "action": request.nav_action, "session_id": request.session_id},
        request.session_id,
    )
    action_taken = f"navigation_{request.nav_action}"
```

### 测试验证
- 新增 `test_voice_confirm_diagnosis_trigger` — 验证触发诊断
- 新增 `test_voice_confirm_grade_change` — 验证分级切换
- 新增 `test_voice_confirm_navigation_control` — 验证导航控制
- 新增 `test_voice_confirm_missing_organ_for_diagnosis` — 验证参数校验

---

## 修复项 #8: F-06 — Phase0A Mock硬编码替换为推理管线接入点

### 问题描述
`diagnosis.py:185-187` 中 `PHASE0A_MOCK_PROCESSING_TIME_MS=2500` 等为硬编码值，`nav.py:413` 导航标注也为硬编码。

### 影响范围
- 诊断结果数据不真实
- 无法切换到实际推理管线

### 实施步骤

**步骤1：将Mock常量移至 `settings.py` 配置**

```python
phase0a_mock_processing_time_ms: int = 2500
phase0a_mock_model_version: str = "thyroid_v1.0.0"
phase0a_mock_kb_version: str = "kb_v1.0.0"
```

**步骤2：在 `diagnosis.py` 中接入推理引擎**

项目已有 `app.state.inference_engine`。修改诊断结果生成逻辑：
```python
inference_engine = getattr(request.app.state, "inference_engine", None)
if inference_engine:
    result = await inference_engine.predict(...)
    model_version = inference_engine.get_model_version()
    processing_time_ms = result.processing_time_ms
else:
    model_version = settings.phase0a_mock_model_version
    processing_time_ms = settings.phase0a_mock_processing_time_ms
```

**步骤3：在 `nav.py` 中接入推理引擎**

将硬编码导航标注替换为推理引擎调用，保留Phase0A fallback。

### 测试验证
- 修改现有 `test_diagnosis.py` 中Mock常量测试改为从settings读取
- 新增 `test_diagnosis_with_inference_engine` — 验证推理引擎接入
- 新增 `test_diagnosis_fallback_to_mock` — 验证无引擎时回退

---

## 修复项 #9: S-01 — 路由前缀不一致

### 问题描述
`health.py:8` 使用 `APIRouter()` 无前缀，而其他模块均使用 `/api/v1/` 前缀。

### 实施步骤

**步骤1：修改 `health.py` 路由前缀**

```python
router = APIRouter(prefix="/api/v1", tags=["health"])
```

**步骤2：确认 `main.py` 中路由挂载无需额外修改**

当前 `app.include_router(health_router)` 会自动使用router的prefix，路径变为 `/api/v1/health` 和 `/api/v1/health/hsm`。

**步骤3：更新相关测试中的health端点路径**

### 测试验证
- 修改现有测试中health端点路径为 `/api/v1/health`
- 新增 `test_health_endpoint_path_consistency` — 验证路径前缀一致

---

## 修复项 #10: Q-06 — 生产依赖混入测试包

### 问题描述
`requirements.txt:15-17` 包含 `pytest==8.4.2`、`pytest-asyncio==0.24.0`、`freezegun==1.5.1`。

### 实施步骤

**步骤1：从 `requirements.txt` 中移除测试依赖**

删除第15-17行（pytest、pytest-asyncio、freezegun）

**步骤2：在 `requirements-dev.txt` 中补充缺失的测试依赖**

确认已包含：
```
pytest==8.4.2
pytest-asyncio==0.24.0
freezegun==1.5.1
```

### 测试验证
- 验证 `pip install -r requirements.txt` 不包含测试包
- 验证 `pip install -r requirements-dev.txt` 包含完整测试工具链
- 运行 `pytest` 确认测试仍可正常执行

---

## 修复项 #11: Q-05 — SessionResponse构造代码重复3次

### 问题描述
`session.py` 中三个路由的 `SessionResponse` 构造代码完全相同（L76-88, L103-115, L148-160）。

### 实施步骤

**步骤1：提取辅助函数**

在 `_calc_ttl_remaining` 函数之后添加：
```python
def _build_session_response(session) -> SessionResponse:
    return SessionResponse(
        session_id=session.session_id,
        patient_id=session.patient_id,
        device_id=session.device_id,
        status=SessionStatus(session.status),
        target_organs=session.target_organs,
        organ_statuses={k: OrganStatus(v) for k, v in session.organ_statuses.items()},
        knowledge_base_version=session.knowledge_base_version,
        created_at=session.created_at,
        updated_at=session.updated_at,
        consent_status_at_creation=ConsentStatus(session.consent_status_at_creation),
        ttl_remaining_seconds=_calc_ttl_remaining(session.updated_at),
    )
```

**步骤2：替换三处重复代码为 `return _build_session_response(session)`**

### 测试验证
- 运行现有session测试确保重构后功能不变
- 新增 `test_session_response_builder` — 验证辅助函数输出正确

---

## 修复项 #12: D-12 — MinIO存储后端未实现

### 问题描述
`dicom_storage.py:83-96` 中 `_save_minio` 为空壳实现，直接fallback到local。

### 实施步骤

**步骤1：在 `requirements.txt` 中添加 `minio>=7.2.0`**

**步骤2：在 `settings.py` 中添加MinIO配置项**

```python
minio_endpoint: str = "localhost:9000"
minio_access_key: str = ""
minio_secret_key: str = ""
minio_bucket: str = "dicom-storage"
minio_secure: bool = False
```

**步骤3：在 `DicomStorageService` 中实现 `_get_minio_client`、`_save_minio`、minio分支的 `retrieve` 和 `delete`**

**步骤4：移除 `_save_minio` 中的静默fallback逻辑，改为抛出异常或正确实现**

### 测试验证
- 新增 `test_dicom_storage_minio_save` — 验证MinIO保存
- 新增 `test_dicom_storage_minio_retrieve` — 验证MinIO读取
- 新增 `test_dicom_storage_minio_delete` — 验证MinIO删除
- 新增 `test_dicom_storage_minio_no_silent_fallback` — 验证不再静默fallback

---

## 验证策略

每项修复完成后执行：

1. **单元测试**：为每项修复编写针对性测试用例
2. **回归测试**：运行 `pytest tests/unit/api/` 确保无回归
3. **集成测试**：运行 `pytest tests/integration/` 确保端到端流程正常
4. **类型检查**：运行 `mypy app/` 确保类型安全
5. **代码规范**：运行 `flake8 app/` 和 `black --check app/` 确保风格一致

---

## 修复报告模板

每项修复完成后记录：

```
### 修复项 #N: [编号] — [标题]
- 问题原因：[根因分析]
- 解决方案：[具体修改内容]
- 修改文件：[文件列表及行号]
- 测试结果：[通过的测试用例]
- 回归验证：[回归测试结果]
- 遗留问题：[如有]
```
