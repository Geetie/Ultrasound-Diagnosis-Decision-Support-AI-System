# Phase0A API接口轨道 — 12项待修复项系统性修复计划

> 审查基线：2026-05-14 | 修复优先级：P0 > P1 > P2

---

## 修复项总览

| # | 编号 | 优先级 | 修复项 | 涉及文件 | 工作量 |
|---|------|--------|--------|---------|--------|
| 1 | F-01 | P0 | DICOM帧缓存缺失（90帧环形缓冲区） | `dicom.py` | 中 |
| 2 | F-05 | P0 | consent状态语义矛盾 | `session.py` | 小 |
| 3 | Q-03/Q-04 | P0 | dicom_storage同步I/O阻塞+Windows跨驱动器rename | `dicom_storage.py` | 小 |
| 4 | F-02 | P1 | 设备恢复token持久化存储+验证 | `device.py`, `device_repository.py`, 新增迁移 | 中 |
| 5 | F-04 | P1 | FHIR资源引用错误(session_id→patient_id) | `fhir.py` | 小 |
| 6 | Q-02 | P1 | SafetyGatewayClient.check_output重试逻辑拆分 | `safety_gateway_client.py` | 中 |
| 7 | F-03 | P2 | 语音确认后业务执行缺失 | `voice.py` | 中 |
| 8 | F-06 | P2 | Phase0A Mock硬编码替换为推理管线接入点 | `diagnosis.py`, `nav.py` | 大 |
| 9 | S-01 | P2 | 路由前缀不一致(health.py) | `health.py`, `main.py` | 小 |
| 10 | Q-06 | P2 | 生产依赖混入测试包 | `requirements.txt`, `requirements-dev.txt` | 小 |
| 11 | Q-05 | P2 | SessionResponse构造代码重复3次 | `session.py` | 小 |
| 12 | D-12 | P2 | MinIO存储后端未实现 | `dicom_storage.py` | 大 |

---

## 修复项 #1: F-01 — DICOM帧缓存缺失

### 问题描述
03-§1.2规范要求"边缘端缓存最近90帧已上传的DICOM数据"。当前[dicom.py:264-276](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/api/websocket/dicom.py#L264)在接收帧后仅记录日志，未缓存帧数据。当CRC校验失败发送`retransmit_request`时，后端无缓存可提供，重传机制形同虚设。

### 影响范围
- 医疗影像传输完整性无法保障
- CRC校验失败的帧永久丢失
- 重传请求无实际意义

### 修复方案

**步骤1：实现环形帧缓冲区类**

在 `app/api/websocket/dicom.py` 中新增 `FrameBuffer` 类：

```python
from collections import OrderedDict

class FrameBuffer:
    """90帧环形缓冲区，按frame_index存储最近90帧DICOM数据"""

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

**步骤2：在WebSocket连接中集成帧缓存**

修改 `websocket_dicom_endpoint` 函数：
- 在连接初始化时创建 `FrameBuffer` 实例
- 在帧CRC校验通过后（当前L264附近），调用 `frame_buffer.put(frame_index, dicom_data)`
- 在发送`retransmit_request`之前，先检查缓存中是否有该帧数据，若有则直接重发而非请求客户端重传

**步骤3：添加帧缓存配置项**

在 `app/config/settings.py` 中添加：
```python
dicom_frame_buffer_capacity: int = 90
```

### 测试验证
- 新增测试：`test_frame_buffer_put_get` — 验证帧存入和读取
- 新增测试：`test_frame_buffer_eviction` — 验证超过90帧时自动驱逐最旧帧
- 新增测试：`test_frame_buffer_get_range` — 验证范围查询
- 新增测试：`test_dicom_retransmit_from_cache` — 集成测试：CRC失败时从缓存重发

---

## 修复项 #2: F-05 — consent状态语义矛盾

### 问题描述
[consent.py:67-70](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/api/v1/consent.py#L67)对无同意记录抛`PatientNotConsentedException`(403/E1008)，但[session.py:42-46](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/api/v1/session.py#L42)对无同意记录默认`ConsentStatus.DENIED`但仍允许创建session。两个模块对"无同意记录"的处理逻辑矛盾。

### 影响范围
- 可能绕过同意检查创建session
- 诊断流程在无同意记录的情况下仍可运行

### 修复方案

**步骤1：统一session.py中的consent检查逻辑**

修改 `session.py` 的 `create_session` 函数（L42-46），将：
```python
consent_record = await consent_repo.get_latest_consent(request.patient_id)
consent_status = (
    ConsentStatus(consent_record.status)
    if consent_record
    else ConsentStatus.DENIED
)
```
改为：
```python
consent_record = await consent_repo.get_latest_consent(request.patient_id)
if not consent_record:
    raise PatientNotConsentedException(
        error_message=f"患者 {request.patient_id} 未签署知情同意书，无法创建诊断会话",
        detail="consent_not_found",
    )
consent_status = ConsentStatus(consent_record.status)
if consent_status == ConsentStatus.WITHDRAWN:
    raise PatientConsentWithdrawnException(
        error_message=f"患者 {request.patient_id} 已撤回知情同意，无法创建诊断会话",
        detail="consent_withdrawn",
    )
```

**步骤2：添加必要的import**

在 `session.py` 顶部添加：
```python
from app.api.v1.errors import PatientNotConsentedException, PatientConsentWithdrawnException
```

### 测试验证
- 新增测试：`test_create_session_no_consent_raises_403` — 无同意记录时创建session应抛403
- 新增测试：`test_create_session_withdrawn_consent_raises_403` — 已撤回同意时创建session应抛403
- 修改现有测试：确保原"无同意记录创建session"的测试用例更新为期望403

---

## 修复项 #3: Q-03/Q-04 — dicom_storage同步I/O阻塞+Windows跨驱动器

### 问题描述
[dicom_storage.py:64-66](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/services/dicom_storage.py#L64)在async方法中使用同步`open()`和`os.rename()`，会阻塞事件循环。且`os.rename`在Windows跨驱动器场景下会抛`OSError`。

### 影响范围
- 高并发DICOM上传时API响应延迟
- Windows开发环境下上传可能失败

### 修复方案

**步骤1：使用aiofiles替代同步文件I/O**

项目已依赖`aiofiles==23.2.1`（见requirements.txt L11），直接使用：

```python
import aiofiles
import shutil

async def _save_local(self, content, patient_id, session_id, sop_instance_uid):
    storage_path = self._build_storage_path(patient_id, session_id, sop_instance_uid)
    os.makedirs(os.path.dirname(storage_path), exist_ok=True)

    temp_path = storage_path + ".tmp"
    try:
        async with aiofiles.open(temp_path, "wb") as f:
            await f.write(content)
        shutil.move(temp_path, storage_path)
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise
    ...
```

**步骤2：使用shutil.move替代os.rename**

`shutil.move` 可处理跨驱动器移动（先复制后删除），解决Windows兼容性问题。

**步骤3：retrieve和delete方法也改为异步**

```python
async def retrieve(self, storage_path: str) -> Optional[bytes]:
    if self._backend == "local":
        if os.path.exists(storage_path):
            async with aiofiles.open(storage_path, "rb") as f:
                return await f.read()
        return None
    return None
```

**步骤4：修复delete中的os.removedirs问题**

`os.removedirs`会递归删除空父目录，可能意外删除共享日期目录。改为仅删除文件本身：
```python
async def delete(self, storage_path: str) -> bool:
    if self._backend == "local":
        if os.path.exists(storage_path):
            os.remove(storage_path)
            return True
        return False
    return False
```

**步骤5：修复模块级单例创建时机**

将 `dicom_storage = DicomStorageService()` 改为延迟初始化：
```python
_dicom_storage: Optional[DicomStorageService] = None

def get_dicom_storage() -> DicomStorageService:
    global _dicom_storage
    if _dicom_storage is None:
        _dicom_storage = DicomStorageService()
    return _dicom_storage
```

### 测试验证
- 新增测试：`test_dicom_storage_save_local_async` — 验证异步保存
- 新增测试：`test_dicom_storage_cross_drive_move` — 验证shutil.move兼容性
- 新增测试：`test_dicom_storage_delete_no_parent_removal` — 验证删除不清理父目录
- 新增测试：`test_dicom_storage_lazy_init` — 验证延迟初始化

---

## 修复项 #4: F-02 — 设备恢复token持久化存储+验证

### 问题描述
[device.py:126](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/api/v1/device.py#L126)生成的`recovery_token`和[device.py:170](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/api/v1/device.py#L170)生成的`new_api_key`仅返回客户端，未持久化存储，无法后续验证token有效性。

### 影响范围
- 任何人可伪造recovery_token执行恢复操作
- remote_recovery的new_api_key无法被后续认证流程识别

### 修复方案

**步骤1：在DeviceRepository中添加token存储方法**

在 `device_repository.py` 中添加：
```python
async def store_recovery_token(self, device_id: str, token: str, expires_at: datetime) -> None:
    # 使用Redis存储，设置TTL自动过期
    redis_client = await RedisPool.get_client()
    key = f"device:recovery_token:{device_id}"
    await redis_client.setex(key, int((expires_at - datetime.now(timezone.utc)).total_seconds()), token)

async def verify_recovery_token(self, device_id: str, token: str) -> bool:
    redis_client = await RedisPool.get_client()
    key = f"device:recovery_token:{device_id}"
    stored = await redis_client.get(key)
    if stored and stored.decode() == token:
        await redis_client.delete(key)
        return True
    return False

async def store_new_api_key_hash(self, device_id: str, api_key: str) -> None:
    hashed = self._hash_api_key(api_key)
    # 更新数据库中的api_key_hash
    device = await self.get_device(device_id)
    if device:
        device.api_key_hash = hashed
```

**步骤2：修改device.py中的recovery_device**

```python
recovery_token = secrets.token_urlsafe(32)
expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
await device_repo.store_recovery_token(device_id, recovery_token, expires_at)
```

**步骤3：修改device.py中的remote_recovery_device**

```python
new_api_key = secrets.token_urlsafe(32)
await device_repo.store_new_api_key_hash(device_id, new_api_key)
```

**步骤4：添加Redis key到redis_keys.py**

```python
device_recovery_token: str  # device:recovery_token:{device_id}
```

### 测试验证
- 新增测试：`test_recovery_token_stored_in_redis` — 验证token存储
- 新增测试：`test_recovery_token_verification` — 验证token验证流程
- 新增测试：`test_recovery_token_expiry` — 验证token过期自动清理
- 新增测试：`test_remote_recovery_api_key_hashed` — 验证新api_key已哈希存储

---

## 修复项 #5: F-04 — FHIR资源引用错误

### 问题描述
[fhir.py:83](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/api/v1/fhir.py#L83)中`DiagnosticReport`的`subject`引用`session_id`而非`patient_id`，[fhir.py:119](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/api/v1/fhir.py#L119)中`Observation`同样错误。

### 影响范围
- FHIR R4标准合规性失败
- 前端/外部系统无法通过FHIR接口正确关联患者

### 修复方案

**步骤1：修正DiagnosticReport的subject引用**

修改 `get_diagnostic_report` 函数（L79-91），通过session获取patient_id：
```python
session = await db.get(DiagnosisSession, diagnosis.session_id)
patient_id = session.patient_id if session else "unknown"

return {
    "resourceType": "DiagnosticReport",
    "id": id,
    "status": "final" if diagnosis.status == "completed" else "registered",
    "subject": {"reference": f"Patient/{patient_id}"},
    ...
}
```

**步骤2：修正Observation的subject引用**

修改 `list_observations` 函数（L110-123），同样通过session获取patient_id：
```python
session = await db.get(DiagnosisSession, diagnosis.session_id)
patient_id = session.patient_id if session else "unknown"

return {
    ...
    "entry": [{
        "resource": {
            ...
            "subject": {"reference": f"Patient/{patient_id}"},
            ...
        }
    }],
}
```

**步骤3：修正create_imaging_study的输入验证**

将 `request: Request` 替换为Pydantic模型：
```python
class ImagingStudyCreateRequest(BaseModel):
    session_id: str

@router.post("/ImagingStudy")
async def create_imaging_study(
    request: ImagingStudyCreateRequest,
    ...
)
```

### 测试验证
- 新增测试：`test_fhir_diagnostic_report_subject_is_patient` — 验证subject引用patient_id
- 新增测试：`test_fhir_observation_subject_is_patient` — 验证subject引用patient_id
- 新增测试：`test_fhir_imaging_study_uses_pydantic_model` — 验证输入校验

---

## 修复项 #6: Q-02 — SafetyGatewayClient.check_output重试逻辑拆分

### 问题描述
[safety_gateway_client.py:97-218](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/services/safety_gateway_client.py#L97)的`check_output`方法约120行，混合了快速失败判定、指数退避、超时判断、多种异常处理，可读性差，锁竞争风险高。

### 影响范围
- 高并发诊断请求时延迟增加
- 代码维护困难

### 修复方案

**步骤1：提取快速失败检查为独立方法**

```python
def _should_fast_fail(self) -> bool:
    return self._consecutive_failures >= self.FAST_FAIL_THRESHOLD
```

**步骤2：提取单次请求尝试为独立方法**

```python
async def _attempt_check(self, session_id, patient_id, output_data, consent_status_at_input, desensitization_degraded) -> tuple[dict | None, bool, str | None]:
    """单次安全网关检查尝试。返回 (result, should_retry, error_code)"""
    try:
        client = await self._get_client()
        response = await client.post(...)
        if response.status_code == 200:
            return response.json(), False, None
        elif response.status_code == 503:
            ...
            return None, True, sub_code
        else:
            ...
            return None, True, None
    except httpx.TimeoutException:
        return None, True, None
    except (httpx.ConnectError, httpx.RemoteProtocolError):
        return None, True, None
    except Exception:
        return self._e4005_response(), False, None
```

**步骤3：提取失败计数更新为独立方法**

```python
async def _record_failure(self) -> int:
    async with self._lock:
        self._consecutive_failures += 1
        return self._consecutive_failures

async def _record_success(self) -> None:
    async with self._lock:
        self._consecutive_failures = 0
```

**步骤4：简化check_output主方法**

```python
async def check_output(self, session_id, patient_id, output_data, consent_status_at_input, desensitization_degraded=False):
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
- 修改现有测试：确保拆分后所有`test_safety_gateway_handling.py`测试通过
- 新增测试：`test_safety_gateway_fast_fail_check` — 验证快速失败判定
- 新增测试：`test_safety_gateway_retry_backoff` — 验证退避策略

---

## 修复项 #7: F-03 — 语音确认后业务执行缺失

### 问题描述
[voice.py:54-63](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/api/v1/voice.py#L54)中`confirmed=True`时仅设置`action_taken`字符串，未实际执行任何业务逻辑（触发诊断、切换分级、导航控制），`request_id`始终为`None`。

### 影响范围
- 语音指令功能完全不可用
- 前端调用语音确认后无实际效果

### 修复方案

**步骤1：实现diagnosis_trigger命令**

```python
if request.command_type == "diagnosis_trigger":
    from app.api.v1.diagnosis import create_diagnosis_request
    diagnosis_request = DiagnosisRequest(
        session_id=request.session_id,
        patient_id=session.patient_id,
        organ=request.organ,
        idempotency_key=f"voice-{request.session_id}-{request.organ}",
    )
    result = await create_diagnosis_request(diagnosis_request, db=db, current_user=current_user)
    action_taken = "diagnosis_initiated"
    request_id = result.request_id
```

> 注意：直接调用diagnosis路由函数存在耦合问题。更好的方案是提取诊断请求的核心逻辑为service层函数，voice和diagnosis路由共同调用。

**步骤2：实现grade_change命令**

```python
elif request.command_type == "grade_change":
    new_tier = ResponseTier(request.grade_target)
    session_repo.update_session(session.session_id, {"response_tier": new_tier.value})
    action_taken = f"grade_changed_to_{new_tier.value}"
```

**步骤3：实现navigation_control命令**

```python
elif request.command_type == "navigation_control":
    await nav_manager.send_personal_message(
        session_id=request.session_id,
        message={"type": "navigation_control", "action": request.nav_action}
    )
    action_taken = f"navigation_{request.nav_action}"
```

**步骤4：扩展VoiceConfirmRequest schema**

在 `schemas.py` 中为 `VoiceConfirmRequest` 添加可选字段：
```python
organ: Optional[str] = None
grade_target: Optional[str] = None
nav_action: Optional[str] = None
```

### 测试验证
- 新增测试：`test_voice_confirm_diagnosis_trigger` — 验证触发诊断
- 新增测试：`test_voice_confirm_grade_change` — 验证分级切换
- 新增测试：`test_voice_confirm_navigation_control` — 验证导航控制
- 新增测试：`test_voice_confirm_missing_organ_for_diagnosis` — 验证参数校验

---

## 修复项 #8: F-06 — Phase0A Mock硬编码替换为推理管线接入点

### 问题描述
[diagnosis.py:185-187](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/api/v1/diagnosis.py#L185)中`PHASE0A_MOCK_PROCESSING_TIME_MS=2500`、`PHASE0A_MOCK_MODEL_VERSION="thyroid_v1.0.0"`为硬编码值，[nav.py:413](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/api/websocket/nav.py#L413)导航标注也为硬编码。

### 影响范围
- 诊断结果数据不真实
- 无法切换到实际推理管线

### 修复方案

**步骤1：在diagnosis.py中接入推理引擎**

项目已有 `app.inference.factory.create_inference_engine_from_settings` 和 `app.state.inference_engine`。修改诊断结果生成逻辑：

```python
# 替换硬编码Mock
inference_engine = request.app.state.inference_engine
if inference_engine:
    result = await inference_engine.predict(session_id, patient_id, organ, imaging_data)
    model_version = inference_engine.get_model_version()
    processing_time_ms = result.processing_time_ms
else:
    # Phase0A fallback
    model_version = PHASE0A_MOCK_MODEL_VERSION
    processing_time_ms = PHASE0A_MOCK_PROCESSING_TIME_MS
```

**步骤2：在nav.py中接入推理引擎**

将硬编码导航标注替换为推理引擎调用：
```python
inference_engine = app.state.inference_engine
if inference_engine:
    annotation = await inference_engine.get_navigation_annotation(session_id, frame_data)
else:
    # Phase0A fallback: 硬编码导航标注
    annotation = _get_mock_annotation()
```

**步骤3：将Mock常量移至配置**

```python
# settings.py
phase0a_mock_processing_time_ms: int = 2500
phase0a_mock_model_version: str = "thyroid_v1.0.0"
phase0a_mock_kb_version: str = "kb_v1.0.0"
```

### 测试验证
- 修改现有测试：`test_diagnosis.py` 中的Mock常量测试改为从settings读取
- 新增测试：`test_diagnosis_with_inference_engine` — 验证推理引擎接入
- 新增测试：`test_diagnosis_fallback_to_mock` — 验证无引擎时回退到Mock

---

## 修复项 #9: S-01 — 路由前缀不一致

### 问题描述
[health.py:8](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/api/v1/health.py#L8)使用`APIRouter()`无前缀，而其他模块均使用`/api/v1/`前缀。

### 影响范围
- API路径规范不一致
- 前端/监控系统需特殊处理health端点路径

### 修复方案

**步骤1：修改health.py路由前缀**

```python
router = APIRouter(prefix="/api/v1", tags=["health"])
```

**步骤2：更新main.py中的路由挂载**

当前 `main.py` 中 health_router 的挂载路径需要确认。如果当前是 `app.include_router(health_router)`，则修改后路径自动变为 `/api/v1/health`，需确保前端和监控系统的health check URL同步更新。

**步骤3：更新前端/监控配置**

在08-前端交互与使用指南.md中更新health check路径。

### 测试验证
- 修改现有测试：确保health端点路径测试更新为`/api/v1/health`
- 新增测试：`test_health_endpoint_path_consistency` — 验证路径前缀一致

---

## 修复项 #10: Q-06 — 生产依赖混入测试包

### 问题描述
[requirements.txt:15-17](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/requirements.txt#L15)包含`pytest==8.4.2`、`pytest-asyncio==0.24.0`、`freezegun==1.5.1`，这些是测试工具不应出现在生产依赖中。

### 影响范围
- 生产镜像体积增大
- 攻击面扩大
- 不符合依赖管理最佳实践

### 修复方案

**步骤1：从requirements.txt中移除测试依赖**

删除以下行：
```
pytest==8.4.2
pytest-asyncio==0.24.0
freezegun==1.5.1
```

**步骤2：在requirements-dev.txt中补充缺失的测试依赖**

确认 `requirements-dev.txt` 中已包含：
```
pytest==8.4.2
pytest-asyncio==0.24.0
freezegun==1.5.1
```

**步骤3：更新CI/CD配置**

确保CI流水线安装 `requirements-dev.txt` 而非仅 `requirements.txt`。

### 测试验证
- 验证：`pip install -r requirements.txt` 不包含测试包
- 验证：`pip install -r requirements-dev.txt` 包含完整测试工具链
- 运行：`pytest` 确认测试仍可正常执行

---

## 修复项 #11: Q-05 — SessionResponse构造代码重复3次

### 问题描述
[session.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/api/v1/session.py)中`create_session`(L70-82)、`get_session`(L97-109)、`update_session`(L142-154)三个路由的`SessionResponse`构造代码完全相同。

### 影响范围
- 维护成本增加
- 修改易遗漏

### 修复方案

**步骤1：提取辅助函数**

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

**步骤2：替换三处重复代码**

```python
return _build_session_response(session)
```

### 测试验证
- 运行现有session测试：确保重构后功能不变
- 新增测试：`test_session_response_builder` — 验证辅助函数输出正确

---

## 修复项 #12: D-12 — MinIO存储后端未实现

### 问题描述
[dicom_storage.py:82-95](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/services/dicom_storage.py#L82)中`_save_minio`为空壳实现，直接fallback到local。`retrieve`和`delete`对minio后端返回`None`/`False`。

### 影响范围
- 配置为minio后端时数据静默存到本地磁盘
- retrieve/delete在minio模式下不可用

### 修复方案

**步骤1：添加minio依赖**

在 `requirements.txt` 中添加：
```
minio>=7.2.0
```

**步骤2：实现_save_minio**

```python
from minio import Minio

async def _save_minio(self, content, patient_id, session_id, sop_instance_uid):
    object_key = self._build_minio_key(patient_id, session_id, sop_instance_uid)
    client = self._get_minio_client()
    from io import BytesIO
    client.put_object(
        self._minio_bucket,
        object_key,
        BytesIO(content),
        length=len(content),
        content_type="application/dicom",
    )
    logger.info("dicom_file_saved", backend="minio", object_key=object_key, size=len(content))
    return f"minio://{self._minio_bucket}/{object_key}"
```

**步骤3：实现retrieve和delete的minio分支**

```python
async def retrieve(self, storage_path):
    if self._backend == "local":
        ...
    elif self._backend == "minio":
        if storage_path.startswith("minio://"):
            _, _, path = storage_path.partition("minio://")
            bucket, _, key = path.partition("/")
            client = self._get_minio_client()
            response = client.get_object(bucket, key)
            return response.read()
    return None
```

**步骤4：添加MinIO配置项**

在 `settings.py` 中添加：
```python
minio_endpoint: str = "localhost:9000"
minio_access_key: str = ""
minio_secret_key: str = ""
minio_bucket: str = "dicom-storage"
minio_secure: bool = False
```

### 测试验证
- 新增测试：`test_dicom_storage_minio_save` — 验证MinIO保存
- 新增测试：`test_dicom_storage_minio_retrieve` — 验证MinIO读取
- 新增测试：`test_dicom_storage_minio_delete` — 验证MinIO删除
- 新增测试：`test_dicom_storage_minio_fallback_disabled` — 验证不再静默fallback

---

## 实施顺序与依赖关系

```
Phase 1 (P0 — 必须立即修复):
  #2 F-05 consent语义统一 ← 无依赖，可立即开始
  #3 Q-03/Q-04 dicom_storage异步化 ← 无依赖，可立即开始
  #1 F-01 DICOM帧缓存 ← 依赖#3完成后测试更可靠

Phase 2 (P1 — 本迭代内修复):
  #5 F-04 FHIR引用修正 ← 无依赖，可与Phase 1并行
  #4 F-02 设备token持久化 ← 需新增Redis key，可与#5并行
  #6 Q-02 SafetyGateway拆分 ← 无依赖，可独立进行

Phase 3 (P2 — 可排期修复):
  #11 Q-05 SessionResponse去重 ← 无依赖，小改动
  #10 Q-06 依赖分离 ← 无依赖，小改动
  #9 S-01 路由前缀统一 ← 无依赖，小改动
  #7 F-03 语音确认业务执行 ← 依赖#2(consent语义统一)
  #8 F-06 Mock替换 ← 依赖推理管线轨道进度
  #12 D-12 MinIO实现 ← 工作量大，可推迟至Phase0B
```

## 验证策略

每项修复完成后执行：

1. **单元测试**：为每项修复编写针对性测试用例
2. **回归测试**：运行 `pytest tests/unit/api/` 确保无回归
3. **集成测试**：运行 `pytest tests/integration/` 确保端到端流程正常
4. **类型检查**：运行 `mypy app/` 确保类型安全
5. **代码规范**：运行 `flake8 app/` 和 `black --check app/` 确保风格一致
