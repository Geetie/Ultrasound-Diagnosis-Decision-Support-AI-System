# SafetyGateway轨道系统性审查计划

## 审查概述

**审查对象**：SafetyGateway（SG）安全网关轨道的全部验证环节和测试逻辑
**审查原则**：独立、客观、深入、批判性
**审查目标**：确保SG轨道实施的代码质量、功能正确性、与设计文档的一致性
**审查基准文档优先级**：P0=01-系统架构设计 + 04-安全合规 | P1=06-开发环境与进度规划 | P2=02-核心流程与知识库 + 00b-统一配置参考手册

---

## 已识别的代码库概况

| 维度 | 实际值 | 文档要求 |
|------|--------|----------|
| 源文件数 | 36个（safety_gateway/目录） | 独立Python package |
| 测试文件数 | 6个（5单元+1基准） | 含降级路径专项测试 |
| 测试函数总数 | 316个 | ~200-250个（06文档）/ ~80-120个（用户输入） |
| 集成测试 | **0个** | 需覆盖7种降级场景 |
| API端点 | 8个（含3个健康检查） | /health + 校验端点 |
| 校验阶段 | 5个Phase | 4项核心校验 |
| DegradedReason枚举 | 11个值 | 7种降级场景 |
| Docker配置 | oom_score_adj=-1000 ✓ | oom_score_adj=-1000 |
| 端口 | 9001 ✓ | 9001 |

---

## 审查步骤

### 步骤1：进程隔离与独立进程架构审查（P0）

**审查文件**：[main.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/main.py)、[Dockerfile.safety-gateway.dev](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/Dockerfile.safety-gateway.dev)、[docker-compose.dev.yml](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/docker-compose.dev.yml)、[deploy/supervisor/safety-gateway.conf](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/deploy/supervisor/safety-gateway.conf)、[scripts/start_safety_gateway.sh](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/scripts/start_safety_gateway.sh)

**审查项**：
1. 验证SafetyGateway是否作为独立uvicorn实例运行（supervisord配置中`command=uvicorn safety_gateway.main:app --host 0.0.0.0 --port 9001 --workers 1`）
2. 验证HTTP localhost通信机制是否正确（FastAPI端点定义）
3. 验证Docker Compose中SG与主服务的depends_on关系是否正确（主服务应依赖SG，而非SG依赖主服务）
4. 验证Dockerfile中USER sguser与start_safety_gateway.sh中root检查的兼容性
5. 验证supervisord的autorestart配置是否满足≤5s重启要求
6. 验证进程崩溃时的E4005返回机制

**已知潜在问题**：
- Dockerfile设置`USER sguser`，但start_safety_gateway.sh中OOM保护逻辑需root权限执行。在Docker环境下，start脚本中的`echo -1000 > /proc/self/oom_score_adj`不会执行（因为以sguser运行），仅依赖docker-compose中的`oom_score_adj: -1000`。若直接`docker run`而非通过docker-compose启动，OOM保护将缺失。

**验证方法**：逐文件代码审查 + 与01-系统架构设计.md§3.4对照

---

### 步骤2：延迟基准P99<5ms审查（P0）

**审查文件**：[health/latency_tracker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/health/latency_tracker.py)、[sg_config.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/sg_config.py)、[checker/orchestrator.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker/orchestrator.py)、[tests/benchmark/sg/test_safety_gateway_performance.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/benchmark/sg/test_safety_gateway_performance.py)

**审查项**：
1. 验证LatencyTracker的P99计算逻辑正确性（deque采样窗口、百分位算法）
2. 验证P99告警阈值配置（warning=4.0ms, critical=5.0ms）与文档一致性
3. 验证端到端P99阈值配置（warning=40.0ms, critical=50.0ms）与文档一致性
4. 验证orchestrator中ElapsedTimer的计时精度（time.monotonic()）
5. 验证延迟记录点是否覆盖完整请求生命周期
6. 验证性能基准测试是否真正测量P99延迟

**已知潜在问题**：
- LatencyTracker使用deque最大500样本，在高并发下可能快速轮转，导致P99统计窗口过短
- 性能基准测试中HTTP端到端延迟测试标记为集成测试，可能不在CI中运行

**验证方法**：代码审查 + 运行基准测试验证

---

### 步骤3：降级路径与7种降级场景审查（P0）

**审查文件**：[degraded_output.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/degraded_output.py)、[validation_result.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/validation_result.py)、[checker/orchestrator.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker/orchestrator.py)、各Phase实现

**审查项**：
1. 逐一验证7种降级场景的实现完整性：
   - 场景1：融合层不可用 → SG降级输出仅观察描述（DegradedReason.FUSION_UNAVAILABLE）
   - 场景2：SG不可用 → 所有诊断输出拒绝（E4005）
   - 场景3：融合层不可用 + SG短暂不可用（≤15s）→ 排队等待或超时拒绝
   - 场景4：脱敏降级 + SG不可用 → 拒绝所有诊断输出（E4005A1）
   - 场景5：DB超时 → 降级输出（DegradedReason.DB_TIMEOUT）
   - 场景6：禁忌命中 → 降级输出（DegradedReason.CONTRAINDICATION_HIT）
   - 场景7：置信度低于阈值 → 降级输出（DegradedReason.CONFIDENCE_BELOW_THRESHOLD）
2. 验证DegradedReason枚举（11个值）与7种场景的映射关系
3. 验证降级输出逻辑：移除diagnostic_conclusion、仅保留白名单字段、脱敏处理、过滤禁忌分级术语
4. 验证降级标记（fusion_bypassed、conclusion_suppressed等）的完整性
5. 验证双重降级场景（融合层+SG同时不可用）的处理

**已知潜在问题**：
- DegradedReason有11个值但文档只定义7种场景，多出4个（EDGE_DISCONNECTED、FUSION_UNAVAILABLE_SENSITIVE_DETECTED、DESENSITIZATION_DEGRADED_SENSITIVE_DETECTED、SG_UNAVAILABLE_CONSENT_WITHDRAWN、DUAL_DEGRADATION_FUSION_AND_SG_UNAVAILABLE），需确认这些是否为合理的细化
- 场景3（排队等待逻辑）在SG代码中未实现，排队逻辑应在主服务侧实现，需确认SG侧是否有配合机制
- `apply_degraded_output`中`conclusion_suppressed=True`硬编码，但某些降级场景（如DB_TIMEOUT）可能不应抑制结论

**验证方法**：逐场景代码路径追踪 + 与01-系统架构设计.md§3.4降级策略总表对照

---

### 步骤4：OOM保护审查（P0）

**审查文件**：[scripts/start_safety_gateway.sh](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/scripts/start_safety_gateway.sh)、[docker-compose.dev.yml](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/docker-compose.dev.yml)、[health/providers.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/health/providers.py)、[Dockerfile.safety-gateway.dev](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/Dockerfile.safety-gateway.dev)

**审查项**：
1. 验证双重OOM保护机制：Docker Compose层`oom_score_adj: -1000` + 启动脚本层`echo -1000 > /proc/self/oom_score_adj`
2. 验证内存预算限制：Docker Compose中`memory: 512M`与配置`sg_memory_budget_mb: 512`一致
3. 验证health/providers.py中OOM保护检查逻辑（读取/proc/pid/oom_score_adj + docker-compose回退）
4. 验证Dockerfile中USER sguser与OOM保护root权限的冲突处理
5. 验证MemoryMonitor的跨平台适配（Linux/Windows/psutil）

**已知潜在问题**：
- Dockerfile设置`USER sguser`导致start_safety_gateway.sh中的OOM保护代码路径不可达（以非root运行），仅依赖docker-compose配置
- health/providers.py中OOM检查在Windows上返回"unknown"状态，需确认CI/CD环境是否为Linux

**验证方法**：代码审查 + Docker部署验证

---

### 步骤5：健康检查完整性审查（P0）

**审查文件**：[health/checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/health/checker.py)、[health/providers.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/health/providers.py)、[health/fusion_probe.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/health/fusion_probe.py)、[health/memory_monitor.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/health/memory_monitor.py)、[health/latency_tracker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/health/latency_tracker.py)、[main.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/main.py)

**审查项**：
1. 验证/health端点返回结构的完整性（status/components/latency_stats/memory/oom_protection）
2. 验证/health/ready就绪检查逻辑（核心组件=database+rules_engine必须healthy）
3. 验证/health/watchdog看门狗检查（alive+p99_status+timestamp）
4. 验证健康检查三级状态（healthy/degraded/unhealthy）的判定逻辑
5. 验证compact模式下Redis不健康的特殊降级处理
6. 验证融合层探测（FusionProbe）的TTL缓存机制
7. 验证Docker Compose健康检查配置（interval=4s, timeout=1s, retries=3）与文档一致性
8. 验证连续3次失败≈15s的判定时间计算

**已知潜在问题**：
- /health端点对degraded状态返回200+X-SG-Status头，但主服务可能只检查HTTP状态码，需确认主服务是否解析自定义头
- readiness检查未包含Redis（compact模式下），与00b-统一配置参考手册中"compact模式下readiness探针不检查Redis连接"一致
- fusion_probe的TTL缓存默认4秒，与主服务4秒探测间隔一致，但存在探测结果过期的边界风险

**验证方法**：代码审查 + 与01-系统架构设计.md§3.4健康检查规范对照

---

### 步骤6：核心校验职责审查——脱敏校验（P0）

**审查文件**：[desensitization.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/desensitization.py)、[checker/desensitization_phase.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker/desensitization_phase.py)、[desensitization_rules_safety.yaml](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/desensitization_rules_safety.yaml)、[desensitization_rules_safety_patch.yaml](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/desensitization_rules_safety_patch.yaml)、[medical_person_names.yaml](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/medical_person_names.yaml)

**审查项**：
1. 验证三重脱敏防线中SG的第三道防线实现：结构化身份模式拦截（正则匹配+医疗人名词典）
2. 验证异构冗余：SG规则与主规则源使用不同正则模式（11条主规则+4条补丁规则）
3. 验证规则热更新回滚机制（reload_rules中的old_rules/old_patterns备份恢复）
4. 验证Redis Pub/Sub规则同步（desensitization_rules_update频道）
5. 验证人名检测逻辑（姓氏+称谓组合匹配 + 常见全名匹配）
6. 验证脱敏格式`[REDACTED:{类型}]`的一致性
7. 验证递归深度限制（MAX_RECURSION_DEPTH=20）
8. 验证线程安全（_rules_lock快照模式）

**已知潜在问题**：
- `sg_desensitization_engine = get_safety_gateway_desensitization_engine()`在模块级别执行，可能在import时触发规则加载，若YAML文件不存在则回退到硬编码默认规则
- `check_person_name`方法使用`name_combo in matched_text`子串匹配，可能产生误报（如"张医生"匹配到"小张医生"）
- `_load_patch_rules`在加载失败时静默忽略错误，仅记录日志，可能导致补丁规则缺失而不知情
- 人名模式`[\u4e00-\u9fa5]{1,4}`可能匹配过短（1个字符）或过长（4个字符非姓名）的文本

**验证方法**：代码审查 + 与04-安全合规.md§3/§4对照 + 正则模式测试

---

### 步骤7：核心校验职责审查——同意状态校验（P0）

**审查文件**：[consent_checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/consent_checker.py)、[checker/consent_phase.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker/consent_phase.py)、[sg_db.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/sg_db.py)、[sg_models.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/sg_models.py)

**审查项**：
1. 验证两阶段校验：入口快速拒绝（consent_status_at_input参数）+ 输出前直查PostgreSQL
2. 验证直查PostgreSQL不使用缓存的设计（代码中无Redis缓存回退）
3. 验证PostgreSQL不可用时的处理：返回E4005B（DatabaseUnavailableError）
4. 验证PostgreSQL+融合层同时不可用时的处理：返回E4005C（CombinedUnavailableError）
5. 验证熔断器保护（CircuitBreaker包裹DB查询）
6. 验证重试机制（retry_with_backoff，max_attempts=3，指数退避）
7. 验证DB查询超时配置（db_query_timeout=5s）
8. 验证无同意记录时的处理（返回DENIED，与03-接口规范§5.1一致）

**已知潜在问题**：
- 04-安全合规.md提到"PostgreSQL不可用时，SafetyGateway从Redis只读缓存读取同意状态（TTL 60s）"，但代码中ConsentChecker**完全没有Redis回退逻辑**——PG不可用时直接返回error。这是与文档的显著偏差，但代码实现遵循了更严格的"直查PG不使用缓存"原则，需评估是否为合理的更优实现
- `sg_db_retry_max_total_time_seconds`默认值为15.0，但00b-统一配置参考手册中为10.0，存在配置偏差
- consent_phase.py中`consent_status is None`（查询返回None）时进入降级模式而非拒绝，与"权威防线"定位是否一致需评估

**验证方法**：代码审查 + 与04-安全合规.md§6对照 + 与01-系统架构设计.md§3.4对照

---

### 步骤8：核心校验职责审查——禁忌校验与阈值校验（P0）

**审查文件**：[contraindication.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/contraindication.py)、[threshold.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/threshold.py)、[checker/contraindication_threshold_phase.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker/contraindication_threshold_phase.py)、[contraindication_rules_safety.yaml](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/contraindication_rules_safety.yaml)

**审查项**：
1. 验证禁忌规则引擎的条件匹配逻辑（器官级+全局规则）
2. 验证禁忌严重级别（high=必须拦截, medium=需警告）的处理差异
3. 验证阈值引擎的默认阈值（thyroid=0.85, liver=0.88, cardiac=0.92）
4. 验证阈值动态加载（PostgreSQL）和热更新（Redis Pub/Sub）
5. 验证阈值持久化（_persist_threshold_to_db）
6. 验证禁忌+阈值联合校验的组合故障判定逻辑
7. 验证未知器官的默认阈值（sg_default_unknown_organ_threshold=0.90）

**已知潜在问题**：
- contraindication_threshold_phase.py中`active_failures >= 2 or combined_with_fusion_unavailable`的组合判定逻辑较复杂，需验证边界情况
- threshold.py中`_load_thresholds_from_db_once`在并发场景下可能被多次调用（虽有_load_event保护，但首次加载前check()可能使用默认值）
- 禁忌规则仅8条（甲状腺3+肝脏2+心脏2+全局1），覆盖范围是否充分需评估

**验证方法**：代码审查 + 与01-系统架构设计.md核心校验职责对照

---

### 步骤9：核心校验职责审查——一致性校验（P0）

**审查文件**：[checker/consistency_phase.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker/consistency_phase.py)

**审查项**：
1. 验证一致性校验的三个维度：分级不匹配（grade_mismatch）、发现不匹配（finding_mismatch）、置信度分歧（confidence_divergence > 0.3）
2. 验证结论-依据矛盾检测（CONTRADICTION_PAIRS + NEGATION_PREFIXES）
3. 验证融合层自洽性检查（无专科结论时的自检）
4. 验证一致性失败时的处理（返回failed而非degraded）
5. 验证矛盾对列表的完备性（仅3对：良性/恶性、正常/异常、阴性/阳性）

**已知潜在问题**：
- CONTRADICTION_PAIRS仅3对，医疗领域存在更多矛盾对（如"囊性/实性"、"弥漫性/局灶性"等）
- 否定前缀列表缺少"无恶性"（已包含但重复，"无"已覆盖"无恶性"的前缀匹配）
- `_is_negated`方法使用`text.startswith(prefix + word)`，但否定词可能在句子中间而非开头
- confidence_divergence阈值0.3硬编码，未配置化

**验证方法**：代码审查 + 与02-核心流程与知识库.md交互流程对照

---

### 步骤10：E4005错误码体系审查（P0）

**审查文件**：[errors.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/errors.py)、[main.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/main.py)（异常处理器）、各Phase实现

**审查项**：
1. 验证E4005子码拆分完整性：
   - E4005A1：脱敏防线降级+敏感数据（DesensitizationSafetyViolationError）
   - E4005A2：SG不可用+同意已撤回（ConsentSafetyViolationError）
   - E4005B：数据库不可用（DatabaseUnavailableError）
   - E4005C：SG+融合层同时不可用（CombinedUnavailableError）
2. 验证异常处理器中HTTP状态码映射（503 for E4005系列）
3. 验证to_dict()序列化格式的完整性
4. 验证异常层次结构的合理性（SafetyGatewayError基类 → 5个二级子类 → 细分三级）

**已知潜在问题**：
- 文档定义E4005A为"SG不可用（健康检查连续失败）"，但代码中E4005A1和E4005A2分别对应脱敏和同意场景，与文档的E4005A定义不完全对齐
- ServiceUnavailableError的code为"E4005"（无子码），与文档中E4005A的定义存在歧义
- main.py中sg_endpoint_handler装饰器捕获所有非SafetyGatewayError异常并包装为ServiceUnavailableError(E4005)，可能掩盖真实错误类型

**验证方法**：代码审查 + 与01-系统架构设计.md§3.4 E4005错误码体系对照

---

### 步骤11：代码质量评估（P1）

**审查范围**：safety_gateway/全部36个源文件

**审查项**：
1. **健壮性**：
   - 异常处理覆盖率（是否有未捕获的异常路径）
   - 边界条件处理（空输入、None值、超长字符串、特殊字符）
   - 资源泄漏风险（DB session、Redis连接、asyncio Task）
2. **可维护性**：
   - 模块职责是否单一清晰
   - 依赖关系是否合理（是否存在循环依赖）
   - checker.py与checker/__init__.py内容完全重复是否合理
   - sg_types.py作为兼容层是否仍有必要
3. **可扩展性**：
   - 新增校验Phase的扩展难度
   - 新增降级场景的扩展难度
   - 新增脱敏规则的扩展难度
4. **性能**：
   - 正则匹配性能（每次请求重新编译vs缓存）
   - 数据库查询性能（N+1查询风险）
   - 内存使用（规则缓存、人名词典、延迟追踪器）

**已知潜在问题**：
- checker.py和checker/__init__.py内容完全相同，存在维护风险
- sg_types.py标记为废弃但仍存在，增加维护负担
- desensitization.py模块级实例化`sg_desensitization_engine = get_safety_gateway_desensitization_engine()`可能在import时触发副作用
- circuit_breaker.py中_update_state()方法存在线程安全注释警告（依赖asyncio单线程约束）
- orchestrator.py中perform_full_check方法较长（约80行），可读性可优化

**验证方法**：静态代码分析 + 代码复杂度评估

---

### 步骤12：代码风格合规性审查（P1）

**审查范围**：safety_gateway/全部源文件

**审查项**：
1. 命名约定一致性（类名PascalCase、函数名snake_case、常量UPPER_SNAKE_CASE）
2. 类型注解完整性（函数签名、返回值类型）
3. 日志格式一致性（structlog使用规范、日志级别选择）
4. 文档字符串规范（模块级、类级、方法级docstring）
5. import组织（标准库→第三方→项目内）
6. 代码格式化（缩进、行宽、空行规范）

**验证方法**：静态代码分析 + ruff/mypy检查

---

### 步骤13：配置完整性与正确性审查（P1）

**审查文件**：[sg_config.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/sg_config.py)、[sg_validator.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/sg_validator.py)

**审查项**：
1. 逐一对照00b-统一配置参考手册中SG相关配置项，验证SGSettings是否完整覆盖
2. 验证配置校验逻辑（端口冲突、正数校验、跨字段约束）
3. 验证默认值与文档的一致性
4. 验证环境变量注入机制（pydantic-settings + .env文件）

**已知配置偏差**：
| 配置项 | 代码默认值 | 文档值 | 偏差 |
|--------|-----------|--------|------|
| sg_db_retry_max_total_time_seconds | 15.0 | 10.0 | 超出文档值50% |
| sg_consent_cache_ttl_seconds | 10 | 60 | 代码中未使用此配置（直查PG策略） |
| sg_http_client_timeout_seconds | 3.0 | 3.0 | 一致 ✓ |

**验证方法**：逐项对照00b-统一配置参考手册

---

### 步骤14：测试逻辑完整性与准确性审查（P1）

**审查文件**：tests/unit/sg/下5个文件 + tests/benchmark/sg/下1个文件

**审查项**：
1. 验证316个测试函数的覆盖率和有效性
2. 验证7种降级场景的测试覆盖（TestDegradedPathSevenScenarios）
3. 验证边界条件测试（TestBoundaryConditions + TestBoundaryConditionsExtended）
4. 验证进程隔离测试（TestServiceIsolation + TestMainServiceCrashScenario）
5. 验证OOM保护测试（TestOOMProtectionScenario + TestOOMProtectionHealthCheck）
6. 验证P99延迟告警测试（TestP99LatencyAlert + TestP99AlertTrigger）
7. 验证熔断器测试（TestCircuitBreaker）
8. 验证规则热更新回滚测试（TestDesensitizationRuleReloadWithRollback）
9. 验证集成测试缺失的影响评估
10. 验证测试隔离性（autouse fixture、mock策略）

**已知潜在问题**：
- **集成测试完全缺失**：tests/integration/下无SG相关测试文件，7种降级场景的端到端验证仅通过单元测试模拟
- test_safety_gateway.py中239个测试函数集中在单个文件中，可维护性差
- 测试中大量使用mock，可能掩盖真实集成问题
- 性能基准测试标记为集成测试，可能不在CI中运行

**验证方法**：测试代码审查 + 覆盖率分析 + 运行测试套件

---

### 步骤15：Docker与部署配置审查（P1）

**审查文件**：[Dockerfile.safety-gateway.dev](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/Dockerfile.safety-gateway.dev)、[docker-compose.dev.yml](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/docker-compose.dev.yml)、[deploy/supervisor/safety-gateway.conf](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/deploy/supervisor/safety-gateway.conf)、[scripts/start_safety_gateway.sh](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/scripts/start_safety_gateway.sh)

**审查项**：
1. 验证Docker Compose中SG服务的完整配置
2. 验证depends_on关系（SG依赖postgres+redis，主服务依赖SG）
3. 验证健康检查配置与文档一致性
4. 验证资源限制（CPU 2.0/0.5, Memory 512M/256M）
5. 验证安全配置（oom_score_adj、非root用户、网络隔离）
6. 验证supervisord配置的合理性

**已知潜在问题**：
- docker-compose.dev.yml中SG的depends_on仅包含postgres和redis，未包含主服务（正确，因为主服务应依赖SG）
- 但主服务(api)的depends_on未在docker-compose.dev.yml中定义对SG的依赖，需确认是否遗漏
- Dockerfile中COPY . . 在pip install之后，可能导致代码变更时缓存失效

**验证方法**：配置审查 + 与06-开发环境与进度规划.md对照

---

### 步骤16：安全级别与零容忍策略审查（P0）

**审查范围**：全部校验逻辑

**审查项**：
1. 验证A级（诊断级）安全标准的执行：先校验后输出
2. 验证零容忍策略：SG不可用时拒绝所有诊断输出
3. 验证安全底线通用原则的实现：
   - 融合层+SG均可用 → 完整诊断输出
   - 融合层不可用+SG可用 → 仅observations，标记fusion_bypassed
   - SG不可用 → 拒绝所有诊断输出（E4005）
4. 验证不存在绕过安全校验的代码路径
5. 验证降级输出仍经过脱敏处理

**验证方法**：代码路径分析 + 与01-系统架构设计.md安全底线原则对照

---

## 审查执行顺序

```
步骤1  进程隔离与独立进程架构     (P0) ─┐
步骤2  延迟基准P99<5ms           (P0)  │
步骤5  健康检查完整性             (P0)  ├── 可并行
步骤10 E4005错误码体系            (P0)  │
步骤16 安全级别与零容忍策略       (P0) ─┘
                                        ↓
步骤6  脱敏校验                   (P0) ─┐
步骤7  同意状态校验               (P0)  ├── 可并行
步骤8  禁忌+阈值校验              (P0) ─┘
                                        ↓
步骤3  降级路径与7种场景          (P0) ← 依赖步骤6-8结果
步骤4  OOM保护                    (P0) ← 依赖步骤1结果
步骤9  一致性校验                 (P0)
                                        ↓
步骤11 代码质量评估               (P1) ─┐
步骤12 代码风格合规性             (P1)  ├── 可并行
步骤13 配置完整性与正确性         (P1) ─┘
                                        ↓
步骤14 测试逻辑完整性             (P1) ← 依赖步骤1-13发现
步骤15 Docker与部署配置           (P1)
```

---

## 审查输出格式

审查完成后，每个发现将按以下格式记录：

```
### [SG-FINDING-XXX] 问题标题

**严重程度**：P0-Critical / P1-High / P2-Medium / P3-Low
**审查维度**：代码质量 / 代码风格 / 功能正确性
**涉及文件**：文件路径
**文档偏差**：与XX文档XX章节的偏差描述
**问题描述**：详细描述
**影响分析**：对系统安全性/可靠性的影响
**修复建议**：具体修复方案
**优先级**：修复优先级及理由
```

最终输出一份完整的**SafetyGateway轨道审查报告**，显式声明所有属于SG轨道应当进行的修复项，按优先级排序。
