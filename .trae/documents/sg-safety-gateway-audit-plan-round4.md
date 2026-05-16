# SG安全网关轨道系统性审查计划（第四轮）

**审查目标**：对SG安全网关轨道的所有验证环节和测试逻辑进行全面、系统性的审查，确保其正确有效性  
**审查原则**：独立、客观、深入、批判性  
**创建日期**：2026-05-10  
**前置依赖**：第三轮审查报告（sg-safety-gateway-audit-report-round3.md）中30项修复的落地验证

---

## 一、审查范围

### 1.1 核心源码（17个文件，约3500行）

| 文件 | 行数 | 审查重点 |
|------|------|----------|
| `safety_gateway/main.py` | ~357 | FastAPI端点正确性、lifespan资源管理、规则热更新订阅 |
| `safety_gateway/checker.py` | ~774 | 四阶段校验流程、降级输出逻辑、递归深度、单例模式 |
| `safety_gateway/desensitization.py` | ~419 | 脱敏引擎规则加载/热更新/回滚、线程安全、正则正确性 |
| `safety_gateway/consent_checker.py` | ~212 | 同意状态直查PG、Redis缓存降级、熔断器集成、重试机制 |
| `safety_gateway/contraindication.py` | ~216 | 禁忌规则加载/匹配、全局/器官级规则、条件匹配逻辑 |
| `safety_gateway/threshold.py` | ~560 | 阈值加载/缓存/热更新、asyncio.Event并发保护、PG持久化 |
| `safety_gateway/health.py` | ~305 | 健康检查聚合逻辑、P99延迟统计、内存监控、OOM保护检查 |
| `safety_gateway/circuit_breaker.py` | ~218 | 三态转换、线程安全、fallback执行 |
| `safety_gateway/validation_result.py` | ~97 | 结果模型完整性、DegradedReason枚举覆盖度 |
| `safety_gateway/errors.py` | ~187 | 错误层次结构、E4005系列错误码完整性 |
| `safety_gateway/sg_config.py` | ~56 | 配置项完整性、默认值合理性 |
| `safety_gateway/sg_validator.py` | ~51 | 配置校验规则完整性 |
| `safety_gateway/sg_db.py` | ~37 | 数据库会话管理、事务边界 |
| `safety_gateway/sg_models.py` | ~35 | ORM模型定义 |
| `safety_gateway/sg_types.py` | ~25 | 类型定义 |
| `safety_gateway/redis_pool.py` | ~71 | Redis连接池、URL解析、DB编号替换 |
| `safety_gateway/__init__.py` | 0 | 包初始化 |

### 1.2 客户端与集成层（6个文件）

| 文件 | 审查重点 |
|------|----------|
| `app/services/safety_gateway_client.py` | HTTP客户端超时/重试、E4005降级响应 |
| `app/security/three_layer_desensitization.py` | 三层脱敏集成、SG第三层兜底 |
| `app/api/v1/diagnosis.py` | SG校验调用、异常处理 |
| `app/api/v1/errors.py` | SG异常定义 |
| `app/config/settings.py` | SG相关配置项 |
| `app/config/validator.py` | SG配置校验 |

### 1.3 测试文件（7个文件，约3000+行）

| 文件 | 审查重点 |
|------|----------|
| `tests/unit/test_safety_gateway.py` | ~2264行，核心单元测试，30+测试类 |
| `tests/benchmark/test_safety_gateway_performance.py` | P99延迟基准、吞吐量测试 |
| `tests/unit/test_desensitization.py` | 主脱敏引擎测试 |
| `tests/benchmark/test_desensitization_performance.py` | 脱敏性能基准 |
| `tests/unit/test_sec_audit.py` | 安全合规审计测试 |
| `tests/unit/test_security_compliance.py` | 安全合规测试 |
| `tests/unit/test_security_compliance_complete.py` | 安全合规完整测试 |

### 1.4 配置与部署（7个文件）

| 文件 | 审查重点 |
|------|----------|
| `docker-compose.dev.yml` | SG容器定义、健康检查、OOM保护、depends_on |
| `Dockerfile.safety-gateway.dev` | 镜像构建、非root用户 |
| `deploy/supervisor/safety-gateway.conf` | supervisor配置、自动重启 |
| `scripts/start_safety_gateway.sh` | OOM保护设置、用户切换 |
| `desensitization_rules_safety.yaml` | SG脱敏主规则 |
| `desensitization_rules_safety_patch.yaml` | SG脱敏补丁规则 |
| `medical_person_names.yaml` | 医疗人员姓名库 |

---

## 二、审查维度与方法

### 维度1：代码质量评估（健壮性/可维护性/可扩展性/性能）

**审查方法**：逐文件静态代码分析 + 执行路径追踪 + 异常分支覆盖

#### 2.1.1 运行时崩溃风险审查
- [ ] **SG-FIX-01验证**：`/health`端点`result["overall"]`键名Bug是否已修复为`result["status"]`
- [ ] **SG-FIX-02验证**：`get_start_time()/get_uptime()`是否已从classmethod改为实例方法
- [ ] 审查所有字典键访问是否使用`.get()`或有存在性检查
- [ ] 审查所有类型转换是否有try-except保护
- [ ] 审查所有import是否可能因模块缺失而崩溃

#### 2.1.2 并发安全审查
- [ ] **SG-FIX-08验证**：threshold `_lazy_load`是否使用`asyncio.Event`替代`_load_in_progress`标志
- [ ] **SG-FIX-13验证**：`reload_rules()`是否添加了Lock保护
- [ ] **SG-FIX-23验证**：`get_safety_gateway_checker()`是否添加了双重检查锁定
- [ ] 审查所有全局单例模式的线程安全性
- [ ] 审查所有共享状态的并发访问（`_latency_samples`、`_rules`、`_thresholds`等）
- [ ] 审查asyncio与threading混用场景的安全性

#### 2.1.3 资源泄漏审查
- [ ] **SG-FIX-11验证**：`_rules_sync_subscriber`是否使用`async with redis.pubsub()`
- [ ] 审查所有异步资源（DB session、Redis连接、HTTP连接）的生命周期管理
- [ ] 审查lifespan中的资源清理完整性
- [ ] 审查熔断器统计信息的内存增长是否有上限

#### 2.1.4 性能瓶颈审查
- [ ] 审查`check_sensitive_data()`中规则排序是否每次调用都执行（应缓存排序结果）
- [ ] 审查`check_person_name()`中姓名集合匹配的时间复杂度
- [ ] 审查`_percentile()`在大样本量下的性能
- [ ] 审查`perform_full_check()`中4个阶段的串行执行是否有优化空间
- [ ] 审查Redis/PG连接池配置是否满足P99<5ms要求

### 维度2：代码风格合规性

**审查方法**：对照项目编码规范逐项检查

#### 2.2.1 命名一致性
- [ ] **SG-FIX-18验证**：白名单字段名是否与设计文档一致
- [ ] **SG-FIX-28验证**：`SG_DEPLOYMENT_MODE`是否已改为`DEPLOYMENT_PROFILE`
- [ ] 审查所有配置项命名是否遵循`sg_`前缀约定
- [ ] 审查错误码命名是否遵循E4005系列约定

#### 2.2.2 文档与注释
- [ ] 审查模块docstring是否完整描述职责和降级策略
- [ ] 审查关键方法的docstring是否描述参数、返回值、异常
- [ ] 审查TODO/FIXME/HACK标记是否有对应的issue跟踪

#### 2.2.3 代码组织
- [ ] 审查safety_gateway/是否完全独立（不导入融合层模块）
- [ ] 审查Redis连接池是否独立（DB 1 vs DB 0）
- [ ] 审查NER方案是否独立（纯正则 vs ML NER）

### 维度3：功能正确性验证

**审查方法**：系统性测试逻辑审查 + 边界条件分析 + 异常场景覆盖

#### 2.3.1 核心校验流程正确性
- [ ] **SG-FIX-04验证**：同意状态检查（WITHDRAWN/DENIED）是否优先于is_timeout检查
- [ ] **SG-FIX-03验证**：is_db_error=True时WITHDRAWN分支是否可达
- [ ] **SG-FIX-05验证**：`_filter_forbidden_terms`是否替换所有匹配项而非仅第一个
- [ ] **SG-FIX-20验证**：快速拒绝路径是否包含"pending"状态
- [ ] 审查四阶段校验顺序：同意→脱敏→禁忌+阈值→一致性
- [ ] 审查入口快速拒绝路径的完整性
- [ ] 审查`conclusion_suppressed`标记处理规则

#### 2.3.2 降级策略正确性
- [ ] **7种降级场景端到端验证**：
  1. Redis不可用→PG降级
  2. 融合层不可用→SG降级输出
  3. SG不可用→拒绝E4005
  4. 边缘断连→本地降级
  5. 组合故障
  6. 脱敏降级+SG不可用E4005A
  7. SG不可用+患者撤回E4005A
- [ ] 审查每种降级场景的输出结构是否符合设计文档
- [ ] 审查降级输出中白名单字段过滤的正确性
- [ ] 审查`fusion_bypassed`标记的设置条件
- [ ] 审查双重降级场景（SG+融合层同时不可用）的处理

#### 2.3.3 同意状态校验正确性
- [ ] **SG-FIX-06验证**：熔断器OPEN后是否不再直接查PG
- [ ] **SG-FIX-07验证**：Redis缓存TTL是否已缩短或实现主动失效
- [ ] **SG-FIX-15验证**：熔断器OPEN后PG查询成功是否写入Redis缓存
- [ ] **SG-FIX-16验证**：重试总时间预算检查是否包含下一次查询耗时
- [ ] 审查"直查PostgreSQL"要求的实现（正常路径不使用缓存）
- [ ] 审查Redis缓存仅作为降级fallback的使用方式
- [ ] 审查consent_status_at_output字段的设置

#### 2.3.4 进程隔离与OOM保护
- [ ] 审查SG是否作为独立uvicorn实例运行
- [ ] 审查主服务崩溃时SG是否可独立返回E4005
- [ ] 审查oom_score_adj=-1000的配置路径（启动脚本+Docker Compose）
- [ ] 审查supervisor看门狗重启时间≤5s的配置
- [ ] 审查内存预算≤512MB的监控机制

#### 2.3.5 规则异构冗余
- [ ] **SG-FIX-14验证**：默认规则与YAML规则手机号脱敏replacement是否同步
- [ ] 审查desensitization_rules_safety.yaml是否独立编写（非复制主规则源）
- [ ] 审查desensitization_rules_safety_patch.yaml的独立维护性
- [ ] 审查SG执行引擎与主规则源的异构性（纯正则 vs ML NER）

#### 2.3.6 健康检查完整性
- [ ] 审查/health端点的组件覆盖（Redis/DB/规则引擎/熔断器/阈值引擎/OOM）
- [ ] 审查healthy/degraded/unhealthy三级状态的判定逻辑
- [ ] 审查compact模式下Redis不可用的降级处理
- [ ] **SG-FIX-26验证**：compact模式降级是否仅限Redis为唯一unhealthy组件
- [ ] 审查P99延迟告警的三级状态（ok/warning/critical）

---

## 三、测试逻辑审查

### 3.1 测试覆盖率审查
- [ ] **SG-FIX-17验证**：`_rules_sync_subscriber`是否已有测试覆盖
- [ ] **SG-FIX-19验证**：`SGCombinedUnavailableError (E4005C)`是否已有测试覆盖
- [ ] **SG-FIX-22验证**：异步方法mock是否已添加`new_callable=AsyncMock`
- [ ] 审查测试用例总数是否达到80-120个要求
- [ ] 审查降级路径专项测试的7种场景覆盖
- [ ] 审查E4005/E4005A/E4005B/E4005C所有错误码的测试覆盖

### 3.2 测试质量审查
- [ ] **SG-FIX-21验证**：熔断器测试是否使用teardown_method清理
- [ ] 审查Mock对象的正确性（返回值类型、异步/同步匹配）
- [ ] 审查测试隔离性（全局状态清理、fixture正确使用）
- [ ] 审查边界条件测试的完整性
- [ ] 审查参数化测试的覆盖范围

### 3.3 性能基准测试审查
- [ ] 审查P99<5ms基准测试的预热机制
- [ ] 审查GC禁用对测试结果的影响
- [ ] 审查并发吞吐量测试的合理性
- [ ] 审查基准测试与生产环境的差异

---

## 四、与参考文档的一致性审查

### 4.1 P0文档：01-系统架构设计.md
- [ ] 进程架构：独立进程、HTTP localhost通信、P99<5ms
- [ ] A级安全标准：先校验后输出、零容忍策略
- [ ] 降级策略表：4种SG故障场景的处理策略
- [ ] 三道防线职责分层：SG仅做结构化身份模式拦截
- [ ] 代码组织：独立package、不导入融合层、独立Redis连接池
- [ ] 同意状态校验：直查PG、禁用缓存的安全论证
- [ ] OOM保护：oom_score_adj=-1000
- [ ] 内存预算：≤512MB

### 4.2 P0文档：04-安全合规.md
- [ ] 三重脱敏防线：SG为第三道防线的实现
- [ ] 规则异构冗余：独立编写规则集
- [ ] 双重降级场景脱敏保障
- [ ] 患者知情同意管理：两阶段校验

### 4.3 P1文档：06-开发环境与进度规划.md
- [ ] 代码量：2500-3000行（实际约3500行）
- [ ] 测试用例：80-120个（实际约199个）
- [ ] 端口配置：SAFETY_GATEWAY_PORT=9001
- [ ] Gate判定标准：Gate 0进程隔离、7种降级场景
- [ ] Docker Compose：depends_on + 健康检查

### 4.4 P2文档：02-核心流程与知识库.md + 00b-统一配置参考手册.md
- [ ] 降级输出observations白名单字段名
- [ ] **SG-FIX-27验证**：21个SG配置项是否已在00b文档登记
- [ ] 配置项约束关系（排队超时5s~15s、端口无冲突等）

---

## 五、审查执行步骤

### 步骤1：第三轮30项修复落地验证（预计审查15分钟）
1. 逐项检查SG-FIX-01~SG-FIX-30的修复状态
2. 对每项修复进行代码级验证（读取相关代码确认修复）
3. 标记每项为FIXED/NOT FIXED/PARTIALLY FIXED

### 步骤2：代码质量深度审查（预计审查30分钟）
1. 运行时崩溃风险：逐文件检查KeyError/AttributeError/TypeError风险点
2. 并发安全：审查所有共享状态的访问模式
3. 资源泄漏：审查所有异步资源的生命周期
4. 性能瓶颈：识别热路径上的性能问题

### 步骤3：功能正确性审查（预计审查30分钟）
1. 四阶段校验流程：追踪perform_full_check的每个分支
2. 7种降级场景：逐场景验证输出结构
3. 同意状态校验：验证直查PG和Redis降级路径
4. 进程隔离与OOM保护：验证部署配置

### 步骤4：测试逻辑审查（预计审查20分钟）
1. 测试覆盖率：识别未覆盖的关键路径
2. 测试质量：审查Mock正确性和测试隔离性
3. 性能基准：审查基准测试的合理性

### 步骤5：文档一致性审查（预计审查15分钟）
1. 逐项对照4份核心文档的要求
2. 记录所有偏差并评估严重程度

### 步骤6：生成审查报告（预计15分钟）
1. 汇总所有发现的问题
2. 按严重程度分级（P0/P1/P2）
3. 显式声明属于SafetyGateway轨道的修复项
4. 提出改进建议和优先级

---

## 六、审查输出

### 输出物
1. **审查报告**：`.trae/documents/sg-safety-gateway-audit-report-round4.md`
2. **修复项清单**：显式声明属于SafetyGateway轨道的修复项，按P0/P1/P2分级

### 报告结构
1. 审查范围与方法
2. 第三轮30项修复落地验证结果
3. 新发现的问题（按严重程度分级）
4. 与参考文档的偏差分析
5. **SafetyGateway轨道修复项显式声明**
6. 改进建议
7. 审查结论

---

## 七、关键审查检查点（Checklist）

### 安全合规关键项
- [ ] 患者撤回同意后是否立即拒绝所有输出（零容忍）
- [ ] SG不可用时是否拒绝所有诊断输出（E4005）
- [ ] 降级输出是否仅保留白名单字段
- [ ] Redis缓存TTL是否在安全窗口内
- [ ] 熔断器OPEN后是否不再直接查PG

### 进程隔离关键项
- [ ] SG是否独立进程（独立uvicorn实例）
- [ ] 主服务崩溃时SG是否可独立返回E4005
- [ ] oom_score_adj=-1000是否正确配置
- [ ] supervisor看门狗重启≤5s

### 性能关键项
- [ ] HTTP通信开销P99<5ms
- [ ] 端到端处理延迟P99<50ms
- [ ] PostgreSQL直查同意状态P99<10ms
- [ ] 内存预算≤512MB

### 测试关键项
- [ ] 7种降级场景全部通过
- [ ] E4005/E4005A/E4005B/E4005C错误码全部可达且有测试
- [ ] 进程隔离测试（Gate 0）
- [ ] P99延迟基准测试
