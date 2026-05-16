# P2 修复实施计划 — 剩余工作

## 已完成（上一会话）

- ✅ 阶段一：P2-1 (sign_jwt算法校验), P2-2 (_safe_run_async_task数据丢失), P2-3 (SoftHSM .days截断)
- ✅ 阶段二：P2-4 (恢复确认参数), P2-5 (密钥自动生成审计), P2-6 (返回类型已修复), P2-7 (TTL从HSM恢复计时)
- ✅ P2-8：re_encrypt_with_new_key 事务性保证（ReEncryptionError + resume_from_id）

## 剩余 16 项待实施

---

## 阶段三（续）：脱敏还原模块（P2-9, P2-10）

### P2-9：内存级速率限制改为 asyncio.Lock

**文件**: `app/security/desensitization_restore.py`

**变更**:
1. `__init__` 中添加 `self._async_lock = asyncio.Lock()`
2. `_check_rate_limit()` 改为 `async` 方法，使用 `async with self._async_lock`
3. `_check_rate_limit_persistent()` 中 Redis 限速拒绝时回滚内存计数
4. 添加 `import asyncio` 到文件顶部

**关键代码**:
```python
# __init__ 添加:
self._async_lock = asyncio.Lock()

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

# _check_rate_limit_persistent 中 Redis 拒绝时回滚:
if count > limit:
    async with self._async_lock:
        self._rate_limit_counter[hour_key] = max(
            0, self._rate_limit_counter.get(hour_key, 1) - 1
        )
    return False
```

### P2-10：batch_restore 性能优化

**文件**: `app/security/desensitization_restore.py`

**变更**:
1. 返回类型 `Dict[str, Optional[str]]`（已是 `Dict[str, str]`，需改为含 None）
2. 批量仅做一次限速检查
3. 批量查询 DB 获取所有 mapping
4. `asyncio.gather` + `Semaphore(5)` 并发恢复

**关键代码**:
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
                logger.error("batch_restore_failed_for_record", record_id=record_id, error=str(e))
                return record_id, None
    tasks = [_restore_one(rid) for rid in record_ids]
    results_list = await asyncio.gather(*tasks)
    return dict(results_list)
```

---

## 阶段四：三层脱敏模块（P2-11, P2-12, P2-13, P2-14）

### P2-11：pii_leakage_detected 语义修正

**文件**: `app/security/three_layer_desensitization.py`

**变更**:
1. `ThreeLayerDesensitizationResult.__init__` 中：`pii_leakage_detected` → `security_check_failed`，添加 `degraded_mode`
2. 添加 `@property pii_leakage_detected` 向后兼容
3. `process_full_pipeline` 末尾赋值逻辑：区分 `has_security_violation` 和 `has_degradation`

### P2-12：统一 DesensitizationRule 类型

**文件**:
- `app/common/desensitization/rule.py` — 添加 `is_patch: bool = False`
- `app/security/desensitization.py` — 删除本地 `DesensitizationRule`，改为 `from app.common.desensitization.rule import DesensitizationRule`
- `safety_gateway/desensitization_rules.py` — 删除本地 `DesensitizationRule`，改为 `from app.common.desensitization.rule import DesensitizationRule`

### P2-13：标记死代码

**文件**:
- `app/common/desensitization/base.py` — 顶部添加 `# DEPRECATED` 注释
- `app/common/desensitization/__init__.py` — `BaseDesensitizationEngine` 导出标记 deprecated

### P2-14：融合层引擎独立性注释

**文件**: `app/fusion/desensitization.py` — 更新模块 docstring

---

## 阶段五：同意模块（P2-15, P2-16, P2-17, P2-18, P2-20）

### P2-15：ConsentStatus 枚举一致性注释

**文件**: `safety_gateway/validation_result.py`, `app/models/schemas.py`
- 两处 `ConsentStatus` 定义上方添加一致性约束注释

### P2-16：data_retention 门诊/住院归档区分

**文件**: `app/models/database.py`, `app/services/data_retention.py`
- `SessionArchive` 添加 `patient_type` 字段
- `purge_expired_archives` 查询添加 `patient_type` 过滤
- `verify_retention_compliance` 分别查询门诊/住院

### P2-17：data_retention 闰年修正

**文件**: `app/services/data_retention.py`
- 所有 `* 365` → `int(* 365.25)`

### P2-18：consent_repository 访问私有属性

**文件**: `app/repositories/consent_repository.py`
- `hsm_manager._degraded_at` → `hsm_manager.get_degraded_at()`

### P2-20：scan_iter 替换为分页 scan

**文件**: `app/security/consent_withdrawal_consumer.py`
- 两处 `scan_iter` → 手动 `scan` + `await asyncio.sleep(0)`

---

## 阶段六：SafetyGateway（P2-21, P2-22, P2-23, P2-24, P2-25）

### P2-21：P99 硬约束限流

**文件**: `safety_gateway/main.py`
- `perform_safety_check` 入口添加 `health_checker._latency_tracker.is_throttling` 检查

### P2-22：CheckPhase 基类 check() 协议修复

**文件**: `safety_gateway/checker/phase_base.py`
- `check()` 改为委托 `check_with_params(context)`
- `check_with_params()` 改为抽象方法

### P2-23：EVIDENCE_INSUFFICIENT → degraded

**文件**: `safety_gateway/checker/consistency_phase.py`
- `CheckResult.failed()` → `CheckResult.degraded()`

### P2-24：fusion_available 修复

**文件**: `safety_gateway/main.py`
- `fusion_available=False` → 根据 `check_result.degraded_reason` 推断

### P2-25：LatencyTracker 状态修改加锁

**文件**: `safety_gateway/health/latency_tracker.py`
- `_critical_count`/`_throttling` 修改移入 `async with self._lock`

---

## 实施顺序

| 步骤 | 内容 | 文件数 |
|------|------|--------|
| 1 | P2-9, P2-10 脱敏还原 | 1 |
| 2 | P2-11, P2-12, P2-13, P2-14 三层脱敏 | 5 |
| 3 | P2-15, P2-16, P2-17, P2-18, P2-20 同意模块 | 4 |
| 4 | P2-21, P2-22, P2-23, P2-24, P2-25 SafetyGateway | 5 |
| 5 | 最终验证 | - |
