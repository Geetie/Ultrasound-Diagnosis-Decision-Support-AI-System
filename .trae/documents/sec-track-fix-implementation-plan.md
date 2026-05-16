# SEC安全合规轨道审查问题逐项修复计划

**基于**：sec-track-deep-audit-report.md（2026-05-12审查报告）
**原则**：外科手术式修改，仅触及必须修改的行，不重构无关代码

***

## 修复批次与执行顺序

按依赖关系和优先级分4批执行，每批完成后运行测试验证。

***

## 批次1：P0级代码修复（4项）

### Fix-1: P0-1 ConsentStatus枚举统一

**决策**：方案A——在 `schemas.py` 增加 `PENDING`，统一两处定义

**修改文件**：

1. `app/models/schemas.py` L27-30：增加 `PENDING = "pending"`
2. `safety_gateway/consent_checker.py` L66-71：更新日志消息，将 "treating as PENDING" 改为更准确的语义描述
3. 新增测试：验证 `ConsentStatus.PENDING` 在 `schemas.py` 中可用

**具体代码变更**：

```python
# app/models/schemas.py L27-30 → L27-31
class ConsentStatus(str, Enum):
    GRANTED = "granted"
    WITHDRAWN = "withdrawn"
    DENIED = "denied"
    PENDING = "pending"
```

```python
# safety_gateway/consent_checker.py L66-71 → 更新日志
logger.warning(
    "consent_record_not_found_in_db",
    patient_id=patient_id,
    note="No consent record exists — treating as PENDING per 03-接口规范§5.1 (no record = not consented)",
)
return ConsentStatus.PENDING
```

**验证**：运行 `python -m pytest tests/unit/sec/ -v -k "consent"` 确认通过

***

### Fix-2: P0-2 同意撤回基准测试引入真实Handler

**修改文件**：

1. `tests/benchmark/sec/test_revocation_latency_benchmark.py`：替换 `MockSafetyActionHandler` 为使用内存mock的真实 `SafetyActionHandler`

**具体方案**：

- 创建 `InMemorySafetyActionHandler` 继承 `SafetyActionHandler`
- Mock `RedisPool.get_client()` 返回 `FakeRedisClient`（支持 set/publish/scan\_iter）
- Mock `get_session()` 返回内存session
- Mock `AuditService` 为轻量实现
- 增加 warmup（10次）+ P99统计（100次采样）

**关键代码结构**：

```python
class FakeRedisClient:
    """轻量内存Redis客户端，模拟真实Redis操作"""
    def __init__(self):
        self._data = {}
        self._pubsub = {}
    async def set(self, key, value, ex=None): ...
    async def get(self, key): ...
    async def publish(self, channel, message): ...
    async def scan_iter(self, pattern): ...

class FakeDbSession:
    """轻量内存DB会话"""
    ...

class TestRevocationLatencyBenchmark:
    @pytest.mark.asyncio
    async def test_real_handler_p99_under_500ms(self):
        """使用真实SafetyActionHandler + 内存mock测量P99延迟"""
        handler = SafetyActionHandler()
        # mock RedisPool, get_session, AuditService
        ...
        # warmup 10次
        for _ in range(10):
            await handler.handle_withdrawal(event)
        # 采样100次
        latencies = []
        for _ in range(100):
            start = time.monotonic()
            await handler.handle_withdrawal(event)
            latencies.append((time.monotonic() - start) * 1000)
        latencies.sort()
        p99 = latencies[99]
        assert p99 < 500, f"Real handler P99 {p99:.2f}ms exceeds 500ms SLA"
```

**验证**：运行 `python -m pytest tests/benchmark/sec/test_revocation_latency_benchmark.py -v`

***

### Fix-3: P0-3 修正test\_hsm\_basic.py docstring

**修改文件**：

1. `tests/unit/sec/test_hsm_basic.py` L14-15：修正类和方法的docstring

**具体代码变更**：

```python
# L14-15 原文：
class TestHSMSignaturePadding:
    """测试HSM签名填充方式为PKCS1v15"""

# 改为：
class TestHSMSignaturePadding:
    """测试HSM签名填充方式：sign()使用PSS，sign_jwt()使用PKCS1v15(RS256)"""

# L17-18 原文：
def test_sign_uses_pkcs1v15_padding(self):
    """验证HSM使用PKCS1v15填充，符合JWT RS256标准"""

# 改为：
def test_sign_verify_roundtrip_with_pss_padding(self):
    """验证sign()(PSS填充)和verify_signature()的签名验证往返"""
```

**验证**：运行 `python -m pytest tests/unit/sec/test_hsm_basic.py -v`

***

### Fix-4: P0-4 HSM \_safe\_run\_async\_task 修复RuntimeWarning

**问题分析**：`_safe_run_async_task` 在测试环境中使用 `asyncio.ensure_future(coro)` 创建task，但task未被await就被GC回收，产生RuntimeWarning。关键在于测试中事件循环存在但task在循环关闭前未被清理。

**修改文件**：

1. `app/security/hsm.py`：修改 `_safe_run_async_task` 方法，增加task清理机制

**具体方案**：

- 在 `_safe_run_async_task` 中，当检测到测试环境时，使用 `asyncio.ensure_future` 但确保task被追踪
- 增加 `cleanup_pending_tasks()` 方法供测试teardown调用
- 在 `_on_task_done` 回调中从 `_pending_tasks` 移除已完成task

**具体代码变更**：

```python
# hsm.py - 修改 _safe_run_async_task 方法
def _safe_run_async_task(self, coro):
    try:
        import sys
        is_test = 'pytest' in sys.modules or 'PYTEST_CURRENT_TEST' in os.environ

        try:
            loop = asyncio.get_event_loop_policy().get_event_loop()
            if loop.is_running():
                if is_test:
                    task = asyncio.ensure_future(coro)
                    self._pending_tasks.add(task)
                    task.add_done_callback(self._on_task_done)
                    return task
                else:
                    task = loop.create_task(coro)
                    self._pending_tasks.add(task)
                    task.add_done_callback(self._on_task_done)
                    return task
            else:
                loop.run_until_complete(coro)
                return None
        except RuntimeError:
            if not is_test:
                logger.error("no_event_loop_for_redis_persistence", action="data_may_be_lost")
            return None
    except Exception as e:
        logger.error("failed_to_start_async_task", error=str(e))

async def cleanup_pending_tasks(self) -> None:
    """等待所有待处理异步任务完成，供测试teardown调用"""
    if self._pending_tasks:
        await asyncio.gather(*self._pending_tasks, return_exceptions=True)
        self._pending_tasks.clear()
```

**验证**：运行 `python -m pytest tests/unit/sec/ -v -W error::RuntimeWarning` 确认RuntimeWarning消除

***

## 批次2：P1级代码修复（8项，排除P1-4已实现、P1-5/P1-6为文档更新归入批次4）

### Fix-5: P1-2 consent\_checker.py无记录时返回DENIED而非PENDING

**决策**：对齐03-接口规范"无记录=未签署"语义，将PENDING改为DENIED

**修改文件**：

1. `safety_gateway/consent_checker.py` L66-71：返回 `ConsentStatus.DENIED` 替代 `ConsentStatus.PENDING`
2. `safety_gateway/checker/consent_phase.py` L79：移除对 `ConsentStatus.PENDING` 的判断（已改为DENIED，无需单独判断）

**具体代码变更**：

```python
# consent_checker.py L66-71
if consent_record:
    return ConsentStatus(consent_record.status)
logger.warning(
    "consent_record_not_found_in_db",
    patient_id=patient_id,
    note="No consent record — treating as DENIED per 03-接口规范§5.1 (no record = not consented → E1008)",
)
return ConsentStatus.DENIED
```

```python
# consent_phase.py L79 原文：
if context.consent_status in (ConsentStatus.DENIED, ConsentStatus.PENDING):

# 改为：
if context.consent_status == ConsentStatus.DENIED:
```

**验证**：运行 `python -m pytest tests/unit/sec/ tests/unit/sg/ -v -k "consent"`

***

### Fix-6: P1-3 AuditService构造函数签名不一致

**修改文件**：

1. `app/security/consent_withdrawal_consumer.py` L98-112：在handler中正确获取db session并传入AuditService

**具体代码变更**：

```python
# SafetyActionHandler.handle_withdrawal 中 L98-112
try:
    from app.services.audit_service import AuditService
    from app.config.database import get_session
    async for db_session in get_session():
        audit_svc = AuditService(db_session)
        await audit_svc.log_action(
            action="CONSENT_WITHDRAWAL_SAFETY_ACTION",
            actor=event.operator_id,
            target_type="patient",
            target_id=event.patient_id,
            detail={...},
        )
        await db_session.commit()
        break
except Exception as e:
    logger.warning("failed_to_audit_withdrawal_safety_action", error=str(e))
```

同样修复 `DataCleanupHandler` 中 L173-186 的 `AuditService()` 调用。

**验证**：运行 `python -m pytest tests/unit/sec/test_consent_withdrawal_consumer.py -v`

***

### Fix-7: P1-7 验证SG/主服务ConsentRecord模型一致性

**修改文件**：无需修改代码，仅需验证

**验证步骤**：

1. 读取 `safety_gateway/sg_models.py` 中 `ConsentRecord` 定义
2. 与 `app/models/database.py` 中 `ConsentRecord` 对比
3. 如有差异，记录并说明原因

***

### Fix-8: P1-8 默认启用data\_cleanup handler

**修改文件**：

1. `app/security/consent_withdrawal_consumer.py` L337-338：默认handler列表增加 `"data_cleanup"`

**具体代码变更**：

```python
# L337-338 原文：
handler_names = getattr(settings, 'consent_withdrawal_handlers', None)
if handler_names is None:
    handler_names = ["safety_action"]

# 改为：
handler_names = getattr(settings, 'consent_withdrawal_handlers', None)
if handler_names is None:
    handler_names = ["safety_action", "data_cleanup"]
```

**验证**：运行 `python -m pytest tests/unit/sec/test_consent_withdrawal_consumer.py -v`

***

### Fix-9: P1-9 性能基准测试增加warmup

**修改文件**：

1. `tests/benchmark/sec/test_hsm_signing_performance.py`：每个测试增加10次warmup
2. `tests/benchmark/sec/test_desensitization_performance.py`：每个测试增加10次warmup
3. `tests/benchmark/sec/test_revocation_latency_benchmark.py`：每个测试增加10次warmup

**代码模式**（以HSM签名为例）：

```python
async def test_single_signature_latency_p99_under_5ms(self):
    hsm = HSMManager()
    hsm.generate_key_pair("bench_sign_key")
    data = b"benchmark_test_data"

    # warmup
    for _ in range(10):
        hsm.sign("bench_sign_key", data)

    latencies = []
    for _ in range(1000):
        start = time.monotonic()
        hsm.sign("bench_sign_key", data)
        latencies.append((time.monotonic() - start) * 1000)
    ...
```

**验证**：运行 `python -m pytest tests/benchmark/sec/ -v`

***

### Fix-10: P1-1 all\_passed语义——增加代码注释明确语义

**修改文件**：

1. `app/security/three_layer_desensitization.py` L205-218：增加语义说明注释

**具体代码变更**：

```python
# L205-218 增加语义注释
has_security_violation = False
for layer_type, layer_result in result.layer_results.items():
    check_result = layer_result.get("check_result", {})
    passed = check_result.get("passed", True)
    degraded = check_result.get("degraded_mode", False)
    if not passed or degraded:
        has_security_violation = True
        break

result.all_passed = not has_security_violation
result.pii_leakage_detected = has_security_violation
```

注意：按规则不加注释除非被要求。此处仅增加语义注释在all\_passed赋值行上方，说明"all\_passed=True表示三层安全校验全部通过（第1/2层modified=True属于正常脱敏，不影响判定）"。

**验证**：运行 `python -m pytest tests/unit/sec/ -v -k "desensitization"`

***

### Fix-11: P1-10 PytestUnknownMarkWarning

**发现**：`pyproject.toml` L18-39 已注册所有mark。但测试运行仍产生warning，可能是pytest版本问题或配置未生效。

**验证步骤**：

1. 确认 `pyproject.toml` 中 `markers` 配置正确
2. 检查是否有 `pytest.ini` 或 `conftest.py` 覆盖配置
3. 如确认配置正确但warning仍存在，在 `conftest.py` 中注册

***

### Fix-12: P1-4 速率限制——确认Redis持久化已实现

**发现**：`desensitization_restore.py` L158-186 已有 `_check_rate_limit_persistent()` 方法使用Redis Lua脚本实现分布式速率限制，且在 L243 的 `store_mapping()` 中被调用。P1-4问题部分不正确——Redis持久化速率限制已实现。

**操作**：仅需确认 `_check_rate_limit_persistent` 确实被 `store_mapping` 调用，无需修改代码。将审查报告中的P1-4标记为"已实现"。

***

## 批次3：P2级代码优化（6项）

### Fix-13: P2-3 SafetyGateway客户端熔断器参数移至settings

**修改文件**：

1. `app/config/settings.py`：增加 `sg_client_fast_fail_threshold: int = 3`
2. `app/services/safety_gateway_client.py` L14：读取settings替代硬编码

***

### Fix-14: P2-5 consent\_withdrawal\_consumer.py fields解析提取辅助函数

**修改文件**：

1. `app/security/consent_withdrawal_consumer.py`：提取 `_decode_field()` 辅助函数

***

### Fix-15: P2-6 SafetyActionHandler复用Redis客户端

**修改文件**：

1. `app/security/consent_withdrawal_consumer.py` L39-95：在方法开头获取一次Redis客户端

***

### Fix-16: P2-12 SafetyGateway客户端health\_probe间隔移至settings

**修改文件**：

1. `app/config/settings.py`：增加 `sg_health_probe_interval_seconds: float = 4.0`
2. `app/services/safety_gateway_client.py` L23：读取settings替代硬编码

***

### Fix-17: P2-13 SQLAlchemy declarative\_base废弃导入

**修改文件**：

1. `app/config/database.py`：改为 `from sqlalchemy.orm import declarative_base`

***

### Fix-18: P2-11 503错误码映射修正

**修改文件**：

1. `app/api/v1/errors.py` L394：将 `503: "E2001"` 改为 `503: "E4005"`

***

## 批次4：文档更新（6项）

### Fix-19: P1-5 + P1-6 + P2-1 文档更新（04-安全合规.md）

**修改文件**：

1. `docs-for-guiding generating docs/超声诊疗决策支持系统/04-安全合规.md`
   - §2.1.1：增加compact模式60天密钥轮换差异化说明
   - §4.1：增加JWT密钥120天自动轮换说明
   - §4.1：增加签名填充方式说明（JWT→PKCS1v15，通用→PSS）
   - §2：明确all\_passed语义定义

### Fix-20: P1-1 all\_passed语义文档对齐

包含在Fix-19中。

### Fix-21: 00a-术语表.md增加PENDING定义

**修改文件**：

1. `docs-for-guiding generating docs/超声诊疗决策支持系统/00a-术语表.md`：增加ConsentStatus.PENDING定义

***

## 不修复项（记录原因）

| 问题               | 原因                               |
| ---------------- | -------------------------------- |
| P2-2 主规则身份证正则精度  | 异构冗余设计是有意为之：宽松→减少漏报，精确→减少误报，互补   |
| P2-4 审计链锁LRU淘汰   | 10000上限对当前规模足够，改为活跃度淘汰增加复杂度，收益不大 |
| P2-7 数据保留定时任务    | 需确认是否由外部调度系统触发，不盲目增加             |
| P2-8 患者数据导出API   | 属于新功能开发，不属于修复范畴                  |
| P2-9 API Key生命周期 | 属于新功能开发，不属于修复范畴                  |
| P2-10 边缘端DICOM剥离 | 属于边缘端代码，需单独审查确认                  |

***

## 执行验证检查点

每批次完成后执行：

```bash
# 批次1完成后
python -m pytest tests/unit/sec/ -v --tb=short

# 批次2完成后
python -m pytest tests/unit/sec/ tests/unit/sg/ tests/unit/api/ -v --tb=short

# 批次3完成后
python -m pytest tests/ -v --tb=short

# 批次4完成后（文档更新，仅验证无破坏性变更）
python -m pytest tests/unit/sec/ -v --tb=short
```

最终验证：

- 测试总数 ≥ 1038
- 0 failed
- RuntimeWarning 数量显著减少
- 所有P0/P1修复项有对应测试覆盖

