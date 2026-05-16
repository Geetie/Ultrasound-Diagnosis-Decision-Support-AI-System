# SG安全网关轨道系统性审查计划（第六轮）

**审查日期**：2026-05-10
**审查范围**：SafetyGateway轨道全部源码、测试、配置与部署文件
**审查原则**：独立、客观、深入、批判性
**前置参考**：第五轮审查报告（sg-safety-gateway-audit-report-round5.md）

---

## 一、审查背景与目标

### 1.1 前置审查总结

前五轮审查已累计发现并修复大量问题，第五轮审查结论为：

- **修复率**：第四轮10项中7项已修复（70%），3项遗留
- **问题严重程度趋势**：从运行时崩溃风险（R4）→ 代码规范/死代码风险（R5），显著降低
- **文档一致性**：核心架构文档偏差已从前几轮的多个P0偏差改善为仅剩配置项文档化（P2）
- **安全合规关键项**：11项审查全部通过

### 1.2 本轮审查目标

本轮审查聚焦三大核心维度，以独立、客观、深入且批判性的原则全面评估SG轨道实施质量：

1. **代码质量评估**：健壮性、可维护性、可扩展性、性能表现
2. **代码风格合规性**：编码规范、命名约定、文档标准
3. **功能正确性验证**：需求规格实现、边界条件、异常处理

### 1.3 审查核心参考文档

| 优先级 | 文档 | 审查重点 |
|--------|------|----------|
| P0 | 01-系统架构设计.md | 进程隔离、HTTP localhost通信、P99<5ms、E4005系列、A级安全、OOM保护 |
| P0 | 04-安全合规.md | 三重脱敏防线、规则异构冗余、两阶段同意校验、零容忍策略 |
| P1 | 06-开发环境与进度规划.md | 代码组织、测试用例数、端口配置、Gate 0验证、Docker Compose |
| P2 | 02-核心流程与知识库.md | 融合层与SG交互流程、脱敏数据来源 |
| P2 | 00b-统一配置参考手册.md | SG配置项完整性和正确性 |

---

## 二、审查范围与文件清单

### 2.1 核心源码（safety_gateway/ 模块，17个文件）

| 文件 | 行数 | 审查深度 |
|------|------|----------|
| main.py | ~360 | 逐行审查：API端点、请求/响应模型、lifespan管理 |
| checker.py | ~803 | 逐行审查：四阶段校验流程、降级逻辑、白名单过滤 |
| consent_checker.py | ~202 | 逐行审查：PG直查、Redis降级、熔断器集成 |
| desensitization.py | ~421 | 逐行审查：规则加载、热更新、NER匹配、线程安全 |
| threshold.py | ~562 | 逐行审查：ORM操作、热更新、Redis订阅 |
| contraindication.py | 待确认 | 关键路径审查：禁忌规则匹配 |
| health.py | ~307 | 逐行审查：健康检查逻辑、OOM保护、P99延迟统计 |
| circuit_breaker.py | ~218 | 关键路径审查：状态转换、线程安全 |
| sg_config.py | ~56 | 逐行审查：配置项完整性、默认值合理性 |
| sg_types.py | ~25 | 一致性审查：类型定义与设计文档对齐 |
| sg_validator.py | ~50 | 逐行审查：配置校验约束 |
| sg_db.py | 待确认 | 关键路径审查：连接管理、事务语义 |
| sg_models.py | 待确认 | 一致性审查：数据模型与DB schema对齐 |
| redis_pool.py | 待确认 | 关键路径审查：独立连接池、DB 1隔离 |
| errors.py | ~188 | 一致性审查：错误码与设计文档E4005系列对齐 |
| validation_result.py | ~97 | 一致性审查：结果模型完整性 |
| __init__.py | 待确认 | 导出完整性 |

### 2.2 客户端与集成层（6个文件）

| 文件 | 审查重点 |
|------|----------|
| app/services/safety_gateway_client.py | HTTP通信、超时重试、E4005返回、死代码 |
| app/config/settings.py | SG相关配置项 |
| app/config/validator.py | 配置验证 |
| app/security/three_layer_desensitization.py | 三层脱敏集成、进程隔离 |
| app/api/v1/diagnosis.py | SG调用链路 |
| app/api/v1/errors.py | SG相关错误定义 |

### 2.3 测试文件（12个文件）

| 文件 | 审查重点 |
|------|----------|
| tests/unit/test_safety_gateway.py | 核心单元测试覆盖率与质量 |
| tests/benchmark/test_safety_gateway_performance.py | P99延迟基准测试 |
| tests/benchmark/test_desensitization_performance.py | 脱敏性能基准 |
| tests/unit/test_desensitization.py | 脱敏单元测试 |
| tests/unit/test_security_compliance.py | 安全合规测试 |
| tests/unit/test_security_compliance_complete.py | 安全合规完整测试 |
| tests/unit/test_audit_fixes.py | 审计修复验证测试 |
| tests/unit/test_sec_audit.py | 安全审计测试 |
| tests/unit/test_sec_craft_fixes.py | 安全工艺修复测试 |
| tests/unit/test_white_fixes.py | 白盒修复测试 |
| tests/unit/test_white_round2_fixes.py | 白盒第二轮修复测试 |
| tests/unit/test_k1_fixes.py | K1修复测试 |

### 2.4 配置与部署文件（8个文件）

| 文件 | 审查重点 |
|------|----------|
| desensitization_rules_safety.yaml | 规则异构冗余验证 |
| desensitization_rules_safety_patch.yaml | 补丁规则完整性 |
| desensitization_rules.yaml | 与主规则源对比（异构冗余） |
| medical_person_names.yaml | 人名词典完整性 |
| Dockerfile.safety-gateway.dev | 独立进程构建配置 |
| docker-compose.dev.yml | depends_on、健康检查、OOM保护、端口映射 |
| deploy/supervisor/safety-gateway.conf | 看门狗配置 |
| scripts/start_safety_gateway.sh | 启动脚本、OOM保护设置 |

---

## 三、审查执行计划

### 阶段1：第五轮遗留修复项验证（P0优先）

**目标**：验证第五轮审查报告中的9项修复建议是否已落地

| 修复项 | 描述 | 验证方法 |
|--------|------|----------|
| SG-FIX-R5-01 | threshold.py `_delete_threshold_from_db()`使用Core级delete → ORM级delete | 读取threshold.py第367-400行，确认使用`delete()` ORM API |
| SG-FIX-R5-02 | consent_checker.py `_cache_consent_to_redis()`死代码移除 | 读取consent_checker.py，确认方法已移除或标注 |
| SG-FIX-R5-03 | SafetyCheckRequest.desensitization_degraded接收但未使用 | 读取checker.py `perform_full_check()`，确认参数已使用或移除 |
| SG-FIX-R5-04 | safety_gateway_client.py `_degraded_response()`死代码 | 读取client.py，确认方法已移除或集成 |
| SG-FIX-R5-05 | safety_gateway_client.py使用已弃用asyncio.get_event_loop() | 读取client.py，确认已改为asyncio.get_running_loop() |
| SG-FIX-R5-06 | sg_db.py get_db_session()自动commit事务语义文档化 | 读取sg_db.py，确认文档字符串已补充 |
| SG-FIX-R5-07 | checker.py apply_degraded_output() list中None值未过滤 | 读取checker.py第706-717行，确认None过滤逻辑 |
| SG-FIX-R5-09 | safety_gateway_client.py类型注解风格不一致 | 读取client.py，确认已统一为Python 3.10+风格 |
| SG-FIX-R5-11 | health.py OOM保护状态在非Linux环境添加configured_in_compose | 读取health.py第278-288行，确认字段已添加 |

**验证标准**：逐项代码级确认，记录每项的修复状态（FIXED/NOT FIXED/PARTIAL）

### 阶段2：代码质量评估

#### 2.1 健壮性审查

| 审查项 | 审查方法 | 验证标准 |
|--------|----------|----------|
| 并发安全 | 检查所有共享状态的锁保护 | `_rules_lock`、`_sg_checker_lock`、`_latency_samples._lock`覆盖完整 |
| 资源泄漏 | 检查所有异步资源的正确关闭 | Redis连接、DB session、httpx client均有close/cleanup |
| 异常处理完整性 | 检查所有外部调用的异常捕获 | PG查询、Redis操作、HTTP调用均有try/except |
| 单例模式线程安全 | 检查所有单例的double-checked locking | `get_safety_gateway_checker()`、`get_consent_checker()`等 |
| 熔断器状态转换原子性 | 检查CircuitBreaker的_lock保护范围 | record_failure/record_success均在锁内 |

#### 2.2 可维护性审查

| 审查项 | 审查方法 | 验证标准 |
|--------|----------|----------|
| 模块职责边界 | 检查各模块的职责是否清晰单一 | checker/consent_checker/desensitization/threshold各司其职 |
| 依赖方向 | 检查safety_gateway/是否不导入融合层模块 | 无`from app.`或`from fusion.`导入 |
| 配置集中度 | 检查所有可调参数是否在sg_config.py中 | 无硬编码超时值、阈值等 |
| 错误类型层次 | 检查errors.py的异常继承结构 | 基类→子类层次清晰，错误码与设计文档对齐 |

#### 2.3 可扩展性审查

| 审查项 | 审查方法 | 验证标准 |
|--------|----------|----------|
| 降级场景扩展 | 检查DegradedReason枚举是否易于扩展 | 新增降级场景只需添加枚举值+处理分支 |
| 规则热更新 | 检查规则加载/重载机制 | reload_rules()支持回滚、Pub/Sub同步 |
| 阈值动态配置 | 检查阈值引擎的DB加载+Redis订阅 | 支持器官级和病种级阈值 |

#### 2.4 性能审查

| 审查项 | 审查方法 | 验证标准 |
|--------|----------|----------|
| P99延迟基准 | 检查health.py的延迟统计实现 | 500样本滑动窗口、百分位计算正确 |
| HTTP通信开销 | 检查client.py的连接池配置 | keep-alive + 连接池（20连接） |
| PG查询优化 | 检查consent_checker.py的查询效率 | 单条查询+索引友好 |
| Redis连接池隔离 | 检查redis_pool.py使用DB 1 | 与主服务DB 0隔离 |
| 内存预算监控 | 检查health.py的内存统计 | 512MB预算+超预算告警 |

### 阶段3：代码风格合规性审查

#### 3.1 编码规范

| 审查项 | 审查方法 | 验证标准 |
|--------|----------|----------|
| 类型注解风格 | 检查是否统一使用Python 3.10+语法 | `str \| None`而非`Optional[str]`，`dict[str, Any]`而非`Dict[str, Any]` |
| 命名约定 | 检查变量/函数/类命名一致性 | 类名PascalCase、函数名snake_case、常量UPPER_SNAKE |
| 文档字符串 | 检查关键类和方法的docstring | 公共API有docstring，说明参数和返回值 |
| 导入组织 | 检查import分组和排序 | 标准库→第三方→本地，组内字母序 |

#### 3.2 代码一致性

| 审查项 | 审查方法 | 验证标准 |
|--------|----------|----------|
| 错误处理模式 | 检查异常处理风格统一性 | SG内部抛SGError子类，不抛原生Exception |
| 日志格式 | 检查structlog使用一致性 | 统一使用`logger.info/warning/error`，结构化字段命名一致 |
| 异步模式 | 检查async/await使用一致性 | 无遗漏的async调用，无阻塞操作在异步上下文中 |
| 模型定义 | 检查Pydantic模型风格 | 统一使用`model_config`而非`class Config` |

### 阶段4：功能正确性验证

#### 4.1 进程隔离验证（P0）

| 验证项 | 设计要求 | 验证方法 |
|--------|----------|----------|
| 独立uvicorn实例 | SG以独立进程运行 | main.py第352-360行uvicorn.run独立启动 |
| HTTP localhost通信 | 主服务通过HTTP调用SG | client.py通过httpx AsyncClient调用 |
| 主服务崩溃时独立存活 | 返回E4005错误码 | docker-compose.dev.yml独立容器 + supervisor看门狗 |
| Docker Compose depends_on | api依赖safety-gateway健康检查 | docker-compose.dev.yml第75-76行 |
| OOM保护 | oom_score_adj=-1000 | docker-compose.dev.yml第130行 + start_safety_gateway.sh |

#### 4.2 四阶段校验流程验证（P0）

| 阶段 | 设计要求 | 验证方法 |
|------|----------|----------|
| 阶段1：同意状态校验 | 入口快速拒绝 + 输出前直查PG | checker.py第267-287行(快速拒绝) + 第330-410行(PG直查) |
| 阶段2：脱敏校验 | 结构化身份模式拦截 + 医疗人名词典 | checker.py第437-505行 |
| 阶段3：禁忌+阈值校验 | 禁忌规则引擎 + 置信度阈值 | checker.py第507-624行 |
| 阶段4：一致性校验 | 分级/发现/置信度差异检测 | checker.py第626-659行 |
| 执行顺序 | consent→desensitization→contraindication→threshold→consistency | perform_full_check()方法调用顺序 |

#### 4.3 7种降级场景验证（P0）

| 降级场景 | DegradedReason枚举 | 验证方法 |
|----------|---------------------|----------|
| 1. 敏感数据检测 | SENSITIVE_DATA_DETECTED | checker.py第494-505行 |
| 2. SG不可用 | SG_UNAVAILABLE | client.py超时→E4005 |
| 3. DB超时 | DB_TIMEOUT | consent_checker.py第164-170行 |
| 4. 禁忌命中 | CONTRAINDICATION_HIT | checker.py第566-586行 |
| 5. 置信度低于阈值 | CONFIDENCE_BELOW_THRESHOLD | checker.py第588-606行 |
| 6. 融合层不可用 | FUSION_UNAVAILABLE | checker.py第608-622行 |
| 7. 组合故障 | COMBINED_FAILURE | checker.py第542-564行 |
| 额外：边缘断连 | EDGE_DISCONNECTED | checker.py第412-435行 |
| 额外：脱敏降级+SG不可用 | DESENSITIZATION_DEGRADED_SG_UNAVAILABLE | checker.py第468-486行 |
| 额外：SG不可用+患者撤回 | SG_UNAVAILABLE_CONSENT_WITHDRAWN | checker.py第342-355行 |

#### 4.4 E4005系列错误码验证（P0）

| 错误码 | 触发条件 | 验证方法 |
|--------|----------|----------|
| E4005 | SG不可用（客户端通用） | client.py第184-191行_e4005_response() |
| E4005A | SG不可用（健康检查失败）或脱敏降级+SG检测到敏感数据 | errors.py第144-148行 + checker.py第454-466行 |
| E4005B | PG不可用+Redis缓存未命中 | errors.py第150-153行 + checker.py第363-366行 |
| E4005C | SG+融合层同时不可用 | errors.py第155-159行 + checker.py第358-362行 |

#### 4.5 安全合规关键项验证（P0）

| 验证项 | 设计要求 | 验证方法 |
|--------|----------|----------|
| 三重脱敏防线 | SG为第三道防线（结构化身份模式拦截） | three_layer_desensitization.py通过HTTP调用SG |
| 规则异构冗余 | desensitization_rules_safety.yaml独立编写 | 对比desensitization_rules.yaml，确认规则不完全相同 |
| 同意状态直查PG | 正常路径不写Redis缓存 | consent_checker.py无_cache_consent_to_redis调用 |
| 两阶段同意校验 | 入口快速拒绝 + 输出前直查PG | checker.py第267-287行 + 第330-410行 |
| A级安全标准 | 先校验后输出，零容忍 | perform_full_check()四阶段全部通过才PASSED |
| 禁忌校验fail-closed | 异常时默认存在禁忌 | client.py第165/168/171行返回has_contraindication=True |
| 降级输出仅白名单 | 过滤diagnostic_conclusion，仅保留observations | apply_degraded_output()白名单过滤 |
| OOM保护 | oom_score_adj=-1000 | docker-compose.dev.yml + start_safety_gateway.sh |

#### 4.6 边界条件与异常场景验证（P1）

| 验证项 | 验证方法 |
|--------|----------|
| 同意状态为None时的处理 | checker.py第381行：None→DENIED |
| 同意状态为非法值时的处理 | checker.py第275-277行：ValueError→DENIED |
| 递归深度超限时的处理 | checker.py第99/119/669行：MAX_RECURSION_DEPTH=10 |
| 规则编译失败时的处理 | desensitization.py第203-209行：跳过该规则 |
| 规则重载失败时的回滚 | desensitization.py第391-394行：恢复old_rules+old_patterns |
| 阈值加载失败时的降级 | threshold.py第171-176行：fallback到默认阈值 |
| Redis不可用时的降级 | consent_checker.py第155-162行：熔断器OPEN→Redis缓存→E4005B |
| PG不可用+Redis缓存未命中 | consent_checker.py第161-162行：返回(None, False, True)→E4005B |
| 组合故障（≥2项同时发生） | checker.py第542-543行：active_failures≥2→COMBINED_FAILURE |
| 空专科结论列表 | checker.py第168-173行：返回consistent=True |
| 白名单字段值为None | checker.py第700-701行：直接赋值None |
| 白名单字段值为list | checker.py第706-717行：逐项脱敏+None过滤 |

### 阶段5：测试逻辑审查

#### 5.1 测试覆盖率评估

| 指标 | 要求 | 当前 | 审查方法 |
|------|------|------|----------|
| 测试用例数 | 80-120 | 202（R5报告） | 统计tests/unit/test_safety_gateway.py测试方法数 |
| 降级场景覆盖 | 7种 | 7种（R5报告） | 检查TestDegradedPathSevenScenarios |
| E4005系列 | E4005/A/B/C | 全部（R5报告） | 检查对应测试方法 |
| 进程隔离 | Gate 0 | 存在（R5报告） | 检查TestServiceIsolation |
| P99延迟基准 | <5ms | 存在（R5报告） | 检查test_safety_gateway_performance.py |

#### 5.2 测试质量评估

| 维度 | 审查方法 |
|------|----------|
| Mock正确性 | 检查AsyncMock使用是否正确模拟异步行为 |
| 测试隔离性 | 检查setup/teardown是否正确清理全局状态 |
| 边界条件 | 检查TestBoundaryConditions是否覆盖关键边界 |
| 参数化覆盖 | 检查@pytest.mark.parametrize使用是否充分 |
| 断言完整性 | 检查断言是否验证了关键输出字段 |

#### 5.3 测试盲区识别

| 盲区（R5已识别） | 本轮验证方法 |
|-------------------|--------------|
| load_thresholds_from_db无真实DB测试 | 检查是否有集成测试覆盖 |
| _delete_threshold_from_db无测试 | 检查是否已添加测试 |
| 并发请求场景无测试 | 检查是否已添加并发测试 |
| client超时重试完整流程 | 检查端到端重试测试 |

### 阶段6：文档一致性比对

#### 6.1 与01-系统架构设计.md的一致性

| 需求项 | 设计要求 | 验证方法 |
|--------|----------|----------|
| 进程隔离 | 独立进程，HTTP localhost通信 | 确认three_layer_desensitization.py通过HTTP调用SG |
| 代码组织 | 不导入融合层模块 | grep safety_gateway/目录无融合层导入 |
| Redis连接 | 独立连接池(DB 1) | 确认SafetyGatewayRedisPool使用DB 1 |
| NER方案 | 纯正则+词典，不依赖ML NER | 确认desensitization.py无ML依赖 |
| 同意状态校验 | 直查PG，正常路径禁用缓存 | 确认consent_checker.py正常路径不写Redis |
| OOM保护 | oom_score_adj=-1000 | 确认Docker Compose + 启动脚本 |
| A级安全标准 | 先校验后输出，零容忍 | 确认四阶段校验流程 |
| 降级策略 | SG不可用→E4005 | 确认client.py + checker.py |
| 内存预算 | ≤512MB | 确认health.py监控 + sg_memory_budget_mb |
| 健康检查 | 每4s探测，超时1s，3次失败 | 确认Docker Compose配置 |
| HTTP通信开销P99 | <5ms | 确认P99阈值配置(warning=4ms, critical=5ms) |
| 端到端处理延迟P99 | <50ms | 检查是否有端到端延迟监控 |

#### 6.2 与04-安全合规.md的一致性

| 需求项 | 设计要求 | 验证方法 |
|--------|----------|----------|
| 三重脱敏防线 | SG为第三道防线 | 确认three_layer_desensitization.py实现 |
| 规则异构冗余 | 独立编写规则集 | 对比两个YAML文件的规则差异 |
| 双重降级保障 | 双重缺失时拒绝输出 | 确认E4005A错误码实现 |
| 患者同意管理 | 两阶段校验 | 确认入口快速拒绝 + 输出前直查PG |
| 禁忌校验fail-closed | 安全系统默认拒绝 | 确认client.py异常时返回has_contraindication=True |
| consent_status_at_output | SG输出前直查PG获取同意状态快照 | 确认SafetyCheckResponse包含该字段 |

#### 6.3 与06-开发环境与进度规划.md的一致性

| 需求项 | 设计要求 | 验证方法 |
|--------|----------|----------|
| 代码组织 | 独立Python package safety_gateway/ | 确认目录结构 |
| 代码量 | 2500-3000行 | 统计safety_gateway/目录总行数 |
| 测试用例数 | 80-120个 | 统计测试方法数 |
| 端口配置 | SAFETY_GATEWAY_PORT=9001 | 确认sg_config.py默认值 |
| Gate 0 | 主服务崩溃时独立返回E4005 | 确认TestServiceIsolation |
| Docker Compose | depends_on + 健康检查 | 确认docker-compose.dev.yml |

---

## 四、审查执行步骤

### Step 1：第五轮遗留修复项验证
- 逐项读取相关源码文件，确认9项修复建议的落地状态
- 记录每项状态：FIXED / NOT FIXED / PARTIAL / SUPERSEDED

### Step 2：代码质量深度审查
- 2.1 逐文件审查safety_gateway/模块17个源码文件
- 2.2 审查app/services/safety_gateway_client.py
- 2.3 识别新发现的代码质量问题

### Step 3：代码风格合规性审查
- 3.1 检查类型注解风格统一性
- 3.2 检查命名约定一致性
- 3.3 检查文档字符串完整性

### Step 4：功能正确性验证
- 4.1 进程隔离验证
- 4.2 四阶段校验流程验证
- 4.3 降级场景验证
- 4.4 E4005错误码验证
- 4.5 安全合规关键项验证
- 4.6 边界条件验证

### Step 5：测试逻辑审查
- 5.1 测试覆盖率评估
- 5.2 测试质量评估
- 5.3 测试盲区识别

### Step 6：文档一致性比对
- 6.1 与01-系统架构设计.md比对
- 6.2 与04-安全合规.md比对
- 6.3 与06-开发环境与进度规划.md比对

### Step 7：生成审查报告
- 汇总所有发现
- 按P0/P1/P2分级
- 显式声明SG轨道修复项
- 提出改进建议及优先级

---

## 五、审查报告结构

审查报告将包含以下章节：

1. **审查范围与方法**
2. **第五轮遗留修复项验证结果**
3. **新发现的问题**（按P0/P1/P2分级）
4. **代码质量评估结果**
5. **代码风格合规性评估结果**
6. **功能正确性验证结果**
7. **测试逻辑审查结果**
8. **与参考文档的偏差分析**
9. **SafetyGateway轨道修复项显式声明**（核心输出）
10. **审查结论与改进建议**

---

## 六、SafetyGateway轨道修复项显式声明格式

每个修复项将按以下格式声明：

```
### SG-FIX-R6-XX：[简要描述]
- **严重程度**：P0/P1/P2
- **文件**：[具体文件路径]
- **行号**：[具体行号范围]
- **问题描述**：[详细描述]
- **设计文档参考**：[引用具体文档和章节]
- **修复建议**：[具体修复方案]
- **修复类型**：代码修复 / 代码清理 / 代码增强 / 文档补充 / 配置修正
```

---

## 七、自我反思机制

审查完成后，将进行以下自我反思：

1. **与前五轮对比**：问题严重程度是否持续降低？是否有新引入的风险？
2. **审查盲区**：是否有遗漏的审查维度？
3. **修复建议可行性**：修复建议是否具备可操作性？
4. **设计偏差合理性**：代码实现优于设计文档的情况是否被正确识别？
