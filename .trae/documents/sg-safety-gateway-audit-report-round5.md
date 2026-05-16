# SG安全网关轨道系统性审查报告（第五轮）

**审查日期**：2026-05-10
**审查范围**：SafetyGateway轨道全部源码、测试、配置与部署文件
**审查原则**：独立、客观、深入、批判性
**前置参考**：第四轮审查报告（sg-safety-gateway-audit-report-round4.md）

---

## 一、审查范围与方法

### 1.1 审查范围

| 类别 | 文件数 | 行数 | 审查深度 |
|------|--------|------|----------|
| 核心源码 `safety_gateway/` | 17 | ~3,500 | 逐文件静态分析 + 执行路径追踪 |
| 客户端与集成层 | 6 | ~800 | 关键路径审查 |
| 测试文件 | 12 | ~3,200 | 覆盖率 + 质量审查 |
| 配置与部署 | 7 | ~200 | 一致性审查 |

### 1.2 审查方法

1. **第四轮10项修复落地验证**：逐项代码级确认
2. **静态代码分析**：运行时崩溃风险、并发安全、资源泄漏、性能瓶颈
3. **执行路径追踪**：四阶段校验流程、7种降级场景、同意状态校验
4. **测试逻辑审查**：覆盖率、Mock正确性、隔离性、边界条件
5. **文档一致性比对**：对照5份核心设计文档

---

## 二、第四轮10项修复落地验证

### 验证结果汇总

| 修复项 | 描述 | 状态 | 验证说明 |
|--------|------|------|----------|
| SG-FIX-R4-01 | threshold.py Core Select返回错误数据类型 | ✅ FIXED | `load_thresholds_from_db()`第91行使用`select(OrganDiseaseThreshold)`（ORM select），`_persist_threshold_to_db()`第306-311行和第325-328行也使用ORM select |
| SG-FIX-R4-02 | ThreeLayerDesensitization进程内调用SG违反进程隔离 | ✅ FIXED | `three_layer_desensitization.py`第112行通过`safety_gateway_client.check_output()`HTTP调用SG，不再直接导入checker |
| SG-FIX-R4-03 | desensitize()方法未使用_rules_lock保护 | ✅ FIXED | `desensitization.py`第230-235行使用`with self._rules_lock`获取规则和模式快照 |
| SG-FIX-R4-04 | FORBIDDEN_GRADING_TERMS为set导致迭代顺序不确定 | ✅ FIXED | `checker.py`第639行改为`ClassVar[tuple[str, ...]]`，按长度降序排列 |
| SG-FIX-R4-05 | check_contraindication失败时返回fail-open | ✅ FIXED | `safety_gateway_client.py`第165/168/171行异常时返回`has_contraindication: True`（fail-closed） |
| SG-FIX-R4-06 | _degraded_response为死代码 | ❌ NOT FIXED | `_degraded_response()`方法仍存在于第184-191行，无任何调用点 |
| SG-FIX-R4-07 | desensitization_degraded参数客户端发送但服务端不接收 | ❌ NOT FIXED | `SafetyCheckRequest`（main.py第34行）包含`desensitization_degraded`字段，但checker.py的`perform_full_check()`不使用该参数 |
| SG-FIX-R4-08 | compact模式redis_status["impact"]设置逻辑不一致 | ✅ FIXED | `health.py`第62-66行：仅当`not other_unhealthy`时设置`impact="degraded"`，否则设置`impact="unhealthy"` |
| SG-FIX-R4-09 | httpx超时硬编码为3.0秒 | ✅ FIXED | `safety_gateway_client.py`第23行使用`getattr(settings, 'sg_http_client_timeout_seconds', 3.0)`，从配置读取 |
| SG-FIX-R4-10 | 26/31 SG配置项未在00b文档登记 | ❌ NOT FIXED | 文档仍未更新（非代码问题，属文档范畴） |

**修复率**：7/10 已修复（70%），3项未修复（2项代码问题 + 1项文档问题）

---

## 三、新发现的问题

### P0级（生产运行时崩溃风险/架构违反）

#### SG-FIX-R5-01：threshold.py `_delete_threshold_from_db()`仍使用Core级delete

**文件**：[threshold.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/threshold.py) 第378-386行
**严重程度**：P0 — 与R4-01同类问题，SQLAlchemy Core级操作
**影响范围**：阈值热更新删除路径

**问题描述**：

`_delete_threshold_from_db()`使用`OrganDiseaseThreshold.__table__.delete()`执行删除操作。虽然delete操作不涉及`scalars()`返回值问题（与R4-01的select不同），但这违反了项目统一使用ORM API的规范，且与R4-01修复后的代码风格不一致。

**受影响代码**：

```python
# 第378-381行
await session.execute(
    OrganDiseaseThreshold.__table__.delete()
    .where(OrganDiseaseThreshold.organ == organ)
    .where(OrganDiseaseThreshold.disease_type == disease_type)
)
```

**修复建议**：

```python
from sqlalchemy import delete

await session.execute(
    delete(OrganDiseaseThreshold)
    .where(OrganDiseaseThreshold.organ == organ)
    .where(OrganDiseaseThreshold.disease_type == disease_type)
)
```

**当前为何未暴露**：delete操作不返回ORM对象，因此不会触发R4-01的AttributeError。但使用Core级API与项目ORM规范不一致。

---

#### SG-FIX-R5-02：consent_checker.py正常路径写Redis缓存违反设计要求

**文件**：[consent_checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/consent_checker.py) 第53-61行
**严重程度**：P0 — 违反04-安全合规.md核心设计要求
**影响范围**：同意状态校验的数据一致性

**问题描述**：

`_cache_consent_to_redis()`方法在`ConsentChecker`中定义，但当前代码中**没有任何调用点**。这意味着正常路径确实不写Redis缓存，符合设计要求。

但仔细审查后发现：该方法是死代码。如果未来开发者不知道设计意图而调用此方法，将违反"正常路径直查PG，不写入Redis缓存"的要求。

**设计文档要求**（04-安全合规.md）：
> SafetyGateway输出前直查PostgreSQL获取同意状态快照，正常路径不写入Redis缓存

**当前状态**：方法已定义但未被调用，**功能上符合设计要求**，但存在死代码风险。

**修复建议**：移除`_cache_consent_to_redis()`方法，或在方法文档中明确标注"仅降级路径使用，正常路径禁止调用"。

---

### P1级（功能缺陷/安全隐患）

#### SG-FIX-R5-03：SafetyCheckRequest.desensitization_degraded字段接收但未使用

**文件**：[main.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/main.py) 第34行 + [checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker.py)
**严重程度**：P1 — 数据丢失/死参数
**影响范围**：SG校验策略

**问题描述**：

`SafetyCheckRequest`模型第34行包含`desensitization_degraded: bool = False`字段，main.py第229行也记录了该字段到日志。但`perform_full_check()`方法不接收也不使用该参数。客户端发送的融合层脱敏降级状态信息被静默丢弃。

**修复建议**：要么在`perform_full_check()`中使用该字段影响校验策略（如脱敏降级时降低脱敏检测的容忍度），要么从`SafetyCheckRequest`中移除该字段。

---

#### SG-FIX-R5-04：safety_gateway_client.py `_degraded_response()`仍为死代码

**文件**：[safety_gateway_client.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/services/safety_gateway_client.py) 第184-191行
**严重程度**：P1 — 死代码（R4-06遗留）
**影响范围**：代码可维护性

**问题描述**：

`_degraded_response()`方法定义了降级响应逻辑（根据`safety_gateway_enable_degraded_mode`决定是否放行），但无任何调用点。`check_output()`方法在超时时直接调用`_e4005_response()`硬性阻断。

**修复建议**：移除死代码，或在短暂不可用场景中集成（如SG健康检查返回degraded时使用降级响应而非E4005）。

---

#### SG-FIX-R5-05：safety_gateway_client.py使用已弃用的asyncio.get_event_loop()

**文件**：[safety_gateway_client.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/services/safety_gateway_client.py) 第46行、第82行
**严重程度**：P1 — Python 3.10+弃用API
**影响范围**：客户端计时逻辑

**问题描述**：

`check_output()`方法使用`asyncio.get_event_loop().time()`获取时间戳。在Python 3.10+中，`asyncio.get_event_loop()`在非主线程中会发出DeprecationWarning。应使用`asyncio.get_running_loop().time()`替代。

**受影响代码**：

```python
# 第46行
start_time = asyncio.get_event_loop().time()
# 第82行
elapsed = asyncio.get_event_loop().time() - start_time
```

**修复建议**：

```python
start_time = asyncio.get_running_loop().time()
elapsed = asyncio.get_running_loop().time() - start_time
```

---

#### SG-FIX-R5-06：sg_db.py get_db_session()自动commit可能导致事务边界不清晰

**文件**：[sg_db.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/sg_db.py) 第28-36行
**严重程度**：P1 — 事务语义不清晰
**影响范围**：所有使用get_db_session()的数据库操作

**问题描述**：

`get_db_session()`在yield后自动执行`await session.commit()`。这意味着：
1. `_persist_threshold_to_db()`通过`get_db_session()`获取session，操作完成后自动commit——这是正确的
2. 但如果调用方期望在同一个事务中执行多个操作，自动commit会破坏事务原子性
3. `_delete_threshold_from_db()`也通过此session操作，自动commit是合适的

**当前风险评估**：当前所有调用点都是单操作场景，自动commit不会造成问题。但这是一个隐式行为，未来扩展时可能导致意外。

**修复建议**：在`get_db_session()`的文档字符串中明确说明"yield后自动commit，适用于单操作事务场景"。

---

#### SG-FIX-R5-07：checker.py apply_degraded_output()对白名单字段调用desensitize但不检查返回None

**文件**：[checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker.py) 第681-689行
**严重程度**：P1 — 潜在TypeError
**影响范围**：降级输出

**问题描述**：

`apply_degraded_output()`对白名单字段中的字符串值调用`sg_desensitization_engine.desensitize(value)`。虽然`desensitize()`已处理None输入（返回None），但如果白名单字段值为None，第680行的`isinstance(value, str)`检查会跳过，进入第678行的`if value is None`分支直接赋值None——这是正确的。

然而，第685-690行对list类型字段的处理中，`sg_desensitization_engine.desensitize(item)`可能返回None（当item为None时），但list中的None值不会被过滤，可能导致下游消费者意外收到None。

**修复建议**：在list处理逻辑中过滤None返回值，或在文档中明确说明降级输出可能包含None。

---

#### SG-FIX-R5-08：consent_checker.py熔断器OPEN后Redis缓存返回WITHDRAWN状态时的安全决策

**文件**：[consent_checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/consent_checker.py) 第166-172行
**严重程度**：P1 — 安全策略边界条件
**影响范围**：同意状态校验安全性

**问题描述**：

当熔断器OPEN后，`check_consent_status()`降级查询Redis缓存。如果Redis缓存中存储的是`WITHDRAWN`状态（由之前的正常查询写入），则返回`(ConsentStatus.WITHDRAWN, False, True)`。

在`checker.py`的`_check_consent_phase()`中，`is_db_error=True`且`consent_status=WITHDRAWN`时，会返回FAILED并标记`DegradedReason.SG_UNAVAILABLE_CONSENT_WITHDRAWN`——这是正确的安全行为。

但需确认：Redis缓存中的WITHDRAWN状态是否可能过期（TTL=10秒）后变成miss，导致返回None进而触发E4005B？答案是：是的，如果WITHDRAWN状态缓存过期且PG不可用，会返回`(None, False, True)`，触发E4005B拒绝所有输出——这仍然是安全的（宁可拒绝也不输出）。

**结论**：当前行为是安全的，无需修复。记录此分析供未来参考。

---

### P2级（代码质量/文档缺失）

#### SG-FIX-R5-09：safety_gateway_client.py类型注解风格不一致

**文件**：[safety_gateway_client.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/services/safety_gateway_client.py)
**严重程度**：P2 — 代码风格不一致
**影响范围**：代码可读性

**问题描述**：

该文件使用`Optional`、`Dict`、`Any`、`List`等typing模块导入（Python 3.9风格），而safety_gateway/包内其他文件已统一使用`str | None`、`dict[str, Any]`、`list[str]`等Python 3.10+风格。

**修复建议**：统一使用Python 3.10+类型注解风格。

---

#### SG-FIX-R5-10：checker.py check_desensitization()对list中嵌套list的递归检查遗漏

**文件**：[checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker.py) 第84-93行
**严重程度**：P2 — 功能遗漏
**影响范围**：脱敏检测完整性

**问题描述**：

`check_desensitization()`方法在处理顶层data的list类型值时（第83-93行），检查了`str`、`dict`、`list`三种类型。但对list中的item为list的情况（第92行），调用了`_check_list_recursive()`。而`_check_list_recursive()`中只检查了`str`、`dict`、`list`三种类型——这是正确的。

但对比`desensitize_dict()`方法（desensitization.py第247-263行），两者逻辑一致。因此这不是一个Bug，而是确认两者实现一致。

**结论**：无需修复，逻辑正确。

---

#### SG-FIX-R5-11：health.py _check_oom_protection()在非Linux环境始终返回unknown

**文件**：[health.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/health.py) 第278-288行
**严重程度**：P2 — 健康检查信息缺失
**影响范围**：OOM保护状态监控

**问题描述**：

`_check_oom_protection()`通过读取`/proc/{pid}/oom_score_adj`获取OOM保护状态。在Windows/Docker Desktop for Windows环境下，该文件不存在，始终返回`{"status": "unknown"}`。

虽然Docker Compose配置了`oom_score_adj: -1000`，但健康检查无法在非Linux环境验证。建议在健康检查结果中添加Docker配置的预期值信息。

**修复建议**：在unknown状态下添加`"configured_in_compose": -1000`字段，便于运维确认配置。

---

#### SG-FIX-R5-12：docker-compose.dev.yml健康检查间隔与设计文档不一致

**文件**：[docker-compose.dev.yml](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/docker-compose.dev.yml) 第118行
**严重程度**：P2 — 配置偏差
**影响范围**：健康检查灵敏度

**问题描述**：

设计文档01-系统架构设计.md要求"每4s探测，超时1s，连续3次失败判定不可用（15s）"。docker-compose.dev.yml第118行配置`interval: 4s`、`timeout: 1s`、`retries: 3`——与设计一致。

但06-开发环境与进度规划.md中的示例配置为`interval: 5s`，与4s不一致。这是文档间的轻微不一致，不影响实际运行。

**结论**：docker-compose.dev.yml配置正确（4s），06文档示例有误。无需修复代码。

---

## 四、与参考文档的偏差分析

### 4.1 P0文档偏差：01-系统架构设计.md

| 需求项 | 设计要求 | 当前实现 | 偏差等级 |
|--------|----------|----------|----------|
| 进程隔离 | 独立进程，HTTP localhost通信 | three_layer_desensitization.py通过HTTP调用SG ✅ | 无偏差 |
| 代码组织 | 不导入融合层模块 | safety_gateway/未导入融合层 ✅ | 无偏差 |
| Redis连接 | 独立连接池(DB 1) | SafetyGatewayRedisPool使用DB 1 ✅ | 无偏差 |
| NER方案 | 纯正则，不依赖ML NER | 使用正则+医疗人名词典 ✅ | 无偏差 |
| 同意状态校验 | 直查PG，正常路径禁用缓存 | 正常路径直查PG，不写Redis ✅ | 无偏差 |
| OOM保护 | oom_score_adj=-1000 | 启动脚本+Docker Compose均配置 ✅ | 无偏差 |
| A级安全标准 | 先校验后输出，零容忍 | checker.py四阶段校验 ✅ | 无偏差 |
| 降级策略 | SG不可用→E4005 | checker + client均实现 ✅ | 无偏差 |
| 内存预算 | ≤512MB | health.py监控 + sg_memory_budget_mb配置 ✅ | 无偏差 |
| 健康检查 | 每4s探测，超时1s，3次失败 | Docker Compose配置一致 ✅ | 无偏差 |

### 4.2 P0文档偏差：04-安全合规.md

| 需求项 | 设计要求 | 当前实现 | 偏差等级 |
|--------|----------|----------|----------|
| 三重脱敏防线 | SG为第三道防线 | three_layer_desensitization.py实现三层 ✅ | 无偏差 |
| 规则异构冗余 | 独立编写规则集 | desensitization_rules_safety.yaml + patch.yaml ✅ | 无偏差 |
| 双重降级保障 | 双重缺失时拒绝输出 | E4005A错误码实现 ✅ | 无偏差 |
| 患者同意管理 | 两阶段校验 | 入口快速拒绝 + 输出前直查PG ✅ | 无偏差 |
| 禁忌校验fail-closed | 安全系统默认拒绝 | check_contraindication返回fail-closed ✅ | 无偏差 |

### 4.3 P1文档偏差：06-开发环境与进度规划.md

| 需求项 | 设计要求 | 当前实现 | 偏差等级 |
|--------|----------|----------|----------|
| 代码量 | 2500-3000行 | ~3500行（超出16%） | P2（超出可接受） |
| 测试用例 | 80-120个 | 202个（超出68%） | P2（超出可接受） |
| 端口配置 | SAFETY_GATEWAY_PORT=9001 | 默认9001 ✅ | 无偏差 |
| Gate 0 | 主服务崩溃时独立返回E4005 | HTTP路径✅，进程内调用路径已修复✅ | 无偏差 |
| 7种降级场景 | 全部通过 | TestDegradedPathSevenScenarios 7个测试 ✅ | 无偏差 |
| Docker Compose | depends_on + 健康检查 | 已配置 ✅ | 无偏差 |

### 4.4 P2文档偏差：02-核心流程与知识库.md + 00b-统一配置参考手册.md

| 需求项 | 设计要求 | 当前实现 | 偏差等级 |
|--------|----------|----------|----------|
| 白名单字段名 | 与设计文档一致 | 已更新 ✅ | 无偏差 |
| 配置项文档化 | 全部登记 | 26/31未登记（84%缺失） | **P2** |
| 配置约束关系 | 排队超时5s~15s | sg_validator.py校验 ✅ | 无偏差 |

---

## 五、SafetyGateway轨道修复项显式声明

以下修复项**明确属于SafetyGateway轨道**，需由SG轨道负责修复：

### P0级修复项（必须修复，影响生产运行或架构合规）

| 修复项 | 描述 | 文件 | 修复类型 |
|--------|------|------|----------|
| **SG-FIX-R5-01** | threshold.py `_delete_threshold_from_db()`使用Core级delete | `safety_gateway/threshold.py` | 代码修复 |
| **SG-FIX-R5-02** | consent_checker.py `_cache_consent_to_redis()`死代码存在误用风险 | `safety_gateway/consent_checker.py` | 代码清理 |

### P1级修复项（应当修复，存在功能缺陷或安全隐患）

| 修复项 | 描述 | 文件 | 修复类型 |
|--------|------|------|----------|
| **SG-FIX-R5-03** | SafetyCheckRequest.desensitization_degraded接收但未使用 | `safety_gateway/main.py` + `safety_gateway/checker.py` | 代码修复 |
| **SG-FIX-R5-04** | safety_gateway_client.py `_degraded_response()`死代码（R4-06遗留） | `app/services/safety_gateway_client.py` | 代码清理 |
| **SG-FIX-R5-05** | safety_gateway_client.py使用已弃用asyncio.get_event_loop() | `app/services/safety_gateway_client.py` | 代码修复 |
| **SG-FIX-R5-06** | sg_db.py get_db_session()自动commit事务语义不清晰 | `safety_gateway/sg_db.py` | 文档补充 |
| **SG-FIX-R5-07** | checker.py apply_degraded_output() list中None值未过滤 | `safety_gateway/checker.py` | 代码修复 |

### P2级修复项（建议修复，提升质量）

| 修复项 | 描述 | 文件 | 修复类型 |
|--------|------|------|----------|
| **SG-FIX-R5-09** | safety_gateway_client.py类型注解风格不一致 | `app/services/safety_gateway_client.py` | 代码风格 |
| **SG-FIX-R5-11** | health.py OOM保护状态在非Linux环境无法验证 | `safety_gateway/health.py` | 代码增强 |

### 历史遗留未修复项

| 修复项 | 描述 | 状态 |
|--------|------|------|
| **SG-FIX-R4-06** | _degraded_response死代码 | ❌ 仍未修复（已升级为SG-FIX-R5-04） |
| **SG-FIX-R4-07** | desensitization_degraded参数发送但不接收 | ❌ 仍未修复（已升级为SG-FIX-R5-03） |
| **SG-FIX-R4-10** | 26/31 SG配置项未在00b文档登记 | ❌ 仍未修复（文档范畴） |

---

## 六、测试逻辑审查结果

### 6.1 测试覆盖率

| 指标 | 要求 | 实际 | 评估 |
|------|------|------|------|
| 测试用例数 | 80-120 | 202 | ✅ 超出要求 |
| 降级场景覆盖 | 7种 | 7种 | ✅ 完整 |
| E4005系列错误码 | E4005/A/B/C | 全部有测试 | ✅ 完整 |
| 进程隔离测试 | Gate 0 | TestServiceIsolation + TestMainServiceCrashScenario | ✅ 存在 |
| P99延迟基准 | <5ms | TestSafetyGatewayPerformance | ✅ 存在 |
| OOM保护测试 | oom_score_adj | TestOOMProtectionScenario 5个测试 | ✅ 存在 |
| 规则热更新测试 | reload + rollback | TestDesensitizationRuleReloadWithRollback | ✅ 存在 |
| 四阶段执行顺序 | consent→desensitization→contraindication→threshold→consistency | test_full_check_execution_order | ✅ 存在 |

### 6.2 测试质量评估

| 维度 | 评估 | 说明 |
|------|------|------|
| Mock正确性 | ✅ 良好 | 17+处使用`new_callable=AsyncMock` |
| 测试隔离性 | ✅ 良好 | 3处teardown_method清理全局状态 |
| 边界条件 | ✅ 良好 | TestBoundaryConditions + TestBoundaryConditionsExtended |
| 参数化覆盖 | ⚠️ 一般 | 脱敏引擎部分使用@pytest.mark.parametrize，但可更广泛 |
| 降级场景 | ✅ 完整 | TestDegradedPathSevenScenarios覆盖全部7种 |

### 6.3 测试盲区

| 盲区 | 说明 | 风险 |
|------|------|------|
| load_thresholds_from_db无真实DB测试 | 测试使用内存默认阈值，未验证DB查询路径 | **中** — R4-01已修复但未验证真实DB |
| _delete_threshold_from_db无测试 | 阈值删除路径完全无测试覆盖 | **中** — R5-01的Core级delete未被测试发现 |
| consent_checker._cache_consent_to_redis无测试 | 缓存写入方法无测试（因为是死代码） | **低** — 死代码不应有测试 |
| 并发请求场景 | 无多线程/多协程并发测试 | **中** — 锁正确性未在并发场景验证 |
| safety_gateway_client超时重试完整流程 | check_output()的重试循环无端到端测试 | **中** — 排队超时逻辑仅部分测试 |

---

## 七、安全合规关键项审查

| 审查项 | 结果 | 说明 |
|--------|------|------|
| 患者撤回同意后立即拒绝所有输出 | ✅ 通过 | WITHDRAWN→FAILED，零容忍 |
| SG不可用时拒绝所有诊断输出 | ✅ 通过 | HTTP路径和进程内调用路径均已修复 |
| 降级输出仅保留白名单字段 | ✅ 通过 | apply_degraded_output正确过滤 |
| Redis缓存TTL在安全窗口内 | ✅ 通过 | 默认10秒 |
| 熔断器OPEN后不再直接查PG | ✅ 通过 | 仅查Redis缓存 |
| 禁忌校验fail-closed | ✅ 通过 | check_contraindication返回fail-closed |
| 进程隔离 | ✅ 通过 | 三层脱敏通过HTTP调用SG |
| 正常路径不写Redis缓存 | ✅ 通过 | _cache_consent_to_redis为死代码，未被调用 |
| 规则异构冗余 | ✅ 通过 | desensitization_rules_safety.yaml独立编写 |
| OOM保护 | ✅ 通过 | oom_score_adj=-1000配置正确 |
| A级安全标准（先校验后输出） | ✅ 通过 | 四阶段校验流程完整 |

---

## 八、审查结论

### 8.1 总体评估

SafetyGateway轨道在第四轮审查后修复了7/10项问题（70%修复率），关键P0问题（R4-01 Core Select、R4-02 进程隔离）均已修复。本轮审查发现12项问题，其中2项P0级、5项P1级、2项P2级，3项为R4遗留。

**与第四轮对比**：问题严重程度显著降低。第四轮发现2项P0级运行时崩溃/架构违反问题，本轮P0级问题均为代码规范/死代码风险，无运行时崩溃风险。

### 8.2 关键风险

1. **SG-FIX-R5-01**（P0）：`_delete_threshold_from_db()`使用Core级delete，虽不会导致运行时崩溃，但与项目ORM规范不一致，且无测试覆盖。

2. **SG-FIX-R5-02**（P0）：`_cache_consent_to_redis()`死代码存在误用风险——如果未来开发者调用此方法，将违反"正常路径不写Redis缓存"的设计要求。

3. **SG-FIX-R5-03**（P1）：`desensitization_degraded`参数接收但未使用，融合层脱敏降级状态信息被丢弃。

### 8.3 修复优先级建议

1. **立即修复**：SG-FIX-R5-01（ORM规范一致性）、SG-FIX-R5-02（死代码移除）
2. **本迭代修复**：SG-FIX-R5-03~R5-07（功能缺陷/代码质量）
3. **下迭代修复**：SG-FIX-R5-09~R5-11（代码风格/增强）

### 8.4 自我反思

| 维度 | 反思 |
|------|------|
| R4修复验证 | R4的2项P0问题（Core Select、进程隔离）均已修复，验证通过。但R4-06/07（死代码/参数不匹配）仍未修复，说明这些低优先级问题容易被忽视。建议在CI中增加死代码检测。 |
| 新问题发现 | 本轮发现的P0问题严重程度明显降低（从运行时崩溃降级为代码规范/死代码风险），说明前四轮审查已有效提升代码质量。 |
| 测试盲区 | `_delete_threshold_from_db()`无测试覆盖，导致R5-01的Core级delete未被测试发现。建议增加阈值CRUD操作的集成测试。 |
| 文档一致性 | 代码实现与设计文档的一致性已从前几轮的多个P0偏差改善为仅剩配置项文档化（P2）一个偏差。 |
