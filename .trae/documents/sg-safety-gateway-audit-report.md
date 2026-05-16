# SafetyGateway轨道系统性审查报告

**审查日期**：2026-05-10
**审查范围**：SafetyGateway（SG安全网关）轨道全部验证环节和测试逻辑
**审查方法**：静态代码分析 + 文档对照审查 + 测试逻辑审查
**审查原则**：独立、客观、深入、批判性

---

## 一、审查范围与方法

### 1.1 审查文件清单

| 类别 | 文件数 | 说明 |
|------|--------|------|
| 核心源代码 | 17 | safety_gateway/*.py |
| 集成文件 | 2 | safety_gateway_client.py, three_layer_desensitization.py |
| 测试文件 | 2 | test_safety_gateway.py (201个测试), test_safety_gateway_performance.py |
| 部署文件 | 4 | docker-compose.dev.yml, Dockerfile.safety-gateway.dev, supervisor conf, start script |
| 规则配置 | 4 | desensitization_rules_safety.yaml, patch, contraindication, person_names |

### 1.2 对照文档

| 优先级 | 文档 | 审查重点 |
|--------|------|----------|
| P0 | 01-系统架构设计.md | 进程架构、核心校验职责、降级策略、OOM保护 |
| P0 | 04-安全合规.md | 三重脱敏防线、规则异构冗余、同意状态校验 |
| P1 | 06-开发环境与进度规划.md | 代码组织、测试要求、端口配置、验证标准 |
| P2 | 02-核心流程与知识库.md | 融合层与SG交互流程 |
| P2 | 00b-统一配置参考手册.md | SG配置项完整性和正确性 |

### 1.3 审查方法

1. **静态代码分析**：逐文件审查代码行数、import依赖、类型注解
2. **文档对照审查**：将代码实现与架构文档逐项对比
3. **测试逻辑审查**：验证测试覆盖的完整性和mock设置的准确性
4. **部署配置审查**：验证Docker Compose、Supervisor、OOM配置

---

## 二、静态代码分析结果

### 2.1 代码量统计

| 文件 | 行数 |
|------|------|
| checker.py | 891 |
| threshold.py | 569 |
| desensitization.py | 448 |
| main.py | 381 |
| health.py | 307 |
| contraindication.py | 233 |
| circuit_breaker.py | 218 |
| consent_checker.py | 211 |
| errors.py | 188 |
| validation_result.py | 97 |
| redis_pool.py | 83 |
| sg_config.py | 55 |
| sg_validator.py | 49 |
| sg_models.py | 46 |
| sg_db.py | 42 |
| sg_types.py | 38 |
| __init__.py | 25 |
| **合计** | **3881** |

**偏差**：文档预估1500-2000行（06-开发环境与进度规划.md），实际3881行，超出93.5%。即使对照更新后的2500-3000行预估，仍超出29.4%。

### 2.2 测试用例统计

- **单元测试**：201个测试函数，46个测试类
- **性能测试**：6个测试函数
- **文档预估**：80-120个
- **偏差**：超出67.5%

### 2.3 依赖隔离验证

✅ **通过**：safety_gateway/目录无任何`from app.`或`import app.`导入，与融合层代码完全隔离。

---

## 三、逐项审查结果

### 3.1 进程隔离架构（P0）

| 检查项 | 预期 | 实际 | 结果 |
|--------|------|------|------|
| 独立uvicorn实例 | main.py通过uvicorn.run启动 | ✅ main.py启动独立FastAPI服务 | 通过 |
| HTTP localhost通信 | SafetyGatewayClient通过httpx调用 | ✅ safety_gateway_client.py使用httpx.AsyncClient | 通过 |
| P99延迟基准<5ms | 健康检查计算P99 | ✅ health.py计算P50/P95/P99 | 通过 |
| Docker Compose depends_on | 主服务depends_on SG | ✅ docker-compose.dev.yml:75-76 | 通过 |
| 主服务崩溃时SG独立存活 | SG独立进程 | ✅ 独立容器+独立进程 | 通过 |

**⚠️ 偏差发现**：

> **[P0-1] 客户端快速失败机制与架构文档健康检查机制不一致**
>
> 架构文档描述"主服务每4秒向SafetyGateway发送HTTP GET /health请求，超时1s，连续3次失败判定不可用(15s)"。但`safety_gateway_client.py`实现的是**请求驱动快速失败**（`_consecutive_failures`阈值3），而非**周期性健康检查**。
>
> 两种机制的时间特性不同：
> - 架构文档：3 × (4s + 1s) = 15s 才判定不可用
> - 实际实现：3次连续请求失败即判定不可用，可能在数秒内触发
>
> **评估**：实际实现的快速失败策略比文档描述更保守（更快拒绝），从安全角度更优。但与文档不一致，需更新文档或增加周期性健康检查。

### 3.2 OOM保护（P0）

| 检查项 | 预期 | 实际 | 结果 |
|--------|------|------|------|
| oom_score_adj = -1000 | docker-compose.dev.yml配置 | ✅ docker-compose.dev.yml:130 | 通过 |
| 启动脚本设置 | start_safety_gateway.sh | ✅ start_safety_gateway.sh:5-6 | 通过 |
| 内存预算≤512MB | docker-compose.dev.yml | ✅ docker-compose.dev.yml:126 | 通过 |
| 健康检查报告OOM状态 | health.py _check_oom_protection() | ✅ health.py:278-288 | 通过 |

**❌ 偏差发现**：

> **[P0-2] OOM保护状态未参与健康检查判定逻辑**
>
> `health.py:34-40`中`check_all()`方法计算`components_healthy`时仅检查redis、db、rules、circuit_breaker、threshold五个组件，**不包含`oom_status`**。`oom_status`仅作为信息字段返回（line 95），不影响健康检查结果。
>
> 这意味着即使`oom_score_adj`未正确设置为-1000（OOM保护失效），健康检查仍可能返回"healthy"，主服务不会感知到OOM保护缺失。
>
> **影响**：安全关键配置缺失无法被自动检测，违反"先校验后输出"的安全原则。

### 3.3 核心校验流程（P0）

| 检查项 | 预期 | 实际 | 结果 |
|--------|------|------|------|
| 四项校验执行顺序 | 同意→脱敏→禁忌+阈值→一致性 | ✅ checker.py:350-370 | 通过 |
| 先校验后输出 | 全部通过才PASSED | ✅ checker.py:372-384 | 通过 |
| 零容忍策略 | 任一失败即拒绝或降级 | ✅ 各_phase方法 | 通过 |
| 降级输出白名单过滤 | 仅输出白名单内字段 | ✅ checker.py:763-848 | 通过 |
| 禁用分级术语过滤 | 降级输出中过滤TI-RADS等 | ✅ checker.py:736-761 | 通过 |

**❌ 偏差发现**：

> **[P0-14] conclusion_suppressed标记处理逻辑缺失**
>
> 架构文档要求"当融合层主动将diagnostic_conclusion置空（标记conclusion_suppressed:true），SafetyGateway跳过diagnostic_conclusion字段的过滤逻辑，仅对observations字段执行常规结构化身份模式拦截"。
>
> 当前`checker.py`的`perform_full_check()`和`apply_degraded_output()`均未处理此场景——降级输出始终过滤非白名单字段，不检查`conclusion_suppressed`标记。虽然`apply_degraded_output()`在line 803设置了`degraded_data["conclusion_suppressed"] = True`，但这只是输出标记，不是对输入标记的响应。
>
> **影响**：融合层主动抑制诊断结论时，SG仍会执行不必要的过滤逻辑，可能产生冗余的降级标记。

> **[P0-15] 严重幻觉输出处理逻辑缺失**
>
> 架构文档要求"SafetyGateway在输出校验阶段检测到hallucination_severity='severe'时，须在输出中附加醒目安全标记"。
>
> 当前代码未实现幻觉严重程度检测和标记逻辑。`perform_full_check()`的输入参数和校验流程中均无`hallucination_severity`相关处理。
>
> **影响**：严重幻觉输出无法被SG自动标记，前端无法显示红色警告横幅，违反A级安全标准。

> **[P0-16] 降级模式NER"类型化脱敏"策略未实现**
>
> 架构文档要求"SafetyGateway降级输出时，规则增强NER扫描继续执行并采用'类型化脱敏'策略——NER检出的身份信息替换为[REDACTED:{类型}]"。
>
> 当前`desensitization_rules_safety.yaml`中的替换格式不一致：
> - `sg_phone`: `PHONE_***`（应为`[REDACTED:PHONE]`）
> - `sg_patient_name`: `[REDACTED_NAME_SG]`（接近但格式不同，应为`[REDACTED:NAME]`）
> - `sg_email`: `***@***.***`（应为`[REDACTED:EMAIL]`）
> - `sg_id_card`: `***-****-****`（应为`[REDACTED:ID_CARD]`）
>
> 仅患者姓名规则部分实现了类型化脱敏格式，其他类型均未实现。
>
> **影响**：降级输出中脱敏格式不统一，无法通过类型标注保留上下文可读性，违反架构文档降级模式设计要求。

### 3.4 同意状态校验两阶段机制（P0）

| 检查项 | 预期 | 实际 | 结果 |
|--------|------|------|------|
| 入口快速拒绝 | consent_status_at_input非granted即拒绝 | ✅ checker.py:326-346 | 通过 |
| 输出前实时校验 | 直查PostgreSQL | ✅ consent_checker.py:74-87 | 通过 |
| PG不可用降级 | 降级为Redis只读缓存 | ✅ consent_checker.py:163-170 | 通过 |
| Redis缓存未命中 | 拦截所有输出(E4005B) | ✅ consent_checker.py:169-170 | 通过 |
| 不写入Redis缓存 | 正常路径直查PG不双写Redis | ✅ 无写入逻辑 | 通过 |

**❌ 偏差发现**：

> **[P0-5] Redis同意状态缓存数据来源不明**
>
> `consent_checker.py`仅从Redis读取缓存（`_check_redis_cache`），从不写入。代码注释（line 6-7）明确说明"正常路径：直查PostgreSQL获取最新同意状态，不写入Redis缓存"。但Redis中的同意状态数据来源未在SG代码中体现——必须由主服务或其他服务写入。
>
> **影响**：如果主服务未正确写入Redis缓存，SG降级路径将始终缓存未命中，导致E4005B错误。此依赖关系未在SG代码或配置中显式声明。

> **[P0-6] Redis同意状态缓存TTL配置(10s)与文档(60s)不一致**
>
> `sg_config.py:43`中`sg_consent_cache_ttl_seconds=10`，但架构文档要求Redis缓存TTL为60s。
>
> 实际上`sg_consent_cache_ttl_seconds`在`consent_checker.py`中**未被使用**——TTL由Redis写入方控制。此配置项为死配置。
>
> **影响**：配置项与文档不一致，且该配置项未生效，可能误导运维人员。

### 3.5 脱敏规则异构冗余（P0）

| 检查项 | 预期 | 实际 | 结果 |
|--------|------|------|------|
| 独立规则文件 | desensitization_rules_safety.yaml | ✅ 存在 | 通过 |
| 补丁规则独立维护 | desensitization_rules_safety_patch.yaml | ✅ 存在，含4条补充规则 | 通过 |
| 不同执行引擎 | SG纯正则 vs 主服务ML NER+规则增强NER | ✅ SG仅使用正则匹配 | 通过 |
| 规则热更新 | Redis Pub/Sub订阅 | ✅ main.py:127-150 | 通过 |

**✅ 异构冗余验证通过**：

逐条对比主服务规则（`desensitization_rules.yaml`）与SG规则（`desensitization_rules_safety.yaml`），确认所有规则类型均实现异构：

| 规则类型 | 主服务模式 | SG模式 | 异构 |
|----------|-----------|--------|------|
| 身份证号 | 简单18位数字 | 精确日期格式校验 | ✅ |
| 手机号 | 分组匹配(保留后4位) | 整体匹配(无分组) | ✅ |
| 患者姓名 | 多条独立规则 | 合并模式+前后缀定位 | ✅ |
| 邮箱 | 简单ASCII模式 | 支持中文用户名+多标签域名 | ✅ |
| 住址 | 城市分组匹配 | 行政区划关键词定位 | ✅ |
| 病历号 | MR前缀模式 | 关键词+灵活前缀 | ✅ |
| DICOM UID | 数字点分隔模式 | 关键词+长度校验 | ✅ |
| IP地址 | 版本排除模式 | 语义前缀关键词 | ✅ |

### 3.6 降级路径完整性（P0）

| 降级场景 | 代码实现 | 测试覆盖 | 结果 |
|----------|---------|----------|------|
| 正常模式 | ✅ checker.py:372-384 | ✅ TestFullCheckIntegration | 通过 |
| 融合层不可用→SG降级输出 | ✅ checker.py:683-697 | ✅ test_fusion_unavailable_sg_degraded_output | 通过 |
| SG不可用(≤15s)→排队等待 | ✅ safety_gateway_client.py:71-149 | ❌ 无测试 | 不通过 |
| SG不可用(>15s)→E4005 | ✅ safety_gateway_client.py:63-69 | ⚠️ 仅测试E4005B | 部分通过 |
| 融合层+SG同时不可用→E4005C | ✅ checker.py:417-421 | ✅ test_sg_combined_unavailable_e4005c | 通过 |
| 脱敏降级+SG不可用→E4005A | ✅ checker.py:527-541 | ✅ test_desensitization_degraded_sg_unavailable_e4005a | 通过 |
| SG不可用+患者撤回同意→E4005A | ✅ checker.py:401-414 | ✅ test_sg_unavailable_consent_withdrawn_e4005a | 通过 |
| 边缘端断连→本地降级 | ✅ checker.py:487-510 | ✅ test_edge_disconnected_local_degradation | 通过 |
| 组合故障→组合降级 | ✅ checker.py:617-639 | ✅ test_combined_failure_degradation | 通过 |

**❌ 偏差发现**：

> **[P0-8] DegradedReason.SG_UNAVAILABLE为死代码路径**
>
> `validation_result.py`中`DegradedReason`枚举定义了`SG_UNAVAILABLE`，`apply_degraded_output()`中也有对应处理逻辑（line 809-811）。但在`perform_full_check()`的所有代码路径中，没有任何位置将`degraded_reason`设置为`DegradedReason.SG_UNAVAILABLE`。
>
> SG不可用场景在服务端由异常（E4005/E4005A/E4005B/E4005C）处理，在客户端由快速失败机制处理，均不经过`perform_full_check()`的降级输出路径。因此`SG_UNAVAILABLE`降级原因永远不会被赋值。
>
> **影响**：死代码路径，`apply_degraded_output()`中`SG_UNAVAILABLE`分支永远不会执行。

> **[P0-4/P0-10] DESENSITIZATION_DEGRADED_SG_UNAVAILABLE降级原因语义混淆**
>
> `checker.py:527-541`：当`fusion_available=False`且检测到敏感数据时，降级原因为`DESENSITIZATION_DEGRADED_SG_UNAVAILABLE`。但`fusion_available=False`的含义是"融合层不可用"，而非"SG不可用"。
>
> `checker.py:543-561`：当`desensitization_degraded=True`且检测到敏感数据时，降级原因也为`DESENSITIZATION_DEGRADED_SG_UNAVAILABLE`。但此场景下SG本身是可用的，只是融合层脱敏服务降级了。
>
> 对照架构文档降级策略总表：
> - "脱敏降级+SG不可用"应为"融合层脱敏服务超时且SafetyGateway不可达"
> - 当前代码的两个场景均非"SG不可达"
>
> **影响**：降级原因命名与实际触发条件不匹配，可能导致运维人员误判故障位置。

> **[P0-9] 客户端排队等待逻辑无单元测试**
>
> `safety_gateway_client.py:71-149`实现了排队等待逻辑（重试间隔1s，超时10s），但`TestSafetyGatewayClient`仅测试了`_e4005_response()`方法，未测试：
> - 排队等待重试逻辑
> - 超时后返回E4005
> - 连续失败快速失败机制
> - 连接池预热
>
> **影响**：客户端核心容错逻辑无测试覆盖，回归风险高。

### 3.7 E4005错误码子码（P0）

| 子错误码 | 触发条件 | 代码实现 | 测试覆盖 | 结果 |
|----------|---------|----------|----------|------|
| E4005 | SG服务不可用 | ✅ ServiceUnavailableError | ✅ test_e4005_error_code_constant | 通过 |
| E4005A | 脱敏降级+SG不可用 / SG不可用+撤回同意 | ✅ ConsentSafetyViolationError | ✅ 两种场景均有测试 | 通过 |
| E4005B | PG不可用+Redis缓存未命中 | ✅ DatabaseUnavailableError | ✅ test_sg_returns_e4005_when_db_unavailable | 通过 |
| E4005C | SG+融合层同时不可用 | ✅ CombinedUnavailableError | ✅ test_sg_combined_unavailable_e4005c | 通过 |

**⚠️ 偏差发现**：

> **[P1-5] E4005A错误码覆盖两种不同场景**
>
> `ConsentSafetyViolationError`(E4005A)同时覆盖"脱敏降级+SG不可用"和"SG不可用+患者撤回同意"两种场景。这两种场景的降级原因不同（`DESENSITIZATION_DEGRADED_SG_UNAVAILABLE` vs `SG_UNAVAILABLE_CONSENT_WITHDRAWN`），且`apply_degraded_output()`对这两种原因的处理逻辑不同。
>
> 前端可能需要区分这两种场景以显示不同的提示信息，但当前E4005A无法提供此区分。
>
> **影响**：前端无法根据E4005A子码区分不同故障场景，可能显示不精确的错误提示。

### 3.8 端口配置与配置约束（P0）

| 检查项 | 预期 | 实际 | 结果 |
|--------|------|------|------|
| SAFETY_GATEWAY_PORT=9001 | sg_config.py:12 | ✅ | 通过 |
| 健康检查端点 | main.py:192-202 | ✅ /health | 通过 |
| 排队超时>5s且<15s | sg_validator.py:37-41 | ✅ | 通过 |
| P99警告<临界阈值 | sg_validator.py:34-35 | ✅ | 通过 |

**❌ 偏差发现**：

> **[P0-12] 端口冲突防护未实现互斥检测**
>
> `sg_validator.py`仅验证端口范围1-65535，未验证与其他服务端口（8000/8001/8002）的互斥性。架构文档要求"所有端口值须互不相同"，但启动时无端口冲突检测。
>
> **影响**：如果环境变量配置错误导致端口冲突，SG可能启动失败但无明确错误提示。

> **[P0-13] 数据库重试参数可能超过max_total_time限制**
>
> 当前默认值：`sg_db_retry_max_attempts=2`、`sg_db_retry_max_total_time_seconds=4.0`、`db_query_timeout=2`。
>
> 最坏情况重试耗时：2s(第1次查询) + 0.1s(退避延迟) + 2s(第2次查询) = 4.1s > 4.0s。
>
> 虽然`_retry_query()`方法在line 99有`elapsed + delay + self._query_timeout <= self._retry_max_total_time`检查，会在超限时提前终止，但这意味着第2次重试可能被跳过，降低了重试的有效性。
>
> **影响**：配置参数逻辑不一致，可能导致重试次数不足。

### 3.9 健康检查完整性（P1）

| 检查项 | 预期 | 实际 | 结果 |
|--------|------|------|------|
| Docker健康检查interval=4s | docker-compose.dev.yml | ✅ line 118 | 通过 |
| Docker健康检查timeout=1s | docker-compose.dev.yml | ✅ line 119 | 通过 |
| Docker健康检查retries=3 | docker-compose.dev.yml | ✅ line 120 | 通过 |
| supervisor看门狗 | safety-gateway.conf | ✅ autorestart=true | 通过 |
| 崩溃后≤5s重启 | startretries=100 + startsecs=2 | ✅ | 通过 |
| Docker depends_on+健康检查 | 主服务depends_on SG | ✅ docker-compose.dev.yml:75-76 | 通过 |

### 3.10 测试逻辑审查（P1）

**性能基准测试**：
- ✅ 预热机制：50次迭代预热（test_safety_gateway_performance.py:73-74）
- ✅ GC控制：测量期间禁用GC（line 76-85）
- ✅ 高精度计时：使用time.perf_counter()（line 80, 82）
- ✅ P99 < 5ms阈值验证（line 102）
- ⚠️ Mock consent_checker：不测试真实PostgreSQL查询延迟
- ⚠️ 无网络延迟模拟：未测试HTTP localhost通信延迟

**熔断器测试**：
- ✅ 状态转换测试：closed → open → half_open → closed
- ✅ 测试隔离：teardown_method清理测试创建的breaker
- ⚠️ cooldown_period=1.0（测试）vs 30.0（生产），差异可接受

**测试覆盖缺失**：
- ❌ 客户端排队等待逻辑无测试
- ❌ 无Redis主从切换测试
- ❌ 无规则加载失败降级测试
- ❌ 无脱敏规则实时同步延迟测试
- ❌ 无Docker Compose集成测试（进程隔离仅通过mock验证）

---

## 四、与参考文档的偏差分析

### 4.1 01-系统架构设计.md 偏差

| 偏差项 | 文档要求 | 实际实现 | 严重程度 |
|--------|---------|----------|----------|
| 健康检查机制 | 每4秒周期性健康检查 | 请求驱动快速失败 | P1（实际更优，但文档需更新） |
| OOM保护参与健康检查 | 应影响健康状态 | 仅信息字段，不影响判定 | P0 |
| conclusion_suppressed处理 | SG跳过已置空的diagnostic_conclusion | 未实现 | P0 |
| 严重幻觉输出处理 | 附加安全标记 | 未实现 | P0 |
| 降级模式类型化脱敏 | [REDACTED:{类型}] | 仅部分实现 | P0 |
| 降级原因命名 | 语义清晰 | DESENSITIZATION_DEGRADED_SG_UNAVAILABLE语义混淆 | P0 |

### 4.2 04-安全合规.md 偏差

| 偏差项 | 文档要求 | 实际实现 | 严重程度 |
|--------|---------|----------|----------|
| Redis缓存TTL | 60s | sg_consent_cache_ttl_seconds=10（未生效） | P1 |
| Redis缓存数据来源 | 隐含由其他服务写入 | 未在SG代码中声明依赖 | P1 |

### 4.3 06-开发环境与进度规划.md 偏差

| 偏差项 | 文档要求 | 实际实现 | 严重程度 |
|--------|---------|----------|----------|
| 代码量 | 1500-2000行 | 3881行 | P2 |
| 测试用例数 | 80-120个 | 201个 | P2 |
| 客户端排队等待测试 | 降级路径专项测试 | 无测试 | P0 |

### 4.4 00b-统一配置参考手册.md 偏差

| 偏差项 | 文档要求 | 实际实现 | 严重程度 |
|--------|---------|----------|----------|
| 端口冲突防护 | 所有端口值须互不相同 | 仅验证范围，无互斥检测 | P0 |
| 重试参数一致性 | 隐含要求 | max_total_time可能不足 | P0 |

---

## 五、SafetyGateway轨道应当进行的修复项（显式声明）

### 🔴 P0级修复项（必须修复，影响安全底线）

| 编号 | 修复项 | 涉及文件 | 修复方案 |
|------|--------|---------|----------|
| **SG-FIX-01** | OOM保护状态参与健康检查判定 | health.py | 在`check_all()`中将`oom_status`纳入`components_healthy`/`components_degraded`计算。当`oom_status["status"]`为"warning"时标记为degraded，为"unknown"时在Linux环境标记为unhealthy |
| **SG-FIX-02** | 实现conclusion_suppressed标记处理 | checker.py | 在`perform_full_check()`入口检查`output_data.get("conclusion_suppressed")`，若为True则跳过`diagnostic_conclusion`字段的过滤逻辑，仅对`observations`字段执行结构化身份模式拦截 |
| **SG-FIX-03** | 实现严重幻觉输出处理 | checker.py, main.py | 在`perform_full_check()`中增加`hallucination_severity`参数检查，当值为"severe"时在CheckResult中附加`hallucination_warning=True`标记 |
| **SG-FIX-04** | 实现降级模式类型化脱敏策略 | desensitization_rules_safety.yaml, desensitization.py | 将所有规则的replacement字段改为`[REDACTED:{类型}]`格式：`[REDACTED:PHONE]`、`[REDACTED:NAME]`、`[REDACTED:EMAIL]`、`[REDACTED:ID_CARD]`等 |
| **SG-FIX-05** | 修正降级原因命名语义 | checker.py, validation_result.py | 将`DESENSITIZATION_DEGRADED_SG_UNAVAILABLE`拆分为：1) `FUSION_UNAVAILABLE_SENSITIVE_DETECTED`（融合层不可用+敏感数据）；2) `DESENSITIZATION_DEGRADED_SENSITIVE_DETECTED`（脱敏降级+敏感数据）。保留`DESENSITIZATION_DEGRADED_SG_UNAVAILABLE`仅用于"融合层脱敏超时+SG不可达"的真实双重不可用场景 |
| **SG-FIX-06** | 清理DegradedReason.SG_UNAVAILABLE死代码 | validation_result.py, checker.py | 移除`SG_UNAVAILABLE`枚举值及`apply_degraded_output()`中对应分支，或在客户端侧实现SG_UNAVAILABLE降级路径 |
| **SG-FIX-07** | 增加客户端排队等待逻辑测试 | tests/unit/test_safety_gateway_client.py | 新增测试类`TestSafetyGatewayClientQueueLogic`，覆盖：排队重试、超时返回E4005、连续失败快速失败、连接池预热 |
| **SG-FIX-08** | 增加端口冲突互斥检测 | sg_validator.py | 在`validate_sg_config()`中增加与其他已知端口（8000/8001/8002）的互斥检测 |
| **SG-FIX-09** | 修正数据库重试参数一致性 | sg_config.py | 将`sg_db_retry_max_total_time_seconds`从4.0调整为5.0，或减少`sg_db_retry_max_attempts`为1，确保重试总耗时不超过max_total_time |

### 🟡 P1级修复项（应当修复，影响运维和合规）

| 编号 | 修复项 | 涉及文件 | 修复方案 |
|------|--------|---------|----------|
| **SG-FIX-10** | 明确Redis同意状态缓存数据来源 | consent_checker.py, docs | 在代码注释和架构文档中明确声明Redis同意状态缓存由主服务写入，SG仅读取。增加启动时Redis缓存可用性检查 |
| **SG-FIX-11** | 修正Redis缓存TTL配置 | sg_config.py | 移除未使用的`sg_consent_cache_ttl_seconds`配置项，或在consent_checker.py中实际使用该配置验证缓存TTL |
| **SG-FIX-12** | 更新架构文档健康检查描述 | 01-系统架构设计.md | 将"每4秒健康检查"描述更新为"请求驱动快速失败+Docker健康检查"双机制，或实现周期性健康检查 |
| **SG-FIX-13** | 考虑E4005A子码拆分 | errors.py, checker.py | 评估是否将E4005A拆分为E4005A1（脱敏降级+SG不可用）和E4005A2（SG不可用+患者撤回同意），以便前端区分显示 |
| **SG-FIX-14** | 增加规则加载失败降级测试 | tests/unit/test_safety_gateway.py | 新增测试：YAML规则文件不存在时使用默认规则、规则文件格式错误时的降级行为 |
| **SG-FIX-15** | 增加Redis主从切换测试 | tests/unit/test_safety_gateway.py | 新增测试：Redis连接中断后重连的降级和恢复行为 |

### 🟢 P2级修复项（建议改进）

| 编号 | 修复项 | 涉及文件 | 修复方案 |
|------|--------|---------|----------|
| **SG-FIX-16** | 更新代码量预估 | 06-开发环境与进度规划.md | 将代码量预估从1500-2000行更新为3500-4000行 |
| **SG-FIX-17** | 更新测试用例数预估 | 06-开发环境与进度规划.md | 将测试用例数预估从80-120个更新为180-220个 |
| **SG-FIX-18** | 确认PENDING状态快速拒绝策略 | checker.py | 确认`consent_status_at_input="pending"`时是否应在入口阶段即拒绝（当前实现），还是应等到输出前校验阶段 |

---

## 六、审查总结

### 6.1 总体评估

SafetyGateway轨道的实现**基本符合架构设计要求**，在进程隔离、核心校验流程、降级路径覆盖、脱敏规则异构冗余等关键方面实现正确。但存在**3项架构文档要求的功能未实现**（conclusion_suppressed处理、严重幻觉输出处理、类型化脱敏策略），以及**2项配置一致性问题**（OOM保护未参与健康检查、Redis缓存TTL不一致），这些是需要优先修复的安全关键问题。

### 6.2 问题统计

| 严重程度 | 数量 | 说明 |
|----------|------|------|
| P0（必须修复） | 9 | 影响安全底线或功能正确性 |
| P1（应当修复） | 6 | 影响运维效率或合规性 |
| P2（建议改进） | 3 | 文档更新或设计确认 |
| **合计** | **18** | |

### 6.3 优势亮点

1. **进程隔离架构完整**：独立容器、独立进程、HTTP通信、Docker depends_on健康检查全部实现
2. **脱敏规则异构冗余充分**：8种规则类型全部实现异构，同一逻辑缺陷不会同时击穿三道防线
3. **降级路径覆盖全面**：7种降级场景均有代码实现和测试覆盖（客户端排队等待除外）
4. **熔断器模式实现正确**：CLOSED→OPEN→HALF_OPEN→CLOSED状态转换完整
5. **测试覆盖超出预期**：201个测试函数，包含7种降级场景端到端测试、OOM保护测试、P99延迟告警测试等
6. **配置校验器实现合理**：排队超时范围、P99阈值逻辑等关键约束已验证

### 6.4 风险评估

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|----------|
| OOM保护失效未被发现 | 中 | 高 | SG-FIX-01 |
| 严重幻觉输出无安全标记 | 高 | 高 | SG-FIX-03 |
| 降级原因语义混淆导致误判 | 中 | 中 | SG-FIX-05 |
| 客户端排队等待逻辑回归 | 中 | 中 | SG-FIX-07 |
| Redis缓存数据源不可用 | 低 | 高 | SG-FIX-10 |

---

**审查人**：AI审查代理
**审查完成时间**：2026-05-10
**下次审查建议**：SG-FIX-01至SG-FIX-09修复完成后进行复审
