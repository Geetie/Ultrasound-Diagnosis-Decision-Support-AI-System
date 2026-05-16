# SG安全网关轨道第八轮系统性审查报告

**审查日期**：2026-05-11
**审查范围**：SafetyGateway轨道全部源码（18文件~3600行）、客户端与集成层（6文件）、测试文件（13+文件~3500行）、配置与部署文件（8文件）、YAML规则文件（4文件）
**审查原则**：独立、客观、深入、批判性
**前置参考**：第七轮审查报告（sg-safety-gateway-audit-report-round7.md）

---

## 一、审查范围与方法

### 1.1 审查范围

| 类别 | 文件数 | 行数 | 审查深度 |
|------|--------|------|----------|
| 核心源码 `safety_gateway/` | 18 | ~3,600 | 逐文件静态分析 + 执行路径追踪 |
| 客户端与集成层 | 6 | ~800 | 关键路径审查 |
| 测试文件 | 13+ | ~3,500 | 覆盖率 + 质量审查 |
| 配置与部署 | 8 | ~200 | 一致性审查 |
| YAML规则文件 | 4 | ~200 | 异构冗余验证 |

### 1.2 审查方法

1. **R7遗留14项修复落地验证**：逐项代码级确认
2. **静态代码分析**：运行时崩溃风险、并发安全、资源泄漏、性能瓶颈
3. **执行路径追踪**：四阶段校验流程、10种降级场景、同意状态校验
4. **键名一致性全链路追踪**：服务端模型→客户端调用→集成层处理
5. **测试逻辑审查**：覆盖率、Mock正确性、隔离性、边界条件
6. **文档一致性比对**：对照5份核心设计文档
7. **规则异构冗余验证**：逐条对比desensitization_rules_safety.yaml与desensitization_rules.yaml
8. **部署配置审查**：Docker Compose、supervisor、启动脚本、OOM保护

---

## 二、R7遗留修复项验证结果

### 验证结果汇总

| 修复项 | 描述 | R7状态 | R8验证 | 验证说明 |
|--------|------|--------|--------|----------|
| SG-FIX-R7-01 | three_layer_desensitization.py键名不匹配 | ❌ | ⚠️ 部分修复 | `degraded`键名已修正（匹配客户端fallback），但仍遗漏服务端`degraded_mode`键；`allowed`键名已修正（匹配客户端fallback），但仍遗漏服务端`passed`键；`desensitization_degraded`参数已传递 |
| SG-FIX-R7-02 | SafetyGatewayClient并发竞态条件 | ❌ | ✅ 已修复 | `_lock = asyncio.Lock()`已保护`_consecutive_failures`和`_get_client()` |
| SG-FIX-R7-03 | medical_person_names.yaml known_combinations声明 | ❌ | ✅ 已修复 | 注释中已移除`known_combinations`声明 |
| SG-FIX-R7-04 | 独立姓名脱敏规则覆盖不足 | ❌ | ⚠️ 部分修复 | 新增`sg_patient_name_simple`规则，但仍要求"姓名/名为/名叫"前缀，主规则`patient_name_simple`无前缀要求，覆盖缺口仍存在 |
| SG-FIX-R7-05 | Dockerfile与supervisor HOME目录不一致 | ❌ | ✅ 已修复 | supervisor配置已改为`HOME="/app"` |
| SG-FIX-R7-06 | 客户端E4005响应不区分子类型 | ❌ | ❌ 未修复 | `_e4005_response()`仍返回`error_code="E4005"`，不区分E4005A/B/C |
| SG-FIX-R7-07 | supervisor startretries限制 | ❌ | ✅ 已修复 | `startretries=100` |
| SG-FIX-R7-08 | R6遗留7项类型注解风格 | ❌ | ⚠️ 部分修复 | `__init__.py`已添加公共API导出；`consent_checker.py`仅保留`NamedTuple`导入（可接受）；其余文件类型注解已基本统一为3.10+风格 |
| SG-FIX-R7-09 | 性能测试time.time()精度 | ❌ | ✅ 已修复 | 全部改用`time.perf_counter()` |
| SG-FIX-R7-10 | medical_person_names.yaml suffixes含"患者" | ❌ | ✅ 已修复 | suffixes已移除"患者" |
| SG-FIX-R7-11 | start_safety_gateway.sh缺set -e | ❌ | ✅ 已修复 | 脚本第2行已添加`set -e` |
| SG-FIX-R7-12 | supervisor日志轮转缺失 | ❌ | ✅ 已修复 | 已配置`stdout_logfile_maxbytes=50MB`和`stdout_logfile_backups=5` |
| SG-FIX-R6-01 | SGError→HTTP状态码映射 | ❌ | ✅ 已修复 | main.py第174-183行`sg_error_handler`已映射E4005A1/A2/B/C→503 |
| SG-FIX-R6-02 | 单例线程安全 | ❌ | ✅ 已修复 | contraindication.py第228-237行已使用double-checked locking |

**修复率**：14项中9项完全修复（64%），3项部分修复（21%），2项未修复（14%）

---

## 三、新发现的问题

### P0级（运行时安全缺陷）

#### SG-FIX-R8-01：three_layer_desensitization.py键名不匹配——服务端响应键名遗漏（R7-01深化）

- **严重程度**：P0 — SG服务端正常响应时降级检测结果丢失，`all_passed`判断错误
- **文件**：[three_layer_desensitization.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/three_layer_desensitization.py) 第127行、第199行
- **问题描述**：
  R7-01部分修复后，当前代码使用`check_result.get("degraded", False)`和`check_result.get("allowed", True)`，仅匹配客户端`_e4005_response()`返回的键名。但SG服务端正常响应（HTTP 200）使用不同的键名：
  
  | 场景 | 降级标记键名 | 校验通过键名 |
  |------|-------------|-------------|
  | SG服务端正常响应（SafetyCheckResponse） | `degraded_mode` | `passed` |
  | 客户端E4005 fallback（_e4005_response） | `degraded` | `allowed` |
  
  当前代码只检查`degraded`和`allowed`，导致：
  1. SG服务端返回`degraded_mode=True`时，`check_result.get("degraded", False)`返回`False`，降级状态丢失
  2. SG服务端返回`passed=False`时，`check_result.get("allowed", True)`返回`True`，安全违规未检测到
  
  **后果**：当SG服务端正常返回降级结果时，三层脱敏集成层无法正确感知，`all_passed`错误地保持`True`，降级输出可能被当作正常输出传递给前端。
- **设计文档参考**：04-安全合规.md §2 三重脱敏防线——三道防线防御目标不同，不可互相替代
- **修复建议**：
  ```python
  # 同时检查两个键名
  degraded_mode = check_result.get("degraded_mode", check_result.get("degraded", False))
  allowed = check_result.get("passed", check_result.get("allowed", True))
  ```
- **修复类型**：代码修复（关键安全缺陷）

#### SG-FIX-R8-02：desensitization_degraded语义错误——"modified"≠"降级"

- **严重程度**：P0 — 可能导致SG误判脱敏状态，触发错误的E4005A1降级路径
- **文件**：[three_layer_desensitization.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/three_layer_desensitization.py) 第169行、第187行
- **问题描述**：
  `process_full_pipeline()`中：
  ```python
  layer1_degraded = layer1_result.get("modified", False)  # 第169行
  ...
  desensitization_degraded=layer1_degraded,  # 第187行
  ```
  `modified`表示脱敏引擎做了替换（预期行为，说明第一道防线正常工作），不等于"降级"。`desensitization_degraded`的语义应为"脱敏服务处于降级/故障状态"。
  
  **后果**：当第一层脱敏引擎正常工作并成功替换敏感数据时（modified=True），代码错误地告诉SG"脱敏已降级"。如果此时SG第三层也检测到残留敏感数据（第二层遗漏），会触发E4005A1（脱敏降级+敏感数据→拒绝输出），但实际脱敏并未降级——第一层正常工作，只是第二层可能遗漏。
- **设计文档参考**：01-系统架构设计.md §3.3 三道防线严格职责分层——各防线独立工作
- **修复建议**：
  ```python
  # "modified"不等于"降级"，应传递实际的降级状态
  layer1_degraded = layer1_result.get("degraded", False)  # 第一层是否有降级
  ```
  或者在第一层结果中明确区分`modified`（正常替换）和`degraded`（服务降级）。
- **修复类型**：代码修复（语义错误导致安全路径误触发）

### P1级（功能缺陷/安全隐患）

#### SG-FIX-R8-03：SafetyGatewayClient发送的fusion_available字段被SG服务端忽略

- **严重程度**：P1 — 客户端与SG服务端对融合层可用性的判断可能不一致
- **文件**：[safety_gateway_client.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/services/safety_gateway_client.py) 第127行、[main.py SafetyCheckRequest](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/main.py) 第36-46行
- **问题描述**：
  客户端`check_output()`发送`fusion_available`字段，但SG服务端`SafetyCheckRequest`模型中没有此字段。Pydantic默认忽略额外字段，因此`fusion_available`被静默丢弃。SG服务端通过`HealthChecker.get_fusion_availability()`自主检测融合层状态。
  
  这实际上是正确的设计（SG不应信任客户端声称的融合层状态），但客户端代码具有误导性——发送了一个被忽略的字段，调用方可能误以为SG会使用此字段。
- **设计文档参考**：01-系统架构设计.md §3.4 SafetyGateway判定融合层是否可用须基于进程可达性
- **修复建议**：从客户端`check_output()`请求体中移除`fusion_available`字段，或添加注释说明此字段被SG忽略
- **修复类型**：代码清理

#### SG-FIX-R8-04：consent_checker.py Redis缓存TTL逻辑与设计文档不一致

- **严重程度**：P1 — SG的Redis降级窗口可能远小于设计文档要求的60s
- **文件**：[consent_checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/consent_checker.py) 第79行、[sg_config.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/sg_config.py) 第47行
- **问题描述**：
  设计文档要求"PostgreSQL不可用时降级为Redis只读缓存（TTL 60s）"，但`sg_consent_cache_ttl_seconds=10`。consent_checker.py第79行检查`if ttl > self._consent_cache_ttl_seconds`，当Redis key剩余TTL > 10s时拒绝使用缓存。
  
  如果主服务写入Redis时设置TTL=60s，则：
  - 刚写入时TTL≈60s → 60 > 10 → SG拒绝使用（缓存"太新鲜"）
  - 写入50s后TTL≈10s → 10 ≤ 10 → SG接受使用
  
  这意味着SG仅接受写入50-60秒后的缓存条目，Redis降级窗口实际仅约10s，远小于设计文档的60s。
- **设计文档参考**：01-系统架构设计.md §3.4 PostgreSQL不可用时降级为Redis只读缓存（TTL 60s）
- **修复建议**：
  1. 将`sg_consent_cache_ttl_seconds`改为60（与设计文档一致）
  2. 或修改TTL检查逻辑：仅检查TTL > 0（未过期），不限制最大TTL
- **修复类型**：配置修复/逻辑修复

#### SG-FIX-R8-05：desensitization_rules_safety.yaml独立姓名脱敏规则覆盖仍不足

- **严重程度**：P1 — 不带"姓名/名为/名叫"前缀的独立中文姓名在SafetyGateway层不会被脱敏
- **文件**：[desensitization_rules_safety.yaml](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/desensitization_rules_safety.yaml) 第47-52行
- **问题描述**：
  R7-04部分修复后新增了`sg_patient_name_simple`规则，但其pattern为`(?:姓名[：:]\\s*|名为\\s*|名叫\\s*)[\\u4e00-\\u9fa5]{2,4}(?![\\u4e00-\\u9fa5])`，仍要求"姓名/名为/名叫"前缀。而主规则`patient_name_simple`的pattern为`^([\u4e00-\u9fa5]{2,4})(?=，|,|$)`，匹配独立2-4字中文姓名无需任何前缀。
  
  不带任何前缀或称谓的独立中文姓名（如"张三，"）在SafetyGateway层仍不会被脱敏。
- **设计文档参考**：04-安全合规.md §2 SafetyGateway结构化身份模式拦截为第三道防线（快速正则匹配兜底）
- **修复建议**：在`desensitization_rules_safety.yaml`中增加一条与主规则`patient_name_simple`对应的规则，匹配独立中文姓名
- **修复类型**：规则补充

#### SG-FIX-R8-06：SafetyGatewayClient E4005响应不区分子类型（R7-06遗留）

- **严重程度**：P1 — 客户端丢失E4005A/B/C诊断信息，前端无法区分不同不可用原因
- **文件**：[safety_gateway_client.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/services/safety_gateway_client.py) 第285-293行
- **问题描述**：`_e4005_response()`统一返回`error_code="E4005"`，不区分E4005A/B/C。服务端已正确定义子类型（R6-01已修复，E4005A1/A2/B/C→503），但客户端未从SG响应中提取具体错误码。
- **修复建议**：从SG服务端503响应体中提取`code`字段，客户端返回对应的E4005A/B/C
- **修复类型**：代码增强

### P2级（代码质量/风格一致性）

#### SG-FIX-R8-07：three_layer_desensitization.py类型注解风格混用

- **严重程度**：P2 — 代码风格不一致
- **文件**：[three_layer_desensitization.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/three_layer_desensitization.py)
- **问题描述**：仍使用`from typing import Any`和`from enum import Enum`，与safety_gateway/包内其他文件的Python 3.10+风格不完全一致
- **修复类型**：代码风格

#### SG-FIX-R8-08：sg_db.py使用Python 3.9风格类型注解

- **严重程度**：P2 — `from typing import AsyncGenerator`可使用`AsyncGenerator`原生语法
- **文件**：[sg_db.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/sg_db.py) 第3行
- **修复类型**：代码风格

#### SG-FIX-R8-09：circuit_breaker.py _update_state()线程安全声明需文档化

- **严重程度**：P2 — 当前asyncio单线程模型下安全，但缺乏显式约束文档
- **文件**：[circuit_breaker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/circuit_breaker.py) 第70-86行
- **问题描述**：`_update_state()`在`state`/`is_available`属性中被调用，这些属性可能在`record_failure`/`record_success`持有`_lock`时被并发访问。代码注释声明"asyncio单线程模型下安全"，但若未来引入多线程需添加同步保护。建议在类文档中添加显式约束声明。
- **修复类型**：文档增强

---

## 四、代码质量评估结果

### 4.1 健壮性评估

| 审查项 | 评估 | 说明 |
|--------|------|------|
| 并发安全 | ✅ 良好 | R7-02已修复，`_lock`保护`_consecutive_failures`和`_get_client()`；contraindication.py/threshold.py已使用double-checked locking |
| 资源泄漏 | ✅ 良好 | lifespan中正确关闭Redis连接池、checker、threshold订阅；client.py `close()`正确关闭httpx客户端 |
| 异常处理完整性 | ✅ 良好 | R6-01已修复，`sg_error_handler`映射E4005A1/A2/B/C→503；perform_full_check()异常路径完整 |
| 熔断器状态转换 | ✅ 良好 | record_failure/record_success均在_lock保护内 |
| 规则热更新回滚 | ✅ 良好 | reload_rules()失败时恢复old_rules+old_patterns |
| 单例模式线程安全 | ✅ 良好 | 所有单例均使用double-checked locking（checker/consent_checker/contraindication/threshold） |
| 三层脱敏集成 | ⚠️ 缺陷 | 键名不匹配（SG-FIX-R8-01）+ 语义错误（SG-FIX-R8-02） |

### 4.2 可维护性评估

| 审查项 | 评估 | 说明 |
|--------|------|------|
| 模块职责边界 | ✅ 良好 | safety_gateway/未导入app.或fusion.模块，进程隔离正确 |
| 依赖方向 | ✅ 良好 | 无循环依赖 |
| 配置集中度 | ✅ 良好 | 所有可调参数在sg_config.py中 |
| 错误类型层次 | ✅ 良好 | errors.py 18种异常类型，E4005系列与设计文档对齐 |
| 公共API导出 | ✅ 良好 | __init__.py已导出核心类型 |
| 类型注解一致性 | ⚠️ 基本一致 | sg_db.py/three_layer_desensitization.py仍有少量旧式导入 |

### 4.3 性能评估

| 审查项 | 评估 | 说明 |
|--------|------|------|
| P99延迟基准（HTTP通信） | ✅ 良好 | 500样本滑动窗口，warning=4ms/critical=5ms |
| P99延迟基准（端到端处理） | ⚠️ 缺失 | 仅有HTTP通信开销监控，缺少含内部处理的端到端P99<50ms监控 |
| PG查询优化 | ✅ 良好 | consent_checker.py单条查询+索引(patient_id, status) |
| Redis连接池隔离 | ✅ 良好 | redis_pool.py强制替换URL为DB 1 |
| 内存预算监控 | ✅ 良好 | health.py多平台内存统计，512MB预算 |
| 性能测试精度 | ✅ 良好 | R7-09已修复，全部使用time.perf_counter() |

---

## 五、代码风格合规性评估结果

### 5.1 类型注解风格

| 文件 | 当前风格 | 评估 |
|------|----------|------|
| checker.py | Python 3.10+ | ✅ |
| main.py | Python 3.10+ | ✅ |
| desensitization.py | Python 3.10+ | ✅ |
| threshold.py | Python 3.10+ | ✅ |
| health.py | Python 3.10+ | ✅ |
| circuit_breaker.py | Python 3.10+ | ✅ |
| errors.py | Python 3.10+ | ✅ |
| validation_result.py | Python 3.10+ | ✅ |
| sg_types.py | Python 3.10+ | ✅ |
| consent_checker.py | Python 3.10+ | ✅ |
| sg_config.py | Python 3.10+ | ✅ |
| sg_validator.py | Python 3.10+ | ✅ |
| redis_pool.py | Python 3.10+ | ✅ |
| contraindication.py | Python 3.10+ | ✅ |
| degraded_output.py | Python 3.10+ | ✅ |
| sg_models.py | Python 3.10+ | ✅ |
| sg_db.py | **混合** | ⚠️ `from typing import AsyncGenerator` |
| safety_gateway_client.py | Python 3.10+ | ✅ |
| three_layer_desensitization.py | **混合** | ⚠️ `from typing import Any` |

### 5.2 命名约定

| 审查项 | 评估 |
|--------|------|
| 类名PascalCase | ✅ |
| 函数名snake_case | ✅ |
| 常量UPPER_SNAKE | ✅ |
| 私有方法_前缀 | ✅ |

---

## 六、功能正确性验证结果

### 6.1 进程隔离验证（P0）

| 验证项 | 设计要求 | 当前实现 | 结果 |
|--------|----------|----------|------|
| 独立uvicorn实例 | SG以独立进程运行 | main.py第400-408行uvicorn.run独立启动 | ✅ |
| HTTP localhost通信 | 主服务通过HTTP调用SG | client.py通过httpx AsyncClient调用 | ✅ |
| 主服务崩溃时独立存活 | 返回E4005错误码 | docker-compose.dev.yml独立容器 + supervisor看门狗 | ✅ |
| Docker Compose depends_on | api依赖safety-gateway健康检查 | docker-compose.dev.yml第75-76行condition: service_healthy | ✅ |
| OOM保护 | oom_score_adj=-1000 | docker-compose.dev.yml第130行 + start_safety_gateway.sh第6行 | ✅ |
| 独立Redis连接池(DB 1) | 与主服务DB 0隔离 | redis_pool.py第56行强制替换URL为/1 | ✅ |

### 6.2 四阶段校验流程验证（P0）

| 阶段 | 设计要求 | 当前实现 | 结果 |
|------|----------|----------|------|
| 阶段1：同意状态校验 | 入口快速拒绝 + 输出前直查PG | checker.py第307-327行(快速拒绝) + 第378-475行(PG直查) | ✅ |
| 阶段2：脱敏校验 | 结构化身份模式拦截 + 医疗人名词典 | checker.py第502-574行 | ✅ |
| 阶段3：禁忌+阈值校验 | 禁忌规则引擎 + 置信度阈值 | checker.py第576-700行 | ✅ |
| 阶段4：一致性校验 | 分级/发现/置信度差异检测 | checker.py第702-735行 | ✅ |
| 执行顺序 | consent→edge→desensitization→contraindication→threshold→consistency | perform_full_check()第267-376行 | ✅ |

### 6.3 降级场景验证（P0）

| 降级场景 | DegradedReason | 代码位置 | 结果 |
|----------|----------------|----------|------|
| 1. 敏感数据检测 | SENSITIVE_DATA_DETECTED | checker.py第563-574行 | ✅ |
| 2. DB超时 | DB_TIMEOUT | consent_checker.py第198-205行 | ✅ |
| 3. 禁忌命中 | CONTRAINDICATION_HIT | checker.py第642-662行 | ✅ |
| 4. 置信度低于阈值 | CONFIDENCE_BELOW_THRESHOLD | checker.py第664-682行 | ✅ |
| 5. 融合层不可用 | FUSION_UNAVAILABLE | checker.py第684-698行 | ✅ |
| 6. 组合故障 | COMBINED_FAILURE | checker.py第619-640行 | ✅ |
| 7. 边缘断连 | EDGE_DISCONNECTED | checker.py第485-500行 | ✅ |
| 8. 融合层不可用+敏感数据 | FUSION_UNAVAILABLE_SENSITIVE_DETECTED | checker.py第521-535行 | ✅ |
| 9. 脱敏降级+敏感数据 | DESENSITIZATION_DEGRADED_SENSITIVE_DETECTED | checker.py第537-555行 | ✅ |
| 10. SG不可用+患者撤回 | SG_UNAVAILABLE_CONSENT_WITHDRAWN | checker.py第392-404行 | ✅ |

**⚠️ 关键问题**：降级场景在SG服务端实现正确，但three_layer_desensitization.py中第三层降级检测因键名不匹配仍可能失效（SG-FIX-R8-01），导致10种降级场景在三层脱敏集成层无法被正确感知。

### 6.4 E4005系列错误码验证（P0）

| 错误码 | 触发条件 | 服务端代码 | 服务端HTTP状态码 | 客户端处理 | 结果 |
|--------|----------|------------|------------------|------------|------|
| E4005 | SG不可用（客户端通用） | — | — | client.py第285-293行 | ✅ |
| E4005A1 | 脱敏降级+SG检测到敏感数据 | errors.py第144-155行 | 503 | 客户端不区分子类型 | ⚠️ SG-FIX-R8-06 |
| E4005A2 | SG不可用+患者撤回同意 | errors.py第157-167行 | 503 | 客户端不区分子类型 | ⚠️ SG-FIX-R8-06 |
| E4005B | PG不可用+Redis缓存未命中 | errors.py第169-172行 | 503 | 客户端不区分子类型 | ⚠️ SG-FIX-R8-06 |
| E4005C | SG+融合层同时不可用 | errors.py第175-178行 | 503 | 客户端不区分子类型 | ⚠️ SG-FIX-R8-06 |

**服务端修复确认**：R6-01已修复，`sg_error_handler`正确映射E4005A1/A2/B/C→503。
**客户端遗留**：R7-06仍未修复，客户端不区分子类型。

### 6.5 安全合规关键项验证（P0）

| 验证项 | 设计要求 | 当前实现 | 结果 |
|--------|----------|----------|------|
| 三重脱敏防线 | SG为第三道防线 | three_layer_desensitization.py实现三层 | ⚠️ 第三层降级检测可能失效（SG-FIX-R8-01） |
| 规则异构冗余 | 独立编写规则集 | 11条SG规则 vs 16条主规则，逐条对比确认实质性差异 | ✅ |
| 同意状态直查PG | 正常路径不写Redis缓存 | consent_checker.py无_cache_consent_to_redis方法 | ✅ |
| 两阶段同意校验 | 入口快速拒绝 + 输出前直查PG | checker.py第307-327行 + 第378-475行 | ✅ |
| A级安全标准 | 先校验后输出，零容忍 | perform_full_check()四阶段全部通过才PASSED | ✅ |
| 禁忌校验fail-closed | 异常时默认存在禁忌 | client.py返回has_contraindication=True | ✅ |
| 降级输出仅白名单 | 过滤diagnostic_conclusion，仅保留observations | apply_degraded_output()白名单过滤 | ✅ |
| OOM保护 | oom_score_adj=-1000 | docker-compose.dev.yml + start_safety_gateway.sh | ✅ |
| consent_status_at_output | SG输出前直查PG获取同意状态快照 | SafetyCheckResponse第51行 | ✅ |
| NER方案 | 纯正则+词典，不依赖ML NER | desensitization.py使用正则+医疗人名词典 | ✅ |
| 内存预算 | ≤512MB | health.py监控 + sg_memory_budget_mb=512 | ✅ |
| 独立姓名脱敏覆盖 | 三道防线覆盖一致 | SG规则仍要求前缀，主规则不要求 | ⚠️ SG-FIX-R8-05 |
| Redis降级窗口 | TTL 60s | sg_consent_cache_ttl_seconds=10，实际窗口约10s | ⚠️ SG-FIX-R8-04 |

---

## 七、测试逻辑审查结果

### 7.1 测试覆盖率

| 指标 | 要求 | 实际 | 评估 |
|------|------|------|------|
| 测试用例数 | 80-120 | ~202 | ✅ 超出要求 |
| 降级场景覆盖 | 7种 | 10种 | ✅ 完整 |
| E4005系列错误码 | E4005/A/B/C | 全部有测试 | ✅ 完整 |
| 进程隔离测试 | Gate 0 | TestServiceIsolation + TestMainServiceCrashScenario | ✅ 存在 |
| P99延迟基准 | <5ms | TestSafetyGatewayPerformance | ✅ 存在 |
| OOM保护测试 | oom_score_adj | TestOOMProtectionScenario | ✅ 存在 |

### 7.2 测试盲区

| 盲区 | 风险 | 建议 |
|------|------|------|
| three_layer_desensitization.py三层集成端到端测试 | **高** | 补充三层脱敏端到端测试，验证服务端`degraded_mode`/`passed`键名正确传递 |
| SafetyGatewayClient E4005子类型提取 | **高** | 补充客户端从503响应中提取E4005A/B/C的测试 |
| desensitization_degraded语义正确性 | **高** | 补充测试验证"modified=True"不等于"degraded=True" |
| 并发请求场景 | 中 | 添加多协程并发测试验证锁正确性 |
| 端到端延迟P99<50ms | 中 | 添加含HTTP调用的端到端延迟测试 |
| Redis降级窗口实际值 | 中 | 添加测试验证sg_consent_cache_ttl_seconds与设计文档60s一致 |

### 7.3 性能测试评估

| 项目 | 评估 |
|------|------|
| time.perf_counter() | ✅ R7-09已修复，全部使用perf_counter() |
| 预热机制 | ✅ 50次预热迭代 |
| GC干扰控制 | ✅ gc.disable()/gc.enable() |
| HTTP端到端测试 | ✅ TestHTTPEndToEndLatency（需运行SG服务） |
| 端到端处理延迟P99<50ms | ❌ 仅有HTTP通信开销P99<5ms测试，缺少含内部处理的端到端P99<50ms测试 |

---

## 八、与参考文档的偏差分析

### 8.1 P0文档偏差：01-系统架构设计.md

| 需求项 | 设计要求 | 当前实现 | 偏差等级 |
|--------|----------|----------|----------|
| 进程隔离 | 独立进程，HTTP localhost通信 | ✅ 实现 | 无偏差 |
| E4005子码差异化 | E4005A/B/C应有不同前端提示 | 服务端已修复（503），客户端不区分 | **P1** — SG-FIX-R8-06 |
| 三重脱敏防线 | SG为第三道防线 | 第三层降级检测因键名不匹配可能失效 | **P0** — SG-FIX-R8-01 |
| 端到端处理延迟P99 | <50ms | 仅有HTTP通信开销监控，缺少端到端监控 | **P2** |
| OOM保护 | oom_score_adj=-1000 | ✅ 实现 | 无偏差 |
| A级安全标准 | 先校验后输出，零容忍 | ✅ 实现 | 无偏差 |
| 同意状态校验 | 直查PG，正常路径禁用缓存 | ✅ 实现 | 无偏差 |
| HTTP通信P99 | <5ms | ✅ 监控机制完整 | 无偏差 |
| 健康检查 | /health端点，4s探测，1s超时，3次失败 | ✅ docker-compose.dev.yml配置正确 | 无偏差 |
| supervisor看门狗 | 崩溃后自动重启≤5s | ✅ startretries=100 | 无偏差 |
| Redis降级窗口 | TTL 60s | sg_consent_cache_ttl_seconds=10，实际窗口约10s | **P1** — SG-FIX-R8-04 |

### 8.2 P0文档偏差：04-安全合规.md

| 需求项 | 设计要求 | 当前实现 | 偏差等级 |
|--------|----------|----------|----------|
| 三重脱敏防线 | 三道防线防御目标不同，不可互相替代 | 第三层降级检测可能失效 + desensitization_degraded语义错误 | **P0** — SG-FIX-R8-01 + R8-02 |
| 规则异构冗余 | 独立编写规则集 | ✅ 逐条对比确认 | 无偏差 |
| 双重降级保障 | 双重缺失时拒绝输出 | E4005A1错误码实现 | 无偏差 |
| 患者同意管理 | 两阶段校验 | ✅ 实现 | 无偏差 |
| 独立姓名脱敏覆盖 | SafetyGateway结构化身份模式拦截 | 无前缀独立中文姓名覆盖仍不足 | **P1** — SG-FIX-R8-05 |

### 8.3 P1文档偏差：06-开发环境与进度规划.md

| 需求项 | 设计要求 | 当前实现 | 偏差等级 |
|--------|----------|----------|----------|
| 代码组织 | 独立Python package safety_gateway/ | ✅ | 无偏差 |
| 代码量 | 2500-3000行 | ~3600行（超出20%） | P2（合理偏差，降级场景覆盖更全面） |
| 测试用例数 | 80-120个 | ~202个（超出68%） | P2（超出可接受） |
| 端口配置 | SAFETY_GATEWAY_PORT=9001 | ✅ | 无偏差 |
| Docker Compose | depends_on + 健康检查 | ✅ | 无偏差 |
| Gate 0验证 | 主服务崩溃时独立返回E4005 | ✅ | 无偏差 |

---

## 九、SafetyGateway轨道修复项显式声明

以下修复项**明确属于SafetyGateway轨道**，需由SG轨道负责修复：

### P0级修复项（必须修复，存在运行时安全缺陷）

| 修复项 | 描述 | 文件 | 修复类型 |
|--------|------|------|----------|
| **SG-FIX-R8-01** | three_layer_desensitization.py键名不匹配——服务端响应`degraded_mode`/`passed`键名遗漏，SG服务端正常返回降级结果时三层脱敏集成层无法正确感知 | `app/security/three_layer_desensitization.py` 第127行、第199行 | 代码修复（关键安全缺陷） |
| **SG-FIX-R8-02** | desensitization_degraded语义错误——`modified`（脱敏引擎正常替换）≠`degraded`（服务降级），可能导致SG误判脱敏状态触发错误E4005A1 | `app/security/three_layer_desensitization.py` 第169行、第187行 | 代码修复（语义错误） |

### P1级修复项（应当修复，存在功能缺陷或安全隐患）

| 修复项 | 描述 | 文件 | 修复类型 |
|--------|------|------|----------|
| **SG-FIX-R8-03** | SafetyGatewayClient发送的`fusion_available`字段被SG服务端SafetyCheckRequest忽略，客户端代码具有误导性 | `app/services/safety_gateway_client.py` 第127行 | 代码清理 |
| **SG-FIX-R8-04** | consent_checker.py Redis缓存TTL逻辑与设计文档不一致——`sg_consent_cache_ttl_seconds=10`导致实际降级窗口约10s，远小于设计文档要求的60s | `safety_gateway/consent_checker.py` 第79行、`safety_gateway/sg_config.py` 第47行 | 配置修复/逻辑修复 |
| **SG-FIX-R8-05** | desensitization_rules_safety.yaml独立姓名脱敏规则覆盖仍不足——`sg_patient_name_simple`仍要求前缀，不带前缀的独立中文姓名不会被SG脱敏 | `desensitization_rules_safety.yaml` 第47-52行 | 规则补充 |
| **SG-FIX-R8-06** | SafetyGatewayClient E4005响应不区分子类型（R7-06遗留）——客户端丢失E4005A/B/C诊断信息 | `app/services/safety_gateway_client.py` 第285-293行 | 代码增强 |

### P2级修复项（建议修复，提升质量）

| 修复项 | 描述 | 文件 | 修复类型 |
|--------|------|------|----------|
| **SG-FIX-R8-07** | three_layer_desensitization.py类型注解风格混用 | `app/security/three_layer_desensitization.py` | 代码风格 |
| **SG-FIX-R8-08** | sg_db.py使用Python 3.9风格类型注解`from typing import AsyncGenerator` | `safety_gateway/sg_db.py` 第3行 | 代码风格 |
| **SG-FIX-R8-09** | circuit_breaker.py _update_state()线程安全声明需文档化 | `safety_gateway/circuit_breaker.py` 第70-86行 | 文档增强 |

---

## 十、审查结论

### 10.1 总体评估

SafetyGateway轨道在第七轮审查后修复了14项中的9项（64%修复率），本轮审查发现9项问题，其中2项P0级、4项P1级、3项P2级。

**与前七轮对比**：

| 轮次 | P0级 | P1级 | P2级 | 修复率 |
|------|------|------|------|--------|
| R4 | 2 | 4 | 4 | 70% |
| R5 | 2 | 5 | 2 | 100%(R6验证) |
| R6 | 0 | 3 | 5 | 12.5%(R7验证) |
| R7 | 2 | 5 | 5 | 64%(本轮验证) |
| **R8** | **2** | **4** | **3** | — |

**关键进展**：
1. R6-01（SGError→HTTP状态码映射）和R6-02（单例线程安全）终于修复
2. 部署配置（supervisor/Dockerfile/启动脚本）问题全部修复
3. 性能测试精度问题已修复

**关键风险**：
1. **SG-FIX-R8-01**（P0）：三层脱敏第三层降级检测仍可能失效。R7-01部分修复后，键名从`degraded_mode`改为`degraded`，匹配了客户端fallback响应但遗漏了SG服务端正常响应的`degraded_mode`键名。这是一个"修复了A面但暴露了B面"的典型问题。
2. **SG-FIX-R8-02**（P0）：`desensitization_degraded`语义错误。`modified`（脱敏引擎正常替换）被错误地等同于`degraded`（服务降级），可能导致SG误判脱敏状态。

### 10.2 修复优先级建议

1. **立即修复（本迭代）**：
   - SG-FIX-R8-01（三层脱敏键名不匹配——同时检查`degraded_mode`/`degraded`和`passed`/`allowed`）
   - SG-FIX-R8-02（desensitization_degraded语义修正——`modified`≠`degraded`）
   - SG-FIX-R8-06（客户端E4005子类型区分，R7-06遗留）

2. **本迭代修复**：
   - SG-FIX-R8-04（Redis缓存TTL与设计文档对齐）
   - SG-FIX-R8-05（独立姓名脱敏规则补充）

3. **下迭代修复**：
   - SG-FIX-R8-03（fusion_available字段清理）
   - SG-FIX-R8-07~R8-09（代码风格和文档增强）

### 10.3 自我反思

| 维度 | 反思 |
|------|------|
| R7修复验证 | R7的14项修复9项完全修复（64%），较R6→R7的12.5%有显著提升。但R7-01的"部分修复"暴露了审查方法论的不足——仅验证修复是否改变了代码，未验证修复是否完整覆盖所有场景。 |
| 新问题发现 | 本轮2项P0级问题均是对R7-01的深化分析结果。R7-01描述"键名不匹配"但仅关注了客户端fallback的键名，未追踪SG服务端正常响应的键名。R8通过全链路键名追踪发现了更深层的问题。 |
| desensitization_degraded语义 | R7未发现`modified`≠`degraded`的语义错误。这是因为R7审查聚焦于键名匹配，未深入分析参数传递的语义正确性。R8扩展了审查维度，从"键名是否匹配"深入到"语义是否正确"。 |
| 审查方法论改进 | 建议后续审查增加"全链路语义追踪"维度：不仅追踪数据从A到B是否正确传递，还要验证数据在A处产生时的语义是否与B处消费时的语义一致。 |
