# SafetyGateway 24项修复实施计划

## 概述

基于系统性审查报告，对SafetyGateway轨道的24个修复项制定分阶段实施计划。
按优先级分3批执行：P0（安全底线）→ P1（功能正确性）→ P2（健壮性提升）。

---

## 批次1：P0修复（安全底线，6项）

### SG-FIX-09/18: 实现入口快速拒绝阶段

**文件**: `safety_gateway/checker/consent_phase.py`
**问题**: 设计文档要求两阶段校验（入口快速拒绝 + 输出前PG直查），当前缺少独立的第一阶段
**修复方案**:
1. 在 `ConsentPhase.check_with_params` 开头增加入口快速拒绝逻辑
2. 当 `consent_status_at_input` 为 `denied`/`withdrawn`/`pending` 时直接返回失败，跳过PG查询
3. `consent_status_source` 标注为 `"input_fast_reject"`
4. 补充单元测试验证入口快速拒绝路径

**验证**: 运行 `tests/unit/sg/test_sg_consent.py` 和 `tests/unit/sg/test_checker_phases.py`

---

### SG-FIX-01: OOM保护unknown状态应视为degraded

**文件**: `safety_gateway/health/checker.py`
**问题**: `oom_status["status"] == "unknown"` 被纳入健康判定，掩盖OOM保护未生效风险
**修复方案**:
1. 在 `HealthChecker.check_all()` 中，将 `oom_status["status"] == "unknown"` 从 `components_healthy` 条件中移除
2. `unknown` 状态应导致整体状态降级为 `degraded`（而非 `healthy`）
3. 补充测试验证 unknown 状态下的健康检查结果

**验证**: 运行 `tests/unit/sg/test_sg_health.py`

---

### SG-FIX-02: 扩展配置验证覆盖

**文件**: `safety_gateway/sg_validator.py`
**问题**: `validate_sg_config()` 仅验证 redis_url，未覆盖 database_url、规则文件存在性等
**修复方案**:
1. 验证 `database_url` 非空（空时拒绝启动，而非仅打日志）
2. 验证 `desensitization_rules_safety_path` 文件存在性（不存在时警告但不阻止启动，有硬编码兜底）
3. 验证 `safety_gateway_port` 不与 `_KNOWN_SERVICE_PORTS` 冲突（已有 `SGSettings.validate_port`，但 `validate_sg_config` 未调用）
4. 补充配置验证测试

**验证**: 运行 `tests/unit/sg/test_sg_checker.py` 中配置验证相关测试

---

### SG-FIX-03: 确认retry_with_backoff的max_total_time生效

**文件**: `safety_gateway/retry.py`（已确认逻辑正确）
**问题**: 审查时怀疑 `max_total_time` 参数可能无效
**确认结果**: `retry_with_backoff` 函数正确接受 `max_total_time` 参数，逻辑完整（第40-51行）
**修复方案**: 无需修改代码，但需补充测试验证 `max_total_time` 超限时的提前终止行为
**降级**: 从P0降为P2（仅缺测试）

---

### SG-FIX-20: 补充阈值引擎异步接口测试

**文件**: `tests/unit/sg/test_sg_threshold.py`
**问题**: `ThresholdEngine.check()` 和 `check_multiple()` 异步方法无直接测试
**修复方案**:
1. 添加 `test_check_async` 测试 `check()` 方法
2. 添加 `test_check_multiple_async` 测试 `check_multiple()` 方法
3. 添加 `test_ensure_loaded_triggers_lazy_load` 测试懒加载

**验证**: 运行 `tests/unit/sg/test_sg_threshold.py`

---

### SG-FIX-21: 补充熔断器half-open→open回退测试

**文件**: `tests/unit/sg/test_sg_circuit_breaker.py`
**问题**: 未测试 half-open 下再次失败回到 open 的场景
**修复方案**:
1. 添加 `test_half_open_to_open_on_failure` 测试
2. 添加 `test_half_open_partial_success_then_failure` 测试
3. 添加 `test_cooldown_auto_transition_to_half_open` 测试

**验证**: 运行 `tests/unit/sg/test_sg_circuit_breaker.py`

---

## 批次2：P1修复（功能正确性，8项）

### SG-FIX-04: FusionProbe关闭时清理httpx客户端

**文件**: `safety_gateway/health/checker.py`
**修复方案**:
1. 在 `HealthChecker` 中添加 `async def aclose()` 方法
2. 在 `aclose()` 中调用 `self._fusion_probe.aclose()`
3. 在 `main.py` 的 `lifespan` 关闭流程中调用 `health_checker.aclose()`

---

### SG-FIX-05: 并发限制参数化

**文件**: `safety_gateway/sg_config.py`、`safety_gateway/main.py`
**修复方案**:
1. 在 `SGSettings` 中添加 `sg_max_concurrent_checks: int = 100`
2. 在 `main.py` 中使用 `settings.sg_max_concurrent_checks` 替代硬编码 `100`

---

### SG-FIX-06: all_passed在consent_check_timeout时应为False

**文件**: `safety_gateway/main.py`
**修复方案**:
1. 修改 `SafetyCheckResponse` 的 `all_passed` 赋值逻辑
2. 当 `consent_check_timeout=True` 时，`all_passed=False`
3. 补充测试

---

### SG-FIX-10: 降级输出conclusion_suppressed标记逻辑修正

**文件**: `safety_gateway/degraded_output.py`
**修复方案**:
1. 所有降级场景都设置 `conclusion_suppressed: True`（因为 `diagnostic_conclusion` 始终被移除）
2. 或保留 `diagnostic_conclusion` 字段但置为 `null`（当降级原因不在 `_CONCLUSION_SUPPRESSED_REASONS` 中时）
3. 选择方案1（更简单）：`conclusion_suppressed` 始终为 True，因为降级输出中已无诊断结论

---

### SG-FIX-11: consent_status_at_output来源标注精确化

**文件**: `safety_gateway/main.py`
**修复方案**:
1. 当 `consent_status_source` 为 `"input_fast_reject_db_error"` 时，`consent_status_at_output` 应反映实际来源
2. 在 `SafetyCheckResponse` 中增加 `consent_status_source` 字段暴露给前端

---

### SG-FIX-12: 规则引擎零可用规则时返回unhealthy

**文件**: `safety_gateway/health/providers.py`
**修复方案**:
1. 修改 `check_rules_engine()` 中 `enabled_count == 0` 时返回 `"unhealthy"` 而非 `"degraded"`

---

### SG-FIX-14: 边缘断连等非A级降级不应移除diagnostic_conclusion

**文件**: `safety_gateway/degraded_output.py`
**修复方案**:
1. 仅当降级原因属于 `_CONCLUSION_SUPPRESSED_REASONS` 时才移除 `diagnostic_conclusion`
2. 非结论抑制类降级（如 `EDGE_DISCONNECTED`）保留 `diagnostic_conclusion`
3. 补充测试验证边缘断连场景下诊断结论保留

---

### SG-FIX-16: 异构冗余验证改为按功能域匹配

**文件**: `safety_gateway/desensitization_rules.py`
**修复方案**:
1. 在 `validate_heterogeneous_redundancy` 中建立功能域映射（如 `sg_id_card` ↔ `id_card`）
2. 按功能域匹配而非按规则名匹配
3. 补充测试验证异构冗余检测功能

---

## 批次3：P2修复（健壮性提升，10项）

### SG-FIX-07: 模块级单例改为懒初始化
- `desensitization.py` 中 `sg_desensitization_engine` 改为函数调用
- `circuit_breaker.py` 中 `circuit_breaker_manager` 改为函数调用

### SG-FIX-08: SG独立DesensitizationRule模型
- 在 `safety_gateway/` 内定义独立的 `DesensitizationRule` 模型
- 移除对 `app.common.desensitization.rule` 的依赖

### SG-FIX-13: 同步文档中健康检查间隔为4s
- 文档更新项，非代码修改

### SG-FIX-15: 内存超预算事件写入审计日志
- 在 `MemoryMonitor._trigger_memory_defense` 中增加审计日志写入

### SG-FIX-17: 明确标注禁用的sg_patient_name_simple规则
- 在YAML中添加更明确的注释

### SG-FIX-19: 确认consent_records表复合索引
- 检查数据库迁移脚本，确认 `(patient_id, created_at DESC)` 索引存在

### SG-FIX-22: 补充API层HTTP路由测试
- 添加使用 `TestClient` 的端到端HTTP测试

### SG-FIX-23: 补充脱敏引擎并发安全测试
- 添加多线程/协程并发调用脱敏引擎的测试

### SG-FIX-24: 修复手机号异构测试空操作
- 在 `test_desensitization_shared.py` 中实现实际验证逻辑

### SG-FIX-03(降级): 补充retry_with_backoff的max_total_time测试
- 添加测试验证 `max_total_time` 超限时的提前终止行为

---

## 执行顺序

```
批次1 (P0): SG-FIX-09/18 → SG-FIX-01 → SG-FIX-02 → SG-FIX-20 → SG-FIX-21
批次2 (P1): SG-FIX-14 → SG-FIX-06 → SG-FIX-12 → SG-FIX-16 → SG-FIX-04 → SG-FIX-05 → SG-FIX-10 → SG-FIX-11
批次3 (P2): SG-FIX-07 → SG-FIX-08 → SG-FIX-22 → SG-FIX-23 → SG-FIX-24 → SG-FIX-03 → SG-FIX-15 → SG-FIX-17 → SG-FIX-19 → SG-FIX-13
```

## 验证策略

每个批次完成后：
1. 运行 `python -m pytest tests/unit/sg/ -v --tb=short` 确保全部通过
2. 运行 `python -m pytest tests/unit/api/test_safety_gateway_handling.py -v` 验证API层
3. 检查无新增类型错误

全部完成后：
1. 运行完整SG测试套件
2. 运行 `ruff check safety_gateway/` 检查代码风格
3. 确认无回归
