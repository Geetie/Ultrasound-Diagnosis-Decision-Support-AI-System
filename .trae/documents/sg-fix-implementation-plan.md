# SafetyGateway审查报告修复实施计划

根据审查报告18项修复项，按依赖关系排序实施。每项修复包含：修改文件、具体代码变更、验证方法。

---

## 修复批次一：数据模型与类型定义（无外部依赖，其他修复依赖这些）

### SG-FIX-06: 清理DegradedReason.SG_UNAVAILABLE死代码
**文件**: `safety_gateway/validation_result.py`
- 移除 `DegradedReason.SG_UNAVAILABLE = "sg_unavailable"` (line 25)
- 移除 `CheckResultStatus.SG_UNAVAILABLE = "sg_unavailable"` (line 19) — 同为死代码

**文件**: `safety_gateway/checker.py`
- 移除 `apply_degraded_output()` 中 `DegradedReason.SG_UNAVAILABLE` 分支 (line 809-811)

**验证**: 运行现有测试确认无引用断裂

### SG-FIX-05: 修正降级原因命名语义
**文件**: `safety_gateway/validation_result.py`
- 将 `DESENSITIZATION_DEGRADED_SG_UNAVAILABLE = "desensitization_degraded_sg_available"` 重命名为：
  - `FUSION_UNAVAILABLE_SENSITIVE_DETECTED = "fusion_unavailable_sensitive_detected"`
  - `DESENSITIZATION_DEGRADED_SENSITIVE_DETECTED = "desensitization_degraded_sensitive_detected"`
- 保留 `DESENSITIZATION_DEGRADED_SG_UNAVAILABLE` 仅用于真实双重不可用场景（但当前无代码路径触发，可保留为预留）

**文件**: `safety_gateway/checker.py`
- line 538: `DegradedReason.DESENSITIZATION_DEGRADED_SG_UNAVAILABLE` → `DegradedReason.FUSION_UNAVAILABLE_SENSITIVE_DETECTED`
- line 558: `DegradedReason.DESENSITIZATION_DEGRADED_SG_UNAVAILABLE` → `DegradedReason.DESENSITIZATION_DEGRADED_SENSITIVE_DETECTED`
- `apply_degraded_output()` 中更新对应分支处理

**文件**: `safety_gateway/__init__.py`
- 更新导出的 `DegradedReason` 引用

**验证**: 运行测试，更新测试中的枚举值引用

### SG-FIX-13: E4005A子码拆分
**文件**: `safety_gateway/errors.py`
- 将 `ConsentSafetyViolationError` 拆分为：
  - `DesensitizationSafetyViolationError` (code="E4005A1") — 脱敏降级+敏感数据
  - `ConsentSafetyViolationError` (code="E4005A2") — SG不可用+患者撤回同意
- 更新 `ERROR_CODE_MAP`

**文件**: `safety_gateway/checker.py`
- line 529/550: 抛出 `DesensitizationSafetyViolationError` 替代 `ConsentSafetyViolationError`（但当前这些路径返回CheckResult而非抛异常，实际E4005A由main.py的异常处理器生成）
- 实际上checker.py中E4005A场景返回FAILED+降级原因，由main.py根据降级原因构造响应。需审查main.py是否需要调整

**文件**: `safety_gateway/main.py`
- 更新异常处理器中的类型判断

**验证**: 运行测试，更新错误码断言

---

## 修复批次二：核心功能实现

### SG-FIX-01: OOM保护状态参与健康检查判定
**文件**: `safety_gateway/health.py`
- 在 `check_all()` 方法中：
  - 将 `oom_status` 纳入 `components_healthy` 判定：Linux环境下 `oom_status["status"]` 必须为 "protected"
  - 将 `oom_status` 纳入 `components_degraded` 判定：`oom_status["status"]` 为 "warning" 时标记为 degraded
  - Windows/非Linux环境：`oom_status["status"]` 为 "unknown" 时不影响健康判定（因为无法检测）
- 修改 `components_healthy` 计算逻辑，加入 `oom_status` 条件
- 修改 `components_degraded` 列表，加入 `oom_status`

**验证**: 
- 新增测试：OOM保护失效时健康检查返回degraded
- 新增测试：Linux环境OOM保护正常时健康检查返回healthy
- 新增测试：Windows环境OOM状态unknown时不影响健康判定

### SG-FIX-02: 实现conclusion_suppressed标记处理
**文件**: `safety_gateway/checker.py`
- 在 `perform_full_check()` 方法签名中增加 `conclusion_suppressed: bool = False` 参数
- 在入口快速拒绝后、_check_consent_phase前，增加逻辑：
  - 若 `conclusion_suppressed=True`，标记 `ctx.conclusion_suppressed = True`
- 在 `_check_desensitization_phase` 中：若 `ctx.conclusion_suppressed`，仅对 observations 字段执行脱敏校验，跳过 diagnostic_conclusion
- 在 `apply_degraded_output()` 中：若输入数据含 `conclusion_suppressed=True`，跳过 diagnostic_conclusion 的过滤

**文件**: `safety_gateway/main.py`
- 在 `SafetyCheckRequest` 中增加 `conclusion_suppressed: bool = False` 字段
- 在 `perform_safety_check` 中传递 `conclusion_suppressed` 参数

**验证**: 新增测试：conclusion_suppressed=True时跳过diagnostic_conclusion过滤

### SG-FIX-03: 实现严重幻觉输出处理
**文件**: `safety_gateway/checker.py`
- 在 `perform_full_check()` 方法签名中增加 `hallucination_severity: str | None = None` 参数
- 在校验流程末尾（一致性校验后），增加幻觉检测逻辑：
  - 若 `hallucination_severity == "severe"`，在 CheckResult 中附加 `hallucination_warning=True` 标记
- 在 `CheckResult` 模型中增加 `hallucination_warning: bool = False` 字段

**文件**: `safety_gateway/validation_result.py`
- 在 `CheckResult` 中增加 `hallucination_warning: bool = False` 字段

**文件**: `safety_gateway/main.py`
- 在 `SafetyCheckRequest` 中增加 `hallucination_severity: str | None = None` 字段
- 在 `SafetyCheckResponse` 中增加 `hallucination_warning: bool = False` 字段
- 在 `perform_safety_check` 中传递参数并映射响应

**验证**: 新增测试：hallucination_severity="severe"时CheckResult包含hallucination_warning=True

### SG-FIX-04: 实现降级模式类型化脱敏策略
**文件**: `desensitization_rules_safety.yaml`
- 将所有规则的 replacement 字段改为 `[REDACTED:{类型}]` 格式：
  - sg_id_card: `***-****-****` → `[REDACTED:ID_CARD]`
  - sg_phone: `PHONE_***` → `[REDACTED:PHONE]`
  - sg_patient_name: `[REDACTED_NAME_SG]` → `[REDACTED:NAME]`
  - sg_patient_name_standalone: `患者：PATIENT_***` → `患者：[REDACTED:NAME]`
  - sg_patient_name_simple: `[REDACTED_NAME_SG]` → `[REDACTED:NAME]`
  - sg_email: `***@***.***` → `[REDACTED:EMAIL]`
  - sg_birth_date: `出生日期：****-**-**` → `[REDACTED:BIRTH_DATE]`
  - sg_address: `住址：***市***区` → `[REDACTED:ADDRESS]`
  - sg_medical_record_number: `病历号：MR***` → `[REDACTED:MEDICAL_RECORD]`
  - sg_dicom_uid: `DICOM_UID:***` → `[REDACTED:DICOM_UID]`
  - sg_ip_address: `IP地址：***.***.***.***` → `[REDACTED:IP_ADDRESS]`

**文件**: `safety_gateway/desensitization.py`
- 更新 `_get_default_rules()` 中硬编码默认规则的 replacement 字段（与YAML保持一致）

**文件**: `desensitization_rules_safety_patch.yaml`
- 同步更新补丁规则的 replacement 格式

**验证**: 运行现有脱敏测试，更新断言中的期望值

---

## 修复批次三：配置与校验

### SG-FIX-08: 增加端口冲突互斥检测
**文件**: `safety_gateway/sg_validator.py`
- 在 `validate_sg_config()` 中增加端口互斥检测：
  - 定义已知端口列表 `KNOWN_PORTS = {8000, 8001, 8002, 9001}`
  - 检查 `sg_settings.safety_gateway_port` 是否与其他已知端口冲突
  - 注意：仅检查SG自身端口不与其他服务端口重复，无法检查其他服务的动态配置

**验证**: 新增测试：SG端口=8000时校验失败

### SG-FIX-09: 修正数据库重试参数一致性
**文件**: `safety_gateway/sg_config.py`
- 将 `sg_db_retry_max_total_time_seconds` 从 `4.0` 调整为 `5.0`
- 确保最坏情况 2s + 0.1s + 2s = 4.1s < 5.0s

**验证**: 现有重试测试应继续通过

### SG-FIX-11: 修正Redis缓存TTL配置
**文件**: `safety_gateway/sg_config.py`
- 将 `sg_consent_cache_ttl_seconds` 从 `10` 调整为 `60`，与架构文档一致

**文件**: `safety_gateway/consent_checker.py`
- 在 `_check_redis_cache()` 中使用 `settings.sg_consent_cache_ttl_seconds` 验证缓存TTL是否在可接受范围内
- 添加逻辑：如果缓存TTL > sg_consent_cache_ttl_seconds，记录警告但仍然使用

**验证**: 现有测试通过

---

## 修复批次四：测试补充

### SG-FIX-07: 增加客户端排队等待逻辑测试
**文件**: `tests/unit/test_safety_gateway.py`
- 新增 `TestSafetyGatewayClientQueueLogic` 测试类：
  - `test_queue_retry_on_connect_error`: 模拟连接错误后重试
  - `test_queue_timeout_returns_e4005`: 模拟超时后返回E4005
  - `test_fast_fail_after_consecutive_failures`: 模拟连续3次失败后快速失败
  - `test_consecutive_failures_reset_on_success`: 模拟成功后重置失败计数
  - `test_warmup_establishes_connection`: 模拟预热建立连接

### SG-FIX-14: 增加规则加载失败降级测试
**文件**: `tests/unit/test_safety_gateway.py`
- 新增测试：
  - `test_default_rules_used_when_yaml_missing`: YAML文件不存在时使用默认规则
  - `test_graceful_handling_of_malformed_yaml`: YAML格式错误时的降级行为

### SG-FIX-15: 增加Redis主从切换测试
**文件**: `tests/unit/test_safety_gateway.py`
- 新增测试：
  - `test_redis_reconnect_after_disconnect`: Redis断连后重连的降级行为
  - `test_consent_check_degrades_on_redis_loss`: Redis丢失时同意状态校验降级

---

## 修复批次五：文档与声明

### SG-FIX-10: 明确Redis同意状态缓存数据来源
**文件**: `safety_gateway/consent_checker.py`
- 在模块docstring中明确声明：Redis同意状态缓存由主服务写入（key: `sg:consent:{patient_id}`），SG仅读取
- 增加启动时日志提示Redis缓存依赖关系

### SG-FIX-18: 确认PENDING状态快速拒绝策略
**文件**: `safety_gateway/checker.py`
- 在 `perform_full_check()` 入口快速拒绝逻辑中添加注释，明确声明设计决策：PENDING状态在入口阶段即拒绝，因为"未确认的同意状态不应允许诊断输出"

---

## 实施顺序与依赖关系

```
批次一（模型层，无依赖）:
  SG-FIX-06 → SG-FIX-05 → SG-FIX-13

批次二（功能实现，依赖批次一的模型变更）:
  SG-FIX-01 | SG-FIX-02 | SG-FIX-03 | SG-FIX-04（可并行）

批次三（配置校验，无依赖）:
  SG-FIX-08 | SG-FIX-09 | SG-FIX-11（可并行）

批次四（测试补充，依赖批次一至三的代码变更）:
  SG-FIX-07 | SG-FIX-14 | SG-FIX-15（可并行）

批次五（文档声明，最后执行）:
  SG-FIX-10 | SG-FIX-18
```

## 验证计划

每批次完成后：
1. 运行 `pytest tests/unit/test_safety_gateway.py -v` 确认所有测试通过
2. 运行 `pytest tests/benchmark/test_safety_gateway_performance.py -v` 确认性能测试通过
3. 检查类型注解完整性

全部完成后：
1. 运行完整测试套件
2. 验证Docker Compose配置未受影响
3. 确认代码量仍在合理范围
