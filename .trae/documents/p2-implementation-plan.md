# P2 中等优先级问题修复实施计划

## 修复总览

共 24 项 P2 问题（P2-19 已在 P0-4 中修复），按模块分 6 个阶段实施。
每个步骤包含：修改文件、具体代码变更、验证方式。

---

## 阶段一：HSM 模块修复（P2-1, P2-2, P2-3）

### 步骤 1.1：P2-1 — sign_jwt() algorithm 参数一致性校验

**文件**: `app/security/hsm.py`
**位置**: `sign_jwt()` 方法（第634行）

**变更**:
在 `sign_jwt()` 方法体开头（SoftHSM 过期检查之前）添加 algorithm 参数校验：
```python
def sign_jwt(self, key_name: str, header: Dict[str, Any], payload: Dict[str, Any], algorithm: str = "RS256") -> str:
    import base64
    import json as _json

    if algorithm != "RS256":
        logger.warning("hsm_jwt_algorithm_not_supported_forcing_rs256",
                       requested_algorithm=algorithm, forced_algorithm="RS256")
        algorithm = "RS256"
    # ... 后续代码不变
```

**验证**: 传入 `algorithm="RS384"` 时日志记录 warning 且签名仍用 RS256

### 步骤 1.2：P2-2 — _safe_run_async_task() 无事件循环时数据丢失

**文件**: `app/security/hsm.py`
**位置**: `_safe_run_async_task()` 方法（第171行）

**变更**:
1. 在 `__init__` 中添加 `_pending_persistence: list` 属性
2. 修改 `_safe_run_async_task` 中 `except RuntimeError` 分支：将协程的待持久化数据写入 `_pending_persistence` 队列而非直接 `coro.close()`
3. 添加 `_flush_pending_persistence()` 方法，在事件循环可用时恢复执行

```python
# __init__ 中添加:
self._pending_persistence: list = []

# _safe_run_async_task 中修改 except RuntimeError 分支:
except RuntimeError:
    if not is_test:
        logger.error("no_event_loop_for_redis_persistence", action="queued_for_later")
        self._pending_persistence.append(coro)
    else:
        coro.close()
    return None

# 新增方法:
def _flush_pending_persistence(self) -> None:
    if not self._pending_persistence:
        return
    pending = list(self._pending_persistence)
    self._pending_persistence.clear()
    for coro in pending:
        self._safe_run_async_task(coro)
    logger.info("hsm_pending_persistence_flushed", count=len(pending))
```

4. 在 `_restore_degraded_state_from_redis()` 和 `_restore_keys_from_redis()` 末尾调用 `self._flush_pending_persistence()`

**验证**: 模拟无事件循环场景，验证降级状态不丢失

### 步骤 1.3：P2-3 — SoftHSM 过期检查 .days 截断

**文件**: `app/security/hsm.py`
**位置**: `get_soft_hsm_status()` 方法（第267行）

**变更**:
修改 `get_soft_hsm_status()` 中的过期判断逻辑：
```python
# 旧代码:
days_running = (now - self._soft_hsm_enabled_at).days
is_expired = days_running > self.SOFT_HSM_EXPIRY_DAYS

# 新代码:
time_since_enabled = now - self._soft_hsm_enabled_at
is_expired = time_since_enabled > timedelta(days=self.SOFT_HSM_EXPIRY_DAYS)
days_running = time_since_enabled.days
```

注意：`_check_soft_hsm_expiry()` 已经使用 timedelta 比较了（第217-221行），无需修改。

**验证**: SoftHSM 运行 89天23小时时 `is_expired` 为 False（正确），90天0小时时为 True

---

## 阶段二：应急签发模块修复（P2-4, P2-5, P2-6, P2-7）

### 步骤 2.1：P2-4 — 应急签发启用增加恢复确认参数

**文件**: `app/security/emergency_signing.py`
**位置**: `enable_emergency_signing()` 方法（第55行）

**变更**:
1. 添加 `recovery_confirmed: bool = False` 参数
2. 在方法体中添加审计日志记录 `recovery_confirmed` 状态
3. 当 `recovery_confirmed=False` 时记录 warning 日志

```python
async def enable_emergency_signing(
    self, operator_id: str, approver_id: str, reason: str,
    recovery_confirmed: bool = False,
) -> None:
    # ... 现有校验代码不变 ...

    if not recovery_confirmed:
        logger.warning("emergency_signing_enabled_without_recovery_confirmation",
                       operator_id=operator_id, approver_id=approver_id)

    # ... Redis 持久化代码不变 ...

    # 审计日志中添加 recovery_confirmed 字段:
    await audit_svc.log_action(
        action="EMERGENCY_SIGNING_ENABLED",
        actor=operator_id,
        target_type="hsm",
        target_id="emergency_signing",
        detail={
            "approver": approver_id,
            "reason": reason,
            "degraded_phase": phase.value,
            "recovery_confirmed": recovery_confirmed,
        },
    )
```

**验证**: `recovery_confirmed=False` 时审计日志标记"未确认恢复可能性"

### 步骤 2.2：P2-5 — 应急密钥自动生成添加审计日志

**文件**: `app/security/emergency_signing.py`
**位置**: `enable_emergency_signing()` 第80行、`sign_emergency()` 第164行、`sign_jwt_emergency()` 第201行

**变更**:
在三处 `hsm_manager.generate_key_pair()` 调用后，添加审计日志记录：

```python
# enable_emergency_signing 中:
if not key_info:
    hsm_manager.generate_key_pair(_EMERGENCY_SIGNING_KEY_NAME, slot=KeySlot.EMERGENCY_SIGNING)
    try:
        from app.services.audit_service import AuditService
        audit_svc = AuditService()
        await audit_svc.log_action(
            action="EMERGENCY_KEY_AUTO_GENERATED",
            actor="system",
            target_type="hsm",
            target_id=_EMERGENCY_SIGNING_KEY_NAME,
            detail={"source": "enable_emergency_signing", "slot": KeySlot.EMERGENCY_SIGNING.value},
        )
    except Exception as e:
        logger.warning("failed_to_audit_emergency_key_generation", error=str(e))

# sign_emergency 中:
if not emergency_key_info:
    hsm_manager.generate_key_pair(_EMERGENCY_SIGNING_KEY_NAME, slot=KeySlot.EMERGENCY_SIGNING)
    logger.critical("emergency_key_auto_generated",
                    source="sign_emergency", key_name=_EMERGENCY_SIGNING_KEY_NAME)

# sign_jwt_emergency 中:
if not emergency_key_info:
    hsm_manager.generate_key_pair(_EMERGENCY_SIGNING_KEY_NAME, slot=KeySlot.EMERGENCY_SIGNING)
    logger.critical("emergency_key_auto_generated",
                    source="sign_jwt_emergency", key_name=_EMERGENCY_SIGNING_KEY_NAME)
```

**验证**: 密钥自动生成后审计日志中出现 CRITICAL 级别记录

### 步骤 2.3：P2-6 — verify_emergency_signature() 返回类型修复

**文件**: `app/security/emergency_signing.py`
**位置**: `verify_emergency_signature()` 方法（第232行）

**变更**:
外层 `except Exception` 块中 `return None` 已在之前修复中改为 `return False`（第295行），无需再修改。
但需确认当前代码状态——根据读取结果，第295行已经是 `return False`，此问题已修复。

**验证**: 确认异常场景返回 False 而非 None

### 步骤 2.4：P2-7 — 应急签发状态 TTL 改为从 HSM 恢复时计时

**文件**: `app/security/emergency_signing.py`
**位置**: `enable_emergency_signing()` 和 `disable_emergency_signing()`

**变更**:
1. 在 `enable_emergency_signing()` 中设置较长的初始 TTL（72 小时）作为安全上限
2. 在 `disable_emergency_signing()` 中重新设置验证窗口 TTL

```python
# enable_emergency_signing 中修改 TTL:
# 旧: ttl_seconds = settings.hsm_emergency_signing_verification_hours * 3600
# 新:
initial_ttl_seconds = 72 * 3600  # 72小时安全上限
await pipe.set(RedisKey.hsm_emergency_signing("enabled"), "true", ex=initial_ttl_seconds)
# ... 其他 key 也用 initial_ttl_seconds

# disable_emergency_signing 中添加验证窗口重置:
async def disable_emergency_signing(self, operator_id: str) -> None:
    # 先设置验证窗口 TTL（从 HSM 恢复时开始计时）
    try:
        from app.config.redis import RedisPool
        from app.config.redis_keys import RedisKey
        redis_client = await RedisPool.get_client()
        verification_ttl = settings.hsm_emergency_signing_verification_hours * 3600
        # 仅对 enabled key 重设 TTL 作为验证窗口
        await redis_client.expire(RedisKey.hsm_emergency_signing("enabled"), verification_ttl)
        # 其他 key 保持原样（验证窗口内仍可查询操作记录）
    except Exception as e:
        logger.error("failed_to_set_verification_window_ttl", error=str(e))

    # 然后删除其他状态 key（延迟到验证窗口结束后自动过期）
    # ... 原有删除逻辑改为仅删除非 enabled key
```

**验证**: HSM 恢复后禁用应急签发时，验证窗口 TTL 被重置

---

## 阶段三：脱敏还原模块修复（P2-8, P2-9, P2-10）

### 步骤 3.1：P2-8 — re_encrypt_with_new_key 事务性保证

**文件**: `app/security/desensitization_restore.py`
**位置**: `re_encrypt_with_new_key()` 方法（第312行）

**变更**:
1. 添加 `resume_from_id: Optional[str] = None` 参数
2. 添加 `ReEncryptionError` 异常类
3. 在每批处理前记录 `last_processed_id`，失败时抛出 `ReEncryptionError`

```python
class ReEncryptionError(Exception):
    def __init__(self, message: str, processed_count: int, resume_from_id: str):
        super().__init__(message)
        self.processed_count = processed_count
        self.resume_from_id = resume_from_id

async def re_encrypt_with_new_key(self, new_key_version: int, resume_from_id: Optional[str] = None) -> int:
    try:
        from app.config.database import get_session
        from app.models.desensitization_mapping import DesensitizationMapping
        from sqlalchemy import select

        total_re_encrypted = 0
        failed_count = 0
        batch_size = 1000
        last_processed_id = resume_from_id

        async for db_session in get_session():
            query = select(DesensitizationMapping).where(
                DesensitizationMapping.key_version < new_key_version
            )
            if last_processed_id:
                query = query.where(DesensitizationMapping.id > last_processed_id)
            query = query.order_by(DesensitizationMapping.id).limit(batch_size)

            result = await db_session.execute(query)
            mappings = result.scalars().all()

            while mappings:
                for mapping in mappings:
                    try:
                        original_value = await self._decrypt_value(
                            mapping.encrypted_original, mapping.iv, mapping.key_version,
                        )
                        new_encrypted, new_iv, actual_version = await self._encrypt_value(original_value)
                        mapping.encrypted_original = new_encrypted
                        mapping.iv = new_iv
                        mapping.key_version = actual_version
                        total_re_encrypted += 1
                        last_processed_id = mapping.id
                    except Exception as e:
                        logger.error("re_encrypt_failed_for_record",
                                    record_id=mapping.id, error=str(e))
                        failed_count += 1

                await db_session.commit()

                if failed_count > total_re_encrypted * 0.1:
                    raise ReEncryptionError(
                        f"Too many failures: {failed_count}/{total_re_encrypted}",
                        processed_count=total_re_encrypted,
                        resume_from_id=last_processed_id,
                    )

                query = select(DesensitizationMapping).where(
                    DesensitizationMapping.key_version < new_key_version
                )
                if last_processed_id:
                    query = query.where(DesensitizationMapping.id > last_processed_id)
                query = query.order_by(DesensitizationMapping.id).limit(batch_size)

                result = await db_session.execute(query)
                mappings = result.scalars().all()

        logger.info("desensitization_re_encrypt_completed",
                   total_re_encrypted=total_re_encrypted,
                   failed_count=failed_count,
                   new_key_version=new_key_version)
        return total_re_encrypted

    except ReEncryptionError:
        raise
    except Exception as e:
        logger.error("failed_to_re_encrypt_mappings", error=str(e))
        raise ReEncryptionError(str(e), 0, resume_from_id or "")
```

**验证**: 模拟中途失败，验证可从断点恢复

### 步骤 3.2：P2-9 — 内存级速率限制改为 asyncio.Lock

**文件**: `app/security/desensitization_restore.py`
**位置**: `_check_rate_limit()` 方法（第152行）

**变更**:
1. 将 `threading.Lock` 改为 `asyncio.Lock`
2. 将 `_check_rate_limit()` 改为 `async` 方法
3. 内存限速作为快速拒绝的第一道防线，Redis 限速为权威
4. Redis 限速拒绝时回滚内存计数

```python
# __init__ 中修改:
self._async_lock = asyncio.Lock()
# 保留 self._lock = threading.Lock() 用于非 async 方法

# _check_rate_limit 改为 async:
async def _check_rate_limit(self) -> bool:
    from app.config.settings import settings
    limit = settings.desensitization_restore_rate_limit
    now = datetime.now(timezone.utc)

    async with self._async_lock:
        if self._rate_limit_window_start is None or \
           (now - self._rate_limit_window_start) > timedelta(hours=1):
            self._rate_limit_counter = {}
            self._rate_limit_window_start = now

        hour_key = now.strftime("%Y%m%d%H")
        current_count = self._rate_limit_counter.get(hour_key, 0)
        if current_count >= limit:
            return False
        self._rate_limit_counter[hour_key] = current_count + 1

    return True

# _check_rate_limit_persistent 修改:
async def _check_rate_limit_persistent(self) -> bool:
    from app.config.settings import settings
    limit = settings.desensitization_restore_rate_limit
    now = datetime.now(timezone.utc)
    hour_key = now.strftime("%Y%m%d%H")
    redis_key = RedisKey.desens_restore_rate(hour_key)

    if not await self._check_rate_limit():
        return False

    try:
        from app.config.redis import RedisPool
        redis_client = await RedisPool.get_client()
        incr_expire_script = """..."""  # 不变
        count = await redis_client.eval(incr_expire_script, 1, redis_key, 7200)
        if count > limit:
            # 回滚内存计数
            async with self._async_lock:
                self._rate_limit_counter[hour_key] = max(
                    0, self._rate_limit_counter.get(hour_key, 1) - 1
                )
            logger.warning("desensitization_restore_rate_limit_exceeded_persistent",
                          count=count, limit=limit)
            return False
    except Exception as e:
        logger.warning("failed_to_check_persistent_rate_limit", error=str(e))

    return True
```

**验证**: 并发还原请求正确限速

### 步骤 3.3：P2-10 — batch_restore 性能优化

**文件**: `app/security/desensitization_restore.py`
**位置**: `batch_restore()` 方法（第300行）

**变更**:
1. 修改返回类型为 `Dict[str, Optional[str]]`
2. 批量操作仅做一次限速检查
3. 使用 `asyncio.gather` 并发恢复（限制并发数为 5）
4. 批量查询数据库获取所有 mapping 记录

```python
async def batch_restore(self, record_ids: List[str]) -> Dict[str, Optional[str]]:
    if not record_ids:
        return {}

    if not await self._check_rate_limit_persistent():
        raise RuntimeError("Desensitization restore rate limit exceeded")

    from app.config.database import get_session
    from app.models.desensitization_mapping import DesensitizationMapping
    from sqlalchemy import select

    mappings_by_id: Dict[str, DesensitizationMapping] = {}
    async for db_session in get_session():
        result = await db_session.execute(
            select(DesensitizationMapping).where(DesensitizationMapping.id.in_(record_ids))
        )
        for mapping in result.scalars().all():
            mappings_by_id[mapping.id] = mapping
        break

    semaphore = asyncio.Semaphore(5)

    async def _restore_one(record_id: str) -> tuple[str, Optional[str]]:
        mapping = mappings_by_id.get(record_id)
        if not mapping:
            return record_id, None
        async with semaphore:
            try:
                value = await self._decrypt_value(
                    mapping.encrypted_original, mapping.iv, mapping.key_version,
                )
                return record_id, value
            except Exception as e:
                logger.error("batch_restore_failed_for_record",
                            record_id=record_id, error=str(e))
                return record_id, None

    tasks = [_restore_one(rid) for rid in record_ids]
    results_list = await asyncio.gather(*tasks)

    results = dict(results_list)

    try:
        from app.services.audit_service import AuditService
        audit_svc = AuditService()
        await audit_svc.log_action(
            action="DESENSITIZATION_BATCH_RESTORED",
            actor="system",
            target_type="desensitization_mapping",
            target_id="batch",
            detail={"count": len(record_ids), "success_count": sum(1 for v in results.values() if v is not None)},
        )
    except Exception as e:
        logger.warning("failed_to_audit_batch_restore", error=str(e))

    return results
```

**验证**: 100 条批量恢复性能显著提升

---

## 阶段四：三层脱敏模块修复（P2-11, P2-12, P2-13, P2-14）

### 步骤 4.1：P2-11 — pii_leakage_detected 语义修正

**文件**: `app/security/three_layer_desensitization.py`
**位置**: `ThreeLayerDesensitizationResult` 类（第25行）和 `process_full_pipeline` 方法（第166行）

**变更**:
1. 在 `ThreeLayerDesensitizationResult` 中将 `pii_leakage_detected` 重命名为 `security_check_failed`，添加 `degraded_mode` 字段
2. 更新 `process_full_pipeline` 中的赋值逻辑

```python
class ThreeLayerDesensitizationResult:
    def __init__(self):
        self.layer_results: dict[DesensitizationLayer, dict[str, Any]] = {}
        self.final_data: dict[str, Any] | None = None
        self.all_passed: bool = True
        self.security_check_failed: bool = False
        self.degraded_mode: bool = False

    # 保留向后兼容属性
    @property
    def pii_leakage_detected(self) -> bool:
        return self.security_check_failed
```

在 `process_full_pipeline` 末尾：
```python
has_security_violation = False
has_degradation = False
for layer_type, layer_result in result.layer_results.items():
    check_result = layer_result.get("check_result", {})
    passed = check_result.get("passed", True)
    degraded = check_result.get("degraded_mode", False)
    if not passed:
        has_security_violation = True
    if degraded:
        has_degradation = True

result.all_passed = not has_security_violation and not has_degradation
result.security_check_failed = has_security_violation or has_degradation
result.degraded_mode = has_degradation
```

**验证**: 降级模式下 `security_check_failed=True, degraded_mode=True`；校验失败时 `security_check_failed=True, degraded_mode=False`

### 步骤 4.2：P2-12 — 统一 DesensitizationRule 类型

**文件**:
- `app/common/desensitization/rule.py` — 添加 `is_patch` 字段
- `app/security/desensitization.py` — 改为从 common 导入
- `safety_gateway/desensitization_rules.py` — 改为从 common 导入

**变更**:

1. `app/common/desensitization/rule.py` 添加 `is_patch` 字段：
```python
class DesensitizationRule(BaseModel):
    name: str
    pattern: str
    replacement: str
    description: str | None = None
    enabled: bool = True
    priority: int = 10
    is_patch: bool = False
```

2. `app/security/desensitization.py` 删除本地 `DesensitizationRule` 定义，改为导入：
```python
# 删除:
# class DesensitizationRule(BaseModel):
#     name: str
#     pattern: str
#     replacement: str
#     description: Optional[str] = None
#     enabled: bool = True
#     priority: int = 10

# 添加:
from app.common.desensitization.rule import DesensitizationRule
```

3. `safety_gateway/desensitization_rules.py` 删除本地 `DesensitizationRule` 定义，改为导入：
```python
# 删除本地 DesensitizationRule 类定义
# 添加:
from app.common.desensitization.rule import DesensitizationRule
```

**验证**: 三处使用同一 `DesensitizationRule` 类型

### 步骤 4.3：P2-13 — 标记死代码 base.py 和 recursive.py

**文件**:
- `app/common/desensitization/base.py`
- `app/common/desensitization/recursive.py`
- `app/common/desensitization/__init__.py`

**变更**:
1. 在 `base.py` 文件顶部添加弃用标记：
```python
# DEPRECATED: This module is not used by any engine. See individual engine implementations.
```

2. 在 `__init__.py` 中标记 `BaseDesensitizationEngine` 为 deprecated：
```python
# 在导出列表中添加注释标记
__all__ = [
    "DesensitizationRule",
    "BaseDesensitizationEngine",  # DEPRECATED: not used by any engine
    "desensitize_dict",
    "compile_patterns",
    "validate_rules",
    "MAX_RECURSION_DEPTH",
]
```

**验证**: 标记清晰，不影响现有功能

### 步骤 4.4：P2-14 — 融合层引擎独立性注释

**文件**: `app/fusion/desensitization.py`
**位置**: 文件顶部模块注释

**变更**:
在文件顶部模块注释中添加设计决策说明：
```python
"""
融合层脱敏模块 - 三层脱敏的第二层
提供LLM输出后的额外脱敏校验，防止推理过程中意外生成的身份信息

设计决策：融合层复用标准化层引擎是有意的"纵深防御"策略。
- 第一遍：标准化层规则捕获已知模式（身份证、手机号等）
- 第二遍：融合层特有模式捕获 LLM 生成场景（模拟患者、上下文泄露等）
两层使用不同规则集，复用引擎框架而非规则集，确保异构冗余。
"""
```

**验证**: 注释清晰说明设计意图

---

## 阶段五：同意模块修复（P2-15, P2-16, P2-17, P2-18, P2-20）

### 步骤 5.1：P2-15 — ConsentStatus 枚举统一

**文件**:
- `safety_gateway/validation_result.py` — 添加一致性约束注释
- `app/models/schemas.py` — 添加一致性约束注释

**变更**:
1. 在两处 `ConsentStatus` 定义上方添加一致性约束注释：
```python
# CONSISTENCY: Must match the ConsentStatus enum in the other module exactly.
# - safety_gateway/validation_result.py (SG independent process)
# - app/models/schemas.py (main application)
# Any change to either must be reflected in the other.
```

2. 添加跨模块一致性测试（在现有测试文件中添加）

**验证**: 一致性测试通过

### 步骤 5.2：P2-16 — data_retention 门诊/住院归档区分

**文件**: `app/services/data_retention.py` 和 `app/models/database.py`

**变更**:
1. 在 `SessionArchive` 模型中添加 `patient_type` 字段：
```python
patient_type = Column(String(16), nullable=False, default='outpatient')
```

2. 在 `archive_expired_sessions` 中设置 `patient_type`：
```python
archive = SessionArchive(
    session_id=session.session_id,
    patient_id=session.patient_id,
    device_id=session.device_id,
    status=session.status,
    target_organs=getattr(session, 'target_organs', []),
    organ_statuses=getattr(session, 'organ_statuses', {}),
    knowledge_base_version=getattr(session, 'knowledge_base_version', 'unknown'),
    consent_status_at_creation=getattr(session, 'consent_status_at_creation', 'UNKNOWN'),
    patient_type=getattr(session, 'patient_type', 'outpatient'),
    archive_timestamp=datetime.now(timezone.utc),
    created_at=session.created_at,
)
```

3. 修改 `purge_expired_archives` 查询条件，按 `patient_type` 过滤：
```python
async def purge_expired_archives(self, patient_type: str = "outpatient") -> int:
    if patient_type == "inpatient":
        retention_years = settings.data_retention_inpatient_years
    else:
        retention_years = settings.data_retention_outpatient_years

    expiry_date = datetime.now(timezone.utc) - timedelta(days=int(retention_years * 365.25))

    result = await self.db.execute(
        delete(SessionArchive).where(
            and_(
                SessionArchive.archived_at < expiry_date,
                SessionArchive.patient_type == patient_type,
            )
        )
    )
    # ... 后续不变
```

4. 修改 `verify_retention_compliance` 分别查询门诊/住院归档：
```python
# 门诊过期检查
outpatient_expiry = datetime.now(timezone.utc) - timedelta(days=int(settings.data_retention_outpatient_years * 365.25))
overdue_outpatient = await self.db.execute(
    select(func.count()).select_from(SessionArchive).where(
        and_(
            SessionArchive.archived_at < outpatient_expiry,
            SessionArchive.patient_type == "outpatient",
        )
    )
)

# 住院过期检查
inpatient_expiry = datetime.now(timezone.utc) - timedelta(days=int(settings.data_retention_inpatient_years * 365.25))
overdue_inpatient = await self.db.execute(
    select(func.count()).select_from(SessionArchive).where(
        and_(
            SessionArchive.archived_at < inpatient_expiry,
            SessionArchive.patient_type == "inpatient",
        )
    )
)
```

**验证**: 门诊归档 15 年后清除，住院归档 30 年后清除

### 步骤 5.3：P2-17 — data_retention 闰年修正

**文件**: `app/services/data_retention.py`

**变更**:
将所有 `timedelta(days=retention_years * 365)` 改为 `timedelta(days=int(retention_years * 365.25))`：
- 第81行 `purge_expired_archives`
- 第101行 `purge_expired_operation_logs`
- 第166行 `verify_retention_compliance` 中的 outpatient_expiry
- 第178行 `verify_retention_compliance` 中的 inpatient_expiry
- 第151行 `verify_retention_compliance` 中的 archive_threshold（如果涉及年计算）

**验证**: 30 年保留期计算结果为 10957 天

### 步骤 5.4：P2-18 — consent_repository 访问私有属性

**文件**: `app/repositories/consent_repository.py`
**位置**: `record_hsm_degradation_if_needed()` 函数（第23行）

**变更**:
将 `hsm_manager._degraded_at` 替换为 `hsm_manager.get_degraded_at()`：

```python
# 旧代码:
degraded_at = hsm_manager._degraded_at

# 新代码:
degraded_at = hsm_manager.get_degraded_at()
```

注意：`HSMManager` 已有 `get_degraded_at()` 公共方法（hsm.py 第804行），无需新增。

**验证**: 不再访问任何 `_` 前缀属性

### 步骤 5.5：P2-20 — scan_iter 替换为分页 scan

**文件**: `app/security/consent_withdrawal_consumer.py`
**位置**: `SafetyActionHandler.handle_withdrawal()` 第97行和 `DataCleanupHandler.handle_withdrawal()` 第156行

**变更**:
将 `async for key in redis_client.scan_iter(pattern)` 替换为手动分页 `scan` 调用：

```python
# 旧代码:
async for key in redis_client.scan_iter(RedisKey.edge_session_pattern(event.patient_id)):
    session_id = key.decode().split(":")[-1]
    await redis_client.publish(
        RedisKey.edge_stop_ai_annotation(session_id),
        event.patient_id,
    )

# 新代码:
cursor = 0
while True:
    cursor, keys = await redis_client.scan(
        cursor=cursor, match=RedisKey.edge_session_pattern(event.patient_id), count=100
    )
    for key in keys:
        key_str = key.decode() if isinstance(key, bytes) else key
        session_id = key_str.split(":")[-1]
        await redis_client.publish(
            RedisKey.edge_stop_ai_annotation(session_id),
            event.patient_id,
        )
    if cursor == 0:
        break
    await asyncio.sleep(0)
```

同样修改 `DataCleanupHandler` 中的 `scan_iter` 调用。

**验证**: 大量 key 场景下不阻塞事件循环

---

## 阶段六：SafetyGateway 模块修复（P2-21, P2-22, P2-23, P2-24, P2-25）

### 步骤 6.1：P2-21 — P99 < 5ms 硬约束执行

**文件**: `safety_gateway/main.py`
**位置**: `perform_safety_check()` 函数（第291行）

**变更**:
在 `perform_safety_check` 入口添加限流检查：

```python
@app.post("/api/v1/safety/check", response_model=SafetyCheckResponse)
@sg_endpoint_handler("safety_check")
async def perform_safety_check(request: SafetyCheckRequest):
    from safety_gateway.health.memory_monitor import MemoryMonitor
    from safety_gateway.sg_config import sg_settings as _settings
    from safety_gateway.checker import _get_health_checker

    # 新增：P99 限流检查
    health_checker = _get_health_checker()
    if health_checker._latency_tracker.is_throttling:
        raise ServiceUnavailableError(
            error_message="安全网关P99延迟持续超限，拒绝新请求（E4005）",
            detail="sg_p99_throttling",
        )

    # ... 后续代码不变
```

**验证**: P99 连续 3 次超过 5ms 后新请求被拒绝

### 步骤 6.2：P2-22 — CheckPhase 基类 check() 协议修复

**文件**: `safety_gateway/checker/phase_base.py`

**变更**:
将 `check()` 从抽象方法改为模板方法，从 `CheckContext` 提取参数委托给 `check_with_params()`：

```python
class CheckPhase:
    async def check(self, context: CheckContext) -> Any | None:
        return await self.check_with_params(context)

    async def check_with_params(self, context: CheckContext) -> Any | None:
        raise NotImplementedError
```

然后修改各子类的 `check()` 方法：
- `ConsistencyPhase.check()` — 删除 `raise NotImplementedError`，改为调用 `check_with_params(context)`
- 其他 Phase 类似处理

注意：由于编排器 `orchestrator.py` 直接调用 `check_with_params()` 而非 `check()`，此修改仅确保基类协议正确，不影响运行时行为。

**验证**: 编排器可通过统一 `check(context)` 接口调用所有阶段

### 步骤 6.3：P2-23 — ConsistencyPhase EVIDENCE_INSUFFICIENT 死代码清理

**文件**: `safety_gateway/checker/consistency_phase.py`
**位置**: `check_with_params()` 第66-76行

**变更**:
将 `EVIDENCE_INSUFFICIENT` 的处理从 `CheckResult.failed()` 改为 `CheckResult.degraded()`：

```python
if consistency_level == ConsistencyLevel.EVIDENCE_INSUFFICIENT:
    logger.warning(
        "sg_consistency_evidence_insufficient",
        patient_id=patient_id,
    )
    return CheckResult.degraded(
        consent_status=context.consent_status,
        message=f"依据不足，无法验证一致性，需人工复核：{consistency_result['message']}",
        degraded_reason=DegradedReason.CONSISTENCY_PARTIAL,
        elapsed_ms=elapsed_ms,
        consent_check_timeout=context.is_timeout,
        requires_manual_review=True,
    )
```

**验证**: 依据不足时返回降级结果而非拒绝

### 步骤 6.4：P2-24 — main.py 降级输出 fusion_available 修复

**文件**: `safety_gateway/main.py`
**位置**: `_perform_safety_check_inner()` 第361行

**变更**:
将 `fusion_available=False` 改为使用实际融合层状态：

```python
# 旧代码:
response.degraded_output = checker.apply_degraded_output(request.output_data, reason=reason, fusion_available=False)

# 新代码:
response.degraded_output = checker.apply_degraded_output(
    request.output_data, reason=reason,
    fusion_available=check_result.consent_status_source != "unavailable"
    if check_result.degraded_reason and check_result.degraded_reason in (
        DegradedReason.FUSION_UNAVAILABLE,
        DegradedReason.FUSION_UNAVAILABLE_SENSITIVE_DETECTED,
        DegradedReason.DUAL_DEGRADATION_FUSION_AND_SG_UNAVAILABLE,
    ) else True,
)
```

更简洁的实现：从 check_result 中获取融合层可用性。由于 `perform_full_check` 已经查询了融合层状态并写入 context，可以在 `SafetyCheckResponse` 中添加 `fusion_available` 字段，或直接使用请求中的信息：

```python
# 最简实现：使用请求中已有的 edge_connected 和 desensitization_degraded 信息
# 融合层不可用时 degraded_reason 会包含 FUSION_UNAVAILABLE 相关值
fusion_actually_available = not (
    check_result.degraded_reason in (
        DegradedReason.FUSION_UNAVAILABLE,
        DegradedReason.FUSION_UNAVAILABLE_SENSITIVE_DETECTED,
        DegradedReason.DUAL_DEGRADATION_FUSION_AND_SG_UNAVAILABLE,
    )
)
response.degraded_output = checker.apply_degraded_output(
    request.output_data, reason=reason, fusion_available=fusion_actually_available
)
```

**验证**: 融合层可用时降级输出包含更多字段

### 步骤 6.5：P2-25 — LatencyTracker 状态修改加锁

**文件**: `safety_gateway/health/latency_tracker.py`
**位置**: `compute_stats()` 方法（第33行）

**变更**:
将 `_critical_count` 和 `_throttling` 的修改移入 `async with self._lock` 块内：

```python
async def compute_stats(self) -> dict[str, Any]:
    async with self._lock:
        samples = list(self._samples)

        if not samples:
            return { ... }

        sorted_samples = sorted(samples)
        n = len(sorted_samples)

        p99_value = self._percentile(sorted_samples, 99)
        p99_status = self._get_p99_status(
            p99_value,
            settings.sg_p99_latency_warning_threshold_ms,
            settings.sg_p99_latency_critical_threshold_ms,
        )

        if p99_status == "critical":
            self._critical_count += 1
            if self._critical_count >= 3 and not self._throttling:
                self._throttling = True
                logger.critical(...)
            else:
                logger.warning(...)
        elif p99_status == "ok":
            if self._throttling:
                self._throttling = False
                logger.info("sg_p99_latency_recovered_throttling_disabled")
            self._critical_count = 0
        else:
            logger.warning(...)

        return {
            "samples": n,
            "p50_ms": round(self._percentile(sorted_samples, 50), 2),
            "p95_ms": round(self._percentile(sorted_samples, 95), 2),
            "p99_ms": round(p99_value, 2),
            "avg_ms": round(sum(sorted_samples) / n, 2),
            "max_ms": round(max(sorted_samples), 2),
            "min_ms": round(min(sorted_samples), 2),
            "p99_status": p99_status,
            "p99_threshold_warning_ms": settings.sg_p99_latency_warning_threshold_ms,
            "p99_threshold_critical_ms": settings.sg_p99_latency_critical_threshold_ms,
        }
```

关键变更：将原来在锁外执行的 `_critical_count` 和 `_throttling` 修改移到 `async with self._lock` 块内。原来 `samples = list(self._samples)` 后就释放了锁，状态修改在锁外，存在竞态条件。

**验证**: 并发调用 `compute_stats()` 不产生竞态条件

---

## 实施顺序与验证策略

| 批次 | 步骤 | 涉及文件数 | 验证方式 |
|------|------|-----------|---------|
| 1 | 阶段一（HSM: P2-1,2,3） | 1 | `pytest tests/unit/sec/test_hsm_basic.py tests/unit/sec/test_hsm_key_slot.py` |
| 2 | 阶段二（应急签发: P2-4,5,6,7） | 1 | `pytest tests/unit/sec/test_emergency_signing.py` |
| 3 | 阶段三（脱敏还原: P2-8,9,10） | 1 | `pytest tests/unit/sec/test_desensitization_restore.py` |
| 4 | 阶段四（三层脱敏: P2-11,12,13,14） | 5 | `pytest tests/unit/sec/test_desensitization.py` |
| 5 | 阶段五（同意: P2-15,16,17,18,20） | 4 | `pytest tests/unit/sec/test_consent_withdrawal_consumer.py tests/unit/sec/test_security_compliance.py` |
| 6 | 阶段六（SafetyGateway: P2-21,22,23,24,25） | 5 | `pytest tests/unit/sg/` |

最终验证: `pytest tests/unit/sec/ tests/unit/sg/ -q`
