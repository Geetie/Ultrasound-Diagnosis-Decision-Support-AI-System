# SG安全网关轨道系统性审查计划（第九轮）

**审查日期**：2026-05-11
**审查范围**：SafetyGateway轨道全部源码、测试、配置、部署及文档一致性
**审查原则**：独立、客观、深入、批判性
**前置参考**：第八轮审查报告（sg-safety-gateway-audit-report-round8.md）

---

## 一、审查目标

对SG轨道的所有验证环节和测试逻辑进行全面、系统性审查，确保其正确有效性。审查报告将显式声明属于SafetyGateway轨道应当进行的修复项。

---

## 二、审查范围与维度

### 2.1 代码质量评估

| 审查项 | 审查内容 | 对应文件 |
|--------|----------|----------|
| CQ-01 | 健壮性：空值/None处理、类型安全、异常路径完整性 | 全部18个核心模块 |
| CQ-02 | 可维护性：模块职责单一性、依赖关系清晰度、代码重复度 | safety_gateway/ |
| CQ-03 | 可扩展性：新增规则/器官/降级场景的扩展成本 | desensitization.py, contraindication.py, threshold.py |
| CQ-04 | 性能表现：P99<5ms延迟基准可达性、内存预算≤512MB合规性 | health.py, checker.py, benchmark测试 |
| CQ-05 | 并发安全：asyncio.Lock/threading.Lock使用正确性、单例模式线程安全 | redis_pool.py, checker.py, consent_checker.py |
| CQ-06 | 资源泄漏：数据库连接池、Redis连接池、HTTP连接的生命周期管理 | sg_db.py, redis_pool.py, health.py |
| CQ-07 | 运行时崩溃风险：未捕获异常、递归深度溢出、正则ReDoS | checker.py, desensitization.py |

### 2.2 代码风格合规性

| 审查项 | 审查内容 | 对应文件 |
|--------|----------|----------|
| CS-01 | 命名约定：模块/类/函数/变量命名是否遵循项目统一规范 | safety_gateway/ |
| CS-02 | 类型注解：是否统一使用Python 3.10+风格（X \| Y替代Union） | 全部.py文件 |
| CS-03 | 文档标准：模块/类/关键函数是否有docstring，是否与实现一致 | 全部.py文件 |
| CS-04 | 导入规范：是否遵循标准库/第三方/本地模块分组，无未使用导入 | 全部.py文件 |
| CS-05 | 代码一致性：同类操作是否使用统一模式（如单例获取、错误处理） | safety_gateway/ |

### 2.3 功能正确性验证

| 审查项 | 审查内容 | 设计文档参考 |
|--------|----------|-------------|
| FC-01 | 进程隔离：独立uvicorn实例、HTTP localhost通信、主服务崩溃时独立返回E4005 | 01-系统架构设计 §3.4 |
| FC-02 | 核心校验职责：脱敏校验+禁忌校验+同意状态检查+一致性校验四项完整性 | 01-系统架构设计 §3.4 |
| FC-03 | 安全等级A执行：先校验后输出、零容忍策略、未经校验绝对不输出 | 01-系统架构设计 安全分级 |
| FC-04 | OOM保护：oom_score_adj=-1000配置正确性、运行时验证 | 01-系统架构设计 §3.4 + 06-开发环境 |
| FC-05 | 7种降级场景完整性：逐一验证每种DegradedReason的触发路径和输出正确性 | 01-系统架构设计 降级策略总表 |
| FC-06 | P99<5ms延迟基准：HTTP通信开销+端到端处理延迟 | 01-系统架构设计 §3.4 |
| FC-07 | 三重脱敏防线：第三道防线（SG结构化身份模式拦截）实现完整性 | 04-安全合规 §2 |
| FC-08 | 规则异构冗余：desensitization_rules_safety.yaml独立编写、不复制主规则源 | 04-安全合规 §2 |
| FC-09 | 同意状态两阶段校验：入口快速拒绝+输出前直查PostgreSQL | 04-安全合规 §6 |
| FC-10 | 健康检查完整：/health端点、组件级状态、P99延迟统计、OOM状态 | 06-开发环境 |
| FC-11 | Docker Compose配置：depends_on+healthcheck+oom_score_adj+端口9001 | 06-开发环境 |
| FC-12 | E4005错误码体系：E4005/E4005A1/E4005A2/E4005B/E4005C触发路径正确性 | 01-系统架构设计 |
| FC-13 | 降级输出数据完整性：白名单过滤、conclusion_suppressed标记、类型化脱敏[REDACTED:{类型}] | 01-系统架构设计 降级策略 |
| FC-14 | 融合层可用性判定：基于进程可达性(HTTP /health)而非输出结构 | 01-系统架构设计 §3.4 |
| FC-15 | conclusion_suppressed处理：融合层主动抑制时SG跳过diagnostic_conclusion过滤 | 01-系统架构设计 §3.4 |
| FC-16 | 幻觉严重输出处理：hallucination_severity=severe时附加安全标记 | checker.py |
| FC-17 | 规则热更新：Redis Pub/Sub同步+回滚机制+验证 | 01-系统架构设计 脱敏规则同步 |

---

## 三、审查执行步骤

### 步骤1：R8遗留修复项验证（预计30分钟）

逐项验证第八轮审查报告中遗留的未修复/部分修复项：

| R8遗留项 | 描述 | 验证方法 |
|----------|------|----------|
| SG-FIX-R8-01 | three_layer_desensitization.py键名不匹配 | 代码审查three_layer_desensitization.py |
| SG-FIX-R8-02 | desensitization_degraded语义错误 | 代码审查process_full_pipeline() |
| SG-FIX-R7-06 | 客户端E4005响应不区分子类型 | 代码审查_e4005_response() |
| SG-FIX-R7-04 | 独立姓名脱敏规则覆盖不足 | 对比SG规则与主规则源 |
| SG-FIX-R7-01 | 服务端响应键名遗漏 | 代码审查three_layer_desensitization.py |

**验证标准**：每项需代码级确认修复状态，记录为✅已修复/⚠️部分修复/❌未修复

### 步骤2：核心模块逐文件静态分析（预计60分钟）

按以下顺序逐文件审查，重点关注运行时安全缺陷：

| 序号 | 文件 | 审查重点 |
|------|------|----------|
| 1 | main.py | FastAPI应用生命周期、异常处理器、CORS配置、端点定义完整性 |
| 2 | checker.py | 四阶段校验流程正确性、降级路径完整性、_PhaseContext状态管理 |
| 3 | consent_checker.py | 直查PG实现、Redis缓存降级、熔断器集成、重试机制 |
| 4 | desensitization.py | 规则加载/热更新/回滚、线程安全、正则ReDoS风险、人名词典匹配 |
| 5 | contraindication.py | 规则加载、条件匹配逻辑、线程安全 |
| 6 | threshold.py | 动态加载、热更新、缓存TTL、持久化正确性 |
| 7 | health.py | 组件健康检查完整性、P99延迟统计、OOM保护检测、融合层可用性检测 |
| 8 | circuit_breaker.py | 状态机正确性、asyncio单线程约束、回退机制 |
| 9 | degraded_output.py | 白名单过滤、禁止分级词过滤、各降级原因输出标记正确性 |
| 10 | errors.py | 错误码体系完整性、HTTP状态码映射 |
| 11 | sg_config.py | 配置项完整性、默认值合理性 |
| 12 | sg_validator.py | 配置校验规则完整性 |
| 13 | sg_db.py | 连接池配置、会话管理 |
| 14 | redis_pool.py | 连接池隔离、健康检查、客户端重建 |
| 15 | sg_models.py | ORM模型与数据库schema一致性 |
| 16 | sg_types.py | 类型导出完整性 |
| 17 | validation_result.py | 结果模型完整性 |

### 步骤3：7种降级场景端到端验证（预计45分钟）

逐一验证DegradedReason枚举中10种降级原因的触发路径：

| 序号 | DegradedReason | 触发条件 | 预期行为 | 验证方法 |
|------|----------------|----------|----------|----------|
| 1 | SENSITIVE_DATA_DETECTED | 融合层可用+检测到敏感数据 | DEGRADED+降级输出 | 代码路径追踪 |
| 2 | DB_TIMEOUT | 同意状态查询超时 | DEGRADED+降级输出 | 代码路径追踪 |
| 3 | CONTRAINDICATION_HIT | 检测到禁忌条件 | DEGRADED+降级输出 | 代码路径追踪 |
| 4 | CONFIDENCE_BELOW_THRESHOLD | 置信度低于阈值 | DEGRADED+降级输出 | 代码路径追踪 |
| 5 | FUSION_UNAVAILABLE | 融合层不可用（无其他故障） | DEGRADED+仅观察描述 | 代码路径追踪 |
| 6 | COMBINED_FAILURE | 禁忌+阈值+融合不可用组合 | DEGRADED+组合降级 | 代码路径追踪 |
| 7 | EDGE_DISCONNECTED | 边缘后端断连 | DEGRADED+本地降级 | 代码路径追踪 |
| 8 | FUSION_UNAVAILABLE_SENSITIVE_DETECTED | 融合层不可用+敏感数据 | FAILED+E4005A1 | 代码路径追踪 |
| 9 | DESENSITIZATION_DEGRADED_SENSITIVE_DETECTED | 融合层脱敏降级+敏感数据 | FAILED+E4005A1 | 代码路径追踪 |
| 10 | SG_UNAVAILABLE_CONSENT_WITHDRAWN | SG不可用+患者撤回同意 | FAILED+E4005A2 | 代码路径追踪 |

**注意**：设计文档要求7种降级场景，但代码实现10种DegradedReason。需验证是否覆盖了设计文档要求的所有场景，以及额外3种是否合理。

### 步骤4：设计文档一致性审查（预计45分钟）

按优先级对照核心参考文档进行一致性审查：

#### P0：01-系统架构设计.md

| 审查项 | 设计要求 | 实现现状 | 偏差分析 |
|--------|----------|----------|----------|
| 进程架构 | 独立uvicorn实例，HTTP localhost通信 | main.py uvicorn.run() + Dockerfile独立进程 | 需验证 |
| P99延迟 | HTTP通信开销P99<5ms，端到端P99<50ms | benchmark测试存在 | 需验证测试覆盖 |
| 核心校验 | 脱敏+禁忌+同意状态+一致性 | checker.py四阶段 | 需验证完整性 |
| E4005体系 | E4005/E4005A1/A2/B/C | errors.py定义完整 | 需验证触发路径 |
| OOM保护 | oom_score_adj=-1000 | docker-compose.dev.yml+start_safety_gateway.sh | 需验证一致性 |
| 安全等级A | 先校验后输出，零容忍 | checker.py流程 | 需验证 |
| 降级路径 | 融合层不可用→仅观察描述+fusion_bypassed | degraded_output.py | 需验证标记正确性 |
| 融合层判定 | 基于进程可达性(HTTP /health) | health.py get_fusion_availability() | 需验证 |
| NER方案 | 结构化身份模式+医疗人名词典，不依赖ML | desensitization.py | 需验证 |
| 内存预算 | ≤512MB | docker-compose.dev.yml 512M限制 | 需验证 |

#### P0：04-安全合规.md

| 审查项 | 设计要求 | 实现现状 | 偏差分析 |
|--------|----------|----------|----------|
| 三重防线 | SG为第三道防线：结构化身份模式拦截 | desensitization.py | 需验证 |
| 异构冗余 | desensitization_rules_safety.yaml独立编写 | desensitization_rules_safety.yaml | 需逐条对比 |
| 同意校验 | 两阶段：入口快速拒绝+输出前直查PG | consent_checker.py+checker.py | 需验证直查PG |
| SG禁用缓存 | SafetyGateway直查PG，不使用缓存 | consent_checker.py | 需验证（当前有Redis降级路径） |
| 类型化脱敏 | [REDACTED:{类型}]格式 | desensitization.py replacement字段 | 需验证 |
| 双重降级 | fusion_degraded+safety_gateway_unavailable标记 | degraded_output.py | 需验证 |

#### P1：06-开发环境与进度规划.md

| 审查项 | 设计要求 | 实现现状 | 偏差分析 |
|--------|----------|----------|----------|
| 代码组织 | 独立Python package safety_gateway/ | safety_gateway/ | 需验证 |
| 代码量 | 约2500-3000行 | ~3600行（R8报告） | 需重新统计 |
| 测试用例 | 约80-120个 | 需统计 | 需验证 |
| 端口 | SAFETY_GATEWAY_PORT=9001 | sg_config.py默认9001 | ✅ |
| 健康检查 | http://localhost:9001/health | main.py /health端点 | 需验证完整性 |
| Gate 0 | 主服务崩溃时独立返回E4005 | 测试存在 | 需验证 |
| Docker Compose | depends_on+healthcheck | docker-compose.dev.yml | 需验证 |

### 步骤5：测试逻辑审查（预计30分钟）

| 审查项 | 审查内容 | 验证方法 |
|--------|----------|----------|
| TL-01 | 测试覆盖率：统计测试用例数量是否达到80-120个 | 计算test_safety_gateway.py等文件中的测试方法数 |
| TL-02 | 降级路径专项测试：7种降级场景是否全部有测试 | 检查TestDegradedPathSevenScenarios |
| TL-03 | Mock正确性：mock是否正确模拟了外部依赖行为 | 逐个检查mock的返回值和副作用 |
| TL-04 | 测试隔离性：测试之间是否有状态泄漏 | 检查setup/teardown、单例重置 |
| TL-05 | 边界条件覆盖：空值、极值、异常输入 | 检查TestBoundaryConditions |
| TL-06 | 性能测试有效性：P99<5ms基准测试是否可信 | 检查预热、GC控制、样本量 |
| TL-07 | 集成测试覆盖：HTTP端到端、Docker环境 | 检查TestHTTPEndToEndLatency |

### 步骤6：部署配置审查（预计20分钟）

| 审查项 | 审查内容 | 验证方法 |
|--------|----------|----------|
| DC-01 | docker-compose.dev.yml中safety-gateway服务配置完整性 | 逐项对照设计文档 |
| DC-02 | Dockerfile.safety-gateway.dev构建正确性 | 检查依赖安装、用户权限、启动命令 |
| DC-03 | supervisor配置：自动重启≤5s、日志轮转 | 检查safety-gateway.conf |
| DC-04 | start_safety_gateway.sh：OOM保护设置、权限降级 | 检查脚本逻辑 |
| DC-05 | restart策略缺失：docker-compose.dev.yml未配置restart: always | 对照设计文档 |
| DC-06 | 主服务depends_on safety-gateway配置 | 检查api服务定义 |

### 步骤7：规则异构冗余验证（预计20分钟）

| 审查项 | 审查内容 | 验证方法 |
|--------|----------|----------|
| HR-01 | desensitization_rules_safety.yaml与主规则源正则模式对比 | 逐条对比pattern字段 |
| HR-02 | desensitization_rules_safety_patch.yaml补充规则独立性 | 检查是否包含主规则源未覆盖的模式 |
| HR-03 | contraindication_rules_safety.yaml规则完整性 | 检查器官覆盖和条件枚举 |
| HR-04 | medical_person_names.yaml人名词典质量 | 检查姓氏覆盖和误报控制 |

---

## 四、已知风险与关注点

基于前八轮审查历史，以下为高风险关注项：

1. **three_layer_desensitization.py键名不匹配**（R8-01）：SG服务端响应使用`degraded_mode`/`passed`，客户端fallback使用`degraded`/`allowed`，集成层仅检查后者
2. **desensitization_degraded语义错误**（R8-02）：`modified`≠`降级`，可能导致SG误判脱敏状态
3. **客户端E4005不区分子类型**（R7-06）：`_e4005_response()`返回通用E4005，不区分A1/A2/B/C
4. **姓名脱敏规则覆盖缺口**（R7-04）：SG规则要求前缀，主规则无此要求
5. **docker-compose.dev.yml缺少restart策略**：设计文档要求supervisor看门狗+Docker restart
6. **SG同意状态校验存在Redis降级路径**：设计文档要求SG直查PG、禁用缓存，但代码实现了Redis降级
7. **代码量超出预估**：设计文档预估2500-3000行，R8报告~3600行

---

## 五、审查输出格式

审查完成后，将输出以下内容：

1. **审查报告**（sg-safety-gateway-audit-report-round9.md），包含：
   - R8遗留修复项验证结果
   - 新发现的问题（按P0/P1/P2分级）
   - 设计文档偏差分析表
   - 测试覆盖率统计
   - 修复建议与优先级

2. **修复项清单**：显式声明属于SafetyGateway轨道应当进行的修复项，格式为：
   ```
   SG-FIX-R9-{NN}: {问题描述}
   - 严重程度：P0/P1/P2
   - 文件：{文件路径}
   - 设计文档参考：{文档名}§{章节}
   - 修复建议：{具体修复方案}
   - 修复类型：代码修复/配置修复/测试补充/文档更新
   ```

---

## 六、审查执行计划

| 步骤 | 预计耗时 | 产出 |
|------|----------|------|
| 步骤1：R8遗留验证 | 30min | 遗留项验证结果表 |
| 步骤2：核心模块静态分析 | 60min | 代码质量问题清单 |
| 步骤3：降级场景端到端验证 | 45min | 降级路径覆盖矩阵 |
| 步骤4：设计文档一致性审查 | 45min | 偏差分析表 |
| 步骤5：测试逻辑审查 | 30min | 测试质量评估 |
| 步骤6：部署配置审查 | 20min | 配置合规性检查 |
| 步骤7：规则异构冗余验证 | 20min | 规则对比结果 |
| **总计** | **~4小时** | **完整审查报告** |
