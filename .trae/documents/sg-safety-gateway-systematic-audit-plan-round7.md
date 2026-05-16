# SG安全网关轨道第七轮系统性审查计划

**审查日期**：2026-05-10
**审查原则**：独立、客观、深入、批判性
**前置参考**：第六轮审查报告（sg-safety-gateway-audit-report-round6.md）
**审查目标**：对SG轨道的所有验证环节和测试逻辑进行全面、系统性的审查，确保其正确有效性

---

## 一、审查范围

### 1.1 代码范围

| 类别 | 文件数 | 预估行数 | 审查深度 |
|------|--------|----------|----------|
| 核心源码 `safety_gateway/` | 17 | ~3,500 | 逐文件静态分析 + 执行路径追踪 |
| 客户端与集成层 | 6 | ~800 | 关键路径审查 |
| 测试文件 | 13+ | ~3,200 | 覆盖率 + 质量审查 |
| 配置与部署 | 8 | ~200 | 一致性审查 |
| YAML规则文件 | 4 | ~200 | 异构冗余验证 |

### 1.2 核心参考文档

| 优先级 | 文档 | 关键审查点 |
|--------|------|------------|
| P0 | 01-系统架构设计.md | 进程隔离、延迟基准P99<5ms、OOM保护、A级安全标准、E4005错误码 |
| P0 | 04-安全合规.md | 三重脱敏防线、规则异构冗余、两阶段同意校验、双重降级保障 |
| P1 | 06-开发环境与进度规划.md | 代码组织、测试用例数、端口配置、Docker Compose、验证标准 |
| P2 | 02-核心流程与知识库.md | 融合层与SG交互流程、脱敏数据来源 |
| P2 | 00b-统一配置参考手册.md | SG配置项完整性和正确性 |

---

## 二、审查维度与步骤

### 维度1：代码质量评估（P0优先级）

#### 步骤1.1：R6遗留修复项验证
- 验证 SG-FIX-R6-01：main.py SGError异常是否已映射HTTP状态码
- 验证 SG-FIX-R6-02：contraindication.py和threshold.py单例获取函数是否已添加线程安全保护
- 验证 SG-FIX-R6-03：checker.py `_check_desensitization_phase()`参数类型注解是否已修正
- 验证 SG-FIX-R6-04~R6-08：类型注解风格统一、`__init__.py`导出
- 验证历史遗留 SG-FIX-R4-10：26/31 SG配置项是否已在00b文档登记

#### 步骤1.2：健壮性审查
- 并发安全：检查所有共享状态是否有适当的锁保护（_rules_lock、_sg_checker_lock、asyncio.Lock等）
- 资源泄漏：检查lifespan中是否正确关闭所有资源（Redis连接池、checker、threshold订阅）
- 异常处理完整性：检查PG查询、Redis操作、HTTP调用是否均有try/except
- 熔断器状态转换正确性：验证CLOSED→OPEN→HALF_OPEN→CLOSED完整流程
- 规则热更新回滚机制：验证reload_rules()失败时是否正确恢复old_rules+old_patterns

#### 步骤1.3：性能评估
- P99延迟基准：验证500样本滑动窗口、warning=4ms/critical=5ms配置
- HTTP通信开销：验证httpx AsyncClient + keep-alive + 连接池配置
- PG查询优化：验证consent_checker.py单条查询+索引
- Redis连接池隔离：验证redis_pool.py强制替换URL为DB 1
- 内存预算监控：验证health.py多平台内存统计、512MB预算
- **新增**：端到端处理延迟P99<50ms监控是否已实现（R6发现缺失）

#### 步骤1.4：可维护性评估
- 模块职责边界：checker(协调)/consent_checker(同意)/desensitization(脱敏)/threshold(阈值)/contraindication(禁忌)各司其职
- 依赖方向：safety_gateway/未导入app.或fusion.模块
- 配置集中度：所有可调参数在sg_config.py中
- 错误类型层次：errors.py异常类型继承层次清晰

#### 步骤1.5：可扩展性评估
- 降级场景扩展：DegradedReason枚举+apply_degraded_output()分支模式
- 规则热更新：reload_rules()支持回滚、Pub/Sub同步
- 阈值动态配置：DB加载+Redis订阅、器官级和病种级阈值

### 维度2：代码风格合规性（P1优先级）

#### 步骤2.1：类型注解风格统一性
- 检查所有文件是否统一使用Python 3.10+风格（`str | None`、`dict[str, Any]`、`list[str]`）
- 重点检查R6标记的文件：consent_checker.py、sg_config.py、sg_validator.py、redis_pool.py、three_layer_desensitization.py

#### 步骤2.2：命名约定合规性
- 类名PascalCase
- 函数名snake_case
- 常量UPPER_SNAKE_CASE
- 私有方法_前缀

#### 步骤2.3：文档字符串完整性
- 模块级docstring
- 类级docstring
- 关键方法docstring

### 维度3：功能正确性验证（P0优先级）

#### 步骤3.1：进程隔离验证
- 独立uvicorn实例：main.py uvicorn.run独立启动
- HTTP localhost通信：client.py通过httpx AsyncClient调用
- 主服务崩溃时独立存活：Docker Compose独立容器 + supervisor看门狗
- Docker Compose depends_on：api依赖safety-gateway健康检查
- OOM保护：oom_score_adj=-1000配置
- 独立Redis连接池(DB 1)

#### 步骤3.2：四阶段校验流程验证
- 阶段1：同意状态校验（入口快速拒绝 + 输出前直查PG）
- 阶段2：脱敏校验（结构化身份模式拦截 + 医疗人名词典）
- 阶段3：禁忌+阈值校验（禁忌规则引擎 + 置信度阈值）
- 阶段4：一致性校验（分级/发现/置信度差异检测）
- 执行顺序：consent→desensitization→contraindication→threshold→consistency

#### 步骤3.3：降级场景验证（7种核心 + 3种扩展）
- 场景1：敏感数据检测 → SENSITIVE_DATA_DETECTED
- 场景2：SG不可用 → SG_UNAVAILABLE
- 场景3：DB超时 → DB_TIMEOUT
- 场景4：禁忌命中 → CONTRAINDICATION_HIT
- 场景5：置信度低于阈值 → CONFIDENCE_BELOW_THRESHOLD
- 场景6：融合层不可用 → FUSION_UNAVAILABLE
- 场景7：组合故障 → COMBINED_FAILURE
- 扩展场景8：边缘断连 → EDGE_DISCONNECTED
- 扩展场景9：脱敏降级+SG不可用 → DESENSITIZATION_DEGRADED_SG_UNAVAILABLE
- 扩展场景10：SG不可用+患者撤回 → SG_UNAVAILABLE_CONSENT_WITHDRAWN

#### 步骤3.4：E4005系列错误码验证
- E4005：SG不可用（客户端通用）
- E4005A：SG不可用或脱敏降级+敏感数据
- E4005B：PG不可用+Redis缓存未命中
- E4005C：SG+融合层同时不可用
- **重点**：验证E4005B/E4005C是否已正确映射HTTP 503（SG-FIX-R6-01）

#### 步骤3.5：安全合规关键项验证
- 三重脱敏防线：SG为第三道防线
- 规则异构冗余：独立编写规则集
- 同意状态直查PG：正常路径不写Redis缓存
- 两阶段同意校验：入口快速拒绝 + 输出前直查PG
- A级安全标准：先校验后输出，零容忍
- 禁忌校验fail-closed：异常时默认存在禁忌
- 降级输出仅白名单：过滤diagnostic_conclusion，仅保留observations
- OOM保护：oom_score_adj=-1000
- consent_status_at_output：SG输出前直查PG获取同意状态快照
- NER方案：纯正则+词典，不依赖ML NER
- 内存预算：≤512MB

#### 步骤3.6：边界条件和异常场景验证
- 同意状态为None/非法值
- 递归深度超限
- 规则编译失败/重载失败回滚
- 阈值加载失败降级
- Redis不可用降级
- PG不可用+Redis缓存未命中
- 组合故障（≥2项同时）
- 空专科结论列表
- 白名单字段值为None/list
- **新增**：空output_data处理
- **新增**：consent_status_at_input快速拒绝路径

### 维度4：测试逻辑审查（P0优先级）

#### 步骤4.1：测试覆盖率评估
- 测试用例数量（要求80-120个）
- 降级场景覆盖（7种核心场景）
- E4005系列错误码测试
- 进程隔离测试（Gate 0要求）
- P99延迟基准测试
- OOM保护测试
- 规则热更新测试
- 四阶段执行顺序测试
- 熔断器状态转换测试

#### 步骤4.2：测试质量评估
- Mock正确性：异步行为模拟是否正确
- 测试隔离性：全局状态清理
- 边界条件覆盖
- 参数化覆盖
- 断言完整性

#### 步骤4.3：测试盲区识别
- load_thresholds_from_db真实DB测试
- _delete_threshold_from_db测试
- 并发请求场景测试
- client超时重试完整流程测试
- SGError→HTTP状态码映射测试
- **新增**：端到端延迟P99<50ms验证测试
- **新增**：规则热更新Pub/Sub消息丢失场景测试
- **新增**：熔断器半开状态下并发请求测试

### 维度5：与参考文档一致性审查（P0优先级）

#### 步骤5.1：01-系统架构设计.md一致性
- 进程隔离实现
- HTTP localhost通信
- Redis连接隔离
- NER方案
- 同意状态校验
- OOM保护
- A级安全标准
- 降级策略
- 内存预算
- 健康检查配置
- HTTP通信开销P99
- **端到端处理延迟P99<50ms**
- E4005子码差异化

#### 步骤5.2：04-安全合规.md一致性
- 三重脱敏防线
- 规则异构冗余
- 双重降级保障
- 患者同意管理
- 禁忌校验fail-closed
- consent_status_at_output

#### 步骤5.3：06-开发环境与进度规划.md一致性
- 代码组织（独立Python package）
- 代码量（预估2500-3000行）
- 测试用例数（80-120个）
- 端口配置（SAFETY_GATEWAY_PORT=9001）
- Gate 0验证标准
- Docker Compose配置
- supervisor看门狗配置

#### 步骤5.4：02-核心流程与知识库.md一致性
- 融合层与SG交互流程
- 脱敏数据来源正确性

#### 步骤5.5：00b-统一配置参考手册.md一致性
- SG相关配置项完整性和正确性
- SG-FIX-R4-10遗留：26/31配置项是否已登记

---

## 三、审查方法

### 3.1 静态代码分析
- 逐文件阅读核心源码（17个文件）
- 追踪执行路径（四阶段校验、10种降级场景）
- 检查并发安全（锁使用、共享状态）
- 检查资源管理（连接池、会话、订阅）

### 3.2 测试逻辑审查
- 阅读全部测试文件
- 评估Mock正确性和隔离性
- 识别测试盲区
- 验证边界条件覆盖

### 3.3 文档一致性比对
- 逐项对照5份核心设计文档
- 标记偏差等级（P0/P1/P2）
- 评估偏差合理性

### 3.4 规则异构冗余验证
- 逐条对比desensitization_rules_safety.yaml与desensitization_rules.yaml
- 验证正则模式、替换策略、规则拆分的实质性差异

### 3.5 部署配置审查
- Docker Compose配置一致性
- supervisor配置正确性
- 启动脚本正确性
- OOM保护配置

---

## 四、审查输出

### 4.1 审查报告结构
1. 审查范围与方法
2. R6遗留修复项验证结果
3. 新发现的问题（按P0/P1/P2分级）
4. 代码质量评估结果
5. 代码风格合规性评估结果
6. 功能正确性验证结果
7. 测试逻辑审查结果
8. 与参考文档的偏差分析
9. **SafetyGateway轨道修复项显式声明**
10. 审查结论

### 4.2 SafetyGateway轨道修复项显式声明格式
每个修复项包含：
- 修复项编号（SG-FIX-R7-XX）
- 严重程度（P0/P1/P2）
- 描述
- 涉及文件
- 修复类型（代码修复/代码增强/代码风格/文档补充）
- 设计文档参考

---

## 五、实施步骤

### 步骤1：R6遗留修复项验证
逐项代码级确认R6的8项修复是否已落地：
1. SG-FIX-R6-01：main.py SGError→HTTP状态码映射
2. SG-FIX-R6-02：单例获取函数线程安全
3. SG-FIX-R6-03：_check_desensitization_phase()类型注解
4. SG-FIX-R6-04：redis_pool.py类型注解风格
5. SG-FIX-R6-05：sg_config.py/sg_validator.py类型注解风格
6. SG-FIX-R6-06：consent_checker.py类型注解风格
7. SG-FIX-R6-07：three_layer_desensitization.py类型注解风格
8. SG-FIX-R6-08：__init__.py公共API导出

### 步骤2：核心源码逐文件审查
按以下顺序审查17个核心文件：
1. main.py — 入口、路由、异常处理、lifespan
2. checker.py — 主校验器、四阶段流程、降级输出
3. consent_checker.py — 同意状态校验、DB查询、熔断器
4. desensitization.py — 脱敏引擎、规则管理、热更新
5. contraindication.py — 禁忌校验引擎
6. threshold.py — 阈值校验引擎、DB加载、Redis订阅
7. health.py — 健康检查、延迟统计、OOM保护
8. circuit_breaker.py — 熔断器模式
9. errors.py — 错误类型层次
10. sg_types.py — 核心枚举
11. validation_result.py — 校验结果模型
12. sg_config.py — 配置管理
13. sg_validator.py — 配置验证
14. sg_db.py — 数据库连接
15. sg_models.py — ORM模型
16. redis_pool.py — Redis连接池
17. __init__.py — 包导出

### 步骤3：客户端与集成层审查
1. safety_gateway_client.py — HTTP客户端、超时重试、E4005处理
2. safety_service.py — 安全服务
3. three_layer_desensitization.py — 三层脱敏集成
4. diagnosis.py — 诊断接口SG调用
5. errors.py (API) — API错误定义
6. settings.py — 应用配置SG项

### 步骤4：测试文件审查
1. test_safety_gateway.py — 核心单元测试（~2265行）
2. test_safety_gateway_performance.py — 性能基准测试
3. test_desensitization.py — 脱敏单元测试
4. test_desensitization_performance.py — 脱敏性能测试
5. test_security_compliance.py — 安全合规测试
6. test_security_compliance_complete.py — 安全合规完整测试
7. test_sec_audit.py — 安全审计测试
8. test_sec_craft_fixes.py — SEC修复测试
9. test_sec_p0_critical.py — P0关键安全测试
10. test_white_fixes.py — 白盒修复测试
11. test_k1_fixes.py — K1修复测试
12. test_audit_fixes.py — 审计修复测试
13. test_hsm_basic.py — HSM基础测试

### 步骤5：配置与部署审查
1. docker-compose.dev.yml — Docker Compose配置
2. Dockerfile.safety-gateway.dev — Dockerfile
3. start_safety_gateway.sh — 启动脚本
4. safety-gateway.conf — supervisor配置
5. desensitization_rules_safety.yaml — 脱敏安全规则
6. desensitization_rules_safety_patch.yaml — 脱敏安全补丁规则
7. contraindication_rules_safety.yaml — 禁忌安全规则
8. medical_person_names.yaml — 医务人员姓名字典

### 步骤6：文档一致性比对
逐项对照5份核心设计文档，标记偏差

### 步骤7：编写审查报告
按4.1节结构编写完整审查报告，显式声明SafetyGateway轨道修复项

---

## 六、审查重点关注项

基于R6审查结论，本轮审查需特别关注：

1. **R6遗留8项修复是否已落地**（最高优先级）
2. **端到端处理延迟P99<50ms监控**是否已实现（R6发现缺失，P2级偏差）
3. **SGError→HTTP状态码映射**是否已修复（SG-FIX-R6-01，P1级）
4. **单例线程安全**是否已统一（SG-FIX-R6-02，P1级）
5. **类型注解风格**是否已统一（SG-FIX-R6-04~R6-07，P2级）
6. **__init__.py公共API导出**是否已实现（SG-FIX-R6-08，P2级）
7. **规则热更新Pub/Sub消息丢失**场景是否有保护
8. **熔断器半开状态并发请求**行为是否正确
9. **配置项文档登记**是否已完成（SG-FIX-R4-10遗留）
10. **代码量偏差**是否合理（当前~3500行 vs 设计预估2500-3000行）
