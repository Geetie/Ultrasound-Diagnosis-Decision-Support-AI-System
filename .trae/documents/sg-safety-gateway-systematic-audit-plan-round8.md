# SG安全网关轨道系统性审查计划（第八轮）

**审查日期**：2026-05-11
**审查范围**：SafetyGateway轨道全部源码、客户端与集成层、测试文件、配置与部署文件、YAML规则文件
**审查原则**：独立、客观、深入、批判性
**前置参考**：第七轮审查报告（sg-safety-gateway-audit-report-round7.md）

---

## 一、审查目标

对SG轨道的所有验证环节和测试逻辑进行全面、系统性的审查，确保：
1. 进程隔离正确实现，延迟基准P99<5ms
2. 7种降级场景（实际10种）完整覆盖且路径可达
3. OOM保护配置正确（oom_score_adj=-1000）
4. 健康检查端点完整可用
5. 与设计文档一致性

---

## 二、R7遗留修复项验证（12项）

### 2.1 P0级验证

| 修复项 | 描述 | 验证方法 |
|--------|------|----------|
| SG-FIX-R7-01 | three_layer_desensitization.py键名不匹配 | 逐行代码审查：检查`degraded_mode`/`degraded`/`allowed`/`passed`键名是否统一；验证`desensitization_degraded`参数传递链完整性 |
| SG-FIX-R7-02 | SafetyGatewayClient并发竞态条件 | 审查`_consecutive_failures`和`_get_client()`是否已添加asyncio.Lock保护 |

### 2.2 P1级验证

| 修复项 | 描述 | 验证方法 |
|--------|------|----------|
| SG-FIX-R7-03 | medical_person_names.yaml known_combinations声明 | 检查注释与数据一致性 |
| SG-FIX-R7-04 | 独立姓名脱敏规则覆盖不足 | 对比desensitization_rules_safety.yaml与主规则，验证无前缀姓名覆盖 |
| SG-FIX-R7-05 | Dockerfile与supervisor HOME目录不一致 | 对比Dockerfile.safety-gateway.dev与safety-gateway.conf |
| SG-FIX-R7-06 | 客户端E4005响应不区分子类型 | 审查client.py是否从SG响应中提取E4005A/B/C |
| SG-FIX-R7-07 | supervisor startretries=5限制 | 审查safety-gateway.conf配置 |

### 2.3 P2级验证

| 修复项 | 描述 | 验证方法 |
|--------|------|----------|
| SG-FIX-R7-08 | R6遗留7项类型注解风格 | 逐文件检查redis_pool.py/sg_config.py/sg_validator.py/consent_checker.py/three_layer_desensitization.py/__init__.py |
| SG-FIX-R7-09 | 性能测试time.time()精度 | 检查test_safety_gateway_performance.py是否改用perf_counter() |
| SG-FIX-R7-10 | medical_person_names.yaml suffixes含"患者" | 检查YAML数据 |
| SG-FIX-R7-11 | start_safety_gateway.sh缺set -e | 检查脚本 |
| SG-FIX-R7-12 | supervisor日志轮转缺失 | 检查safety-gateway.conf |

### 2.4 R6历史遗留验证

| 修复项 | 描述 | 验证方法 |
|--------|------|----------|
| SG-FIX-R6-01 | SGError→HTTP状态码映射 | 检查main.py是否添加FastAPI exception_handler映射E4005B/E4005C→503 |
| SG-FIX-R6-02 | 单例线程安全 | 检查contraindication.py/threshold.py是否使用double-checked locking |

---

## 三、代码质量深度审查

### 3.1 健壮性审查

**审查步骤**：

1. **并发安全全面审查**
   - 逐模块检查所有共享可变状态的同步保护
   - 重点：checker.py单例、consent_checker.py单例、desensitization.py `_rules_lock`、circuit_breaker.py `_lock`、health.py `_lock`/`_fusion_check_lock`、redis_pool.py `_init_lock`、threshold.py `_thresholds_lock`
   - 验证asyncio.Lock与threading.Lock的使用场景是否正确（asyncio.Lock用于协程间，threading.Lock用于线程间）
   - 检查是否存在锁嵌套死锁风险

2. **资源泄漏审查**
   - lifespan中所有资源（Redis连接池、checker、threshold订阅）是否正确关闭
   - httpx.AsyncClient是否在所有退出路径上关闭
   - 数据库会话是否在所有异常路径上关闭

3. **异常处理完整性审查**
   - perform_full_check()所有异常路径是否正确返回CheckResult或抛出SGError
   - FastAPI异常处理器是否覆盖所有SGError子类
   - three_layer_desensitization.py异常处理是否安全

4. **规则热更新回滚审查**
   - reload_rules()失败时是否正确恢复旧规则
   - 热更新期间是否有请求使用了不一致的规则快照

### 3.2 可维护性审查

**审查步骤**：

1. **模块职责边界审查**
   - safety_gateway/包是否未导入app.或fusion.模块（进程隔离正确性）
   - 各模块间依赖方向是否单向（无循环依赖）
   - checker.py协调职责是否清晰

2. **配置集中度审查**
   - 所有可调参数是否在sg_config.py中
   - 环境变量是否通过SGSettings统一管理
   - 配置验证是否完整（sg_validator.py）

3. **错误类型层次审查**
   - errors.py 18种异常类型继承层次是否清晰
   - E4005系列是否与设计文档完全对齐
   - to_dict()序列化是否完整

### 3.3 性能审查

**审查步骤**：

1. **P99延迟基准审查**
   - HTTP通信开销P99<5ms的监控机制是否完整
   - 端到端处理延迟P99<50ms是否有监控
   - 500样本滑动窗口统计是否正确
   - warning=4ms/critical=5ms阈值是否合理

2. **数据库查询优化审查**
   - consent_checker.py单条查询+索引是否正确
   - 查询超时配置是否合理（db_query_timeout=5s）
   - 重试策略是否会影响P99

3. **Redis连接池审查**
   - 独立DB 1隔离是否正确
   - 连接池大小是否合理（max_connections=50）
   - 健康检查失败后客户端重建是否安全

4. **内存预算审查**
   - 512MB预算是否合理
   - 内存监控多平台支持是否完整
   - 是否有内存泄漏风险

---

## 四、代码风格合规性审查

### 4.1 类型注解风格统一

**审查步骤**：

1. 逐文件检查是否统一使用Python 3.10+风格（`X | Y`替代`Optional[X]`，`list[X]`替代`List[X]`）
2. 重点文件：redis_pool.py、sg_config.py、sg_validator.py、consent_checker.py、three_layer_desensitization.py
3. 检查是否还有`from typing import Optional, List, Dict`等旧式导入

### 4.2 命名约定审查

1. 类名PascalCase
2. 函数名snake_case
3. 常量UPPER_SNAKE_CASE
4. 私有方法_前缀
5. 模块级单例命名一致性

### 4.3 __init__.py公共API导出

1. safety_gateway/__init__.py是否导出公共API
2. 导出列表是否完整

---

## 五、功能正确性验证

### 5.1 进程隔离验证（P0）

**验证项**：

| 验证项 | 设计要求 | 验证方法 |
|--------|----------|----------|
| 独立uvicorn实例 | SG以独立进程运行 | 审查main.py uvicorn.run配置 |
| HTTP localhost通信 | 主服务通过HTTP调用SG | 审查client.py httpx调用 |
| 主服务崩溃时独立存活 | 返回E4005错误码 | 审查docker-compose.dev.yml独立容器配置 |
| Docker Compose depends_on | api依赖safety-gateway健康检查 | 审查docker-compose.dev.yml depends_on配置 |
| OOM保护 | oom_score_adj=-1000 | 审查docker-compose.dev.yml + start_safety_gateway.sh |
| 独立Redis连接池(DB 1) | 与主服务DB 0隔离 | 审查redis_pool.py URL替换逻辑 |

### 5.2 四阶段校验流程验证（P0）

**验证步骤**：

1. 审查perform_full_check()执行顺序：consent→edge→desensitization→contraindication/threshold→consistency
2. 验证每个阶段的快速拒绝/降级/通过逻辑
3. 验证入口快速拒绝（consent_status_at_input非granted时立即拒绝）
4. 验证输出前直查PostgreSQL（consent_checker.py无缓存写入）

### 5.3 降级场景验证（P0）

**10种降级场景逐一验证**：

| 场景 | DegradedReason | 验证要点 |
|------|----------------|----------|
| 1. 敏感数据检测 | SENSITIVE_DATA_DETECTED | 降级输出仅白名单字段 |
| 2. DB超时 | DB_TIMEOUT | consent_checker超时降级 |
| 3. 禁忌命中 | CONTRAINDICATION_HIT | 禁忌规则引擎匹配 |
| 4. 置信度低于阈值 | CONFIDENCE_BELOW_THRESHOLD | 阈值引擎校验 |
| 5. 融合层不可用 | FUSION_UNAVAILABLE | HealthChecker检测 |
| 6. 组合故障 | COMBINED_FAILURE | 多项故障同时发生 |
| 7. 边缘断连 | EDGE_DISCONNECTED | edge_connected=False |
| 8. 融合层不可用+敏感数据 | FUSION_UNAVAILABLE_SENSITIVE_DETECTED | E4005A1 |
| 9. 脱敏降级+敏感数据 | DESENSITIZATION_DEGRADED_SENSITIVE_DETECTED | E4005A1 |
| 10. SG不可用+患者撤回 | SG_UNAVAILABLE_CONSENT_WITHDRAWN | E4005A2 |

### 5.4 E4005系列错误码验证（P0）

**验证步骤**：

1. 审查errors.py E4005/E4005A1/E4005A2/E4005B/E4005C定义
2. 验证服务端异常处理器是否正确映射HTTP状态码
3. 验证客户端是否正确区分子类型
4. 验证three_layer_desensitization.py是否正确处理各子类型

### 5.5 安全合规关键项验证（P0）

**验证项**：

| 验证项 | 设计要求 | 验证方法 |
|--------|----------|----------|
| 三重脱敏防线 | SG为第三道防线 | 审查three_layer_desensitization.py三层集成 |
| 规则异构冗余 | 独立编写规则集 | 逐条对比desensitization_rules_safety.yaml与desensitization_rules.yaml |
| 同意状态直查PG | 正常路径不写Redis缓存 | 审查consent_checker.py无_cache_consent_to_redis |
| 两阶段同意校验 | 入口快速拒绝+输出前直查PG | 审查checker.py两阶段逻辑 |
| A级安全标准 | 先校验后输出，零容忍 | 审查perform_full_check()全部通过才PASSED |
| OOM保护 | oom_score_adj=-1000 | 审查Docker Compose + 启动脚本 |
| 内存预算 | ≤512MB | 审查health.py监控 + sg_memory_budget_mb |

---

## 六、测试逻辑审查

### 6.1 测试覆盖率审查

**审查步骤**：

1. 统计test_safety_gateway.py测试用例数（目标80-120，实际~202）
2. 审查降级场景覆盖（7种设计要求→10种实现）
3. 审查E4005系列错误码测试
4. 审查进程隔离测试
5. 审查P99延迟基准测试
6. 审查OOM保护测试

### 6.2 测试盲区识别

**重点审查**：

1. SafetyGatewayClient HTTP请求/重试/fast-fail是否有完整单元测试
2. three_layer_desensitization.py三层集成是否有端到端测试
3. 并发请求场景是否有测试
4. 端到端延迟P99<50ms是否有测试
5. desensitization_degraded参数传递是否有测试

### 6.3 测试质量审查

**审查步骤**：

1. Mock使用是否正确（是否过度Mock导致测试无意义）
2. 测试隔离性（测试间是否有状态泄漏）
3. 边界条件覆盖
4. 异常场景测试是否充分
5. 性能测试精度（time.time() vs time.perf_counter()）

---

## 七、与参考文档一致性审查

### 7.1 P0文档：01-系统架构设计.md

**审查项**：

| 需求项 | 设计要求 | 验证方法 |
|--------|----------|----------|
| 进程隔离 | 独立进程，HTTP localhost通信 | 代码审查 |
| E4005子码差异化 | E4005A/B/C应有不同前端提示 | 服务端+客户端代码审查 |
| 三重脱敏防线 | SG为第三道防线 | three_layer_desensitization.py审查 |
| 端到端处理延迟P99 | <50ms | 监控机制审查 |
| OOM保护 | oom_score_adj=-1000 | 配置审查 |
| A级安全标准 | 先校验后输出，零容忍 | 代码审查 |
| 同意状态校验 | 直查PG，正常路径禁用缓存 | 代码审查 |
| HTTP通信P99 | <5ms | 监控机制审查 |
| 健康检查 | /health端点，4s探测，1s超时，3次失败 | 代码+配置审查 |
| supervisor看门狗 | 崩溃后自动重启≤5s | 配置审查 |

### 7.2 P0文档：04-安全合规.md

**审查项**：

| 需求项 | 设计要求 | 验证方法 |
|--------|----------|----------|
| 三重脱敏防线 | 三道防线防御目标不同，不可互相替代 | 集成层代码审查 |
| 规则异构冗余 | 独立编写规则集 | YAML规则逐条对比 |
| 双重降级保障 | 双重缺失时拒绝输出 | E4005A代码审查 |
| 患者同意管理 | 两阶段校验 | 代码审查 |
| 独立姓名脱敏覆盖 | SafetyGateway结构化身份模式拦截 | 规则覆盖审查 |

### 7.3 P1文档：06-开发环境与进度规划.md

**审查项**：

| 需求项 | 设计要求 | 验证方法 |
|--------|----------|----------|
| 代码组织 | 独立Python package safety_gateway/ | 目录结构审查 |
| 代码量 | 2500-3000行 | 行数统计 |
| 测试用例数 | 80-120个 | 测试用例统计 |
| 端口配置 | SAFETY_GATEWAY_PORT=9001 | 配置审查 |
| Docker Compose | depends_on+健康检查 | 配置审查 |
| Gate 0验证 | 主服务崩溃时独立返回E4005 | 测试审查 |

---

## 八、新发现维度审查（R7未覆盖）

### 8.1 three_layer_desensitization.py键名一致性深度审查

**关键发现**：R7-01描述的键名不匹配问题可能比报告更复杂。当前代码使用`check_result.get("degraded", False)`，但SG服务端SafetyCheckResponse返回`degraded_mode`字段。需要验证：

1. SG服务端正常响应（200）的键名：`degraded_mode`（SafetyCheckResponse模型）
2. 客户端E4005 fallback响应的键名：`degraded`（_e4005_response方法）
3. 当前代码只检查`degraded`，遗漏了服务端的`degraded_mode`
4. 同理：`passed`（服务端）vs `allowed`（客户端fallback）
5. 修复方案：应同时检查两个键名，或统一键名

### 8.2 desensitization_degraded语义错误审查

**关键发现**：process_full_pipeline()中`desensitization_degraded=layer1_degraded`，但`layer1_degraded`来自`layer1_result.get("modified", False)`。"modified"表示脱敏引擎做了替换（预期行为），不等于"降级"。语义混淆可能导致SG误判脱敏状态。

### 8.3 SafetyGatewayClient.check_output()请求体字段审查

**关键发现**：client.py第121-129行发送的请求体使用`fusion_available`字段，但SG服务端SafetyCheckRequest模型中没有`fusion_available`字段（服务端通过HealthChecker自主检测）。这意味着客户端传入的`fusion_available`被服务端忽略，客户端对融合层可用性的判断与服务端不一致。

### 8.4 consent_checker.py Redis缓存TTL审查

**关键发现**：设计文档要求"PostgreSQL不可用时降级为Redis只读缓存（TTL 60s）"，但sg_config.py中`sg_consent_cache_ttl_seconds=10`。TTL阈值不一致——Redis key的TTL由主服务写入时设置，SG只读取TTL判断是否过期。需验证主服务写入Redis时的TTL是否为60s。

### 8.5 circuit_breaker.py _update_state()线程安全审查

**关键发现**：`_update_state()`方法在`state`和`is_available`属性中被调用，但这些属性可能在持有`_lock`的`record_failure`/`record_success`方法之外被并发访问。代码注释声明"asyncio单线程模型下安全"，但若未来引入多线程需添加同步保护。

---

## 九、审查执行计划

### 步骤1：R7遗留修复项验证（12项 + 2项R6遗留）

- 逐项代码级确认修复状态
- 对未修复项记录当前代码状态
- 验证方法：逐文件Read + Grep

### 步骤2：代码质量深度审查

- 并发安全：逐模块审查共享可变状态同步保护
- 资源泄漏：审查所有资源获取/释放路径
- 异常处理：审查所有异常路径
- 规则热更新：审查reload_rules()回滚逻辑

### 步骤3：功能正确性验证

- 进程隔离：6项验证
- 四阶段校验流程：5项验证
- 降级场景：10种逐一验证
- E4005系列：5项验证
- 安全合规：7项验证

### 步骤4：测试逻辑审查

- 覆盖率统计
- 盲区识别
- 质量评估

### 步骤5：文档一致性审查

- 01-系统架构设计.md：10项
- 04-安全合规.md：5项
- 06-开发环境与进度规划.md：6项

### 步骤6：新发现维度审查

- 键名一致性深度审查
- desensitization_degraded语义审查
- fusion_available字段审查
- Redis缓存TTL审查
- circuit_breaker线程安全审查

### 步骤7：生成审查报告

- 汇总所有发现
- 按P0/P1/P2分级
- 显式声明SG轨道修复项
- 提出修复优先级建议

---

## 十、SafetyGateway轨道修复项显式声明格式

审查报告将包含以下格式的修复项声明：

### P0级修复项（必须修复，存在运行时安全缺陷）

| 修复项 | 描述 | 文件 | 修复类型 |

### P1级修复项（应当修复，存在功能缺陷或安全隐患）

| 修复项 | 描述 | 文件 | 修复类型 |

### P2级修复项（建议修复，提升质量）

| 修复项 | 描述 | 文件 | 修复类型 |

---

## 十一、审查工具与方法

1. **静态代码分析**：逐文件Read + 代码路径追踪
2. **文档比对**：设计文档原文与代码实现对照
3. **规则异构冗余验证**：YAML规则逐条对比
4. **配置一致性审查**：Docker Compose + supervisor + 启动脚本
5. **测试逻辑审查**：测试文件覆盖率 + 质量评估
6. **键名一致性追踪**：服务端模型→客户端调用→集成层处理的完整链路
