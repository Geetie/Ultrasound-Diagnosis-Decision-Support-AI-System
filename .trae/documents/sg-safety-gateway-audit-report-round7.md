# SG安全网关轨道第七轮系统性审查报告

**审查日期**：2026-05-10
**审查范围**：SafetyGateway轨道全部源码（17文件~3500行）、客户端与集成层（6文件）、测试文件（13+文件~3200行）、配置与部署文件（8文件）、YAML规则文件（4文件）
**审查原则**：独立、客观、深入、批判性
**前置参考**：第六轮审查报告（sg-safety-gateway-audit-report-round6.md）

---

## 一、审查范围与方法

### 1.1 审查范围

| 类别 | 文件数 | 行数 | 审查深度 |
|------|--------|------|----------|
| 核心源码 `safety_gateway/` | 17 | ~3,500 | 逐文件静态分析 + 执行路径追踪 |
| 客户端与集成层 | 6 | ~800 | 关键路径审查 |
| 测试文件 | 13+ | ~3,200 | 覆盖率 + 质量审查 |
| 配置与部署 | 8 | ~200 | 一致性审查 |
| YAML规则文件 | 4 | ~200 | 异构冗余验证 |

### 1.2 审查方法

1. **R6遗留8项修复落地验证**：逐项代码级确认
2. **静态代码分析**：运行时崩溃风险、并发安全、资源泄漏、性能瓶颈
3. **执行路径追踪**：四阶段校验流程、10种降级场景、同意状态校验
4. **测试逻辑审查**：覆盖率、Mock正确性、隔离性、边界条件
5. **文档一致性比对**：对照5份核心设计文档
6. **规则异构冗余验证**：逐条对比desensitization_rules_safety.yaml与desensitization_rules.yaml
7. **部署配置审查**：Docker Compose、supervisor、启动脚本、OOM保护

---

## 二、R6遗留修复项验证结果

### 验证结果汇总

| 修复项 | 描述 | 状态 | 验证说明 |
|--------|------|------|----------|
| SG-FIX-R6-01 | main.py SGError异常未映射HTTP状态码 | ❌ NOT FIXED | main.py第268行仍为`except SGError: raise`，未添加FastAPI异常处理器，E4005B/E4005C仍返回HTTP 500 |
| SG-FIX-R6-02 | contraindication.py和threshold.py单例获取函数无线程安全保护 | ❌ NOT FIXED | contraindication.py第225-229行、threshold.py第549-553行仍使用简单全局变量检查，无double-checked locking |
| SG-FIX-R6-03 | checker.py `_check_desensitization_phase()`参数类型注解与实际不符 | ✅ FIXED | checker.py第518行`ctx: _PhaseContext`、第519行`start_time: datetime`，类型注解已修正 |
| SG-FIX-R6-04 | redis_pool.py使用Python 3.9风格类型注解 | ❌ NOT FIXED | redis_pool.py第15行仍使用`from typing import Optional, Any`，第25行`Optional[redis.Redis]` |
| SG-FIX-R6-05 | sg_config.py和sg_validator.py使用Python 3.9风格类型注解 | ❌ NOT FIXED | sg_config.py第1行`from typing import List`、第15行`List[str]`；sg_validator.py第1行`from typing import List`、第7行`List[str]` |
| SG-FIX-R6-06 | consent_checker.py使用Python 3.9风格类型注解 | ❌ NOT FIXED | consent_checker.py第16行`from typing import NamedTuple, Optional`、第40行`Optional[ConsentStatus]` |
| SG-FIX-R6-07 | three_layer_desensitization.py类型注解风格混用 | ❌ NOT FIXED | 第9行`from typing import Dict, Any, Optional, Tuple`，与第107行`str | None`混用 |
| SG-FIX-R6-08 | __init__.py为空文件，未导出公共API | ❌ NOT FIXED | __init__.py仍为空文件（0行） |

**修复率**：1/8 已修复（12.5%），R6遗留8项中仅1项（SG-FIX-R6-03）已落地 ⚠️

---

## 三、新发现的问题

### P0级（运行时安全缺陷）

#### SG-FIX-R7-01：three_layer_desensitization.py键名不匹配导致三层脱敏第三层失效

- **严重程度**：P0 — 三层脱敏第三层（SafetyGateway层）的降级检测和结果判断完全失效
- **文件**：[three_layer_desensitization.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/three_layer_desensitization.py) 第120行、第183-187行
- **问题描述**：
  1. 第120行`check_result.get("degraded_mode", False)`：`safety_gateway_client.check_output()`返回的键名是`degraded`（见client.py `_e4005_response`第234行），不是`degraded_mode`。导致降级模式永远不会被检测到。
  2. 第183-187行`all_passed`判断：对第三层检查`check_result.passed`，但SG客户端返回的键是`allowed`而非`passed`。导致第三层的`passed`永远默认为`True`，无法正确反映SG校验是否通过。
  3. 第112-118行`process_safety_gateway_layer`调用`check_output()`时未传递`desensitization_degraded`参数，导致即使第一层或第二层脱敏已降级，第三层SG也不知道，无法触发E4005A降级路径。
- **设计文档参考**：04-安全合规.md §2 三重脱敏防线——三道防线防御目标不同，不可互相替代
- **修复建议**：
  1. 将`degraded_mode`改为`degraded`
  2. 将`check_result.get("passed", True)`改为`check_result.get("allowed", True)`
  3. 传递`desensitization_degraded`参数给`check_output()`
- **修复类型**：代码修复（关键安全缺陷）

#### SG-FIX-R7-02：SafetyGatewayClient._consecutive_failures并发竞态条件

- **严重程度**：P0 — 并发场景下失败计数可能丢失，导致fast-fail机制失效
- **文件**：[safety_gateway_client.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/services/safety_gateway_client.py) 第21行、第83行、第86行、第100行、第110行
- **问题描述**：`_consecutive_failures`是普通`int`，多个协程并发调用`check_output()`时，`+= 1`不是原子操作。一个协程成功后将计数器重置为0，会抹掉另一个并发协程刚记录的失败计数。同时`_get_client()`也存在竞态——两个协程可能同时创建AsyncClient，导致第一个客户端泄漏。
- **设计文档参考**：01-系统架构设计.md §3.4 SafetyGateway为安全关键组件
- **修复建议**：使用`asyncio.Lock`保护`_consecutive_failures`和`_get_client()`的共享状态
- **修复类型**：代码修复

### P1级（功能缺陷/安全隐患）

#### SG-FIX-R7-03：medical_person_names.yaml声明known_combinations字段但实际缺失

- **严重程度**：P1 — 文件注释声明了`known_combinations`字段但数据中不存在，若代码依赖此字段则功能缺失
- **文件**：[medical_person_names.yaml](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/medical_person_names.yaml) 第10-11行
- **问题描述**：文件头部注释明确声明词典格式包含`known_combinations: 已知的人名+称谓组合（高优先级匹配）`，但实际YAML数据中不存在此字段。当前代码(desensitization.py)通过`surnames × suffixes`笛卡尔积生成组合，未使用`known_combinations`，因此不影响运行时功能，但注释与数据不一致。
- **修复建议**：删除注释中的`known_combinations`声明，或补充该字段数据
- **修复类型**：数据修复

#### SG-FIX-R7-04：desensitization_rules_safety.yaml独立姓名脱敏规则覆盖不足

- **严重程度**：P1 — 不带前缀的独立中文姓名在SafetyGateway层不会被脱敏，形成第三道防线缺口
- **文件**：[desensitization_rules_safety.yaml](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/desensitization_rules_safety.yaml)
- **问题描述**：主规则有`patient_name_simple`（匹配独立的2-4字中文姓名，无前缀要求），而Safety规则的`sg_patient_name_standalone`要求必须有"患者/病人/被检者"前缀。不带任何前缀的独立中文姓名在SafetyGateway层不会被脱敏。
- **设计文档参考**：04-安全合规.md §2 三重脱敏防线——SafetyGateway结构化身份模式拦截为第三道防线（快速正则匹配兜底）
- **修复建议**：在`desensitization_rules_safety.yaml`中增加一条无需前缀的独立中文姓名脱敏规则（与主规则的`patient_name_simple`对应），确保三道防线覆盖一致
- **修复类型**：规则补充

#### SG-FIX-R7-05：Dockerfile与supervisor配置HOME目录不一致

- **严重程度**：P1 — sguser的HOME目录在Dockerfile中为`/app`，在supervisor配置中被覆盖为`/home/sguser`，但`/home/sguser`目录从未创建
- **文件**：[Dockerfile.safety-gateway.dev](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/Dockerfile.safety-gateway.dev) 第13行、[safety-gateway.conf](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/deploy/supervisor/safety-gateway.conf) 第19行
- **问题描述**：若任何代码尝试写入`$HOME`（如`.cache`、`.config`），将因目录不存在而失败
- **修复建议**：将supervisor配置中的`HOME="/home/sguser"`改为`HOME="/app"`，或在Dockerfile中`mkdir -p /home/sguser && chown sguser:sguser /home/sguser`
- **修复类型**：配置修复

#### SG-FIX-R7-06：SafetyGatewayClient E4005响应不区分子类型

- **严重程度**：P1 — 客户端丢失E4005A/B/C诊断信息，前端无法区分不同不可用原因
- **文件**：[safety_gateway_client.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/services/safety_gateway_client.py) 第229-236行
- **问题描述**：SG服务端errors.py定义了E4005A（脱敏降级+SG不可用）、E4005B（数据库不可用）、E4005C（组合不可用），但客户端的`_e4005_response()`统一返回`error_code="E4005"`，不区分子类型。与SG-FIX-R6-01（服务端未映射HTTP状态码）形成叠加问题。
- **设计文档参考**：01-系统架构设计.md §3.4 E4005错误码子码拆分——E4005A/B/C应有不同的前端提示
- **修复建议**：从SG服务端响应中提取具体错误码，客户端返回对应的E4005A/B/C
- **修复类型**：代码增强

#### SG-FIX-R7-07：supervisor startretries=5限制安全关键组件重启次数

- **严重程度**：P1 — SafetyGateway连续5次启动失败后supervisor放弃重启，安全关键组件永久不可用
- **文件**：[safety-gateway.conf](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/deploy/supervisor/safety-gateway.conf)
- **问题描述**：设计文档要求supervisor看门狗在崩溃后≤5s自动重启，但`startretries=5`意味着连续5次启动失败后supervisor将放弃。对于安全关键组件，应考虑更高重试次数或配合`exitcodes=`精确控制。
- **设计文档参考**：01-系统架构设计.md §3.4 SafetyGateway自身进程崩溃由supervisor看门狗自动重启≤5s兜底
- **修复建议**：增加`startretries`至更大值（如100），或配合`exitcodes=0,2`仅对特定退出码放弃重启
- **修复类型**：配置修复

### P2级（代码质量/风格一致性）

#### SG-FIX-R7-08：R6遗留7项类型注解风格修复均未落地

- **严重程度**：P2 — 代码风格不一致
- **文件**：redis_pool.py、sg_config.py、sg_validator.py、consent_checker.py、three_layer_desensitization.py、__init__.py
- **问题描述**：R6提出的SG-FIX-R6-04~R6-08（共5项类型注解风格+1项__init__.py导出）均未修复，与R6-01（HTTP状态码映射）、R6-02（单例线程安全）共7项遗留
- **修复建议**：统一使用Python 3.10+风格，在__init__.py中添加公共API导出
- **修复类型**：代码风格

#### SG-FIX-R7-09：性能测试使用time.time()而非time.perf_counter()

- **严重程度**：P2 — Windows上time.time()精度仅15.6ms，无法准确测量P99<5ms的延迟
- **文件**：[test_safety_gateway_performance.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/benchmark/test_safety_gateway_performance.py) 第79行
- **问题描述**：延迟测试使用`time.time()`，而CI吞吐量测试正确使用了`time.perf_counter()`。在Windows开发环境下，`time.time()`精度不足以验证P99<5ms基准。
- **修复建议**：统一使用`time.perf_counter()`
- **修复类型**：代码修复

#### SG-FIX-R7-10：medical_person_names.yaml suffixes包含"患者"

- **严重程度**：P2 — "患者"不是医疗人员称谓，可能导致"张患者"被误匹配为医疗人员姓名
- **文件**：[medical_person_names.yaml](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/medical_person_names.yaml)
- **问题描述**：suffixes列表包含"患者"，但"患者"是患者身份标识而非医疗人员称谓。虽然"张患者"在真实场景中极少出现，但属于逻辑错误。
- **修复建议**：从suffixes中移除"患者"，将其移至单独的患者姓名匹配规则
- **修复类型**：数据修复

#### SG-FIX-R7-11：start_safety_gateway.sh缺少set -e

- **严重程度**：P2 — 错误可能被静默忽略
- **文件**：[start_safety_gateway.sh](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/scripts/start_safety_gateway.sh)
- **问题描述**：脚本没有`set -e`，如果`echo -1000 > /proc/self/oom_score_adj`写入失败，错误会被静默忽略
- **修复建议**：添加`set -e`和`set -o pipefail`
- **修复类型**：脚本修复

#### SG-FIX-R7-12：supervisor日志轮转配置缺失

- **严重程度**：P2 — 日志文件可能无限增长
- **文件**：[safety-gateway.conf](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/deploy/supervisor/safety-gateway.conf)
- **问题描述**：未配置`stdout_logfile_maxbytes`和`stdout_logfile_backups`
- **修复建议**：添加`stdout_logfile_maxbytes=50MB`和`stdout_logfile_backups=5`
- **修复类型**：配置修复

---

## 四、代码质量评估结果

### 4.1 健壮性评估

| 审查项 | 评估 | 说明 |
|--------|------|------|
| 并发安全 | ⚠️ 部分缺失 | `_rules_lock`(desensitization.py)、`_sg_checker_lock`(checker.py)、`_lock`(health.py/circuit_breaker.py)覆盖关键共享状态；但client.py `_consecutive_failures`和`_get_client()`存在竞态（SG-FIX-R7-02） |
| 资源泄漏 | ✅ 良好 | lifespan中正确关闭Redis连接池、checker、threshold订阅 |
| 异常处理完整性 | ⚠️ 部分缺失 | SGError未映射HTTP状态码（SG-FIX-R6-01遗留）；three_layer_desensitization.py键名不匹配导致降级检测失效（SG-FIX-R7-01） |
| 熔断器状态转换 | ✅ 良好 | record_failure/record_success均在_lock保护内 |
| 规则热更新回滚 | ✅ 良好 | reload_rules()失败时恢复old_rules+old_patterns |
| 单例模式线程安全 | ⚠️ 部分缺失 | checker/consent_checker使用double-checked locking，contraindication/threshold未使用（SG-FIX-R6-02遗留） |

### 4.2 可维护性评估

| 审查项 | 评估 | 说明 |
|--------|------|------|
| 模块职责边界 | ✅ 良好 | checker(协调)/consent_checker(同意)/desensitization(脱敏)/threshold(阈值)/contraindication(禁忌)各司其职 |
| 依赖方向 | ✅ 良好 | safety_gateway/未导入app.或fusion.模块，进程隔离正确 |
| 配置集中度 | ✅ 良好 | 所有可调参数在sg_config.py中 |
| 错误类型层次 | ✅ 良好 | errors.py 18种异常类型，继承层次清晰，E4005系列与设计文档对齐 |
| 类型注解一致性 | ⚠️ 不一致 | 5个文件仍使用Python 3.9风格（SG-FIX-R7-08） |

### 4.3 性能评估

| 审查项 | 评估 | 说明 |
|--------|------|------|
| P99延迟基准（HTTP通信） | ✅ 良好 | 500样本滑动窗口，warning=4ms/critical=5ms |
| P99延迟基准（端到端处理） | ❌ 缺失 | 仅有HTTP通信开销监控，缺少含内部处理的端到端P99<50ms监控（R6发现，仍未修复） |
| PG查询优化 | ✅ 良好 | consent_checker.py单条查询+索引(patient_id, status) |
| Redis连接池隔离 | ✅ 良好 | redis_pool.py强制替换URL为DB 1 |
| 内存预算监控 | ✅ 良好 | health.py多平台内存统计，512MB预算 |
| 性能测试精度 | ⚠️ 问题 | time.time()精度不足（SG-FIX-R7-09） |

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
| consent_checker.py | **混合** | ⚠️ SG-FIX-R6-06遗留 |
| sg_config.py | **Python 3.9** | ⚠️ SG-FIX-R6-05遗留 |
| sg_validator.py | **Python 3.9** | ⚠️ SG-FIX-R6-05遗留 |
| redis_pool.py | **Python 3.9** | ⚠️ SG-FIX-R6-04遗留 |
| three_layer_desensitization.py | **混合** | ⚠️ SG-FIX-R6-07遗留 |
| safety_gateway_client.py | Python 3.10+ | ✅ |

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
| 独立uvicorn实例 | SG以独立进程运行 | main.py第352-360行uvicorn.run独立启动 | ✅ |
| HTTP localhost通信 | 主服务通过HTTP调用SG | client.py通过httpx AsyncClient调用 | ✅ |
| 主服务崩溃时独立存活 | 返回E4005错误码 | docker-compose.dev.yml独立容器 + supervisor看门狗 | ✅ |
| Docker Compose depends_on | api依赖safety-gateway健康检查 | docker-compose.dev.yml第75-76行condition: service_healthy | ✅ |
| OOM保护 | oom_score_adj=-1000 | docker-compose.dev.yml第130行 + start_safety_gateway.sh第5行 | ✅ |
| 独立Redis连接池(DB 1) | 与主服务DB 0隔离 | redis_pool.py第18行强制替换URL为/1 | ✅ |

### 6.2 四阶段校验流程验证（P0）

| 阶段 | 设计要求 | 当前实现 | 结果 |
|------|----------|----------|------|
| 阶段1：同意状态校验 | 入口快速拒绝 + 输出前直查PG | checker.py第326-346行(快速拒绝) + 第389-485行(PG直查) | ✅ |
| 阶段2：脱敏校验 | 结构化身份模式拦截 + 医疗人名词典 | checker.py第512-580行 | ✅ |
| 阶段3：禁忌+阈值校验 | 禁忌规则引擎 + 置信度阈值 | checker.py第582-698行 | ✅ |
| 阶段4：一致性校验 | 分级/发现/置信度差异检测 | checker.py第700-734行 | ✅ |
| 执行顺序 | consent→desensitization→contraindication→threshold→consistency | perform_full_check()第299-384行 | ✅ |

### 6.3 降级场景验证（P0）

| 降级场景 | DegradedReason | 代码位置 | 结果 |
|----------|----------------|----------|------|
| 1. 敏感数据检测 | SENSITIVE_DATA_DETECTED | checker.py第569-580行 | ✅ |
| 2. SG不可用 | SG_UNAVAILABLE | client.py超时→E4005 | ✅ |
| 3. DB超时 | DB_TIMEOUT | consent_checker.py第172-179行 | ✅ |
| 4. 禁忌命中 | CONTRAINDICATION_HIT | checker.py第641-661行 | ✅ |
| 5. 置信度低于阈值 | CONFIDENCE_BELOW_THRESHOLD | checker.py第663-681行 | ✅ |
| 6. 融合层不可用 | FUSION_UNAVAILABLE | checker.py第683-697行 | ✅ |
| 7. 组合故障 | COMBINED_FAILURE | checker.py第617-639行 | ✅ |
| 8. 边缘断连 | EDGE_DISCONNECTED | checker.py第487-510行 | ✅ |
| 9. 脱敏降级+SG不可用 | DESENSITIZATION_DEGRADED_SG_UNAVAILABLE | checker.py第527-560行 | ✅ |
| 10. SG不可用+患者撤回 | SG_UNAVAILABLE_CONSENT_WITHDRAWN | checker.py第401-414行 | ✅ |

**⚠️ 关键问题**：降级场景在SG服务端实现正确，但three_layer_desensitization.py中第三层降级检测因键名不匹配而失效（SG-FIX-R7-01），导致10种降级场景在三层脱敏集成层无法被正确感知。

### 6.4 E4005系列错误码验证（P0）

| 错误码 | 触发条件 | 服务端代码 | 客户端处理 | 结果 |
|--------|----------|------------|------------|------|
| E4005 | SG不可用（客户端通用） | — | client.py第229-236行 | ✅ |
| E4005A | 脱敏降级+SG不可用 | errors.py第144-148行 | 客户端不区分子类型 | ⚠️ SG-FIX-R7-06 |
| E4005B | PG不可用+Redis缓存未命中 | errors.py第150-153行 | 客户端不区分子类型 | ⚠️ SG-FIX-R7-06 |
| E4005C | SG+融合层同时不可用 | errors.py第155-159行 | 客户端不区分子类型 | ⚠️ SG-FIX-R7-06 |

**叠加问题**：服务端E4005B/E4005C返回HTTP 500（SG-FIX-R6-01遗留），客户端不区分子类型（SG-FIX-R7-06），两者叠加导致前端完全无法区分不同不可用原因。

### 6.5 安全合规关键项验证（P0）

| 验证项 | 设计要求 | 当前实现 | 结果 |
|--------|----------|----------|------|
| 三重脱敏防线 | SG为第三道防线 | three_layer_desensitization.py实现三层 | ⚠️ 第三层降级检测失效（SG-FIX-R7-01） |
| 规则异构冗余 | 独立编写规则集 | 9条规则逐条对比确认实质性差异 | ✅ |
| 同意状态直查PG | 正常路径不写Redis缓存 | consent_checker.py无_cache_consent_to_redis方法 | ✅ |
| 两阶段同意校验 | 入口快速拒绝 + 输出前直查PG | checker.py第326-346行 + 第389-485行 | ✅ |
| A级安全标准 | 先校验后输出，零容忍 | perform_full_check()四阶段全部通过才PASSED | ✅ |
| 禁忌校验fail-closed | 异常时默认存在禁忌 | client.py返回has_contraindication=True | ✅ |
| 降级输出仅白名单 | 过滤diagnostic_conclusion，仅保留observations | apply_degraded_output()白名单过滤 | ✅ |
| OOM保护 | oom_score_adj=-1000 | docker-compose.dev.yml + start_safety_gateway.sh | ✅ |
| consent_status_at_output | SG输出前直查PG获取同意状态快照 | SafetyCheckResponse第40行 | ✅ |
| NER方案 | 纯正则+词典，不依赖ML NER | desensitization.py使用正则+医疗人名词典 | ✅ |
| 内存预算 | ≤512MB | health.py监控 + sg_memory_budget_mb=512 | ✅ |
| 独立姓名脱敏覆盖 | 三道防线覆盖一致 | Safety规则要求前缀，主规则不要求 | ⚠️ SG-FIX-R7-04 |

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
| SafetyGatewayClient HTTP请求/重试/fast-fail | **高** | 补充客户端完整单元测试 |
| three_layer_desensitization.py三层集成 | **高** | 补充三层脱敏端到端测试，验证键名匹配 |
| 并发请求场景 | 中 | 添加多协程并发测试验证锁正确性 |
| 端到端延迟P99<50ms | 中 | 添加含HTTP调用的端到端延迟测试 |
| _delete_threshold_from_db | 中 | 添加阈值删除路径测试 |
| client超时重试完整流程 | 中 | 添加端到端重试+排队超时测试 |
| desensitization_degraded参数传递 | 中 | 添加参数传递验证测试 |

### 7.3 性能测试问题

| 问题 | 风险 | 建议 |
|------|------|------|
| 仅测试服务端内部逻辑，未测试HTTP端到端延迟 | 高 | 补充HTTP层性能测试 |
| time.time()精度不足（Windows 15.6ms） | 中 | 改用time.perf_counter()（SG-FIX-R7-09） |
| gc.disable()不反映生产环境 | 低 | 补充GC开启时的P99作为参考 |

---

## 八、与参考文档的偏差分析

### 8.1 P0文档偏差：01-系统架构设计.md

| 需求项 | 设计要求 | 当前实现 | 偏差等级 |
|--------|----------|----------|----------|
| 进程隔离 | 独立进程，HTTP localhost通信 | ✅ 实现 | 无偏差 |
| E4005子码差异化 | E4005A/B/C应有不同前端提示 | 服务端定义完整但HTTP返回500 + 客户端不区分 | **P0** — SG-FIX-R6-01 + SG-FIX-R7-06 |
| 三重脱敏防线 | SG为第三道防线 | 第三层降级检测因键名不匹配失效 | **P0** — SG-FIX-R7-01 |
| 端到端处理延迟P99 | <50ms | 仅有HTTP通信开销监控，缺少端到端监控 | **P2** |
| OOM保护 | oom_score_adj=-1000 | ✅ 实现 | 无偏差 |
| A级安全标准 | 先校验后输出，零容忍 | ✅ 实现 | 无偏差 |
| 同意状态校验 | 直查PG，正常路径禁用缓存 | ✅ 实现 | 无偏差 |

### 8.2 P0文档偏差：04-安全合规.md

| 需求项 | 设计要求 | 当前实现 | 偏差等级 |
|--------|----------|----------|----------|
| 三重脱敏防线 | 三道防线防御目标不同，不可互相替代 | 第三层降级检测失效 | **P0** — SG-FIX-R7-01 |
| 规则异构冗余 | 独立编写规则集 | ✅ 逐条对比确认 | 无偏差 |
| 双重降级保障 | 双重缺失时拒绝输出 | E4005A错误码实现 | 无偏差 |
| 患者同意管理 | 两阶段校验 | ✅ 实现 | 无偏差 |
| 独立姓名脱敏覆盖 | SafetyGateway结构化身份模式拦截 | 无前缀独立姓名覆盖不足 | **P1** — SG-FIX-R7-04 |

### 8.3 P1文档偏差：06-开发环境与进度规划.md

| 需求项 | 设计要求 | 当前实现 | 偏差等级 |
|--------|----------|----------|----------|
| 代码组织 | 独立Python package safety_gateway/ | ✅ | 无偏差 |
| 代码量 | 2500-3000行 | ~3500行（超出17%） | P2（合理偏差，降级场景覆盖更全面） |
| 测试用例数 | 80-120个 | ~202个（超出68%） | P2（超出可接受） |
| 端口配置 | SAFETY_GATEWAY_PORT=9001 | ✅ | 无偏差 |
| Docker Compose | depends_on + 健康检查 | ✅ | 无偏差 |
| supervisor看门狗 | 崩溃后自动重启≤5s | startretries=5限制 | **P1** — SG-FIX-R7-07 |

---

## 九、SafetyGateway轨道修复项显式声明

以下修复项**明确属于SafetyGateway轨道**，需由SG轨道负责修复：

### P0级修复项（必须修复，存在运行时安全缺陷）

| 修复项 | 描述 | 文件 | 修复类型 |
|--------|------|------|----------|
| **SG-FIX-R7-01** | three_layer_desensitization.py键名不匹配（`degraded_mode`→`degraded`、`passed`→`allowed`）+ 缺少`desensitization_degraded`参数传递，导致三层脱敏第三层降级检测完全失效 | `app/security/three_layer_desensitization.py` | 代码修复（关键安全缺陷） |
| **SG-FIX-R7-02** | SafetyGatewayClient._consecutive_failures和_get_client()并发竞态条件 | `app/services/safety_gateway_client.py` | 代码修复 |

### P1级修复项（应当修复，存在功能缺陷或安全隐患）

| 修复项 | 描述 | 文件 | 修复类型 |
|--------|------|------|----------|
| **SG-FIX-R7-03** | medical_person_names.yaml声明known_combinations字段但实际缺失 | `medical_person_names.yaml` | 数据修复 |
| **SG-FIX-R7-04** | desensitization_rules_safety.yaml独立姓名脱敏规则覆盖不足，无前缀独立中文姓名不会被SG脱敏 | `desensitization_rules_safety.yaml` | 规则补充 |
| **SG-FIX-R7-05** | Dockerfile与supervisor配置HOME目录不一致（/app vs /home/sguser） | `Dockerfile.safety-gateway.dev` + `deploy/supervisor/safety-gateway.conf` | 配置修复 |
| **SG-FIX-R7-06** | SafetyGatewayClient E4005响应不区分子类型（E4005A/B/C） | `app/services/safety_gateway_client.py` | 代码增强 |
| **SG-FIX-R7-07** | supervisor startretries=5限制安全关键组件重启次数 | `deploy/supervisor/safety-gateway.conf` | 配置修复 |

### P2级修复项（建议修复，提升质量）

| 修复项 | 描述 | 文件 | 修复类型 |
|--------|------|------|----------|
| **SG-FIX-R7-08** | R6遗留7项类型注解风格修复均未落地（SG-FIX-R6-01/02/04/05/06/07/08） | 多文件 | 代码风格 |
| **SG-FIX-R7-09** | 性能测试使用time.time()而非time.perf_counter() | `tests/benchmark/test_safety_gateway_performance.py` | 代码修复 |
| **SG-FIX-R7-10** | medical_person_names.yaml suffixes包含"患者"（非医疗人员称谓） | `medical_person_names.yaml` | 数据修复 |
| **SG-FIX-R7-11** | start_safety_gateway.sh缺少set -e | `scripts/start_safety_gateway.sh` | 脚本修复 |
| **SG-FIX-R7-12** | supervisor日志轮转配置缺失 | `deploy/supervisor/safety-gateway.conf` | 配置修复 |

### 历史遗留项状态

| 修复项 | 描述 | 状态 |
|--------|------|------|
| SG-FIX-R6-01 | main.py SGError→HTTP状态码映射 | ❌ 未修复（纳入SG-FIX-R7-08） |
| SG-FIX-R6-02 | 单例获取函数线程安全 | ❌ 未修复（纳入SG-FIX-R7-08） |
| SG-FIX-R6-03 | _check_desensitization_phase()类型注解 | ✅ 已修复 |
| SG-FIX-R6-04~08 | 类型注解风格 + __init__.py导出 | ❌ 未修复（纳入SG-FIX-R7-08） |
| SG-FIX-R4-10 | 26/31 SG配置项未在00b文档登记 | ❌ 仍未修复（文档范畴） |

---

## 十、审查结论

### 10.1 总体评估

SafetyGateway轨道在第六轮审查后仅修复了8项中的1项（12.5%修复率），本轮审查发现12项新问题，其中2项P0级、5项P1级、5项P2级。

**与前六轮对比**：

| 轮次 | P0级 | P1级 | P2级 | 修复率 |
|------|------|------|------|--------|
| R4 | 2 | 4 | 4 | 70% |
| R5 | 2 | 5 | 2 | 100%(R6验证) |
| R6 | 0 | 3 | 5 | 12.5%(本轮验证) |
| **R7** | **2** | **5** | **5** | — |

**问题严重程度反弹**：R6无P0级问题，R7发现2项P0级问题。原因：本轮审查深入到客户端与集成层（three_layer_desensitization.py、safety_gateway_client.py），发现了之前审查未覆盖的关键安全缺陷。

### 10.2 关键风险

1. **SG-FIX-R7-01**（P0）：三层脱敏第三层降级检测完全失效。`degraded_mode`/`degraded`键名不匹配 + `passed`/`allowed`键名不匹配 + 缺少`desensitization_degraded`参数传递。这意味着当SafetyGateway返回降级结果时，三层脱敏集成层无法正确感知，`all_passed`判断错误，可能导致降级输出被当作正常输出传递给前端。

2. **SG-FIX-R7-02**（P0）：SafetyGatewayClient并发竞态条件。在高并发诊断场景下，`_consecutive_failures`计数可能丢失，导致fast-fail机制失效，本应快速拒绝的请求仍然尝试调用已不可用的SG服务，增加延迟。

3. **SG-FIX-R6-01 + SG-FIX-R7-06叠加**：服务端E4005B/E4005C返回HTTP 500 + 客户端不区分子类型，导致前端完全无法区分不同不可用原因，无法提供差异化的用户提示。

### 10.3 修复优先级建议

1. **立即修复（本迭代）**：
   - SG-FIX-R7-01（三层脱敏键名不匹配 + 参数缺失）
   - SG-FIX-R7-02（客户端并发竞态）
   - SG-FIX-R6-01（SGError→HTTP状态码映射，R6遗留）
   - SG-FIX-R6-02（单例线程安全，R6遗留）

2. **本迭代修复**：
   - SG-FIX-R7-04（独立姓名脱敏规则补充）
   - SG-FIX-R7-06（客户端E4005子类型区分）
   - SG-FIX-R7-07（supervisor重启次数限制）

3. **下迭代修复**：
   - SG-FIX-R7-03（known_combinations声明清理）
   - SG-FIX-R7-05（HOME目录一致性）
   - SG-FIX-R7-08（类型注解风格统一，含R6遗留7项）
   - SG-FIX-R7-09~R7-12（性能测试精度、数据修复、脚本修复、日志轮转）

### 10.4 自我反思

| 维度 | 反思 |
|------|------|
| R6修复验证 | R6的8项修复仅1项落地（12.5%），修复率显著低于R5→R6的100%。需关注修复执行力度。 |
| 新问题发现 | 本轮发现2项P0级问题，均位于客户端与集成层（前六轮审查未深入覆盖）。说明审查范围扩展是必要的。 |
| 审查范围扩展价值 | three_layer_desensitization.py的键名不匹配问题（SG-FIX-R7-01）是本轮最重要的发现，直接影响三层脱敏防线的有效性。此问题在前六轮审查中未被检出，因为前六轮主要聚焦于safety_gateway/包内部，未深入审查集成层。 |
| 设计偏差合理性 | 代码量超出设计预估17%（3500行 vs 2500-3000行），主要因为降级场景覆盖更全面（10种 vs 设计文档的7种），属于合理偏差。 |
| 规则异构冗余 | 9条规则逐条对比确认实质性差异，异构冗余验证通过。但独立姓名脱敏规则存在覆盖缺口（SG-FIX-R7-04），需补充。 |
