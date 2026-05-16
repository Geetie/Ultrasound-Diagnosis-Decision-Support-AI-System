# SG安全网关轨道系统性审查计划（第五轮）

**审查日期**：2026-05-10
**审查范围**：SafetyGateway轨道全部源码、测试、配置与部署文件
**审查原则**：独立、客观、深入、批判性
**前置参考**：第四轮审查报告（sg-safety-gateway-audit-report-round4.md）

---

## 一、审查背景与目标

### 1.1 前四轮审查回顾

| 轮次 | 修复项数 | 修复率 | 关键发现 |
|------|----------|--------|----------|
| R1 | 15 | 93% | 初始审计，发现基础缺陷 |
| R2 | 12 | 92% | 深入审查，发现并发与资源泄漏 |
| R3 | 30 | 97% | 全面审查，修复大量Mock/测试问题 |
| R4 | 10 | 90% | 发现P0级Core Select数据类型Bug、进程隔离违反 |

### 1.2 本轮审查目标

1. **验证R4遗留问题**：确认SG-FIX-R4-01（threshold.py Core Select）和SG-FIX-R4-02（进程隔离违反）是否已修复
2. **深度代码质量审查**：从健壮性、可维护性、可扩展性、性能四个维度系统性评估
3. **功能正确性验证**：系统性测试边界条件和异常处理机制
4. **文档一致性比对**：对照5份核心设计文档进行一致性审查
5. **测试逻辑完整性审查**：覆盖率、Mock正确性、隔离性、边界条件

---

## 二、审查范围与方法

### 2.1 审查范围

| 类别 | 文件数 | 审查深度 |
|------|--------|----------|
| 核心源码 `safety_gateway/` | 17 | 逐文件静态分析 + 执行路径追踪 |
| 客户端与集成层 | 6 | 关键路径审查 |
| 测试文件 | 12 | 覆盖率 + 质量审查 |
| 配置与部署 | 7 | 一致性审查 |
| 设计文档 | 5 | 一致性比对 |

### 2.2 审查方法

1. **R4遗留验证**：逐项代码级确认SG-FIX-R4-01~R4-10修复状态
2. **静态代码分析**：运行时崩溃风险、并发安全、资源泄漏、性能瓶颈
3. **执行路径追踪**：四阶段校验流程、7种降级场景、同意状态校验
4. **测试逻辑审查**：覆盖率、Mock正确性、隔离性、边界条件
5. **文档一致性比对**：对照5份核心设计文档
6. **安全合规专项**：脱敏规则异构冗余、同意状态校验、降级策略

---

## 三、审查执行步骤

### 步骤1：R4遗留问题验证（P0优先）

**验证项**：

| 修复项 | 验证方法 | 预期结果 |
|--------|----------|----------|
| SG-FIX-R4-01 | 检查threshold.py `load_thresholds_from_db()`和`_persist_threshold_to_db()`是否使用ORM select | 使用`select(OrganDiseaseThreshold)`替代Core Select |
| SG-FIX-R4-02 | 检查three_layer_desensitization.py是否通过HTTP调用SG | 使用`safety_gateway_client.check_output()`替代进程内调用 |
| SG-FIX-R4-03 | 检查desensitization.py `desensitize()`是否使用_rules_lock | 加锁或使用快照模式 |
| SG-FIX-R4-04 | 检查checker.py `FORBIDDEN_GRADING_TERMS`类型 | 改为tuple并按长度降序 |
| SG-FIX-R4-05 | 检查safety_gateway_client.py `check_contraindication()`失败时返回值 | 返回fail-closed |
| SG-FIX-R4-06 | 检查safety_gateway_client.py `_degraded_response()`是否有调用点 | 移除或集成 |
| SG-FIX-R4-07 | 检查SafetyCheckRequest是否包含`desensitization_degraded`字段 | 添加或移除客户端发送 |
| SG-FIX-R4-08 | 检查health.py compact模式redis_status["impact"]逻辑 | 移入条件分支 |
| SG-FIX-R4-09 | 检查safety_gateway_client.py httpx超时是否可配置 | 提取为配置项 |
| SG-FIX-R4-10 | 检查00b文档是否补充26个配置项 | 文档更新 |

### 步骤2：代码质量评估（四大维度）

#### 2.1 健壮性审查

- [ ] **运行时崩溃风险**：检查所有数据库查询路径（threshold.py, consent_checker.py）是否正确使用ORM API
- [ ] **并发安全**：检查所有共享状态的线程安全（checker.py单例、desensitization.py规则锁、threshold.py加载锁）
- [ ] **资源泄漏**：检查HTTP客户端、Redis连接、数据库会话的生命周期管理
- [ ] **异常处理完整性**：检查所有async函数的异常传播链是否正确

#### 2.2 可维护性审查

- [ ] **代码组织**：模块职责是否清晰，是否存在循环依赖
- [ ] **死代码**：是否存在未使用的方法、参数、导入
- [ ] **日志规范**：structlog使用是否一致，关键路径是否都有日志
- [ ] **配置管理**：所有配置项是否通过sg_settings统一管理

#### 2.3 可扩展性审查

- [ ] **新增器官/病种**：添加新器官阈值或禁忌规则是否需要修改核心代码
- [ ] **新增脱敏规则**：添加新规则是否只需修改YAML文件
- [ ] **新增降级场景**：DegradedReason枚举扩展是否方便

#### 2.4 性能审查

- [ ] **P99延迟基准**：校验流程是否满足P99 < 5ms要求
- [ ] **数据库查询优化**：consent_checker查询是否使用索引、是否避免N+1
- [ ] **Redis连接池**：连接池配置是否合理，是否有连接泄漏风险
- [ ] **内存预算**：是否满足≤512MB要求

### 步骤3：功能正确性验证

#### 3.1 四阶段校验流程

- [ ] **阶段1（同意状态）**：GRANTED→继续、DENIED→FAILED、WITHDRAWN→FAILED、PENDING→FAILED、None→FAILED
- [ ] **阶段2（脱敏校验）**：无敏感→继续、有敏感+融合可用→DEGRADED、有敏感+融合不可用→FAILED(E4005A)
- [ ] **阶段3（禁忌+阈值）**：单项→DEGRADED、双项+→COMBINED_FAILURE
- [ ] **阶段4（一致性）**：一致→PASSED、不一致→FAILED

#### 3.2 7种降级场景

1. Redis不可用 → PG轮询降级
2. 融合层不可用 → SG降级输出
3. SG不可用 → 全部输出拒绝(E4005)
4. 边缘后端断连 → 本地降级
5. 组合故障 → COMBINED_FAILURE
6. 脱敏降级+SG不可用 → E4005A
7. SG不可用+患者撤回同意 → E4005A

#### 3.3 E4005子错误码

- E4005A：SafetyGateway不可用
- E4005B：PostgreSQL不可用
- E4005C：SafetyGateway + 融合层同时不可用

#### 3.4 边界条件

- 空输入、空结论、空字典
- 递归深度超限（MAX_RECURSION_DEPTH=10）
- 置信度=0、置信度=1、置信度=阈值精确值
- 未知器官阈值回退（默认0.90）
- 同意状态为无效字符串时的回退

### 步骤4：代码风格合规性

- [ ] **命名规范**：变量名、函数名、类名是否一致
- [ ] **类型注解**：是否使用Python 3.10+类型语法（`str | None`替代`Optional[str]`）
- [ ] **文档字符串**：关键函数是否有docstring
- [ ] **导入规范**：是否使用绝对导入，是否按标准库/第三方/本地分组

### 步骤5：文档一致性比对

#### 5.1 P0文档：01-系统架构设计.md

| 需求项 | 验证方法 |
|--------|----------|
| 独立进程 | 检查three_layer_desensitization.py是否通过HTTP调用 |
| HTTP localhost通信 | 检查safety_gateway_client.py使用httpx调用 |
| P99 < 5ms | 检查性能基准测试 |
| E4005错误码 | 检查errors.py和checker.py |
| oom_score_adj=-1000 | 检查docker-compose.dev.yml和start_safety_gateway.sh |
| A级安全标准 | 检查checker.py四阶段校验流程 |
| 内存预算≤512MB | 检查health.py监控和sg_memory_budget_mb |

#### 5.2 P0文档：04-安全合规.md

| 需求项 | 验证方法 |
|--------|----------|
| 三重脱敏防线 | 检查three_layer_desensitization.py三层实现 |
| 规则异构冗余 | 检查desensitization_rules_safety.yaml与主规则差异 |
| 两阶段同意校验 | 检查入口快速拒绝+输出前直查PG |
| 双重降级保障 | 检查E4005A实现 |

#### 5.3 P1文档：06-开发环境与进度规划.md

| 需求项 | 验证方法 |
|--------|----------|
| 独立Python package | 检查safety_gateway/目录结构 |
| 代码量2500-3000行 | 统计代码行数 |
| 测试用例80-120个 | 统计测试方法数 |
| 端口9001 | 检查sg_config.py默认值 |
| Gate 0验证 | 检查TestMainServiceCrashScenario |
| Docker Compose配置 | 检查depends_on和健康检查 |

#### 5.4 P2文档：02-核心流程与知识库.md + 00b-统一配置参考手册.md

| 需求项 | 验证方法 |
|--------|----------|
| 白名单字段名 | 检查sg_config.py和checker.py |
| 配置项文档化 | 检查00b文档覆盖度 |
| 配置约束关系 | 检查sg_validator.py |

### 步骤6：测试逻辑审查

#### 6.1 覆盖率评估

- [ ] 核心模块测试覆盖：checker、desensitization、consent_checker、threshold、circuit_breaker、health
- [ ] 降级路径测试覆盖：7种降级场景
- [ ] 错误码测试覆盖：E4005/A/B/C
- [ ] 进程隔离测试覆盖：TestServiceIsolation、TestMainServiceCrashScenario
- [ ] 性能基准测试覆盖：P99延迟

#### 6.2 测试质量评估

- [ ] Mock正确性：是否使用AsyncMock、是否mock了正确的对象
- [ ] 测试隔离性：setup/teardown是否清理全局状态
- [ ] 边界条件：空输入、极端值、递归深度
- [ ] 参数化覆盖：脱敏引擎是否使用@pytest.mark.parametrize

#### 6.3 测试盲区识别

- [ ] 真实数据库路径测试（load_thresholds_from_db）
- [ ] 进程隔离端到端测试（三层脱敏HTTP调用）
- [ ] 客户端禁忌校验fail-closed测试
- [ ] Redis连接池耗尽场景
- [ ] 并发请求场景

---

## 四、审查输出

### 4.1 审查报告结构

1. **审查范围与方法**
2. **R4遗留问题验证结果**
3. **新发现的问题**（按P0/P1/P2分级）
4. **与参考文档的偏差分析**
5. **SafetyGateway轨道修复项显式声明**
6. **测试逻辑审查结果**
7. **安全合规关键项审查**
8. **审查结论与修复优先级建议**
9. **自我反思**

### 4.2 修复项编号规则

- 格式：`SG-FIX-R5-{NN}`
- P0级：生产运行时崩溃风险
- P1级：功能缺陷/安全隐患
- P2级：代码质量/文档缺失

---

## 五、审查执行计划

| 步骤 | 内容 | 预计产出 |
|------|------|----------|
| 1 | R4遗留问题代码级验证 | 10项验证结果 |
| 2 | 核心源码逐文件静态分析 | 新发现问题清单 |
| 3 | 执行路径追踪（四阶段+7种降级） | 路径覆盖报告 |
| 4 | 测试逻辑审查 | 盲区清单 |
| 5 | 文档一致性比对 | 偏差分析表 |
| 6 | 安全合规专项审查 | 合规检查表 |
| 7 | 汇总审查报告 | 完整审查报告 |

---

## 六、关键审查关注点（基于前四轮经验）

### 6.1 历史高频问题模式

1. **SQLAlchemy API误用**：Core Select vs ORM Select（R4-01）
2. **进程隔离违反**：进程内调用替代HTTP调用（R4-02）
3. **并发安全缺陷**：锁遗漏、双重检查锁定错误（R4-03）
4. **安全策略违反**：fail-open替代fail-closed（R4-05）
5. **死代码/参数不匹配**：发送但不接收、定义但不调用（R4-06/07）

### 6.2 本轮特别关注

1. **threshold.py `_delete_threshold_from_db()`**：仍使用`OrganDiseaseThreshold.__table__.delete()`（Core级），与R4-01同类问题
2. **consent_checker.py `_cache_consent_to_redis()`**：正常路径不应写Redis缓存（设计要求"直查PG，不写入Redis"），但当前代码在正常路径也会缓存
3. **checker.py `apply_degraded_output()`**：调用`sg_desensitization_engine.desensitize()`时未考虑desensitize()的锁保护（与R4-03关联）
4. **safety_gateway_client.py `check_output()`**：使用`asyncio.get_event_loop()`（Python 3.10+已弃用）
5. **sg_db.py `get_db_session()`**：yield后自动commit，但`_persist_threshold_to_db()`也使用此session，可能导致事务边界不清晰
