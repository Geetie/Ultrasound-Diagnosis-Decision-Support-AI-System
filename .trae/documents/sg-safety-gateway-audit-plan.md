# SG安全网关轨道系统性审查计划

## 审查目标

对SafetyGateway轨道的所有验证环节和测试逻辑进行全面、系统性审查，确保其正确有效性。审查严格遵循独立、客观、深入且批判性的原则。

## 审查范围

### 源代码文件（18个核心文件）
- `safety_gateway/main.py` — FastAPI服务入口
- `safety_gateway/checker.py` — 核心校验器
- `safety_gateway/consent_checker.py` — 同意状态校验器
- `safety_gateway/desensitization.py` — 脱敏引擎
- `safety_gateway/contraindication.py` — 禁忌校验引擎
- `safety_gateway/threshold.py` — 阈值校验引擎
- `safety_gateway/health.py` — 健康检查模块
- `safety_gateway/circuit_breaker.py` — 熔断器模式
- `safety_gateway/sg_config.py` — 配置模块
- `safety_gateway/sg_validator.py` — 配置校验器
- `safety_gateway/sg_models.py` — 数据库ORM模型
- `safety_gateway/sg_types.py` — 类型定义
- `safety_gateway/sg_db.py` — 数据库连接
- `safety_gateway/validation_result.py` — 校验结果模型
- `safety_gateway/errors.py` — 异常类型定义
- `safety_gateway/redis_pool.py` — Redis连接池

### 集成文件
- `app/services/safety_gateway_client.py` — 主服务SG客户端
- `app/security/three_layer_desensitization.py` — 三层脱敏架构

### 测试文件
- `tests/unit/test_safety_gateway.py` — 核心单元测试（~2265行）
- `tests/benchmark/test_safety_gateway_performance.py` — 性能基准测试

### 部署文件
- `scripts/start_safety_gateway.sh` — 启动脚本
- `deploy/supervisor/safety-gateway.conf` — Supervisor配置
- `Dockerfile.safety-gateway.dev` — Docker开发镜像
- `docker-compose.dev.yml` — Docker Compose配置

### 规则配置文件
- `desensitization_rules_safety.yaml` — SG脱敏主规则
- `desensitization_rules_safety_patch.yaml` — SG脱敏补丁规则
- `contraindication_rules_safety.yaml` — SG禁忌规则
- `medical_person_names.yaml` — 人名字典

---

## 审查维度与步骤

### 维度一：代码质量评估（P0）

#### 步骤1.1：进程隔离架构验证
**对照文档**：01-系统架构设计.md §进程架构

| 检查项 | 预期 | 审查方法 |
|--------|------|----------|
| 独立uvicorn实例 | main.py通过uvicorn.run启动独立进程 | 代码审查main.py:373-381 |
| HTTP localhost通信 | SafetyGatewayClient通过httpx调用SG API | 代码审查safety_gateway_client.py |
| P99延迟基准<5ms | 健康检查计算P99，性能测试验证 | 运行benchmark测试 |
| 主服务崩溃时SG独立存活 | SG作为独立进程，不依赖主服务进程 | 审查docker-compose.dev.yml进程隔离配置 |
| Docker Compose depends_on | 主服务depends_on safety-gateway: condition: service_healthy | 审查docker-compose.dev.yml:75-76, 109-113 |

**已识别潜在问题**：
- [P0-1] `safety_gateway_client.py`中`check_output`方法在SG不可用时的排队重试逻辑与架构文档"SG不可用(≤15s)排队等待SG恢复"的描述一致，但缺少对"连续3次健康检查失败判定不可用(15s)"的客户端侧显式健康检查循环。当前实现依赖`_consecutive_failures`快速失败机制（阈值3），而非架构文档描述的"每4秒健康检查"模式。需验证这两种机制是否等价。

#### 步骤1.2：OOM保护验证
**对照文档**：01-系统架构设计.md §OOM保护

| 检查项 | 预期 | 审查方法 |
|--------|------|----------|
| oom_score_adj = -1000 | docker-compose.dev.yml配置 | 审查docker-compose.dev.yml:130 |
| 启动脚本设置 | start_safety_gateway.sh写入/proc/self/oom_score_adj | 审查start_safety_gateway.sh:5-6 |
| 健康检查报告OOM状态 | health.py _check_oom_protection() | 代码审查health.py:278-288 |
| 内存预算≤512MB | docker-compose.dev.yml deploy.resources.limits.memory: 512M | 审查docker-compose.dev.yml:126 |

**已识别潜在问题**：
- [P0-2] `health.py:278-288`中`_check_oom_protection()`仅在Linux环境读取`/proc/{pid}/oom_score_adj`，Windows环境返回`{"status": "unknown"}`。健康检查`check_all()`中`oom_status`未参与`components_healthy`/`components_degraded`判定逻辑（仅作为信息字段返回），OOM保护失效不会导致健康检查降级。这与OOM保护作为安全关键配置的设计意图可能不一致。

#### 步骤1.3：核心校验流程正确性
**对照文档**：01-系统架构设计.md §核心校验职责

| 检查项 | 预期 | 审查方法 |
|--------|------|----------|
| 四项校验执行顺序 | 同意状态→脱敏→禁忌+阈值→一致性 | 代码审查checker.py:299-384 |
| 先校验后输出 | 所有校验通过才输出PASSED | 代码审查checker.py:372-384 |
| 零容忍策略 | 任一校验失败即拒绝或降级 | 代码审查各_phase方法 |
| 降级输出白名单过滤 | 仅输出白名单内字段 | 代码审查checker.py:763-848 |
| 禁用分级术语过滤 | 降级输出中过滤TI-RADS等术语 | 代码审查checker.py:736-761 |

**已识别潜在问题**：
- [P0-3] `checker.py:326-346`中`consent_status_at_input`快速拒绝逻辑：当`consent_status_at_input`为`"pending"`时直接返回FAILED，但架构文档描述入口快速拒绝仅针对"非granted"状态。当前实现`consent_status_at_input != "granted"`即快速拒绝，这与设计文档"两阶段校验（入口快速拒绝 + 输出前实时校验）"一致，但需确认PENDING状态是否应在入口阶段即被拒绝（而非等到输出前校验）。
- [P0-4] `checker.py:527-541`中脱敏降级+SG不可用场景：当`fusion_available=False`且检测到敏感数据时返回FAILED + `DegradedReason.DESENSITIZATION_DEGRADED_SG_UNAVAILABLE`。但`fusion_available=False`的含义是"融合层不可用"而非"SG不可用"，降级原因命名`DESENSITIZATION_DEGRADED_SG_UNAVAILABLE`可能产生语义混淆。对照架构文档降级策略总表，"脱敏降级+SG不可用"应为"融合层脱敏服务超时且SafetyGateway不可达"，当前代码逻辑与文档描述存在偏差。

#### 步骤1.4：同意状态校验两阶段机制
**对照文档**：04-安全合规.md §患者同意状态校验

| 检查项 | 预期 | 审查方法 |
|--------|------|----------|
| 入口快速拒绝 | consent_status_at_input非granted即拒绝 | 代码审查checker.py:326-346 |
| 输出前实时校验 | 直查PostgreSQL获取最新同意状态 | 代码审查consent_checker.py:74-87 |
| PostgreSQL不可用降级 | 降级为Redis只读缓存(TTL 60s) | 代码审查consent_checker.py:151-195 |
| Redis缓存未命中 | 拦截所有输出(E4005B) | 代码审查consent_checker.py:169-170 |
| 不写入Redis缓存 | 正常路径直查PG不双写Redis | 代码审查consent_checker.py（无写入逻辑） |

**已识别潜在问题**：
- [P0-5] `consent_checker.py`中`_check_redis_cache()`方法读取Redis缓存，但正常路径直查PostgreSQL后**未将结果写入Redis缓存**。这意味着Redis缓存中的同意状态数据来源不明——如果SG自身不写入，那Redis中的数据只能来自其他服务（如主服务）。架构文档要求"直查PostgreSQL，不双写Redis"，但未明确Redis缓存数据的写入方。需确认Redis中同意状态数据的来源和TTL管理。
- [P0-6] `consent_checker.py:56-72`中`_check_redis_cache()`读取缓存后检查TTL是否<0（已过期），但TTL<0时返回None（视为未命中），这与"接受≤60s旧值窗口风险"的设计意图一致。但`sg_config.py`中`sg_consent_cache_ttl_seconds=10`，而架构文档要求Redis缓存TTL为60s，存在配置不一致。

#### 步骤1.5：脱敏规则异构冗余
**对照文档**：04-安全合规.md §规则异构冗余

| 检查项 | 预期 | 审查方法 |
|--------|------|----------|
| 独立规则文件 | desensitization_rules_safety.yaml独立编写 | 审查规则文件内容 |
| 补丁规则独立维护 | desensitization_rules_safety_patch.yaml由安全工程师独立编写 | 审查补丁规则文件 |
| 不同执行引擎 | SG使用纯正则匹配 vs 主服务ML NER+规则增强NER | 代码审查desensitization.py（纯正则） |
| 规则热更新 | Redis Pub/Sub订阅规则更新channel | 代码审查main.py:127-150 |

**已识别潜在问题**：
- [P0-7] `desensitization.py`中`_get_default_rules()`方法在YAML文件不存在时使用硬编码默认规则。这些默认规则的正则模式与主服务的脱敏规则是否真正"异构"需要验证。例如`sg_phone`使用`(?<!\d)(1[3-9]\d{9})(?!\d)`整体匹配模式，如果主服务使用分组模式则构成异构；但如果主服务也使用类似模式则冗余度不足。

#### 步骤1.6：降级路径完整性
**对照文档**：01-系统架构设计.md §降级策略总表

| 降级场景 | 代码实现位置 | 测试覆盖 |
|----------|-------------|----------|
| 正常模式 | checker.py:372-384 | TestFullCheckIntegration |
| 融合层不可用→SG降级输出 | checker.py:683-697 | test_fusion_unavailable_sg_degraded_output |
| SG不可用(≤15s)→排队等待 | safety_gateway_client.py:72-149 | 无直接测试 |
| SG不可用(>15s)→E4005 | safety_gateway_client.py:63-69 | test_sg_unavailable_reject_all_output |
| 融合层+SG同时不可用→E4005C | checker.py:417-421 | test_sg_combined_unavailable_e4005c |
| 脱敏降级+SG不可用→E4005A | checker.py:527-541 | test_desensitization_degraded_sg_unavailable_e4005a |
| SG不可用+患者撤回同意→E4005A | checker.py:401-414 | test_sg_unavailable_consent_withdrawn_e4005a |
| 边缘端断连→本地降级 | checker.py:487-510 | test_edge_disconnected_local_degradation |
| 组合故障→组合降级 | checker.py:617-639 | test_combined_failure_degradation |

**已识别潜在问题**：
- [P0-8] 架构文档描述7种降级场景，但`DegradedReason`枚举定义了10种降级原因（含`SG_UNAVAILABLE`、`EDGE_DISCONNECTED`、`COMBINED_FAILURE`等）。`SG_UNAVAILABLE`降级原因在`apply_degraded_output()`中有处理逻辑，但在`perform_full_check()`中未被赋值给任何`DegradedReason`——即`DegradedReason.SG_UNAVAILABLE`是死代码路径。
- [P0-9] 架构文档"SG不可用(≤15s)→排队等待SG恢复"的排队逻辑在`safety_gateway_client.py`中实现，但**无对应的单元测试**。排队超时逻辑（`queue_timeout_seconds=10`）仅在客户端侧实现，SG服务端无排队机制。需验证客户端重试间隔（1s）和超时（10s）是否满足"≤15s判定不可用"的要求。
- [P0-10] `checker.py:543-561`中`desensitization_degraded=True`且检测到敏感数据时返回FAILED + `DESENSITIZATION_DEGRADED_SG_UNAVAILABLE`。但此场景下`fusion_available`仍为True（融合层可用但脱敏服务降级），降级原因命名含"SG_UNAVAILABLE"语义不准确——SG本身是可用的，只是融合层脱敏服务降级了。

#### 步骤1.7：E4005错误码子码拆分
**对照文档**：01-系统架构设计.md §E4005错误码子码拆分

| 子错误码 | 触发条件 | 代码实现 | 测试覆盖 |
|----------|---------|----------|----------|
| E4005 | SG服务不可用(健康检查连续失败) | errors.py:ServiceUnavailableError | test_e4005_error_code_constant |
| E4005A | 脱敏降级+SG不可用 或 SG不可用+患者撤回同意 | errors.py:ConsentSafetyViolationError | test_desensitization_degraded_sg_unavailable_e4005a, test_sg_unavailable_consent_withdrawn_e4005a |
| E4005B | PostgreSQL不可用且Redis缓存未命中 | errors.py:DatabaseUnavailableError | test_sg_returns_e4005_when_db_unavailable |
| E4005C | SG+融合层同时不可用 | errors.py:CombinedUnavailableError | test_sg_combined_unavailable_e4005c |

**已识别潜在问题**：
- [P0-11] `errors.py`中`ConsentSafetyViolationError`(E4005A)同时覆盖"脱敏降级+SG不可用"和"SG不可用+患者撤回同意"两种场景。但`checker.py`中这两种场景的降级原因不同（`DESENSITIZATION_DEGRADED_SG_UNAVAILABLE` vs `SG_UNAVAILABLE_CONSENT_WITHDRAWN`），且`apply_degraded_output()`对这两种原因的处理逻辑不同。需确认E4005A是否需要进一步拆分子码以区分这两种场景。

---

### 维度二：代码风格合规性（P1）

#### 步骤2.1：代码组织规范
**对照文档**：06-开发环境与进度规划.md §代码组织

| 检查项 | 预期 | 实际 | 偏差 |
|--------|------|------|------|
| 独立Python package | safety_gateway/ | ✅ | 无 |
| 不导入融合层模块 | SG代码无融合层import | ✅ 需验证 | — |
| 代码量1500-2000行 | 约1500-2000行 | 需统计 | — |
| 测试用例80-120个 | 含降级路径专项测试 | 需统计 | — |

#### 步骤2.2：命名约定和文档标准
| 检查项 | 审查方法 |
|--------|----------|
| 类命名PascalCase | 代码审查所有类定义 |
| 函数命名snake_case | 代码审查所有函数定义 |
| 常量UPPER_SNAKE_CASE | 代码审查模块级常量 |
| docstring完整性 | 代码审查公共方法docstring |

#### 步骤2.3：依赖隔离
| 检查项 | 预期 | 审查方法 |
|--------|------|----------|
| 独立Redis连接池(db=1) | SafetyGatewayRedisPool使用db=1 | 代码审查redis_pool.py:30-31 |
| 不从Redis读取同意状态(正常路径) | 正常路径直查PG | 代码审查consent_checker.py |
| 独立NER方案 | 结构化身份模式+人名字典 | 代码审查desensitization.py |

---

### 维度三：功能正确性验证（P0）

#### 步骤3.1：端口配置验证
**对照文档**：00b-统一配置参考手册.md

| 检查项 | 预期 | 实际 |
|--------|------|------|
| SAFETY_GATEWAY_PORT=9001 | sg_config.py:12 | ✅ |
| 健康检查端点http://localhost:9001/health | main.py:192-202 | ✅ |
| 端口冲突防护 | sg_validator.py | 需验证 |

**已识别潜在问题**：
- [P0-12] `sg_validator.py`仅验证端口范围1-65535，未验证与其他服务端口（8000/8001/8002）的互斥性。架构文档要求"所有端口值须互不相同"，但启动时无端口冲突检测。

#### 步骤3.2：健康检查完整性
**对照文档**：01-系统架构设计.md §健康检查与故障检测

| 检查项 | 预期 | 实际 |
|--------|------|------|
| 每4秒健康检查 | docker-compose.dev.yml interval:4s | ✅ |
| 超时1s | docker-compose.dev.yml timeout:1s | ✅ |
| 连续3次失败判定不可用 | docker-compose.dev.yml retries:3 | ✅ |
| supervisor看门狗 | safety-gateway.conf autorestart=true | ✅ |
| 崩溃后≤5s重启 | startretries=100 + startsecs=2 | ✅ |
| Docker Compose depends_on+健康检查 | 主服务depends_on SG condition:service_healthy | ✅ |

#### 步骤3.3：配置约束关系验证
**对照文档**：00b-统一配置参考手册.md §配置约束关系

| 约束关系 | 预期 | sg_validator.py实现 |
|----------|------|---------------------|
| 排队超时>看门狗重启时间(5s)且<15s | 10s | ✅ queue_timeout>5且<15 |
| P99警告阈值<临界阈值 | 4.0<5.0 | ✅ warning<critical |
| 端口范围1-65535 | 9001 | ✅ |

**已识别潜在问题**：
- [P0-13] `sg_validator.py`未验证`sg_db_retry_max_attempts`与`sg_db_retry_max_total_time_seconds`的逻辑一致性。当前默认值`max_attempts=2`、`max_total_time=4.0s`，但`db_query_timeout=2s`，2次重试最大耗时=2+0.1+2=4.1s>4.0s，可能在重试过程中超过`max_total_time`限制。

#### 步骤3.4：边界条件和异常场景
| 测试场景 | 测试覆盖 | 缺失 |
|----------|---------|------|
| 空输出数据 | checker.py:312-324 | ✅ |
| 递归深度超限 | checker.py:105-107, 127-129 | ✅ |
| consent_status_at_input快速拒绝 | checker.py:326-346 | ✅ |
| conclusion_suppressed标记处理 | 架构文档要求SG跳过已置空的diagnostic_conclusion | ❌ 未实现 |
| 严重幻觉输出处理 | 架构文档要求附加安全标记 | ❌ 未实现 |
| 降级模式NER"类型化脱敏"策略 | 架构文档要求[REDACTED:{类型}] | ❌ 未实现 |

**已识别潜在问题**：
- [P0-14] 架构文档要求"当融合层主动将diagnostic_conclusion置空（标记conclusion_suppressed:true），SafetyGateway跳过diagnostic_conclusion字段的过滤逻辑，仅对observations字段执行常规结构化身份模式拦截"。当前`checker.py`的`perform_full_check()`和`apply_degraded_output()`均未处理此场景——降级输出始终过滤非白名单字段，不检查`conclusion_suppressed`标记。
- [P0-15] 架构文档要求"SafetyGateway在输出校验阶段检测到hallucination_severity='severe'时，须在输出中附加醒目安全标记"。当前代码未实现幻觉严重程度检测和标记逻辑。
- [P0-16] 架构文档要求"SafetyGateway降级输出时，规则增强NER扫描继续执行并采用'类型化脱敏'策略——NER检出的身份信息替换为[REDACTED:{类型}]"。当前`apply_degraded_output()`使用`sg_desensitization_engine.desensitize()`进行脱敏，替换为固定模式（如`PHONE_***`、`PATIENT_***`），而非`[REDACTED:PHONE]`、`[REDACTED:NAME]`格式。

#### 步骤3.5：测试覆盖率评估
| 测试类 | 测试数量估计 | 覆盖范围 |
|--------|-------------|----------|
| TestDesensitizationEngine | ~20 | 脱敏引擎核心功能 |
| TestContraindicationEngine | ~12 | 禁忌校验核心功能 |
| TestThresholdEngine | ~12 | 阈值校验核心功能 |
| TestSafetyGatewayChecker | ~20 | 校验器核心功能 |
| TestCircuitBreaker | ~6 | 熔断器状态转换 |
| TestHealthChecker | ~8 | 健康检查 |
| TestFullCheckIntegration | ~6 | 完整校验流程 |
| TestServiceIsolation | ~3 | 进程隔离 |
| TestConsentChecker | ~2 | 同意状态校验 |
| TestDegradedPathSevenScenarios | 7 | 7种降级场景 |
| TestDatabaseRetryMechanism | 2 | 数据库重试 |
| TestEmailDesensitizationHeterogeneity | 4 | 邮箱脱敏异构冗余 |
| TestSafetyGatewayErrors | 4 | 错误类型层次 |
| TestP99LatencyAlert | 4 | P99延迟告警 |
| TestThresholdHotReload | 3 | 阈值热更新 |
| TestOOMProtectionScenario | 5 | OOM保护 |
| TestBoundaryConditions | ~10 | 边界条件 |
| TestMainServiceCrashScenario | 4 | 主服务崩溃场景 |
| 其他 | ~20 | 降级输出完整性、禁用术语过滤等 |
| **合计** | **~150+** | 超出预估80-120个 |

**已识别潜在问题**：
- [P0-17] 测试数量约150+，超出文档预估的80-120个。但关键缺失：
  - **无客户端排队等待测试**：`safety_gateway_client.py`的排队重试逻辑无单元测试
  - **无Docker Compose集成测试**：进程隔离能力仅在单元测试中通过mock验证
  - **无Redis主从切换测试**：架构文档要求"Redis主从切换→从节点秒级接管"
  - **无规则加载失败降级测试**：架构文档要求"规则文件加载失败时，三道防线均降级为最保守策略"
  - **无脱敏规则实时同步延迟测试**：架构文档要求"三道防线的规则刷新时差<1s"

---

## 审查执行计划

### Phase 1：静态代码分析（P0项）
1. 统计safety_gateway/目录代码行数，确认是否在1500-2000行范围
2. 统计测试用例总数，按类别分组
3. 验证所有import语句，确认无融合层模块依赖
4. 审查所有降级路径的代码实现与架构文档的一致性
5. 验证E4005错误码子码的触发条件与文档定义的对应关系

### Phase 2：功能正确性深度验证（P0项）
6. 验证conclusion_suppressed标记处理逻辑是否缺失
7. 验证严重幻觉输出处理逻辑是否缺失
8. 验证降级模式NER"类型化脱敏"策略是否缺失
9. 验证consent_checker Redis缓存TTL配置（10s vs 文档60s）
10. 验证客户端排队等待逻辑与架构文档的一致性

### Phase 3：测试逻辑审查（P1项）
11. 审查7种降级场景测试的mock设置是否与实际代码路径一致
12. 审查性能基准测试的预热和GC控制是否充分
13. 审查熔断器测试的冷却时间设置是否影响测试稳定性
14. 验证测试隔离性（setup/teardown是否正确清理全局状态）

### Phase 4：部署配置审查（P1项）
15. 审查Docker Compose配置与架构文档的一致性
16. 审查Supervisor配置的自动重启策略
17. 审查启动脚本的OOM保护设置
18. 验证CORS配置与文档要求的一致性

### Phase 5：代码风格审查（P2项）
19. 检查命名约定一致性
20. 检查docstring完整性
21. 检查类型注解完整性

---

## 审查发现汇总（预识别问题）

### P0级问题（必须修复）

| 编号 | 问题描述 | 影响范围 | 对照文档 |
|------|---------|----------|----------|
| P0-1 | 客户端快速失败机制(3次)与架构文档健康检查机制(4s×3=15s)不一致 | 进程隔离 | 01-系统架构设计.md |
| P0-2 | OOM保护状态未参与健康检查判定逻辑 | 安全底线 | 01-系统架构设计.md |
| P0-4 | DESENSITIZATION_DEGRADED_SG_UNAVAILABLE降级原因语义混淆 | 降级路径 | 01-系统架构设计.md |
| P0-5 | Redis同意状态缓存数据来源不明 | 安全合规 | 04-安全合规.md |
| P0-6 | Redis同意状态缓存TTL配置(10s)与文档(60s)不一致 | 安全合规 | 04-安全合规.md |
| P0-8 | DegradedReason.SG_UNAVAILABLE为死代码路径 | 代码质量 | 01-系统架构设计.md |
| P0-9 | 客户端排队等待逻辑无单元测试 | 测试覆盖 | 06-开发环境与进度规划.md |
| P0-10 | desensitization_degraded场景降级原因命名含"SG_UNAVAILABLE"语义不准确 | 降级路径 | 01-系统架构设计.md |
| P0-12 | 端口冲突防护未实现互斥检测 | 配置安全 | 00b-统一配置参考手册.md |
| P0-13 | 数据库重试参数可能超过max_total_time限制 | 配置一致性 | 00b-统一配置参考手册.md |
| P0-14 | conclusion_suppressed标记处理逻辑缺失 | 功能正确性 | 01-系统架构设计.md |
| P0-15 | 严重幻觉输出处理逻辑缺失 | 功能正确性 | 01-系统架构设计.md |
| P0-16 | 降级模式NER"类型化脱敏"[REDACTED:{类型}]策略未实现 | 功能正确性 | 01-系统架构设计.md |

### P1级问题（应当修复）

| 编号 | 问题描述 | 影响范围 |
|------|---------|----------|
| P1-1 | 客户端排队等待逻辑无单元测试 | 测试覆盖 |
| P1-2 | 无Redis主从切换测试 | 测试覆盖 |
| P1-3 | 无规则加载失败降级测试 | 测试覆盖 |
| P1-4 | 无脱敏规则实时同步延迟测试 | 测试覆盖 |
| P1-5 | E4005A错误码覆盖两种不同场景，可能需要拆分 | 错误码设计 |
| P1-6 | 脱敏规则异构冗余度需验证（默认规则vs主服务规则） | 安全合规 |

### P2级问题（建议改进）

| 编号 | 问题描述 | 影响范围 |
|------|---------|----------|
| P2-1 | 代码量可能超出预估1500-2000行范围 | 工程规范 |
| P2-2 | 部分公共方法缺少docstring | 代码风格 |
| P2-3 | consent_status_at_input快速拒绝的PENDING状态处理需确认 | 功能设计 |

---

## 审查输出

审查完成后将生成详细审查报告，包含：
1. 审查范围与方法
2. 逐项审查结果（通过/不通过/需改进）
3. 发现的问题及严重程度分级
4. 与参考文档的偏差分析
5. SafetyGateway轨道应当进行的修复项（显式声明）
6. 改进建议及优先级
