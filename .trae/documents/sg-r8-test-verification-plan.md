# SG安全网关R8修复验证与历史问题测试计划

**目标**：使用Python 3.13（`py -3.13`）执行测试，验证R4-R8审查报告记录的问题修复情况
**Python路径**：`C:\Users\ghf\AppData\Local\Programs\Python\Python313\python.exe`（通过`py -3.13`调用）
**pytest版本**：9.0.3

---

## 一、测试执行计划

### 步骤1：编译检查所有修改文件

```bash
py -3.13 -m py_compile app/security/three_layer_desensitization.py
py -3.13 -m py_compile app/services/safety_gateway_client.py
py -3.13 -m py_compile safety_gateway/sg_config.py
py -3.13 -m py_compile safety_gateway/sg_db.py
py -3.13 -m py_compile safety_gateway/circuit_breaker.py
py -3.13 -m py_compile safety_gateway/consent_checker.py
```

**验证**：所有文件编译无错误

### 步骤2：运行SG核心单元测试（覆盖R6/R7/R8修复项）

```bash
py -3.13 -m pytest tests/unit/test_safety_gateway.py -v --tb=short -x --timeout=60 2>&1
```

**覆盖的修复项验证**：

| 修复项 | 对应测试类/方法 |
|--------|----------------|
| R6-01 SGError→HTTP状态码映射 | TestSafetyGatewayErrors |
| R6-02 单例线程安全 | 各Engine单例测试 |
| R7-02 客户端并发竞态条件 | TestSafetyGatewayClientQueueLogic |
| R7-09 性能测试time.time()精度 | TestSafetyGatewayPerformance |
| R8-04 Redis TTL对齐 | TestConsentRedisTTLCheck |
| R8-05 独立姓名规则 | TestDesensitizationEngine |
| R8-06 E4005子类型 | TestE4005ASubCodeSplit |
| R8-09 circuit_breaker文档 | TestCircuitBreaker |

### 步骤3：运行三层脱敏集成测试（覆盖R8-01/R8-02）

```bash
py -3.13 -m pytest tests/unit/test_e2e_security_flow.py -v --tb=short -x --timeout=60 2>&1
```

**覆盖的修复项验证**：

| 修复项 | 对应测试 |
|--------|----------|
| R8-01 键名兼容(degraded_mode/passed) | test_full_pipeline_* |
| R8-02 desensitization_degraded语义 | test_full_pipeline_* |
| R8-03 fusion_available移除 | test_full_pipeline_* |

### 步骤4：运行脱敏专项测试（覆盖R8-05规则补充）

```bash
py -3.13 -m pytest tests/unit/test_desensitization.py -v --tb=short -x --timeout=60 2>&1
```

### 步骤5：运行安全合规测试（覆盖R6-01/R7-06/R8-06）

```bash
py -3.13 -m pytest tests/unit/test_security_compliance.py tests/unit/test_security_compliance_complete.py -v --tb=short -x --timeout=60 2>&1
```

### 步骤6：运行性能基准测试（覆盖R7-09）

```bash
py -3.13 -m pytest tests/benchmark/test_safety_gateway_performance.py -v --tb=short -m "not integration" --timeout=120 2>&1
```

### 步骤7：运行SG相关全量测试

```bash
py -3.13 -m pytest tests/unit/ -k "safety or desensitization or security or consent or contraindication or threshold or circuit" -v --tb=short --timeout=60 2>&1
```

### 步骤8：汇总修复验证结果

根据测试结果，逐项确认R4-R8审查报告记录的问题修复状态。

---

## 二、修复项与测试映射

### R6修复项验证

| 修复项 | 描述 | 验证测试 |
|--------|------|----------|
| SG-FIX-R6-01 | SGError→HTTP状态码映射 | test_safety_gateway.py::TestSafetyGatewayErrors |
| SG-FIX-R6-02 | 单例线程安全 | test_safety_gateway.py中各单例测试 |

### R7修复项验证

| 修复项 | 描述 | 验证测试 |
|--------|------|----------|
| SG-FIX-R7-01 | 键名不匹配→R8-01深化修复 | test_e2e_security_flow.py |
| SG-FIX-R7-02 | 客户端并发竞态条件 | test_safety_gateway.py::TestSafetyGatewayClientQueueLogic |
| SG-FIX-R7-03 | medical_person_names.yaml声明 | YAML文件静态检查 |
| SG-FIX-R7-04 | 独立姓名规则→R8-05深化修复 | test_safety_gateway.py::TestDesensitizationEngine |
| SG-FIX-R7-05 | Dockerfile与supervisor HOME | 配置文件静态检查 |
| SG-FIX-R7-06 | E4005子类型→R8-06修复 | test_safety_gateway.py::TestE4005ASubCodeSplit |
| SG-FIX-R7-07 | supervisor startretries | 配置文件静态检查 |
| SG-FIX-R7-08 | 类型注解风格 | py_compile + 代码审查 |
| SG-FIX-R7-09 | time.time()精度 | test_safety_gateway_performance.py |
| SG-FIX-R7-10 | suffixes含"患者" | YAML文件静态检查 |
| SG-FIX-R7-11 | start_safety_gateway.sh缺set -e | 脚本文件静态检查 |
| SG-FIX-R7-12 | supervisor日志轮转 | 配置文件静态检查 |

### R8修复项验证

| 修复项 | 描述 | 验证测试 |
|--------|------|----------|
| SG-FIX-R8-01 | 键名兼容(degraded_mode/passed) | test_e2e_security_flow.py + test_safety_gateway.py |
| SG-FIX-R8-02 | desensitization_degraded语义 | test_e2e_security_flow.py |
| SG-FIX-R8-03 | fusion_available移除 | py_compile + test_e2e_security_flow.py |
| SG-FIX-R8-04 | Redis TTL 5→60 | test_safety_gateway.py::TestConsentRedisTTLCheck |
| SG-FIX-R8-05 | 独立姓名规则补充 | test_safety_gateway.py::TestDesensitizationEngine |
| SG-FIX-R8-06 | E4005子类型区分 | test_safety_gateway.py::TestE4005ASubCodeSplit |
| SG-FIX-R8-07 | 类型注解(评估无需修改) | — |
| SG-FIX-R8-08 | sg_db.py AsyncGenerator | py_compile |
| SG-FIX-R8-09 | circuit_breaker文档 | test_safety_gateway.py::TestCircuitBreaker |

---

## 三、预期结果

- 步骤1：所有6个文件编译通过
- 步骤2：test_safety_gateway.py ~202个测试全部通过
- 步骤3：test_e2e_security_flow.py测试通过
- 步骤4：test_desensitization.py测试通过
- 步骤5：安全合规测试通过
- 步骤6：性能基准测试通过（P99<5ms）
- 步骤7：SG相关全量测试通过率>95%
- 步骤8：输出修复验证汇总表
