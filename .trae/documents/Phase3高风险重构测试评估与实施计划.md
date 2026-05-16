# Phase 3 高风险重构：测试评估与实施计划

## 一、当前测试覆盖评估

### 1.1 测试现状概览

| 维度 | 现状 | 评估 |
|------|------|------|
| 单元测试文件 | 1个文件（2724行），58个测试类，约159个用例 | ❌ 全部集中在一个文件 |
| 集成测试 | 无独立集成测试文件 | ❌ 缺失 |
| 系统测试 | 无 | ❌ 缺失 |
| 性能测试 | 1个文件，7个用例 | ⚠️ 仅基准测试 |
| 覆盖率配置 | 无 | ❌ 无法量化覆盖率 |
| 测试隔离 | 仅checker有_reset | ❌ 大部分组件无法测试隔离 |

### 1.2 四项重构任务的测试覆盖缺口分析

#### O3 统一单例管理 — 测试覆盖缺口

| 功能点/边界条件 | 现有测试 | 缺口 |
|----------------|---------|------|
| 各组件单例获取（get_xxx） | ✅ TestSingletonGetters（3个用例） | — |
| 单例线程安全（双重检查锁） | ❌ 无 | 🔴 并发获取单例竞态测试 |
| 单例重置（_reset） | ✅ checker有隐式覆盖 | 🔴 其他6个组件无reset测试 |
| 延迟初始化 | ❌ 无 | 🔴 首次访问才创建实例 |
| 模块级实例（sg_desensitization_engine等） | ❌ 无 | 🔴 import时即初始化，无法延迟 |
| 注册表统一管理 | ❌ 无（尚未实现） | 🔴 需要全新测试套件 |
| 注册表并发安全 | ❌ 无 | 🔴 多线程同时get/register |
| 注册表reset单个/全部 | ❌ 无 | 🔴 测试隔离核心功能 |
| 注册表factory替换（mock注入） | ❌ 无 | 🔴 测试时替换实现 |
| sg_db懒初始化无锁竞态 | ❌ 无 | 🔴 并发get_db_session |

**覆盖率评估**：当前约15%，核心场景（并发安全、reset、延迟初始化）完全未覆盖。

#### O7 health.py拆分 — 测试覆盖缺口

| 功能点/边界条件 | 现有测试 | 缺口 |
|----------------|---------|------|
| check_all完整结构 | ✅ TestHealthCheckIntegration（1个） | — |
| Redis健康/不健康 | ✅ 2个 | — |
| DB健康/不健康 | ✅ 2个 | — |
| 规则引擎健康/降级/错误 | ✅ 3个 | — |
| 熔断器健康/不健康 | ✅ 2个 | — |
| 阈值引擎健康/降级 | ✅ 1个 | — |
| OOM保护健康/警告/未知 | ✅ 3个 | — |
| 融合层健康/不健康/超时/连接拒绝/缓存/过期 | ✅ 7个 | — |
| compact模式Redis降级 | ❌ 无 | 🔴 部署模式分支 |
| 延迟统计P50/P95/P99 | ✅ 4个 | — |
| 端到端延迟统计 | ❌ 无 | 🟡 e2e_latency_samples独立测试 |
| 内存监控（3平台适配） | ⚠️ 部分覆盖 | 🔴 psutil/proc/Windows ctypes分支 |
| LatencyTracker独立功能 | ❌ 无 | 🔴 拆分后需独立测试 |
| MemoryMonitor独立功能 | ❌ 无 | 🔴 拆分后需独立测试 |
| FusionProbe缓存TTL逻辑 | ✅ 部分覆盖 | 🟡 需更精确的缓存过期测试 |
| HealthCheckProvider策略接口 | ❌ 无 | 🔴 拆分后需全新测试 |
| 拆分后check_all聚合逻辑 | ❌ 无 | 🔴 拆分后编排器测试 |

**覆盖率评估**：当前约55%，拆分后的独立组件（LatencyTracker、MemoryMonitor、FusionProbe、HealthCheckProvider）均无独立测试。

#### O1 checker.py拆分 — 测试覆盖缺口

| 功能点/边界条件 | 现有测试 | 缺口 |
|----------------|---------|------|
| perform_full_check完整流程 | ✅ TestFullCheckIntegration（6个） | — |
| 同意状态校验阶段 | ✅ 隐式覆盖 | — |
| 边缘连接检查 | ⚠️ 部分覆盖 | 🟡 独立阶段测试 |
| 脱敏校验阶段 | ✅ 4个 | — |
| 禁忌+阈值阶段 | ✅ 2个 | — |
| 一致性校验阶段 | ✅ 4个 | — |
| 降级输出apply_degraded_output | ✅ 10个 | — |
| 矛盾检测（重构后） | ✅ 隐式覆盖 | 🟡 需独立阶段测试 |
| CheckPhase策略接口 | ❌ 无 | 🔴 拆分后需全新测试 |
| CheckContext数据传递 | ❌ 无 | 🔴 拆分后需独立测试 |
| 管道编排器（短路/顺序） | ❌ 无 | 🔴 拆分后核心逻辑 |
| 阶段注册/动态增减 | ❌ 无 | 🔴 开闭原则验证 |
| 各阶段独立初始化/依赖注入 | ❌ 无 | 🔴 拆分后各phase独立测试 |
| _PhaseContext字段完整性 | ❌ 无 | 🔴 上下文传递正确性 |

**覆盖率评估**：当前约45%，拆分后的策略接口、管道编排、阶段独立功能均无测试。

#### O2 脱敏引擎共享框架 — 测试覆盖缺口

| 功能点/边界条件 | 现有测试 | 缺口 |
|----------------|---------|------|
| SG脱敏引擎核心功能 | ✅ 44个用例 | — |
| 主服务脱敏引擎 | ❌ 无SG测试文件覆盖 | 🔴 跨模块测试缺失 |
| DesensitizationRule模型 | ✅ SG侧2个 | 🟡 主服务侧无测试 |
| 规则加载/编译/验证 | ✅ SG侧覆盖 | 🟡 主服务侧无测试 |
| 递归脱敏遍历 | ✅ SG侧覆盖 | 🟡 主服务侧无测试 |
| 异构冗余验证 | ✅ 4个 | — |
| BaseDesensitizationEngine基类 | ❌ 无 | 🔴 共享框架核心 |
| 基类→SG子类继承正确性 | ❌ 无 | 🔴 多态行为验证 |
| 基类→主服务子类继承正确性 | ❌ 无 | 🔴 多态行为验证 |
| 共享框架不破坏异构冗余 | ❌ 无 | 🔴 安全关键 |
| 热更新机制差异保持 | ❌ 无 | 🔴 SG Redis vs 主服务文件重载 |

**覆盖率评估**：当前约40%（SG侧良好，主服务侧和共享框架完全未覆盖）。

### 1.3 总体评估结论

**测试严重不充分**，四项重构任务的测试覆盖率均低于60%的安全线。主要风险点：

1. **无集成测试**：组件间交互（checker→consent_checker→circuit_breaker→retry）无独立验证
2. **无并发安全测试**：单例双重检查锁、注册表并发访问完全未测试
3. **拆分后新组件无测试**：CheckPhase、LatencyTracker、MemoryMonitor、BaseDesensitizationEngine等
4. **无覆盖率量化**：项目未配置coverage工具，无法精确评估
5. **测试隔离困难**：仅1个组件支持reset，重构过程中测试可能互相污染

---

## 二、测试补充计划

### 2.0 前置步骤：配置覆盖率工具

**目标**：建立覆盖率量化基线

1. 在 `pyproject.toml` 中添加 pytest-cov 配置
2. 运行现有测试获取基线覆盖率数据
3. 设定核心功能覆盖率目标 ≥90%

### 2.1 O3 统一单例管理 — 补充测试

#### 单元测试（新文件：tests/unit/sg/test_singleton_registry.py）

| 测试类 | 用例数 | 覆盖内容 |
|--------|--------|---------|
| TestSGComponentRegistry | 8 | register+get基本功能、延迟初始化、重复get返回同一实例、reset单个、reset全部、未注册组件抛KeyError、factory参数传递 |
| TestRegistryConcurrency | 3 | 多线程并发get、并发register+get、并发reset+get |
| TestRegistryFactoryOverride | 3 | override factory后get返回mock、reset后恢复原始factory、override不影响其他组件 |
| TestSingletonMigration | 5 | checker通过registry获取与直接获取行为一致、health_checker通过registry获取行为一致、consent_checker、contraindication_engine、threshold_engine |

#### 集成测试（新文件：tests/unit/sg/test_singleton_integration.py）

| 测试类 | 用例数 | 覆盖内容 |
|--------|--------|---------|
| TestRegistryLifecycleIntegration | 3 | 应用启动→registry初始化→各组件可用、registry reset全部→组件重新创建、registry reset单个→其他组件不受影响 |
| TestRegistryWithExistingCode | 3 | main.py通过registry获取checker、health endpoint通过registry获取health_checker、lifespan通过registry管理组件 |

### 2.2 O7 health.py拆分 — 补充测试

#### 单元测试（新文件：tests/unit/sg/test_health_components.py）

| 测试类 | 用例数 | 覆盖内容 |
|--------|--------|---------|
| TestLatencyTracker | 6 | 空样本统计、添加样本后P50/P95/P99、超过maxlen后FIFO、record+compute并发安全、e2e独立统计、P99状态判断 |
| TestMemoryMonitor | 4 | psutil可用时返回正数、/proc读取、Windows ctypes读取、全部不可用返回-1 |
| TestFusionProbe | 5 | 健康时返回True、不健康时返回False、缓存TTL内不重新检查、缓存过期后重新检查、未知状态返回None |
| TestHealthCheckProvider | 4 | RedisProvider健康/不健康、DatabaseProvider健康/不健康、RulesEngineProvider健康/降级/不健康、OOMProvider各状态 |
| TestHealthCheckerOrchestrator | 3 | check_all聚合逻辑、compact模式Redis降级、全部不健康时整体unhealthy |

#### 集成测试

| 测试类 | 用例数 | 覆盖内容 |
|--------|--------|---------|
| TestHealthSplitIntegration | 3 | 拆分后/health端点响应结构不变、latency_stats字段完整、memory字段完整 |

### 2.3 O1 checker.py拆分 — 补充测试

#### 单元测试（新文件：tests/unit/sg/test_checker_phases.py）

| 测试类 | 用例数 | 覆盖内容 |
|--------|--------|---------|
| TestCheckPhaseProtocol | 2 | 实现CheckPhase的类可实例化、未实现check方法抛TypeError |
| TestConsentPhase | 6 | granted通过、denied快速拒绝、withdrawn拒绝+降级标记、timeout降级、db_error抛异常、pending拒绝 |
| TestEdgeConnectivityPhase | 2 | connected通过、disconnected降级 |
| TestDesensitizationPhase | 5 | 无敏感数据通过、有敏感数据+融合可用→降级、有敏感数据+融合不可用→E4005A1、脱敏已降级+敏感→E4005A1变体、conclusion_suppressed跳过诊断结论 |
| TestContraindicationThresholdPhase | 6 | 无禁忌无阈值失败通过、禁忌命中降级、阈值失败降级、双失败组合降级、禁忌+融合不可用组合降级、仅融合不可用降级 |
| TestConsistencyPhase | 4 | 一致通过、分级不匹配失败、发现不匹配失败、结论-依据矛盾失败 |
| TestCheckContext | 3 | 默认值正确、字段赋值传递、跨阶段状态累积 |
| TestSafetyCheckPipeline | 4 | 5阶段顺序执行、任一阶段短路、阶段注册动态增减、空管道返回passed |

#### 集成测试

| 测试类 | 用例数 | 覆盖内容 |
|--------|--------|---------|
| TestCheckerSplitIntegration | 4 | 拆分后/api/v1/safety/check响应不变、拆分后降级输出不变、拆分后错误码不变、拆分后耗时统计不变 |

### 2.4 O2 脱敏引擎共享框架 — 补充测试

#### 单元测试（新文件：tests/unit/sg/test_desensitization_shared.py）

| 测试类 | 用例数 | 覆盖内容 |
|--------|--------|---------|
| TestBaseDesensitizationEngine | 6 | load_rules_from_yaml、_compile_patterns、desensitize_dict递归、_desensitize_list递归、check_sensitive_data、_validate_rules |
| TestSGDesensitizationEngineInherited | 5 | 继承基类后SG专用正则生效、人名匹配器正常工作、is_patch字段保留、热更新Redis Pub/Sub、异构冗余验证 |
| TestMainDesensitizationEngineInherited | 4 | 继承基类后主服务正则生效、HMAC token映射、文件重载热更新、NER集成 |
| TestHeterogeneousRedundancyPreserved | 3 | SG身份证正则≠主服务正则、SG手机号正则≠主服务正则、共享框架不改变脱敏结果 |

#### 集成测试

| 测试类 | 用例数 | 覆盖内容 |
|--------|--------|---------|
| TestDesensitizationSharedIntegration | 3 | 三层脱敏流程不变、SG脱敏结果与重构前一致、主服务脱敏结果与重构前一致 |

### 2.5 回归测试

在每项重构完成后，运行全部现有测试（约159个单元测试 + 7个性能测试），确保无回归。

### 2.6 安全测试

| 测试项 | 用例数 | 覆盖内容 |
|--------|--------|---------|
| TestRefactoringSecurity | 4 | 单例reset不会泄漏状态到其他测试、脱敏引擎共享框架不降低异构冗余安全性、checker拆分不改变校验严格性、health拆分不改变融合层缓存安全性 |

### 2.7 性能测试

在每项重构完成后，运行现有性能基准测试，确保P99延迟不退化。

---

## 三、Phase 3 重构实施计划

### 实施顺序与依赖关系

```
O3（统一单例管理）→ O7（health.py拆分）→ O1（checker.py拆分）→ O2（脱敏引擎共享框架）
```

**理由**：
- O3是基础设施，其他重构依赖统一的组件获取方式
- O7依赖O3的注册表来管理拆分后的health子组件
- O1依赖O3的注册表来管理拆分后的phase组件
- O2最独立但风险最高，放在最后确保前3项稳定

### 3.1 O3 统一单例管理

#### 实施步骤

1. **创建 `safety_gateway/registry.py`**
   - 实现 `SGComponentRegistry` 类
   - 提供 `register(name, factory)` / `get(name)` / `reset(name=None)` / `override(name, factory)` 方法
   - 线程安全：使用 `threading.Lock` 双重检查
   - 延迟初始化：首次 `get()` 时调用factory

2. **创建全局注册表实例**
   ```python
   sg_registry = SGComponentRegistry()
   ```

3. **迁移各组件到注册表**
   - 迁移顺序：checker → health_checker → consent_checker → contraindication_engine → threshold_engine → circuit_breaker_manager → sg_desensitization_engine
   - 每个组件：保留原 `get_xxx()` 函数作为兼容层，内部委托给 `sg_registry.get()`
   - 添加 `_reset_xxx()` 函数委托给 `sg_registry.reset()`

4. **更新 `__init__.py`**
   - 导出 `sg_registry`
   - 保持原有导出不变

5. **更新 `main.py` lifespan**
   - 启动时注册所有组件
   - 关闭时reset所有组件

6. **运行全部测试验证**

#### 回滚方案

- 保留原 `get_xxx()` 函数签名不变
- 注册表是纯增量变更，回滚只需删除registry.py并恢复get_xxx()内部实现
- 每个组件迁移独立，可单独回滚

#### 风险应对

| 风险 | 缓解措施 |
|------|---------|
| 注册表引入全局状态 | 注册表仅在SG进程内使用，不跨进程 |
| factory执行失败 | get()时捕获异常并清理部分初始化状态 |
| 并发竞态 | 双重检查锁+单元测试验证 |

### 3.2 O7 health.py拆分

#### 实施步骤

1. **创建 `safety_gateway/health/` 包**
   ```
   safety_gateway/health/
   ├── __init__.py          # 导出HealthChecker
   ├── checker.py           # HealthChecker编排器
   ├── latency_tracker.py   # LatencyTracker类
   ├── memory_monitor.py    # MemoryMonitor类
   ├── fusion_probe.py      # FusionProbe类
   └── providers.py         # 各HealthCheckProvider
   ```

2. **提取 LatencyTracker**
   - 从HealthChecker中提取 `_latency_samples`、`_e2e_latency_samples`、`_lock`
   - 提取 `record_latency()`、`record_e2e_latency()`、`compute_latency_stats()`、`compute_e2e_latency_stats()`
   - 提取 `_percentile()`、`_get_p99_status()`
   - LatencyTracker独立可测试

3. **提取 MemoryMonitor**
   - 从HealthChecker中提取 `_get_memory_usage_mb()`
   - 3种平台适配逻辑封装为独立方法

4. **提取 FusionProbe**
   - 从HealthChecker中提取 `_fusion_available`、`_fusion_last_check_time`、`_fusion_check_lock`
   - 提取 `_check_fusion_layer()`、`get_fusion_availability()`
   - 缓存TTL逻辑封装在FusionProbe内

5. **提取 HealthCheckProvider 策略**
   - 定义 `HealthCheckProvider` 协议：`async def check() -> dict[str, Any]`
   - 各 `_check_*` 方法提取为独立Provider类

6. **重构 HealthChecker 编排器**
   - 注入 LatencyTracker、MemoryMonitor、FusionProbe、Providers列表
   - `check_all()` 聚合各Provider结果

7. **更新导入路径**
   - `from safety_gateway.health import HealthChecker` 保持不变
   - 内部模块按需导入

8. **注册到sg_registry**
   - LatencyTracker、MemoryMonitor、FusionProbe各自注册

9. **运行全部测试验证**

#### 回滚方案

- `safety_gateway/health/__init__.py` 重新导出所有公共API
- 外部导入路径不变
- 回滚时将health/包替换为原health.py单文件

#### 风险应对

| 风险 | 缓解措施 |
|------|---------|
| 拆分改变check_all行为 | 逐字段对比拆分前后/health端点响应 |
| FusionProbe缓存行为变化 | 独立单元测试验证TTL逻辑 |
| Provider顺序影响聚合结果 | 保持与原_check_*调用顺序一致 |

### 3.3 O1 checker.py拆分

#### 实施步骤

1. **创建 `safety_gateway/checker/` 包**
   ```
   safety_gateway/checker/
   ├── __init__.py                    # 导出SafetyGatewayChecker
   ├── orchestrator.py                # SafetyCheckPipeline编排器
   ├── phase_base.py                  # CheckPhase ABC + CheckContext
   ├── consent_phase.py               # ConsentPhase
   ├── edge_connectivity_phase.py     # EdgeConnectivityPhase
   ├── desensitization_phase.py       # DesensitizationPhase
   ├── contraindication_threshold_phase.py  # ContraindicationThresholdPhase
   └── consistency_phase.py           # ConsistencyPhase
   ```

2. **定义 CheckPhase 协议和 CheckContext**
   ```python
   class CheckPhase(ABC):
       @abstractmethod
       async def check(self, context: CheckContext) -> CheckResult | None: ...

   class CheckContext:
       consent_status: ConsentStatus | None
       is_timeout: bool
       is_db_error: bool
       threshold_results: list[dict[str, Any]]
       contraindication_matches: list[dict[str, Any]]
       fusion_available: bool
       desensitization_degraded: bool
   ```

3. **提取各阶段为独立Phase类**
   - 每个Phase类持有自己需要的依赖（如ConsentPhase持有ConsentChecker）
   - 从SafetyGatewayChecker的 `_check_*_phase` 方法迁移逻辑
   - 保持完全相同的业务逻辑

4. **重构 SafetyGatewayChecker 为编排器**
   - `__init__` 中注册默认5个Phase
   - `perform_full_check` 遍历Phase列表执行
   - 保留 `check_desensitization`、`check_contraindication`、`check_consistency` 作为公开API（委托给对应Phase）

5. **更新导入路径**
   - `from safety_gateway.checker import SafetyGatewayChecker` 保持不变
   - `from safety_gateway.checker import get_safety_gateway_checker` 保持不变

6. **注册到sg_registry**
   - 各Phase可独立注册，支持测试时替换

7. **运行全部测试验证**

#### 回滚方案

- `safety_gateway/checker/__init__.py` 重新导出所有公共API
- 外部导入路径不变
- 回滚时将checker/包替换为原checker.py单文件

#### 风险应对

| 风险 | 缓解措施 |
|------|---------|
| 拆分改变校验顺序 | Phase注册顺序与原_check_*_phase调用顺序完全一致 |
| CheckContext字段遗漏 | 对比原_PhaseContext所有字段，逐一迁移 |
| 短路行为变化 | 单元测试验证每个Phase返回非None时后续Phase不执行 |
| 公开API兼容性 | check_desensitization/check_contraindication/check_consistency保留为委托方法 |

### 3.4 O2 脱敏引擎共享框架

#### 实施步骤

1. **创建 `app/common/desensitization/` 包**
   ```
   app/common/desensitization/
   ├── __init__.py
   ├── base.py              # BaseDesensitizationEngine ABC
   ├── rule.py              # DesensitizationRule共享模型
   └── recursive.py         # 递归脱敏遍历框架
   ```

2. **提取 DesensitizationRule 共享模型**
   - 基础字段：name, pattern, replacement, description, enabled, priority
   - SG扩展字段（is_patch）通过子类添加

3. **提取 BaseDesensitizationEngine ABC**
   - 共享方法：`load_rules_from_yaml()`、`_compile_patterns()`、`_validate_rules()`
   - 共享方法：`desensitize_dict()`、`_desensitize_list()`（从recursive.py调用）
   - 抽象方法：`desensitize()`、`check_sensitive_data()`（子类实现异构逻辑）

4. **提取递归脱敏遍历框架**
   - `desensitize_dict()` / `_desensitize_list()` 通用实现
   - 支持dict/list/tuple/set/嵌套递归
   - MAX_RECURSION_DEPTH常量

5. **重构 SG 脱敏引擎继承基类**
   ```python
   class SGDesensitizationEngine(BaseDesensitizationEngine):
       # SG专用：异构正则、人名匹配器、is_patch、Redis Pub/Sub热更新
   ```

6. **重构主服务脱敏引擎继承基类**
   ```python
   class MainDesensitizationEngine(BaseDesensitizationEngine):
       # 主服务专用：宽松正则、HMAC token映射、文件重载热更新
   ```

7. **验证异构冗余不被破坏**
   - 运行 `validate_heterogeneous_redundancy()` 确认SG正则≠主服务正则
   - 对比重构前后脱敏结果

8. **运行全部测试验证**

#### 回滚方案

- 保留原 `safety_gateway/desensitization.py` 和 `app/security/desensitization.py` 的完整逻辑
- 共享框架是纯增量变更，回滚只需删除app/common/desensitization/并恢复原文件
- 模块级单例 `sg_desensitization_engine` 保持不变

#### 风险应对

| 风险 | 缓解措施 |
|------|---------|
| 共享框架破坏异构冗余 | 基类仅共享框架，正则模式仍各自定义；异构冗余测试验证 |
| 继承层次增加复杂度 | 基类方法全部有默认实现，子类仅需覆盖差异点 |
| 递归遍历行为差异 | 对比重构前后desensitize_dict输出，确保完全一致 |
| 热更新机制差异 | SG用Redis Pub/Sub，主服务用文件重载，各自覆盖reload_rules() |
| 跨模块依赖 | app/common/是全新包，不引入循环依赖 |

---

## 四、实施时间线与检查点

| 阶段 | 任务 | 检查点 |
|------|------|--------|
| **P3-0** | 配置覆盖率工具 + 运行基线 | 覆盖率报告生成 |
| **P3-1** | O3测试补充 + 实现 + 验证 | 全部测试通过 + 覆盖率≥90% |
| **P3-2** | O7测试补充 + 实现 + 验证 | 全部测试通过 + /health响应不变 |
| **P3-3** | O1测试补充 + 实现 + 验证 | 全部测试通过 + /api/v1/safety/check响应不变 |
| **P3-4** | O2测试补充 + 实现 + 验证 | 全部测试通过 + 异构冗余验证通过 |
| **P3-5** | 全量回归测试 + 性能基准 | 159+单元测试 + 7性能测试全部通过 |

每个检查点必须满足：
1. 新增测试全部通过
2. 现有测试无回归
3. 性能基准无退化
4. 覆盖率达标（核心功能≥90%）

---

## 五、质量门禁

| 门禁项 | 标准 | 验证方式 |
|--------|------|---------|
| 单元测试通过率 | 100% | `pytest tests/unit/sg/ -v` |
| 性能基准通过率 | 100% | `pytest tests/benchmark/sg/ -v` |
| 核心功能覆盖率 | ≥90% | `pytest --cov=safety_gateway` |
| API响应兼容性 | 100% | 对比重构前后/health和/safety/check响应 |
| 异构冗余完整性 | SG正则≠主服务正则 | `validate_heterogeneous_redundancy()` |
| 无安全回归 | 降级路径全部可达 | 7种降级场景端到端测试 |
