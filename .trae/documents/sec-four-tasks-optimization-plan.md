# SEC 四项优化任务实施计划

## 概览

本计划涵盖四项独立但相关的SEC优化任务，按优先级和依赖关系排序执行。

---

## 任务一：P99 硬约束限流逻辑审查与 degraded 模式拦截验证

### 1.1 审查范围

两套限流体系需要审查：
- **安全网关 (safety_gateway/main.py)**: P99 硬约束限流（核心）
- **主服务 (app/main.py)**: RateLimitMiddleware 令牌桶限流

### 1.2 审查重点

#### A. P99 限流逻辑正确性
- **文件**: `safety_gateway/health/latency_tracker.py`
- **当前逻辑**: 连续3次 P99 critical → `_throttling=True`；1次 ok → 立即恢复
- **潜在问题**:
  1. `compute_stats()` 在 `_lock` 内修改 `_critical_count` 和 `_throttling`，但 `is_throttling` 属性读取无锁保护 → **竞态条件**
  2. 从 critical 恢复只需1次 ok → **抖动风险**（应考虑恢复窗口，如连续2次 ok 才恢复）
  3. `warning` 状态不重置 `_critical_count` → 如果 warning → critical → warning → critical 循环，`_critical_count` 会持续累加而非重置

#### B. degraded 模式请求拦截验证
- **文件**: `safety_gateway/main.py` L296-324
- **三道防线**:
  1. 内存超预算 → 503 E4005
  2. P99 持续超限 → 503 E4005
  3. 并发信号量排队超时 → 503 E4005
- **验证点**:
  1. 确认 `is_throttling=True` 时请求确实被拦截（返回 ServiceUnavailableError）
  2. 确认拦截后信号量不会被获取（不会泄漏）
  3. 确认降级输出（degraded_output）在通过三道防线后正确应用

#### C. 主服务 RateLimitMiddleware 与 SG 的交互
- **文件**: `app/config/rate_limit.py`
- **验证点**: 主服务限流 429 与 SG 限流 503 的边界是否清晰

### 1.3 实施步骤

1. **为 LatencyTracker 添加线程安全保护**:
   - `is_throttling` 属性改为在锁内读取，或使用 `threading.Event`/`asyncio.Event`
   - 添加恢复窗口：连续2次 ok 才重置 `_throttling`
   - `warning` 状态时重置 `_critical_count`（因为不连续了）

2. **为 P99 限流逻辑添加测试**:
   - 测试连续3次 critical 触发限流
   - 测试1次 ok 不恢复（需连续2次）
   - 测试 warning 状态重置 critical_count
   - 测试竞态条件（并发 compute_stats 和 is_throttling 读取）
   - 测试 main.py 三道防线的集成（mock LatencyTracker.is_throttling=True → 验证返回 503）

3. **验证 degraded 模式端到端流程**:
   - 添加测试：请求通过三道防线 → checker 返回 degraded → 验证输出被正确脱敏
   - 添加测试：P99 限流激活时，请求在第二道防线被拦截，不进入信号量

---

## 任务二：desensitization_restore.py batch_restore 方法日志增强

### 2.1 当前状态

`batch_restore` 方法（L313-353）仅有一条 error 级别日志（单条记录解密失败时），缺少：
- 入口日志（请求数量、record_ids 概要）
- 出口日志（成功/失败/未找到数量、总耗时）
- 频率限制触发日志

### 2.2 实施步骤

1. **入口日志**:
   ```python
   logger.info("batch_restore_started",
               record_count=len(record_ids),
               record_ids_sample=record_ids[:5])
   ```

2. **出口日志**:
   ```python
   # 在 return 之前统计结果
   success_count = sum(1 for _, v in results_list if v is not None)
   not_found_count = sum(1 for rid, _ in results_list if rid not in mappings_by_id)
   failed_count = len(results_list) - success_count - not_found_count
   elapsed_ms = (time.monotonic() - start_time) * 1000
   logger.info("batch_restore_completed",
               total=len(results_list),
               success=success_count,
               not_found=not_found_count,
               failed=failed_count,
               elapsed_ms=round(elapsed_ms, 2))
   ```

3. **频率限制触发日志**:
   ```python
   # 在 raise RuntimeError 之前
   logger.warning("batch_restore_rate_limited",
                   record_count=len(record_ids))
   ```

4. **空列表快速返回日志**:
   ```python
   logger.debug("batch_restore_empty_request")
   ```

5. **更新测试**: 验证日志被正确记录（使用 caplog 或 mock logger）

---

## 任务三：患者同意撤回 Mock 数据构造与测试验证

### 3.1 当前测试不足

- `TestSafetyActionHandler.test_handle_withdrawal_does_not_raise` 仅验证不抛异常，未验证 Redis/DB 操作
- 缺少 `_consume_loop` 端到端测试
- 缺少 `_decode_field` 单元测试
- 死信队列测试仅验证 `xadd` 被调用，未验证数据内容
- 工厂函数测试直接修改全局 `settings`

### 3.2 Mock 数据设计

构造完整的患者同意撤回场景 Mock 数据：

```python
# 场景1: 标准撤回 - 患者有活跃会话
MOCK_WITHDRAWAL_STANDARD = {
    "patient_id": "P20250001",
    "operator_id": "DOC20250001",
    "withdrawn_at": "2025-05-15T10:30:00+08:00",
    "consent_version": "v2.1",
    "event_id": "evt-001",
    "active_sessions": [
        {"session_id": "SES001", "status": "pending"},
        {"session_id": "SES002", "status": "in_progress"},
    ],
    "completed_sessions": [
        {"session_id": "SES003", "status": "completed"},
    ],
    "edge_sessions": ["edge:P20250001:SES001", "edge:P20250001:SES002"],
    "desens_cache_keys": ["desens_session:SES001:P20250001", "desens_session:SES002:P20250001"],
}

# 场景2: 撤回但无活跃会话
MOCK_WITHDRAWAL_NO_ACTIVE = {
    "patient_id": "P20250002",
    "operator_id": "DOC20250002",
    "withdrawn_at": "2025-05-15T11:00:00+08:00",
    "consent_version": "v1.0",
    "event_id": "evt-002",
    "active_sessions": [],
    "completed_sessions": [],
    "edge_sessions": [],
    "desens_cache_keys": [],
}

# 场景3: Redis 部分失败
MOCK_WITHDRAWAL_REDIS_PARTIAL_FAIL = {
    "patient_id": "P20250003",
    "operator_id": "DOC20250003",
    "withdrawn_at": "2025-05-15T11:30:00+08:00",
    "consent_version": "v2.0",
    "event_id": "evt-003",
    "redis_set_fails": True,
}
```

### 3.3 实施步骤

1. **创建 Mock 数据 fixture**（在 conftest.py 或测试文件中）

2. **增强 SafetyActionHandler 测试**:
   - 验证 `redis.set` 被正确调用（参数、TTL=86400）
   - 验证 `redis.publish` 被调用（CONSENT_WITHDRAWAL_NOTIFICATION）
   - 验证 `redis.scan` + `publish` 边缘停止通知
   - 验证数据库会话状态被标记为 "withdrawn"
   - 验证审计日志被正确写入

3. **增强 DataCleanupHandler 测试**:
   - 验证脱敏缓存被删除
   - 验证 completed/needs_review 会话被标记
   - 验证审计日志内容

4. **添加 _decode_field 单元测试**:
   - bytes 输入
   - str 输入
   - 缺失字段使用默认值
   - 混合 bytes/str 字典

5. **添加 _consume_loop 端到端测试**:
   - mock xreadgroup 返回消息 → 验证 _process_event 被调用 → 验证 xack 被调用
   - mock _process_event 抛异常 → 验证 xack 不被调用

6. **增强死信队列测试**:
   - 验证 dead_letter_data 包含 original_msg_id、error、failed_at 字段
   - 验证原始字段被正确转存

7. **修复工厂函数测试**:
   - 使用 `patch.object(settings, 'consent_withdrawal_handlers', ...)` 替代直接修改

---

## 任务四：SEC 测试套件全面重构与优化

### 4.1 当前问题分析

| 问题 | 严重度 | 影响 |
|------|--------|------|
| **测试文件冗余重叠** | 高 | 40个SEC测试文件，大量重复测试类（如脱敏测试在5+文件中重复） |
| **test_safety_gateway.py 过大** | 高 | 2338行/63类，维护困难，执行慢 |
| **缺少测试分类标记** | 中 | 无法按速度/重要性选择性执行 |
| **可观测性不足** | 中 | conftest有进度报告器但缺少中间步骤断言 |
| **Mock 策略不统一** | 中 | 部分用 patch、部分用 fixture、部分直接修改全局 |
| **HSM密钥重复生成** | 中 | conftest有session级预生成但部分测试仍独立生成 |
| **性能测试与单元测试混合** | 低 | benchmark测试在unit目录中，执行时间长 |

### 4.2 重构策略

#### Phase A: 测试分类与标记体系

1. **添加三级速度标记**:
   ```python
   @pytest.mark.sec_speed_fast      # < 0.1s
   @pytest.mark.sec_speed_normal    # 0.1s - 1s
   @pytest.mark.sec_speed_slow      # > 1s
   ```

2. **添加功能域标记**（已有 track_sec，需细化）:
   ```python
   @pytest.mark.sec_domain_auth     # 认证相关
   @pytest.mark.sec_domain_hsm      # HSM相关
   @pytest.mark.sec_domain_desens   # 脱敏相关
   @pytest.mark.sec_domain_consent  # 同意管理
   @pytest.mark.sec_domain_token    # Token管理
   @pytest.mark.sec_domain_audit    # 审计相关
   @pytest.mark.sec_domain_sg       # 安全网关
   ```

3. **添加重要性标记**:
   ```python
   @pytest.mark.sec_critical        # 安全关键路径
   @pytest.mark.sec_regression      # 回归测试
   ```

#### Phase B: 去重与合并

1. **脱敏测试合并**:
   - `test_desensitization.py` (466行) + `test_sec_desensitization_deep.py` (367行) → 合并为 `test_desensitization.py`
   - 识别重复测试类，保留更完整的版本

2. **HSM测试合并**:
   - `test_hsm_basic.py` (256行) + `test_hsm_key_slot.py` (207行) + `test_sec_hsm_deep.py` (288行) → 合并为 `test_hsm.py`
   - 统一 `_make_lightweight_hsm` fixture

3. **审计测试合并**:
   - `test_sec_audit.py` (366行) + `test_audit_deep.py` (217行) + `test_sec_audit_gap_fill.py` (203行) + `test_audit_fixes.py` (441行) → 合并为 `test_audit.py`

4. **补丁类测试合并**:
   - `test_sec_craft_fixes.py` + `test_k1_fixes.py` + `test_white_fixes.py` + `test_white_round2_fixes.py` → 合并为 `test_regression_fixes.py`

5. **综合测试合并**:
   - `test_security_compliance.py` (216行) + `test_security_compliance_complete.py` (421行) → 合并为 `test_compliance.py`

6. **test_safety_gateway.py 拆分**:
   - 按功能域拆分为多个文件：
     - `test_sg_desensitization.py` - 脱敏引擎测试
     - `test_sg_contraindication.py` - 禁忌校验测试
     - `test_sg_threshold.py` - 阈值引擎测试
     - `test_sg_checker.py` - 校验器核心测试
     - `test_sg_circuit_breaker.py` - 熔断器测试
     - `test_sg_degradation.py` - 降级路径测试
     - `test_sg_health.py` - 健康检查测试
     - `test_sg_consent.py` - 同意检查测试
     - `test_sg_integration.py` - 全链路集成测试

#### Phase C: 可观测性增强

1. **增强 SECProgressReporter**:
   - 添加每个测试的执行时间记录
   - 添加慢测试警告（>1s 标黄，>5s 标红）
   - 添加阶段级汇总统计

2. **添加中间步骤断言模式**:
   ```python
   class SECStepReporter:
       """SEC测试步骤报告器，在每个关键步骤记录中间结果"""
       def __init__(self, test_name):
           self.test_name = test_name
           self.steps = []

       def step(self, name, result=None, expected=None):
           self.steps.append({
               "name": name,
               "result": result,
               "expected": expected,
               "passed": result == expected if expected is not None else True,
               "timestamp": time.time(),
           })

       def summary(self):
           return {
               "test": self.test_name,
               "steps": len(self.steps),
               "passed": sum(1 for s in self.steps if s["passed"]),
               "failed": sum(1 for s in self.steps if not s["passed"]),
           }
   ```

3. **添加 fixture**:
   ```python
   @pytest.fixture
   def sec_steps(request):
       reporter = SECStepReporter(request.node.name)
       yield reporter
       summary = reporter.summary()
       if summary["failed"] > 0:
           logger.warning("sec_test_steps_failed", **summary)
   ```

#### Phase D: 性能优化

1. **消除不必要的 sleep**:
   - `test_sec_supplementary.py:76` 的 `time.sleep(0.01)` → 替换为 `asyncio.sleep(0)` 或移除
   - `test_safety_gateway.py:925` 的 `asyncio.sleep(0.02)` → 评估是否可移除

2. **HSM 密钥生成优化**:
   - 确保所有 HSM 测试使用 conftest 中的 session 级预生成密钥
   - 添加 `hsm_with_shared_keys` fixture，复用预生成密钥

3. **参数化测试优化**:
   - 识别可参数化的重复测试模式
   - 使用 `@pytest.mark.parametrize` 减少代码重复

4. **pytest 配置优化**:
   - 添加 `--durations=20` 到 addopts，自动显示最慢的20个测试
   - 添加 `-x --ff` 选项用于快速失败和先运行失败测试

#### Phase E: 测试有效性提升

1. **增强断言逻辑**:
   - 替换 "does_not_raise" 类弱断言为具体行为验证
   - 添加返回值/副作用验证（如 Redis 调用参数、DB 状态变更）

2. **边界条件补充**:
   - 空输入/空列表
   - 超长输入
   - None 值处理
   - 并发竞争条件

3. **异常路径测试**:
   - Redis 连接失败
   - 数据库连接失败
   - 网络超时
   - 内存不足

### 4.3 重构后目标结构

```
tests/unit/sec/
├── conftest.py                    # 统一fixture + 进度报告器 + 步骤报告器
├── test_auth.py                   # JWT认证 + 密码 + 角色权限
├── test_hsm.py                    # HSM密钥管理（合并后）
├── test_emergency_signing.py      # 紧急签名
├── test_desensitization.py        # 脱敏引擎（合并后）
├── test_desensitization_restore.py # 脱敏还原
├── test_consent_withdrawal.py     # 同意撤回消费者（增强后）
├── test_refresh_token.py          # Refresh Token轮换
├── test_redis_token_store.py      # Redis令牌存储
├── test_compliance.py             # 安全合规（合并后）
├── test_audit.py                  # 审计链（合并后）
├── test_e2e_security_flow.py      # 端到端安全流程
├── test_regression_fixes.py       # 回归修复（合并后）
├── test_sec_p0_critical.py        # P0关键路径（保留）
└── test_config_doc_consistency.py # 配置一致性（保留）

tests/unit/sg/
├── conftest.py
├── test_sg_desensitization.py     # 拆分后
├── test_sg_contraindication.py    # 拆分后
├── test_sg_threshold.py           # 拆分后
├── test_sg_checker.py             # 拆分后
├── test_sg_circuit_breaker.py     # 拆分后
├── test_sg_degradation.py         # 拆分后
├── test_sg_health.py              # 拆分后
├── test_sg_consent.py             # 拆分后
├── test_sg_integration.py         # 拆分后
├── test_sg_p99_throttling.py      # 新增：P99限流测试
└── test_desensitization_shared.py # 保留
```

### 4.4 实施顺序

1. **Phase A** (标记体系) → 不影响现有测试，可先行
2. **Phase C** (可观测性) → 增强现有测试，不改变结构
3. **Phase D** (性能优化) → 减少执行时间
4. **Phase B** (去重合并) → 最大的结构变更，最后执行
5. **Phase E** (有效性提升) → 持续改进

---

## 执行计划总览

| 步骤 | 任务 | 预计影响文件数 | 依赖 |
|------|------|---------------|------|
| 1 | 任务二：batch_restore 日志增强 | 2 | 无 |
| 2 | 任务一：P99 限流逻辑审查与修复 | 3-4 | 无 |
| 3 | 任务三：同意撤回 Mock 数据与测试 | 2-3 | 无 |
| 4 | 任务四-PhaseA：测试标记体系 | 40+ | 无 |
| 5 | 任务四-PhaseC：可观测性增强 | 1-2 | PhaseA |
| 6 | 任务四-PhaseD：性能优化 | 5-10 | PhaseA |
| 7 | 任务四-PhaseB：去重合并 | 40+ | PhaseA+C+D |
| 8 | 任务四-PhaseE：有效性提升 | 持续 | PhaseB |

步骤1-3可并行执行，步骤4-8需顺序执行。
