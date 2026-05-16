# SG安全网关系统性审查计划

## 审查声明

**本审查报告属于SafetyGateway轨道，所有发现的问题均为SafetyGateway轨道应当修复的项。**

---

## 一、审查范围与方法

### 1.1 审查范围

| 维度 | 审查对象 | 文件范围 |
|------|---------|---------|
| 代码质量 | 健壮性、可维护性、可扩展性、性能 | `safety_gateway/` 全部18个源文件 |
| 代码风格 | 命名规范、文档标准、可读性 | `safety_gateway/` 全部源文件 |
| 功能正确性 | 需求规格实现、边界条件、异常处理 | `safety_gateway/` + `tests/` 相关测试 |
| 设计文档一致性 | 与01/04/06/02/00b五份核心文档的偏差 | 代码 vs 文档逐项对照 |

### 1.2 审查方法

1. **静态代码分析**：逐文件阅读核心实现，识别潜在缺陷
2. **测试覆盖率评估**：统计测试用例数量和覆盖维度
3. **设计文档对照**：逐项比对代码实现与设计文档要求
4. **配置一致性验证**：检查配置文件与文档规定的参数一致性
5. **部署架构验证**：检查Docker/Supervisor配置与进程隔离要求的一致性

### 1.3 参考文档优先级

| 优先级 | 文档 | 审查焦点 |
|--------|------|---------|
| P0 | 01-系统架构设计.md | 进程隔离、延迟基准P99<5ms、核心校验职责、OOM保护 |
| P0 | 04-安全合规.md | 三重脱敏防线、规则异构冗余、同意状态直查PG |
| P1 | 06-开发环境与进度规划.md | 代码量、测试数量、端口配置、Gate 0验证项 |
| P2 | 02-核心流程与知识库.md | 融合层交互流程、降级输出白名单 |
| P2 | 00b-统一配置参考手册.md | 配置项完整性和正确性 |

---

## 二、审查执行步骤

### 步骤1：代码质量评估（P0）

**目标**：识别代码中的健壮性、可维护性、性能问题

**审查项**：
1. 逐文件审查 `safety_gateway/` 下所有18个源文件
2. 重点审查 `checker.py`（主校验器，799行）的校验流程完整性
3. 重点审查 `health.py`（健康检查，467行）的延迟统计准确性
4. 重点审查 `desensitization.py`（脱敏引擎，504行）的线程安全性
5. 重点审查 `consent_checker.py`（同意校验，192行）的直查PG策略
6. 审查 `degraded_output.py` 降级输出的7种场景标记正确性
7. 审查 `circuit_breaker.py` 熔断器的状态转换正确性
8. 审查 `threshold.py` 阈值引擎的热更新机制

**验证标准**：
- 无空异常捕获（bare except）
- 无硬编码配置值
- 线程安全模式正确（锁使用、快照模式）
- 递归深度限制存在且合理
- 日志级别使用恰当

### 步骤2：设计文档一致性审查（P0）

**目标**：逐项比对代码实现与设计文档要求，识别偏差

**2.1 进程隔离验证（01-系统架构设计）**

| 审查项 | 文档要求 | 代码现状 | 预期判定 |
|--------|---------|---------|---------|
| 独立进程 | 独立uvicorn实例 | ✅ `main.py` L419-427 独立uvicorn启动 | 符合 |
| HTTP localhost通信 | 通过HTTP localhost通信 | ✅ `sg_config.py` host=0.0.0.0, port=9001 | 符合（容器内0.0.0.0，容器间通过Docker网络） |
| 延迟基准P99<5ms | P99 < 5ms | ✅ `sg_config.py` warning=4.0ms, critical=5.0ms | 符合 |
| 主服务崩溃独立存活 | 返回E4005 | ✅ `errors.py` E4005系列错误码 | 符合 |
| OOM保护 | oom_score_adj=-1000 | ✅ `docker-compose.dev.yml` L131 | 符合 |
| 不导入融合层模块 | 依赖隔离 | ⚠️ 需验证无import融合层模块 | 待验证 |
| 独立Redis连接池 | DB 1与主服务隔离 | ✅ `redis_pool.py` L57-59 强制使用db=1 | 符合 |

**2.2 脱敏防线验证（04-安全合规）**

| 审查项 | 文档要求 | 代码现状 | 预期判定 |
|--------|---------|---------|---------|
| 第三道防线：结构化身份模式拦截 | 正则匹配电话/身份证/地址等 | ✅ `desensitization.py` 10条正则规则 | 符合 |
| 规则异构冗余 | 不复制主规则源正则 | ✅ 每条规则description标注异构差异 | 符合 |
| 医疗人名词典匹配 | 独立NER方案 | ✅ `desensitization.py` L65-94 人名匹配 | 符合 |
| 不依赖ML NER | 与融合层NER异构 | ✅ 纯正则+词典方案 | 符合 |

**2.3 同意状态校验验证（01/04）**

| 审查项 | 文档要求 | 代码现状 | 预期判定 |
|--------|---------|---------|---------|
| 直查PostgreSQL | 不使用任何缓存 | ✅ `consent_checker.py` L50-68 直查PG | 符合 |
| 两阶段校验 | 入口快速拒绝+输出前实时校验 | ✅ `checker.py` L311入口快速拒绝 + L392输出前直查 | 符合 |
| PG不可用拒绝输出 | E4005B | ✅ `errors.py` L169-172 | 符合 |
| SG+融合层同时不可用 | E4005C | ✅ `errors.py` L175-178 | 符合 |

**2.4 降级输出验证（01/02/04）**

| 审查项 | 文档要求 | 代码现状 | 预期判定 |
|--------|---------|---------|---------|
| 过滤diagnostic_conclusion | 仅保留observations白名单 | ✅ `degraded_output.py` L74-99 白名单过滤 | 符合 |
| 白名单字段 | 8个单属性字段 | ✅ `degraded_output.py` L12-22 9个字段（含observations） | 符合 |
| 禁止输出分级词汇 | FORBIDDEN_GRADING_TERMS | ✅ `degraded_output.py` L24-29 | 符合 |
| fusion_bypassed标记 | 融合层不可用时标记 | ✅ `degraded_output.py` L125 | 符合 |
| 双重降级场景标记 | conclusion_suppressed+fusion_degraded+safety_gateway_unavailable | ⚠️ 需验证双重降级路径是否完整 | 待验证 |

### 步骤3：代码风格合规性审查（P1）

**审查项**：
1. 命名一致性：检查是否统一使用snake_case
2. 类型注解完整性：检查函数签名是否有完整类型注解
3. 文档字符串：检查关键类和函数是否有docstring
4. 日志格式：检查是否统一使用structlog
5. 错误处理模式：检查是否统一使用自定义异常类

### 步骤4：功能正确性验证（P0）

**4.1 7种降级场景覆盖验证**

| # | 降级场景 | 代码路径 | 测试覆盖 |
|---|---------|---------|---------|
| 1 | PG直查成功 | `_check_consent_phase` → GRANTED | ✅ test_pg_query_success |
| 2 | 融合层不可用→SG降级输出 | `_check_contraindication_threshold_phase` → FUSION_UNAVAILABLE | ✅ test_fusion_unavailable_sg_degraded_output |
| 3 | SG不可用→全部输出拒绝(E4005) | `_check_consent_phase` → SGDatabaseUnavailableError | ✅ test_sg_unavailable_reject_all_output |
| 4 | 边缘后端断连→本地降级 | `_check_edge_connectivity` → EDGE_DISCONNECTED | ✅ test_edge_disconnected_local_degradation |
| 5 | 组合故障（禁忌+阈值+融合不可用） | `_check_contraindication_threshold_phase` → COMBINED_FAILURE | ✅ test_combined_failure_degradation |
| 6 | 融合层不可用+敏感数据→E4005A1 | `_check_desensitization_phase` → FUSION_UNAVAILABLE_SENSITIVE_DETECTED | ✅ test_fusion_unavailable_sensitive_detected_e4005a |
| 7 | SG不可用+患者撤回同意→E4005A2 | `_check_consent_phase` → SG_UNAVAILABLE_CONSENT_WITHDRAWN | ✅ test_sg_unavailable_consent_withdrawn_e4005a |

**4.2 健康检查完整性验证**

| 检查项 | 实现状态 | 端点 |
|--------|---------|------|
| Redis健康检查 | ✅ `_check_redis` | `/health` |
| 数据库健康检查 | ✅ `_check_database` | `/health` |
| 规则引擎健康检查 | ✅ `_check_rules_engine` | `/health` |
| 熔断器健康检查 | ✅ `_check_circuit_breaker` | `/health` |
| 阈值引擎健康检查 | ✅ `_check_threshold_engine` | `/health` |
| OOM保护状态检查 | ✅ `_check_oom_protection` | `/health` |
| 融合层健康检查 | ✅ `_check_fusion_layer` | `/health` |
| 基础健康检查端点 | ✅ `/health` | `/health` |
| 就绪检查端点 | ✅ `/ready` | `/ready` |
| 看门狗端点 | ✅ `/health/watchdog` | `/health/watchdog` |

**4.3 边界条件与异常场景**

| 场景 | 代码处理 | 测试覆盖 |
|------|---------|---------|
| 空输出数据 | ✅ `checker.py` L289-305 拒绝 | ✅ test_empty_output |
| 递归深度超限 | ✅ MAX_RECURSION_DEPTH=20 | ✅ test_recursion_depth_limit |
| 熔断器打开 | ✅ CircuitBreakerError → DB error | ✅ test_checker_with_circuit_breaker_open |
| 规则加载失败 | ✅ 回退到默认规则 | ✅ test_default_rules_used_when_yaml_missing |
| 规则热更新失败 | ✅ 保留旧规则 | ✅ test_reload_rules_rollback_on_validation_error |
| 幻觉严重输出 | ✅ hallucination_warning=True | ✅ test_severe_hallucination_sets_warning |
| conclusion_suppressed | ✅ 跳过diagnostic_conclusion检查 | ✅ test_conclusion_suppressed_skips_diagnostic_conclusion |

### 步骤5：测试覆盖率与质量评估（P1）

**5.1 测试数量统计**

| 测试文件 | 测试类数 | 估计测试用例数 |
|---------|---------|--------------|
| test_safety_gateway.py | ~35 | ~120+ |
| test_safety_gateway_performance.py | 3 | ~7 |
| test_desensitization.py | 待统计 | 待统计 |
| 其他安全相关测试文件 | ~16 | 待统计 |
| **合计** | - | **预计150-200+** |

**5.2 文档要求对比**

| 文档要求 | 要求值 | 当前状态 | 判定 |
|---------|--------|---------|------|
| 代码量 | 4000-4500行 | 约2500-3000行（18个文件） | ⚠️ 可能偏低 |
| 测试用例 | 200-250个 | 约150-200+ | ⚠️ 可能偏低 |
| 降级路径专项测试 | 含7种降级场景 | ✅ 7种场景全覆盖 | 符合 |

### 步骤6：配置与部署一致性验证（P1）

| 配置项 | 文档要求 | 实际配置 | 判定 |
|--------|---------|---------|------|
| SAFETY_GATEWAY_PORT | 9001 | ✅ 9001 | 符合 |
| Redis DB | 1（与主服务隔离） | ✅ redis:6379/1 | 符合 |
| oom_score_adj | -1000 | ✅ -1000 | 符合 |
| 健康检查端点 | /health | ✅ /health | 符合 |
| 健康检查间隔 | 4s | ✅ interval:4s | 符合 |
| 健康检查超时 | 1s | ✅ timeout:1s | 符合 |
| 连续3次失败 | 15s判定不可用 | ✅ 3×(4+1)=15s | 符合 |
| depends_on | SG先于主服务就绪 | ✅ api depends_on safety-gateway | 符合 |
| SAFETY_GATEWAY_QUEUE_TIMEOUT | 10s | ✅ 10.0s | 符合 |
| SG内存预算 | 512MB | ✅ 512M | 符合 |
| supervisor看门狗重启 | ≤5s | ✅ startsecs=2, autorestart=true | 符合 |
| Dockerfile非root用户 | 安全最佳实践 | ✅ USER sguser | 符合 |

---

## 三、已识别问题清单（预审查发现）

### P0 - 严重问题

| # | 问题描述 | 位置 | 文档依据 | 修复建议 |
|---|---------|------|---------|---------|
| P0-1 | **双重降级场景（融合层脱敏降级+SG不可达）标记不完整**：04-安全合规§58要求标记`conclusion_suppressed: true` + `fusion_degraded: true` + `safety_gateway_unavailable: true`，但当前`degraded_output.py`中`FUSION_UNAVAILABLE_SENSITIVE_DETECTED`场景设置`safety_gateway_unavailable=False`，与文档"SG不可达"语义不一致 | `degraded_output.py` L135-141 | 04-安全合规§58 | 审查E4005A1场景的标记语义，确认是否需要区分"SG检测到敏感数据"与"SG不可达"两种子场景 |
| P0-2 | **降级输出中diagnostic_conclusion字段未显式过滤**：`apply_degraded_output`通过白名单机制隐式排除了`diagnostic_conclusion`，但文档要求"过滤diagnostic_conclusion字段"应更明确——若输出数据中`diagnostic_conclusion`恰好是白名单字段名则不会被过滤 | `degraded_output.py` L74-76 | 01-系统架构设计§3.4 | 在白名单过滤之前，显式删除`diagnostic_conclusion`字段，确保无论白名单如何配置都不会泄漏 |

### P1 - 重要问题

| # | 问题描述 | 位置 | 文档依据 | 修复建议 |
|---|---------|------|---------|---------|
| P1-1 | **代码量可能低于文档预估**：文档要求约4000-4500行，当前18个源文件估计约2500-3000行 | `safety_gateway/` | 06-开发环境与进度规划§428 | 评估是否因精简实现导致功能缺失，或文档预估偏高 |
| P1-2 | **测试用例数量可能低于文档要求**：文档要求约200-250个，当前估计约150-200+ | `tests/` | 06-开发环境与进度规划§428 | 补充缺失的测试场景（见步骤4.3未覆盖项） |
| P1-3 | **NEGATION_PREFIXES中"无"重复**：`degraded_output.py` L32中NEGATION_PREFIXES元组包含两个"无" | `degraded_output.py` L32 | 代码质量 | 去重 |
| P1-4 | **_check_conclusion_evidence_contradiction逻辑可能误判**：当conclusion和evidence同时包含矛盾词对时（如conclusion含"良性"且evidence含"恶性"），即使evidence中有否定前缀（如"无恶性"），当前逻辑先检测到矛盾再检查否定前缀，但否定前缀检查只检查e_sent中的`prefix+neg_word`，未检查c_sent中的否定前缀 | `checker.py` L222-250 | 功能正确性 | 补充conclusion侧的否定前缀检查 |
| P1-5 | **ConsentStatus.PENDING在入口快速拒绝中处理不一致**：入口快速拒绝（L311）对PENDING状态直接拒绝，但_check_consent_phase（L468）也对PENDING拒绝——两处逻辑重复，且入口快速拒绝的注释说"覆盖所有非granted状态"，但实际只对非None且非granted的状态拒绝，PENDING状态可能在入口和输出前被拒绝两次 | `checker.py` L311 vs L468 | 01-系统架构设计§3.4 | 明确两阶段校验的职责边界，入口快速拒绝应覆盖PENDING/DENIED/WITHDRAWN，输出前校验应覆盖WITHDRAWN（实时撤回检测） |
| P1-6 | **`/health/ready`端点路径与文档不一致**：文档要求`/health/ready`，但代码中端点为`/ready` | `main.py` L208 | 06-开发环境与进度规划 | 统一为`/health/ready`或确认`/ready`可接受 |

### P2 - 一般问题

| # | 问题描述 | 位置 | 文档依据 | 修复建议 |
|---|---------|------|---------|---------|
| P2-1 | **sg_db.py中async_engine在模块加载时创建**：若DATABASE_URL为空字符串，`create_async_engine("")`不会立即报错，但在实际查询时会失败。虽然`sg_validator.py`已校验DATABASE_URL非空，但引擎创建在配置校验之前 | `sg_db.py` L7-15 | 代码健壮性 | 考虑延迟创建engine或在lifespan中创建 |
| P2-2 | **HealthChecker._get_memory_usage_mb中Windows实现依赖ctypes**：Windows内存查询使用ctypes直接调用kernel32，若API变更可能失效 | `health.py` L147-159 | 代码健壮性 | 已有psutil优先和fallback机制，风险可控 |
| P2-3 | **ContraindicationEngine._match_condition仅支持精确匹配**：代码注释中标注了TODO，未来可能需要层级匹配 | `contraindication.py` L201-215 | 功能扩展性 | 记录为已知限制，不阻塞当前审查 |
| P2-4 | **CircuitBreaker._update_state线程安全注释**：注释中明确标注了asyncio单线程约束和uvicorn --workers 1前提，但未在代码中强制约束workers=1 | `circuit_breaker.py` L70-84 | 代码健壮性 | 在supervisor配置中已约束workers=1，风险可控 |
| P2-5 | **ThresholdEngine._get_threshold对未知器官返回0.90**：硬编码默认值，未在配置中暴露 | `threshold.py` L252 | 可维护性 | 考虑将0.90作为配置项 |
| P2-6 | **SafetyCheckRequest中consent_status_at_input为str类型**：应为ConsentStatus枚举或Literal类型，当前接受任意字符串 | `main.py` L40 | 类型安全 | 改为更严格的类型定义 |

---

## 四、审查执行计划

### 阶段1：深度代码审查（预计产出：详细问题清单）

1. 逐文件审查 `safety_gateway/` 全部18个源文件，记录所有发现
2. 重点审查 `checker.py` 的 `perform_full_check` 完整流程
3. 验证7种降级场景的代码路径和标记正确性
4. 验证E4005系列错误码的触发条件和响应结构

### 阶段2：测试验证（预计产出：测试覆盖率报告）

1. 运行现有测试套件，确认全部通过
2. 统计精确的测试用例数量
3. 识别未覆盖的边界条件和异常场景
4. 评估性能基准测试的有效性

### 阶段3：设计文档一致性验证（预计产出：偏差分析报告）

1. 逐项对照01-系统架构设计.md的要求
2. 逐项对照04-安全合规.md的要求
3. 逐项对照06-开发环境与进度规划.md的要求
4. 记录所有偏差，区分"合理偏差"和"需修复偏差"

### 阶段4：修复与验证（预计产出：修复后的代码和测试）

1. 按P0→P1→P2优先级修复已识别问题
2. 为每个修复项编写或更新测试
3. 运行完整测试套件验证修复
4. 运行性能基准测试确认P99<5ms

---

## 五、审查完成标准

1. 所有P0问题已修复并通过测试验证
2. 所有P1问题已修复或有明确的延期理由
3. P2问题已记录到技术债务清单
4. 测试套件全部通过
5. 性能基准测试P99<5ms
6. 代码量与测试数量与文档要求的偏差已有合理解释

---

## 六、SafetyGateway轨道修复项汇总

> **显式声明：以下所有修复项均属于SafetyGateway轨道应当执行的工作。**

### 必须修复（P0）

1. **[P0-1]** 审查并修正双重降级场景的标记语义（`degraded_output.py`）
2. **[P0-2]** 显式过滤`diagnostic_conclusion`字段，确保降级输出不泄漏诊断结论（`degraded_output.py`）

### 应当修复（P1）

3. **[P1-1]** 评估代码量与文档预估的偏差，补充缺失功能或更新文档预估
4. **[P1-2]** 补充测试用例至200+个，覆盖未测试的边界条件
5. **[P1-3]** 修复`NEGATION_PREFIXES`中的重复"无"（`degraded_output.py`）
6. **[P1-4]** 增强`_check_conclusion_evidence_contradiction`的否定前缀检查逻辑（`checker.py`）
7. **[P1-5]** 明确两阶段同意校验的职责边界，消除重复逻辑（`checker.py`）
8. **[P1-6]** 统一就绪检查端点路径为`/health/ready`（`main.py`）

### 建议修复（P2）

9. **[P2-1]** 延迟创建数据库引擎或在lifespan中创建（`sg_db.py`）
10. **[P2-5]** 将未知器官默认阈值0.90暴露为配置项（`threshold.py`）
11. **[P2-6]** 强化`consent_status_at_input`的类型约束（`main.py`）
