# SG安全网关轨道系统性审查计划

## 审查声明

本审查报告**显式声明属于SafetyGateway轨道应当进行的修复项**，所有发现的问题均标注 `[SG-FIX]` 前缀。

---

## 一、审查范围与方法

### 1.1 审查范围

| 维度 | 覆盖内容 |
|------|----------|
| 代码质量 | safety_gateway/ 全部18个源文件、app/services/safety_gateway_client.py |
| 测试逻辑 | tests/unit/test_safety_gateway.py (237个测试)、tests/benchmark/、安全合规相关测试 |
| 配置文件 | desensitization_rules_safety.yaml、desensitization_rules_safety_patch.yaml、contraindication_rules_safety.yaml、medical_person_names.yaml |
| 部署配置 | docker-compose.dev.yml、Dockerfile.safety-gateway.dev、scripts/start_safety_gateway.sh、deploy/supervisor/safety-gateway.conf |
| 参考文档 | 01-系统架构设计.md(P0)、04-安全合规.md(P0)、06-开发环境与进度规划.md(P1)、02-核心流程与知识库.md(P2)、00b-统一配置参考手册.md(P2) |

### 1.2 审查方法

1. **静态代码分析**：逐文件审查代码逻辑、类型安全、异常处理、线程安全
2. **文档一致性比对**：将实现与5份参考文档逐条对照
3. **测试覆盖率评估**：统计测试用例数量与覆盖场景，识别遗漏
4. **边界条件推演**：对每个降级路径、错误路径进行边界值分析
5. **架构合规性验证**：验证进程隔离、OOM保护、延迟基准等核心架构要求

---

## 二、审查发现与修复项

### 2.1 P0 — 严重问题（影响安全正确性）

#### [SG-FIX-P0-01] threshold.py 并发修改无锁保护

**文件**: [threshold.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/threshold.py#L474-L515)

**问题**: `_handle_threshold_update()` 方法直接修改 `self._disease_thresholds` 和 `self._thresholds` 字典，未持有 `_thresholds_lock`。虽然当前在 asyncio 单线程事件循环中执行是安全的，但 `load_thresholds()` 同步方法使用了 `_thresholds_lock`，存在不一致的锁使用模式。若未来引入 `run_in_executor` 或多 worker，将产生竞态条件。

**设计文档偏差**: 01-系统架构设计要求"一期即为独立操作系统进程"，uvicorn `--workers 1` 保证了单进程单事件循环，但锁使用不一致违反防御性编程原则。

**修复方案**: 在 `_handle_threshold_update()` 中对 `self._thresholds` 的修改加 `_thresholds_lock` 保护，与 `load_thresholds()` 保持一致。

---

#### [SG-FIX-P0-02] 双重降级场景标记字段不完整

**文件**: [degraded_output.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/degraded_output.py#L122-L139)

**问题**: 04-安全合规.md 明确要求双重降级场景（融合层脱敏降级 + SG不可达）输出须标记 `conclusion_suppressed: true` + `fusion_degraded: true` + `safety_gateway_unavailable: true`。当前实现中：
- `FUSION_UNAVAILABLE_SENSITIVE_DETECTED` 分支设置了 `fusion_degraded: True` 和 `safety_gateway_unavailable: True`，但 `conclusion_suppressed` 已在 line 91 统一设置为 `True` ✓
- `SG_UNAVAILABLE_CONSENT_WITHDRAWN` 分支设置了 `safety_gateway_unavailable: True`，但 `fusion_degraded` 为 `False` — 这在逻辑上是正确的（此场景融合层可能可用），但缺少 `fusion_degraded` 字段的显式声明

**设计文档偏差**: 04-安全合规§6.4要求"标记 conclusion_suppressed: true + fusion_degraded: true + safety_gateway_unavailable: true"，当前仅双重降级场景部分满足。

**修复方案**: 
1. 在 `FUSION_UNAVAILABLE_SENSITIVE_DETECTED` 分支确认三标记完整（当前已完整 ✓）
2. 在 `SG_UNAVAILABLE_CONSENT_WITHDRAWN` 分支，当融合层也不可用时应设置 `fusion_degraded: True`（需从调用方传入融合层状态）
3. 考虑在 `apply_degraded_output()` 中增加 `fusion_available` 参数，使标记更精确

---

#### [SG-FIX-P0-03] checker.py 空输出判定逻辑过于严格

**文件**: [checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker.py#L289-L301)

**问题**: `perform_full_check()` 中空输出判定条件 `all(v is None or v == "" or v == [] or v == {} for v in output_data.values())` 会将 `{"count": 0, "active": False}` 等合法值判定为空输出并拒绝。在医疗场景中，`0` 和 `False` 可能是有效的诊断数据（如"未见异常"的量化表示）。

**修复方案**: 改为显式检查 `output_data` 是否为空字典或所有值均为 None/空字符串/空容器，但排除数值 0 和布尔值 False：
```python
if not output_data or all(
    v is None or v == "" or v == [] or v == {} 
    for v in output_data.values() 
    if not isinstance(v, (int, float, bool))
):
```
或者更安全地：仅当 `output_data` 为空字典时拒绝，非空字典即使值全为空也进入校验流程（让后续阶段决定）。

---

#### [SG-FIX-P0-04] consent_checker.py 重试总时间计算可能超出上限

**文件**: [consent_checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/consent_checker.py#L70-L130)

**问题**: `_retry_query()` 的总时间上限检查 `elapsed + delay + self._query_timeout <= self._retry_max_total_time` 存在边界问题：
- 默认配置：`_query_timeout=5`, `_retry_max_attempts=3`, `_retry_delay_base=0.5`, `_retry_max_total_time=10.0`
- 最坏情况：attempt 0 超时(5s) + delay(0.5s) + attempt 1 超时(5s) = 10.5s > 10s上限
- 但代码在 attempt 1 开始前检查 `elapsed + delay + self._query_timeout`，此时 elapsed≈5.5s, delay=1.0s, query_timeout=5s → 11.5s > 10s，会提前终止 ✓
- 实际上 attempt 0 超时后 elapsed≈5s，检查 5+0.5+5=10.5>10 会跳过重试直接抛异常，导致仅尝试1次就放弃

**修复方案**: 调整默认参数使重试策略更合理，或在第一次超时后允许至少一次重试（将 `_retry_max_total_time` 默认值从 10.0 调整为 15.0，或减小 `_query_timeout` 到 3.0）。

---

### 2.2 P1 — 重要问题（影响健壮性/可维护性）

#### [SG-FIX-P1-01] desensitization.py reload_rules() 锁获取模式不一致

**文件**: [desensitization.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/desensitization.py#L384-L481)

**问题**: `reload_rules()` 方法在三个不同位置获取 `_rules_lock`：
1. Line 388-390: 获取旧规则快照
2. Line 460-463: 编译失败时回滚
3. Line 465-467: 替换新规则

在步骤1和步骤3之间，其他线程可能修改了 `_rules`（虽然当前代码路径不太可能），导致回滚时恢复的不是原始快照。此外，步骤2中的回滚在 `except re.error` 分支内，但此时 `_rules_lock` 未被持有（步骤1的锁已释放），回滚操作在步骤2中重新获取锁，但旧规则可能已被其他操作修改。

**修复方案**: 将整个 reload 操作包裹在单次锁获取中，或使用 copy-on-write 模式确保原子性。

---

#### [SG-FIX-P1-02] circuit_breaker.py _update_state() 线程安全依赖隐式假设

**文件**: [circuit_breaker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/circuit_breaker.py#L70-L91)

**问题**: `_update_state()` 方法修改 `self._state` 等可变状态，但未持有 `_lock`。代码注释声明依赖 asyncio 单线程事件循环保证线程安全。然而：
- `state` 和 `is_available` 属性是公开的，可从任何线程调用
- `execute()` 方法在 `self.is_available` 检查和 `await func()` 执行之间存在 TOCTOU 竞态
- 若 uvicorn 使用多 worker（虽然当前 `--workers 1`），将产生数据竞争

**修复方案**: 
1. 将 `_update_state()` 的调用移入已持有 `_lock` 的方法内
2. 或在 `execute()` 中使用 `_lock` 保护整个检查-执行流程
3. 添加显式注释说明单 worker 约束

---

#### [SG-FIX-P1-03] SafetyGatewayClient 缺少排队等待恢复的显式实现

**文件**: [safety_gateway_client.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/services/safety_gateway_client.py#L97-L216)

**问题**: 01-系统架构设计要求"短暂不可用（≤15s）：融合层输出排队等待恢复，超时后拒绝输出（E4005）"。当前 `check_output()` 实现了重试循环+超时机制，功能上等价，但：
1. 没有显式的"排队"语义（只是简单重试）
2. 重试间隔固定为1秒，未考虑指数退避
3. 快速失败阈值（3次连续失败后直接返回E4005）与"排队等待恢复"语义矛盾——第3次失败后不再等待恢复

**设计文档偏差**: 架构文档描述"短暂不可用（≤15s）→排队等待恢复"，但客户端实现在3次失败后快速失败，不再等待。

**修复方案**: 
1. 将快速失败逻辑改为：仅在 SG 持续不可用（健康检查判定不可用）时才快速失败
2. 在排队等待期间使用指数退避重试
3. 区分"SG短暂不可用"（排队等待）和"SG持续不可用"（快速失败）

---

#### [SG-FIX-P1-04] 健康检查端点缺少 /ready 就绪探针

**文件**: [main.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/main.py#L195-L206)

**问题**: 当前仅有 `/health` 端点，未区分存活（liveness）和就绪（readiness）探针。Kubernetes/Docker 部署最佳实践要求：
- `/health`（liveness）：进程是否存活，失败则重启
- `/ready`（readiness）：是否可接收流量，失败则从负载均衡摘除

当前 `/health` 端点在组件降级时返回 200 + `X-SG-Status: degraded` 头，但 Docker 健康检查仅检查 HTTP 状态码，降级状态仍被视为健康。这可能导致降级状态的 SG 仍接收全部流量。

**修复方案**: 
1. 添加 `/ready` 端点，仅在所有核心组件（DB、规则引擎）健康时返回 200
2. 降级状态时 `/ready` 返回 503，使流量路由到其他实例
3. `/health` 保持当前行为（降级也返回 200），用于进程存活检测

---

#### [SG-FIX-P1-05] 缺少 SG_CONSENT_CACHE_TTL_SECONDS 配置项

**文件**: [sg_config.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/sg_config.py)

**问题**: 00b-统一配置参考手册定义了 `SG_CONSENT_CACHE_TTL_SECONDS`（默认10秒，仅降级路径使用），但 `SGSettings` 中未包含此配置项。当前 `consent_checker.py` 严格直查 PostgreSQL 不使用缓存，此配置项虽未使用但应保留以备降级路径扩展。

**设计文档偏差**: 00b-统一配置参考手册列出此配置项，但代码未实现。

**修复方案**: 在 `SGSettings` 中添加 `sg_consent_cache_ttl_seconds: int = 10` 配置项，并在代码注释中说明当前仅预留、直查PG策略不使用缓存。

---

### 2.3 P2 — 一般问题（影响代码质量/一致性）

#### [SG-FIX-P2-01] 代码量超出设计预估

**问题**: 06-开发环境与进度规划预估代码量约2500-3000行。实际 safety_gateway/ 包代码量约4000+行（含注释和空行），超出预估33%以上。

**分析**: 超出部分主要来自 health.py（467行，含Windows内存检测、OOM保护检查、融合层健康检查等设计文档未明确预估的功能）和 threshold.py（583行，含Redis订阅、数据库持久化等设计文档未明确预估的功能）。这些功能是设计文档要求的，但代码量预估时未充分考虑。

**修复方案**: 无需削减代码，但应在项目文档中更新代码量预估。

---

#### [SG-FIX-P2-02] filter_forbidden_terms 替换可能产生误报

**文件**: [degraded_output.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/degraded_output.py#L32-L50)

**问题**: `filter_forbidden_terms()` 将匹配到的禁止术语替换为 `"***"`，但未检查替换后的字符串是否产生了新的匹配。例如，如果某个术语被替换后恰好包含了另一个禁止术语的子串（虽然当前术语列表中不太可能发生）。

**修复方案**: 当前风险极低，可暂不修复。若未来禁止术语列表扩展，应考虑多轮过滤或使用标记-替换两阶段策略。

---

#### [SG-FIX-P2-03] Docker Compose 健康检查间隔与设计文档不完全一致

**文件**: [docker-compose.dev.yml](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/docker-compose.dev.yml#L117-L122)

**问题**: 
- 设计文档要求：间隔4秒、超时1秒、连续3次失败判定不可用（3×(4+1)=15s）
- 实际配置：`interval: 4s`, `timeout: 1s`, `retries: 3`, `start_period: 5s`
- Docker Compose 的 `retries: 3` 含义是"连续3次失败后标记为 unhealthy"，与设计文档一致 ✓
- 但 Docker Compose 的判定时间 = retries × interval = 3 × 4s = 12s（不含超时），与设计文档的15s有差异
- 实际上 Docker 在每次检查时如果超时也算失败，所以最坏情况 = 3 × (4s + 1s) = 15s ✓

**结论**: 配置基本正确，但建议在 docker-compose 注释中明确说明15s判定阈值的计算依据。

---

#### [SG-FIX-P2-04] 一致性校验的矛盾检测过于简单

**文件**: [checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker.py#L222-L250)

**问题**: `_check_conclusion_evidence_contradiction()` 使用硬编码的矛盾词对和否定前缀列表进行文本匹配，存在以下局限：
1. 无法处理语义级别的矛盾（如"未见明显异常"与"发现占位性病变"）
2. 否定前缀列表不完整（缺少"无明显"、"未发现"等组合）
3. 句子分割仅基于中文句号，未考虑英文句号、问号等

**修复方案**: 当前实现满足一期"声明式规则校验"要求，但应在二期考虑引入语义模型增强矛盾检测。

---

#### [SG-FIX-P2-05] 缺少 SAFETY_GATEWAY_HEALTH_CHECK_INTERVAL 和 TIMEOUT 配置项

**文件**: [sg_config.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/sg_config.py)

**问题**: 00b-统一配置参考手册定义了 `SAFETY_GATEWAY_HEALTH_CHECK_INTERVAL` 和 `SAFETY_GATEWAY_HEALTH_CHECK_TIMEOUT` 配置项，以及约束关系"连续3次失败判定不可用（3×(interval+timeout)=15s）"。但 `SGSettings` 中未包含这两个配置项，健康检查间隔在客户端硬编码为4秒。

**修复方案**: 在 `SGSettings` 中添加这两个配置项，并在 `sg_validator.py` 中添加约束校验。

---

### 2.4 设计文档一致性审查总结

| 审查项 | 设计文档要求 | 实现状态 | 偏差 |
|--------|------------|---------|------|
| 独立操作系统进程 | uvicorn独立实例 | ✅ 实现 | 无 |
| HTTP localhost通信 | HTTP API | ✅ 实现 | 无 |
| P99 < 5ms（HTTP开销） | 延迟基准 | ✅ 有性能测试 | 需实际环境验证 |
| P99 < 50ms（端到端） | 端到端延迟 | ✅ 有e2e延迟监控 | 需实际环境验证 |
| 进程隔离（主服务崩溃时独立返回E4005） | Gate 0验证 | ⚠️ 有测试但为mock | 需实际Docker环境验证 |
| A级安全标准（先校验后输出） | 零容忍策略 | ✅ 实现 | 无 |
| OOM保护 oom_score_adj=-1000 | 禁止被OOM Killer杀死 | ✅ Docker/启动脚本配置 | 需实际环境验证 |
| 三重脱敏防线 | SG为第三道防线 | ✅ 实现 | 无 |
| 规则异构冗余 | 不同正则模式 | ✅ 实现 | 有验证函数 |
| 同意状态直查PostgreSQL | 不使用缓存 | ✅ 严格实现 | 无 |
| 两阶段同意校验 | 入口快速拒绝+输出前实时校验 | ✅ 实现 | 无 |
| 降级输出（仅observations） | 白名单字段过滤 | ✅ 实现 | 无 |
| 7种降级场景 | 完整覆盖 | ✅ 10种DegradedReason | 超出设计要求 |
| E4005错误码体系 | E4005/A1/A2/B/C | ✅ 实现 | 无 |
| 端口9001 | SAFETY_GATEWAY_PORT | ✅ 实现 | 无 |
| 健康检查 /health | HTTP GET | ✅ 实现 | 无 |
| Redis DB=1隔离 | 与主服务隔离 | ✅ 实现 | 无 |
| 内存预算 ≤512MB | 资源限制 | ✅ Docker配置 | 无 |
| 代码量 ~2500-3000行 | 预估 | ⚠️ ~4000+行 | 超出33% |
| 测试用例 ~80-120个 | 预估 | ✅ 237+个 | 超出要求 |
| Docker Compose depends_on | SG先于主服务就绪 | ✅ 实现 | 无 |
| 看门狗重启 ≤5s | supervisor配置 | ✅ 实现 | 需实际验证 |
| 排队超时10s | >5s且<15s | ✅ 实现 | 有校验 |
| 熔断器保护 | 数据库查询 | ✅ 实现 | 无 |
| 规则热更新 | Redis Pub/Sub | ✅ 实现 | 有回滚机制 |
| SG_CONSENT_CACHE_TTL_SECONDS | 配置项 | ❌ 未实现 | 需添加 |
| SAFETY_GATEWAY_HEALTH_CHECK_INTERVAL | 配置项 | ❌ 未实现 | 需添加 |
| SAFETY_GATEWAY_HEALTH_CHECK_TIMEOUT | 配置项 | ❌ 未实现 | 需添加 |

---

## 三、测试逻辑审查

### 3.1 测试覆盖率评估

| 测试类别 | 测试文件 | 用例数 | 覆盖评估 |
|----------|---------|--------|---------|
| 核心单元测试 | test_safety_gateway.py | 237 | 充分 |
| 性能基准测试 | test_safety_gateway_performance.py | 7 | 基本覆盖 |
| 安全合规测试 | test_security_compliance_complete.py | 47 | 补充覆盖 |
| 安全审计回归 | test_sec_audit_regression.py | 45 | 代码级检查 |
| 安全缺口填充 | test_sec_gap_fill.py | 50 | 配置/枚举检查 |
| 端到端安全流程 | test_e2e_security_flow.py | 20 | 流程验证 |
| P0关键安全 | test_sec_p0_critical.py | 30 | 关键路径 |
| 脱敏专项 | test_desensitization.py | 21 | 规则验证 |

### 3.2 测试遗漏项

#### [SG-FIX-TEST-01] 缺少主服务崩溃时SG独立返回E4005的实际验证

**问题**: Gate 0 验证项要求"主服务崩溃时独立返回E4005"。当前测试 `TestMainServiceCrashScenario` 使用 mock 模拟，未在真实 Docker 环境中验证进程隔离。

**修复方案**: 添加集成测试脚本，在 Docker Compose 环境中停止 api 容器后，验证 SG 容器仍可独立响应并返回 E4005。

---

#### [SG-FIX-TEST-02] 缺少规则热更新回滚的端到端测试

**问题**: `desensitization.py` 的 `reload_rules()` 实现了回滚机制（编译失败时恢复旧规则），但测试中未验证回滚的正确性——即加载无效规则后，旧规则是否完整恢复且可正常工作。

**修复方案**: 添加测试用例：加载有效规则 → 触发热更新加载无效规则 → 验证回滚到旧规则 → 验证脱敏功能正常。

---

#### [SG-FIX-TEST-03] 缺少OOM保护的实际环境验证

**问题**: OOM 保护测试 `test_oom_score_adj_configuration` 和 `test_oom_score_adj_read_from_proc` 验证了配置值，但未在真实 Linux 环境中验证 `oom_score_adj=-1000` 是否确实阻止 OOM Killer 杀死 SG 进程。

**修复方案**: 添加 Docker 集成测试，在内存压力下验证 SG 进程不被 OOM Killer 杀死。

---

#### [SG-FIX-TEST-04] 缺少并发场景下共享状态一致性的压力测试

**问题**: `threshold.py` 和 `desensitization.py` 存在共享状态（阈值字典、规则列表），当前测试未验证高并发下的数据一致性。

**修复方案**: 添加多线程/多协程压力测试，同时执行阈值查询和热更新，验证无数据损坏。

---

#### [SG-FIX-TEST-05] 缺少SG客户端排队等待恢复的完整场景测试

**问题**: `SafetyGatewayClient.check_output()` 实现了重试+超时逻辑，但测试未覆盖以下场景：
1. SG短暂不可用（<15s）后恢复 → 验证请求成功
2. SG持续不可用（>15s）→ 验证返回E4005
3. 快速失败阈值触发后SG恢复 → 验证是否自动恢复

**修复方案**: 添加客户端排队等待的完整场景测试。

---

## 四、实施步骤

### Phase 1: P0修复（安全正确性）

| 步骤 | 修复项 | 验证方式 |
|------|--------|---------|
| 1 | [SG-FIX-P0-01] threshold.py 加锁保护 | 单元测试：并发修改无数据损坏 |
| 2 | [SG-FIX-P0-02] 双重降级标记字段补全 | 单元测试：验证三标记完整 |
| 3 | [SG-FIX-P0-03] 空输出判定逻辑修正 | 单元测试：0/False值不被拒绝 |
| 4 | [SG-FIX-P0-04] consent重试参数调整 | 单元测试：验证至少1次重试 |

### Phase 2: P1修复（健壮性）

| 步骤 | 修复项 | 验证方式 |
|------|--------|---------|
| 5 | [SG-FIX-P1-01] reload_rules锁模式统一 | 单元测试：并发reload无死锁 |
| 6 | [SG-FIX-P1-02] circuit_breaker线程安全 | 代码审查+注释 |
| 7 | [SG-FIX-P1-03] 客户端排队等待语义 | 集成测试 |
| 8 | [SG-FIX-P1-04] 添加/ready端点 | HTTP测试 |
| 9 | [SG-FIX-P1-05] 添加缺失配置项 | 配置校验测试 |

### Phase 3: 测试补全

| 步骤 | 修复项 | 验证方式 |
|------|--------|---------|
| 10 | [SG-FIX-TEST-01] Gate 0实际验证 | Docker集成测试 |
| 11 | [SG-FIX-TEST-02] 规则热更新回滚测试 | 单元测试 |
| 12 | [SG-FIX-TEST-03] OOM保护实际验证 | Docker集成测试 |
| 13 | [SG-FIX-TEST-04] 并发压力测试 | 压力测试 |
| 14 | [SG-FIX-TEST-05] 客户端排队场景测试 | 集成测试 |

### Phase 4: P2修复（代码质量）

| 步骤 | 修复项 | 验证方式 |
|------|--------|---------|
| 15 | [SG-FIX-P2-03] Docker Compose注释补充 | 文档审查 |
| 16 | [SG-FIX-P2-05] 健康检查配置项添加 | 配置校验测试 |

---

## 五、审查结论

### 5.1 总体评价

SafetyGateway轨道的实现质量**总体良好**，核心架构设计（进程隔离、四阶段校验流水线、降级输出、E4005错误码体系）与设计文档高度一致。关键安全属性（同意状态直查PG、规则异构冗余、OOM保护、A级零容忍策略）均已正确实现。

### 5.2 主要风险

1. **最高风险**: threshold.py 并发修改无锁保护（P0-01），虽当前单worker下安全，但违反防御性编程原则
2. **次高风险**: 双重降级场景标记不完整（P0-02），可能导致前端无法正确显示安全警告
3. **中等风险**: 客户端快速失败逻辑与"排队等待恢复"语义不一致（P1-03）

### 5.3 修复优先级建议

- **立即修复**: P0-01, P0-02, P0-03, P0-04
- **本迭代修复**: P1-01, P1-03, P1-04, P1-05
- **下迭代修复**: P1-02, P2系列, TEST系列
