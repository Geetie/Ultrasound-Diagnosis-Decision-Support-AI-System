# SG安全网关轨道 — 全面重构需求分析与优化建议报告

## 一、代码规模概览

| 模块 | 文件 | 行数 | 职责 |
|------|------|------|------|
| 主校验器 | checker.py | 814 | 四阶段校验编排、降级决策 |
| API入口 | main.py | 427 | FastAPI路由、请求/响应模型 |
| 脱敏引擎 | desensitization.py | 504 | 正则脱敏、规则管理、热更新 |
| 降级输出 | degraded_output.py | 164 | 7种降级场景输出处理 |
| 健康检查 | health.py | 467 | 7组件健康探测、延迟统计 |
| 熔断器 | circuit_breaker.py | 232 | 熔断器模式、管理器 |
| 阈值引擎 | threshold.py | 600 | 阈值管理、DB持久化、Redis订阅 |
| 同意校验 | consent_checker.py | 192 | DB直查、重试、熔断保护 |
| 禁忌校验 | contraindication.py | 238 | 禁忌规则匹配 |
| 配置 | sg_config.py | 64 | Pydantic Settings |
| 类型定义 | sg_types.py | 38 | 枚举+异常别名 |
| 错误层次 | errors.py | 208 | 18种异常类 |
| 校验结果 | validation_result.py | 95 | 结果数据模型 |
| 数据库 | sg_db.py | 72 | 异步引擎/会话工厂 |
| ORM模型 | sg_models.py | 46 | 2张表定义 |
| Redis池 | redis_pool.py | 141 | 连接池管理 |
| 配置校验 | sg_validator.py | 83 | 启动时配置合法性校验 |
| **合计** | **17个源文件** | **~4385** | — |

---

## 二、重构需求分析（按严重程度排序）

### R1. 【P0-严重】checker.py 过度膨胀 — 上帝类反模式

**问题**：`SafetyGatewayChecker`（814行）承担了过多职责：
- 四阶段校验编排（perform_full_check + 5个_phase方法）
- 脱敏递归检查（check_desensitization + _check_value_recursive）
- 禁忌校验（check_contraindication）
- 一致性校验（check_consistency + 3个辅助方法）
- 降级输出代理（apply_degraded_output, _filter_forbidden_terms）
- 同意状态代理（check_consent_status）
- 白名单管理（get_whitelist_fields）

**影响**：
- 修改任一校验阶段逻辑都可能影响其他阶段
- 测试困难：227个测试方法集中在一个测试类中
- 新增校验阶段需要修改此类，违反开闭原则

**重构建议**：将checker.py拆分为校验编排器+独立阶段处理器，详见优化建议O1。

---

### R2. 【P0-严重】脱敏引擎与主服务脱敏引擎的异构冗余代码

**问题**：`safety_gateway/desensitization.py`（504行）与 `app/security/desensitization.py` + `app/security/three_layer_desensitization.py` 存在大量结构相似的代码：
- 两者都有 `DesensitizationRule` 模型、规则加载、YAML解析、正则编译
- 两者都有 `desensitize()`、`desensitize_dict()`、`check_sensitive_data()` 方法
- 两者都有规则热更新机制（SG通过Redis Pub/Sub，主服务通过配置重载）
- 递归遍历逻辑几乎相同（`_check_value_recursive` vs `desensitize_dict`）

**影响**：
- 修改脱敏规则格式需要同步两个引擎
- 测试覆盖需要重复编写
- 异构差异（正则模式不同）是设计意图，但代码框架的重复不是

**重构建议**：抽取共享的脱敏框架基类，详见优化建议O2。

---

### R3. 【P1-重要】单例模式实现不一致且缺乏统一管理

**问题**：SG轨道内至少有5处独立的单例实现，风格各异：

| 组件 | 单例实现方式 | 线程安全 | 重置支持 |
|------|-------------|---------|---------|
| SafetyGatewayChecker | 全局变量+threading.Lock | ✅ | ✅ _reset |
| HealthChecker | 全局变量+threading.Lock | ✅ | ❌ |
| ThresholdEngine | 全局变量+threading.Lock | ✅ | ❌ |
| ContraindicationEngine | 全局变量+threading.Lock | ✅ | ❌ |
| ConsentChecker | 全局变量+threading.Lock | ✅ | ❌ |
| sg_desensitization_engine | 模块级实例（无延迟） | ❌ | ❌ |
| circuit_breaker_manager | 模块级实例（无延迟） | ❌ | ❌ |

**影响**：
- 测试隔离困难：只有SafetyGatewayChecker支持_reset，其他组件测试间可能互相污染
- sg_desensitization_engine和circuit_breaker_manager在import时即初始化，无法延迟加载
- 无法在运行时替换实现（如测试时注入mock）

**重构建议**：统一单例管理机制，详见优化建议O3。

---

### R4. 【P1-重要】degraded_output.py 中降级标记逻辑的if-elif链

**问题**：`apply_degraded_output()` 函数中，针对11种 `DegradedReason` 枚举值使用if-elif链设置降级标记：

```python
if reason == DegradedReason.SENSITIVE_DATA_DETECTED:
    degraded_data["sg_output_degraded"] = True
    degraded_data["fusion_degraded"] = False
    degraded_data["safety_gateway_unavailable"] = False
elif reason == DegradedReason.DB_TIMEOUT:
    degraded_data["fusion_degraded"] = False
    degraded_data["safety_gateway_unavailable"] = False
    degraded_data["db_timeout"] = True
# ... 9 more elif branches
```

**影响**：
- 新增DegradedReason枚举值时必须同步修改此函数，遗漏则无标记输出
- 大量重复的 `fusion_degraded=False, safety_gateway_unavailable=False` 赋值
- 难以一眼看出各降级原因对应的标记差异

**重构建议**：使用策略映射表替代if-elif链，详见优化建议O4。

---

### R5. 【P1-重要】elapsed_ms计算逻辑重复

**问题**：`checker.py` 中每个 `_check_*_phase` 方法都重复相同的耗时计算模式：

```python
elapsed = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
```

此模式在perform_full_check及其5个phase方法中出现了**12次**。

**影响**：
- 代码噪音高，核心逻辑被样板代码淹没
- 如果需要修改计时逻辑（如改用monotonic时钟），需修改12处

**重构建议**：提取计时上下文管理器，详见优化建议O5。

---

### R6. 【P1-重要】CheckResult构造参数过多且不一致

**问题**：`CheckResult` 有11个字段，但各phase方法构造时参数不一致：
- 有些省略 `contraindication_matches=[]`（依赖默认值）
- 有些省略 `threshold_results=[]`
- 有些省略 `sensitive_data_detected=[]`
- `consent_check_timeout` 有时显式传入，有时依赖默认值

**影响**：
- 阅读者无法确定哪些字段在当前上下文中有意义
- 默认值掩盖了字段间的逻辑关系
- 容易遗漏关键字段

**重构建议**：为每个phase引入专用的结果工厂方法，详见优化建议O6。

---

### R7. 【P1-重要】health.py 职责过重

**问题**：`HealthChecker`（467行）承担了过多职责：
- 7个组件的健康检查（Redis、DB、规则引擎、熔断器、阈值引擎、OOM、融合层）
- 延迟统计（P50/P95/P99计算，2套独立的延迟采样）
- 融合层可用性缓存
- 内存使用监控（3种平台适配：psutil、/proc、Windows ctypes）
- OOM保护检查（含docker-compose解析）

**影响**：
- 修改任一组件的健康检查逻辑可能影响其他组件
- 延迟统计逻辑与业务健康检查耦合
- 内存监控和OOM检查是基础设施关注点，不应在业务健康检查器中

**重构建议**：拆分为独立组件，详见优化建议O7。

---

### R8. 【P2-一般】threshold.py 中同步/异步方法并存

**问题**：`ThresholdEngine` 同时提供同步和异步两套API：
- `check()` vs `check_async()`
- `check_multiple()` vs `check_multiple_async()`
- `load_thresholds()` vs `load_thresholds_from_db()`
- `update_threshold()` vs `update_threshold_async()`

同步方法在内部使用 `threading.Lock`，异步方法使用 `asyncio.Event`，两套并发控制机制并存。

**影响**：
- API表面积过大，调用方需要判断使用哪个版本
- 两套并发控制机制增加了正确性验证的复杂度
- 同步方法 `load_thresholds()` 在异步上下文中被 `get_all_thresholds()` 和 `update_threshold()` 调用，可能阻塞事件循环

**重构建议**：统一为异步API，移除同步方法，详见优化建议O8。

---

### R9. 【P2-一般】errors.py 异常层次定义过度

**问题**：定义了18种异常类，但实际使用中：
- `DatabaseConnectionError`、`DatabaseQueryTimeoutError`、`DatabaseQueryError` 在consent_checker中被catch但仅用于日志区分
- `SensitiveDataError`、`ContraindicationError`、`ThresholdError`、`ConsistencyError` 从未被raise
- `ConsentCheckError`、`ConsentStatusUnknownError` 从未被raise
- `RulesValidationError` 从未被raise
- `ERROR_CODE_MAP` 和 `get_error_class_by_code()` 从未被使用

**影响**：
- 约10个异常类是"死代码"，增加了认知负担
- ERROR_CODE_MAP暗示有按错误码反查异常类的需求，但从未实现

**重构建议**：移除未使用的异常类和映射表，详见优化建议O9。

---

### R10. 【P2-一般】sg_types.py 仅做别名重导出

**问题**：`sg_types.py` 的唯一自定义类型是 `ConsentStatus` 枚举（4行），其余全部是从 `errors.py` 的别名重导出：

```python
from safety_gateway.errors import (
    SafetyGatewayError as SGError,
    ServiceUnavailableError as SGServiceUnavailableError,
    ...
)
```

**影响**：
- 增加了一层间接性，阅读者需要跨两个文件才能找到真实定义
- 别名命名（SG前缀）与原始命名不一致，增加混淆
- 如果errors.py中的类名变更，sg_types.py的别名也需要同步

**重构建议**：将ConsentStatus移入validation_result.py（与其他枚举一起），移除sg_types.py，详见优化建议O10。

---

### R11. 【P2-一般】main.py 中API端点错误处理模式重复

**问题**：3个POST端点（perform_safety_check、perform_consistency_check、perform_contraindication_check）都有相同的错误处理模式：

```python
try:
    # 业务逻辑
except SafetyGatewayError:
    raise
except (ValueError, KeyError, TypeError) as e:
    logger.error("sg_xxx_input_error", error=str(e))
    raise SGServiceUnavailableError(...)
except Exception as e:
    logger.error("sg_xxx_unexpected_error", error=str(e), error_type=type(e).__name__)
    raise SGServiceUnavailableError(...)
```

**影响**：
- 3处完全相同的try-except结构
- 新增端点时需要复制此模式
- 错误处理逻辑变更需要同步3处

**重构建议**：提取通用错误处理装饰器，详见优化建议O11。

---

### R12. 【P2-一般】consent_checker.py 重试逻辑可提取

**问题**：`_retry_query()` 方法实现了通用的指数退避重试逻辑（含总时间上限检查），但此逻辑硬编码在ConsentChecker中。threshold.py的 `load_thresholds_from_db()` 也有类似的重试逻辑。

**影响**：
- 重试策略变更需要修改多处
- 两处重试逻辑的细节不同（consent_checker有总时间上限，threshold没有），但框架相同

**重构建议**：提取通用重试工具，详见优化建议O12。

---

### R13. 【P2-一般】Redis Pub/Sub订阅模式重复

**问题**：main.py的 `_rules_sync_subscriber()` 和 threshold.py的 `_subscribe_loop()` 实现了几乎相同的Redis Pub/Sub订阅模式：
- 连接Redis → subscribe → listen → handle message → error → retry
- 都有指数退避重连
- 都有CancelledError处理

**影响**：
- 新增订阅频道需要复制此模式
- 重连策略变更需要同步修改

**重构建议**：提取通用Pub/Sub订阅框架，详见优化建议O13。

---

### R14. 【P2-一般】配置类缺少验证注解

**问题**：`SGSettings` 使用 `pydantic-settings` 但未利用Pydantic的field验证器：
- 端口范围验证在 `sg_validator.py` 中手动实现
- 阈值大小关系验证在 `sg_validator.py` 中手动实现
- 超时值正数验证在 `sg_validator.py` 中手动实现

**影响**：
- 配置验证与配置定义分离，容易遗漏
- Pydantic原生支持的验证能力未被利用
- sg_validator.py中的验证逻辑与SGSettings字段定义需要手动保持一致

**重构建议**：将验证逻辑迁移到Pydantic field验证器中，详见优化建议O14。

---

### R15. 【P2-一般】_check_conclusion_evidence_contradiction 方法复杂度过高

**问题**：`checker.py` 的 `_check_conclusion_evidence_contradiction()` 方法（35行）圈复杂度极高：
- 三层嵌套循环（矛盾词对 × 结论句 × 依据句）
- 双重否定前缀检查
- 正反向矛盾检测
- 逻辑分支众多

**影响**：
- 难以理解和维护
- 测试覆盖困难
- 性能风险：O(矛盾词对 × 结论句 × 依据句 × 前缀)

**重构建议**：拆分为更小的判断方法，引入矛盾词对配置表，详见优化建议O15。

---

## 三、优化建议清单

### O1. 【架构】checker.py 拆分 — 策略模式+校验管道

**目标**：将SafetyGatewayChecker从上帝类重构为校验编排器+独立阶段处理器。

**方案**：

```
safety_gateway/
├── checker/
│   ├── __init__.py          # 导出SafetyGatewayChecker
│   ├── orchestrator.py      # 校验编排器（原perform_full_check）
│   ├── consent_phase.py     # 阶段1：同意状态校验
│   ├── desensitization_phase.py  # 阶段2：脱敏校验
│   ├── contraindication_threshold_phase.py  # 阶段3：禁忌+阈值
│   ├── consistency_phase.py # 阶段4：一致性校验
│   └── phase_base.py        # 阶段基类（定义check接口）
```

定义 `CheckPhase` 协议：
```python
class CheckPhase(ABC):
    @abstractmethod
    async def check(self, context: CheckContext) -> CheckResult | None:
        """执行校验阶段，返回None表示通过"""
```

编排器仅负责阶段注册和顺序执行：
```python
class SafetyGatewayChecker:
    def __init__(self):
        self._phases: list[CheckPhase] = [
            ConsentPhase(),
            EdgeConnectivityPhase(),
            DesensitizationPhase(),
            ContraindicationThresholdPhase(),
            ConsistencyPhase(),
        ]

    async def perform_full_check(self, ...) -> CheckResult:
        ctx = CheckContext(...)
        for phase in self._phases:
            result = await phase.check(ctx)
            if result is not None:
                return result
        return CheckResult(status=PASSED, ...)
```

**收益**：
- 每个阶段独立测试
- 新增阶段只需实现CheckPhase接口并注册
- 编排器逻辑极简

---

### O2. 【架构】脱敏引擎共享框架

**目标**：消除SG脱敏引擎与主服务脱敏引擎之间的代码框架重复。

**方案**：创建共享脱敏框架包：

```
app/common/
├── desensitization_base.py   # 基类：规则模型、加载、编译、递归遍历
├── desensitization_rule.py   # DesensitizationRule Pydantic模型
└── desensitization_types.py  # 共享类型
```

`BaseDesensitizationEngine` 提供：
- `load_rules_from_yaml()`
- `_compile_patterns()`
- `desensitize_dict()` / `_desensitize_list()`（递归遍历）
- `check_sensitive_data()`
- `_validate_rules()`

SG脱敏引擎和主服务脱敏引擎继承基类，仅覆盖：
- 正则模式差异（SG用异构正则，主服务用NER+宽松正则）
- 热更新机制差异（SG用Redis Pub/Sub，主服务用配置重载）

**收益**：
- 消除约200行重复代码
- 规则格式变更只需修改基类
- 递归遍历逻辑统一

---

### O3. 【架构】统一单例管理

**目标**：统一所有组件的单例生命周期管理。

**方案**：引入 `SingletonRegistry`：

```python
class SGComponentRegistry:
    """SG组件注册表，统一管理单例生命周期"""

    def __init__(self):
        self._instances: dict[str, Any] = {}
        self._factories: dict[str, Callable] = {}
        self._lock = threading.Lock()

    def register(self, name: str, factory: Callable) -> None:
        self._factories[name] = factory

    def get(self, name: str) -> Any:
        if name not in self._instances:
            with self._lock:
                if name not in self._instances:
                    self._instances[name] = self._factories[name]()
        return self._instances[name]

    def reset(self, name: str | None = None) -> None:
        with self._lock:
            if name is None:
                self._instances.clear()
            else:
                self._instances.pop(name, None)
```

所有组件通过注册表获取：
```python
sg_registry = SGComponentRegistry()
sg_registry.register("checker", SafetyGatewayChecker)
sg_registry.register("health_checker", HealthChecker)
# ...

def get_safety_gateway_checker() -> SafetyGatewayChecker:
    return sg_registry.get("checker")
```

**收益**：
- 统一的延迟初始化和线程安全
- 统一的reset接口，测试隔离简单
- 可替换实现（测试时注入mock）

---

### O4. 【设计模式】降级标记策略映射表

**目标**：消除degraded_output.py中的if-elif链。

**方案**：定义降级标记映射表：

```python
DEGRADED_MARKS: dict[DegradedReason, dict[str, Any]] = {
    DegradedReason.SENSITIVE_DATA_DETECTED: {
        "sg_output_degraded": True,
    },
    DegradedReason.DB_TIMEOUT: {
        "db_timeout": True,
    },
    DegradedReason.CONTRAINDICATION_HIT: {
        "contraindication_hit": True,
    },
    DegradedReason.CONFIDENCE_BELOW_THRESHOLD: {
        "confidence_below_threshold": True,
    },
    DegradedReason.FUSION_UNAVAILABLE: {
        "fusion_bypassed": True,
    },
    DegradedReason.COMBINED_FAILURE: {
        "fusion_bypassed": True,
        "combined_failure": True,
    },
    DegradedReason.EDGE_DISCONNECTED: {
        "edge_disconnected": True,
    },
    DegradedReason.FUSION_UNAVAILABLE_SENSITIVE_DETECTED: {
        "fusion_degraded": True,
        "fusion_unavailable": True,
        "fusion_unavailable_sensitive_detected": True,
        "error_code": "E4005A1",
    },
    DegradedReason.DESENSITIZATION_DEGRADED_SENSITIVE_DETECTED: {
        "fusion_degraded": True,
        "desensitization_degraded_sensitive_detected": True,
        "error_code": "E4005A1",
    },
    DegradedReason.SG_UNAVAILABLE_CONSENT_WITHDRAWN: {
        "safety_gateway_unavailable": True,
        "sg_unavailable_consent_withdrawn": True,
        "error_code": "E4005A2",
    },
    DegradedReason.DUAL_DEGRADATION_FUSION_AND_SG_UNAVAILABLE: {
        "fusion_degraded": True,
        "safety_gateway_unavailable": True,
    },
}

DEFAULT_MARKS = {"fusion_degraded": False, "safety_gateway_unavailable": False}
```

`apply_degraded_output()` 简化为：
```python
marks = {**DEFAULT_MARKS, **DEGRADED_MARKS.get(reason, {})}
if reason == DegradedReason.SG_UNAVAILABLE_CONSENT_WITHDRAWN:
    marks["fusion_degraded"] = not fusion_available if fusion_available is not None else False
degraded_data.update(marks)
```

**收益**：
- 新增DegradedReason只需在映射表中添加一行
- 标记差异一目了然
- 消除约50行if-elif代码

---

### O5. 【工具】计时上下文管理器

**目标**：消除checker.py中12处重复的elapsed_ms计算。

**方案**：

```python
from contextlib import contextmanager
import time

@contextmanager
def measure_elapsed():
    start = time.monotonic()
    elapsed_holder = [0.0]
    yield elapsed_holder
    elapsed_holder[0] = (time.monotonic() - start) * 1000
```

使用方式：
```python
with measure_elapsed() as elapsed:
    # 校验逻辑
    if condition:
        return CheckResult(..., elapsed_ms=round(elapsed[0], 2))
```

**收益**：
- 消除12处重复的datetime计算
- 改用monotonic时钟避免系统时间调整影响
- 计时逻辑变更只需修改一处

---

### O6. 【设计模式】CheckResult 工厂方法

**目标**：为每个phase提供专用的CheckResult构造方法，减少参数遗漏。

**方案**：在CheckResult上添加类方法：

```python
class CheckResult(BaseModel):
    # ... 现有字段 ...

    @classmethod
    def failed_consent(cls, consent_status, message, elapsed_ms, consent_check_timeout=False, degraded_reason=None, degraded_mode=False):
        return cls(
            status=CheckResultStatus.FAILED,
            consent_status=consent_status,
            sensitive_data_detected=[],
            message=message,
            degraded_mode=degraded_mode,
            degraded_reason=degraded_reason,
            elapsed_ms=elapsed_ms,
            consent_check_timeout=consent_check_timeout,
        )

    @classmethod
    def degraded(cls, consent_status, message, degraded_reason, elapsed_ms, consent_check_timeout=False, **kwargs):
        return cls(
            status=CheckResultStatus.DEGRADED,
            consent_status=consent_status,
            sensitive_data_detected=kwargs.get("sensitive_data_detected", []),
            contraindication_matches=kwargs.get("contraindication_matches", []),
            threshold_results=kwargs.get("threshold_results", []),
            message=message,
            degraded_mode=True,
            degraded_reason=degraded_reason,
            elapsed_ms=elapsed_ms,
            consent_check_timeout=consent_check_timeout,
        )

    @classmethod
    def passed(cls, consent_status, message, elapsed_ms, threshold_results=None, consent_check_timeout=False):
        return cls(
            status=CheckResultStatus.PASSED,
            consent_status=consent_status,
            sensitive_data_detected=[],
            contraindication_matches=[],
            threshold_results=threshold_results or [],
            message=message,
            degraded_mode=False,
            degraded_reason=None,
            elapsed_ms=elapsed_ms,
            consent_check_timeout=consent_check_timeout,
        )
```

**收益**：
- 每种结果类型的必填字段由工厂方法签名强制
- 消除各phase方法中重复的空列表参数
- 新增字段只需修改工厂方法

---

### O7. 【架构】health.py 拆分

**目标**：将HealthChecker拆分为独立关注点。

**方案**：

```
safety_gateway/
├── health/
│   ├── __init__.py
│   ├── checker.py          # HealthChecker编排器
│   ├── latency_tracker.py  # 延迟采样与百分位计算
│   ├── memory_monitor.py   # 内存使用监控（3平台适配）
│   ├── fusion_probe.py     # 融合层可用性探测+缓存
│   └── component_checks.py # 各组件健康检查方法集合
```

- `LatencyTracker`：独立的延迟统计组件，可复用于其他模块
- `MemoryMonitor`：平台适配的内存监控，可复用于主服务
- `FusionProbe`：融合层探测+TTL缓存，独立于健康检查

**收益**：
- 每个组件职责单一
- 延迟统计可被其他模块复用
- 内存监控可被主服务的GPU监控复用

---

### O8. 【API简化】threshold.py 统一为异步API

**目标**：移除同步方法，统一为异步API。

**方案**：
1. 移除 `check()`、`check_multiple()`、`load_thresholds()`、`update_threshold()`
2. 将 `check_async()` 重命名为 `check()`
3. 将 `check_multiple_async()` 重命名为 `check_multiple()`
4. 将 `update_threshold_async()` 重命名为 `update_threshold()`
5. `load_thresholds()` 改为内部方法 `_load_sync_fallback()`（仅在DB加载失败时使用）
6. `get_all_thresholds()` 改为异步方法 `async get_all_thresholds()`

**收益**：
- API表面积减半
- 消除同步/异步混用的风险
- 简化调用方代码

---

### O9. 【清理】errors.py 精简

**目标**：移除未使用的异常类和映射表。

**方案**：
1. 保留实际被raise的异常类：`SafetyGatewayError`、`DatabaseError`、`DatabaseConnectionError`、`DatabaseQueryTimeoutError`、`DatabaseQueryError`、`CircuitBreakerError`、`ConfigurationError`、`ServiceUnavailableError`、`DesensitizationSafetyViolationError`、`ConsentSafetyViolationError`、`DatabaseUnavailableError`、`CombinedUnavailableError`
2. 移除从未被raise的异常类：`ConsentError`、`ConsentCheckError`、`ConsentStatusUnknownError`、`ValidationError`、`SensitiveDataError`、`ContraindicationError`、`ThresholdError`、`ConsistencyError`、`RulesValidationError`
3. 移除 `ERROR_CODE_MAP` 和 `get_error_class_by_code()`
4. 如果未来需要这些异常类，再按需添加

**收益**：
- 减少~60行死代码
- 降低认知负担
- 异常层次更清晰

---

### O10. 【清理】移除sg_types.py

**目标**：消除不必要的间接层。

**方案**：
1. 将 `ConsentStatus` 枚举移入 `validation_result.py`（与 `CheckResultStatus`、`DegradedReason` 等枚举同文件）
2. 修改所有 `from safety_gateway.sg_types import ConsentStatus` 为 `from safety_gateway.validation_result import ConsentStatus`
3. 修改所有 `from safety_gateway.sg_types import SGError, ...` 为直接从 `safety_gateway.errors` 导入
4. 删除 `sg_types.py`

**收益**：
- 消除一层间接性
- 减少文件数量
- 导入路径更直观

---

### O11. 【设计模式】API端点错误处理装饰器

**目标**：消除main.py中3处重复的try-except模式。

**方案**：

```python
def sg_endpoint_handler(error_prefix: str):
    """SG API端点通用错误处理装饰器"""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except SafetyGatewayError:
                raise
            except (ValueError, KeyError, TypeError) as e:
                logger.error(f"sg_{error_prefix}_input_error", error=str(e))
                raise SGServiceUnavailableError(
                    error_message=f"输入数据异常: {e!s}",
                    detail=f"sg_{error_prefix}_input_error",
                )
            except Exception as e:
                logger.error(f"sg_{error_prefix}_unexpected_error", error=str(e), error_type=type(e).__name__)
                raise SGServiceUnavailableError(
                    error_message=f"处理失败: {e!s}",
                    detail=f"sg_{error_prefix}_error",
                )
        return wrapper
    return decorator
```

使用方式：
```python
@app.post("/api/v1/safety/check")
@sg_endpoint_handler("safety_check")
async def perform_safety_check(request: SafetyCheckRequest):
    # 仅保留业务逻辑
    ...
```

**收益**：
- 消除3处重复的try-except
- 新增端点只需添加装饰器
- 错误处理逻辑变更只需修改装饰器

---

### O12. 【工具】通用重试工具

**目标**：提取consent_checker和threshold中重复的重试逻辑。

**方案**：

```python
async def retry_with_backoff(
    func: Callable,
    *args,
    max_attempts: int = 3,
    delay_base: float = 0.5,
    max_total_time: float | None = None,
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
    on_retry: Callable | None = None,
    **kwargs,
) -> Any:
    start_time = asyncio.get_running_loop().time()
    for attempt in range(max_attempts):
        try:
            return await func(*args, **kwargs)
        except retryable_exceptions as e:
            if attempt == max_attempts - 1:
                raise
            elapsed = asyncio.get_running_loop().time() - start_time
            delay = delay_base * (2 ** attempt)
            if max_total_time and elapsed + delay > max_total_time:
                raise
            if on_retry:
                on_retry(attempt, e, elapsed)
            await asyncio.sleep(delay)
```

**收益**：
- 重试策略统一
- 新增需要重试的操作只需调用此工具
- 重试参数可配置化

---

### O13. 【架构】通用Redis Pub/Sub订阅框架

**目标**：消除main.py和threshold.py中重复的Pub/Sub订阅模式。

**方案**：

```python
class RedisSubscriber:
    """通用Redis Pub/Sub订阅框架"""

    def __init__(
        self,
        channel: str,
        message_handler: Callable[[bytes], Awaitable[None]],
        retry_delay_base: float = 1.0,
        retry_delay_max: float = 30.0,
    ):
        self._channel = channel
        self._message_handler = message_handler
        self._retry_delay_base = retry_delay_base
        self._retry_delay_max = retry_delay_max
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._subscribe_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _subscribe_loop(self) -> None:
        retry_delay = self._retry_delay_base
        while self._running:
            try:
                redis = await SafetyGatewayRedisPool.get_client()
                async with redis.pubsub() as pubsub:
                    await pubsub.subscribe(self._channel)
                    retry_delay = self._retry_delay_base
                    async for message in pubsub.listen():
                        if not self._running:
                            break
                        if message["type"] == "message":
                            await self._message_handler(message["data"])
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error("sg_subscriber_error", channel=self._channel, error=str(e))
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, self._retry_delay_max)
```

**收益**：
- 消除2处重复的订阅循环
- 新增订阅频道只需创建RedisSubscriber实例
- 重连策略统一

---

### O14. 【规范】配置验证迁移到Pydantic验证器

**目标**：将sg_validator.py中的手动验证逻辑迁移到SGSettings的Pydantic field验证器。

**方案**：

```python
from pydantic import field_validator, model_validator

class SGSettings(BaseSettings):
    safety_gateway_port: int = 9001
    sg_p99_latency_warning_threshold_ms: float = 4.0
    sg_p99_latency_critical_threshold_ms: float = 5.0
    # ...

    @field_validator("safety_gateway_port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError("must be between 1 and 65535")
        if v in {8000, 8001, 8002}:
            raise ValueError(f"port {v} conflicts with another service")
        return v

    @model_validator(mode="after")
    def validate_thresholds(self) -> "SGSettings":
        if self.sg_p99_latency_warning_threshold_ms >= self.sg_p99_latency_critical_threshold_ms:
            raise ValueError("warning threshold must be less than critical threshold")
        # ... 其他跨字段验证
        return self
```

**收益**：
- 验证逻辑与字段定义同文件，不会遗漏
- Pydantic自动在Settings初始化时执行验证
- 可移除sg_validator.py中的重复验证逻辑

---

### O15. 【算法】矛盾检测方法重构

**目标**：降低_check_conclusion_evidence_contradiction的圈复杂度。

**方案**：

```python
CONTRADICTION_PAIRS = [
    ("良性", "恶性"), ("正常", "异常"), ("阴性", "阳性"),
]

NEGATION_PREFIXES = ("无", "非", "未", "不", "未见", "无恶性", "非恶性", "不伴", "除外", "不考虑", "未提示", "排除")

def _is_negated(self, text: str, word: str) -> bool:
    return any(text.startswith(prefix + word) for prefix in NEGATION_PREFIXES)

def _has_contradiction_in_sentences(self, c_sent: str, e_sent: str) -> bool:
    for pos_word, neg_word in CONTRADICTION_PAIRS:
        if pos_word in c_sent and neg_word in e_sent:
            if not self._is_negated(e_sent, neg_word) and not self._is_negated(c_sent, pos_word):
                return True
        if neg_word in c_sent and pos_word in e_sent:
            if not self._is_negated(e_sent, pos_word) and not self._is_negated(c_sent, neg_word):
                return True
    return False

def _check_conclusion_evidence_contradiction(self, conclusion: str, evidence: str) -> bool:
    conclusion_sentences = conclusion.replace("，", "。").replace("；", "。").split("。")
    evidence_sentences = evidence.replace("，", "。").replace("；", "。").split("。")
    for c_sent in conclusion_sentences:
        for e_sent in evidence_sentences:
            if self._has_contradiction_in_sentences(c_sent, e_sent):
                return True
    return False
```

**收益**：
- 圈复杂度从~20降至~5
- 矛盾词对可配置化（未来可从YAML加载）
- 否定前缀检查提取为独立方法

---

## 四、重构优先级与实施路径

### Phase 1：低风险高收益（1-2天）

| 编号 | 重构项 | 风险 | 预期收益 |
|------|--------|------|---------|
| O5 | 计时上下文管理器 | 低 | 消除12处重复 |
| O10 | 移除sg_types.py | 低 | 消除间接层 |
| O9 | errors.py精简 | 低 | 消除~60行死代码 |
| O11 | API错误处理装饰器 | 低 | 消除3处重复 |
| O14 | 配置验证迁移 | 低 | 验证与定义统一 |
| O4 | 降级标记策略映射 | 低 | 消除if-elif链 |

### Phase 2：中等风险中等收益（2-3天）

| 编号 | 重构项 | 风险 | 预期收益 |
|------|--------|------|---------|
| O6 | CheckResult工厂方法 | 中 | 减少参数遗漏 |
| O12 | 通用重试工具 | 中 | 统一重试策略 |
| O15 | 矛盾检测重构 | 中 | 降低复杂度 |
| O8 | threshold统一异步 | 中 | API简化 |
| O13 | 通用Pub/Sub框架 | 中 | 消除订阅重复 |

### Phase 3：高风险高收益（3-5天）

| 编号 | 重构项 | 风险 | 预期收益 |
|------|--------|------|---------|
| O3 | 统一单例管理 | 高 | 生命周期统一 |
| O7 | health.py拆分 | 高 | 职责分离 |
| O1 | checker.py拆分 | 高 | 消除上帝类 |
| O2 | 脱敏引擎共享框架 | 高 | 消除跨模块重复 |

---

## 五、技术债务清单

| # | 债务项 | 位置 | 影响 | 清理建议 |
|---|--------|------|------|---------|
| D1 | `_LazyAsyncEngine` 代理模式 | sg_db.py | 调试困难，IDE无法推断类型 | 改为显式初始化函数 |
| D2 | `ConsentRecord` ORM模型与主服务重复 | sg_models.py vs app/models/database.py | 两个ORM模型定义同一张表 | SG直接引用主服务模型或提取共享模型包 |
| D3 | `_KNOWN_SERVICE_PORTS` 硬编码 | sg_validator.py | 新增服务需手动更新 | 从docker-compose.yml自动读取 |
| D4 | `MAX_RECURSION_DEPTH=20` 重复定义 | checker.py + degraded_output.py + desensitization.py | 修改需同步3处 | 提取为sg_config配置项 |
| D5 | `FORBIDDEN_GRADING_TERMS` 重复定义 | checker.py + degraded_output.py | 修改需同步2处 | 单一来源定义 |
| D6 | `DEFAULT_WHITELIST_FIELDS` 重复定义 | checker.py + degraded_output.py + sg_config.py | 修改需同步3处 | 仅在sg_config.py中定义 |
| D7 | 融合层健康检查每次创建新httpx.AsyncClient | health.py:415 | 连接未复用，每次TCP握手 | 复用持久化httpx客户端 |
| D8 | `_check_fusion_layer` 中设置 `self._fusion_available` 但异常时也设置 `self._fusion_last_check_time` | health.py:443 | unknown状态也更新了检查时间，可能导致缓存判断不准确 | 区分healthy/unhealthy/unknown的缓存策略 |
| D9 | `SafetyGatewayClient.check_output()` 中重试循环无最大重试次数限制 | safety_gateway_client.py | 仅靠queue_timeout限制，可能导致大量重试 | 添加max_retries参数 |

---

## 六、性能优化机会

| # | 优化项 | 当前状态 | 优化方案 | 预期收益 |
|---|--------|---------|---------|---------|
| P1 | 融合层健康检查HTTP客户端 | 每次创建新连接 | 复用持久化httpx.AsyncClient | 减少TCP握手，降低~50ms延迟 |
| P2 | 脱敏规则排序 | 每次desensitize/check时排序 | 初始化/重载时排序一次 | 减少O(N log N)排序开销 |
| P3 | 禁忌条件匹配 | 精确字符串匹配 | 预编译为set查找 | O(1) vs O(N) |
| P4 | 延迟百分位计算 | 每次请求排序全部样本 | 使用t-digest或固定桶直方图 | O(1) vs O(N log N) |
| P5 | 同意状态查询 | 每次校验直查PG | 保持直查策略（安全要求），但优化查询索引 | 确保idx_consent_patient_status覆盖查询 |

---

## 七、可维护性改进总结

| 维度 | 当前状态 | 重构后预期 |
|------|---------|-----------|
| 文件数 | 17个源文件 | ~22个源文件（拆分后更多但更小） |
| 最大文件行数 | 814行（checker.py） | <300行/文件 |
| 单例管理 | 7种不同实现 | 统一注册表 |
| 重复代码 | ~300行（脱敏框架+重试+订阅+计时） | <50行 |
| 死代码 | ~60行（未使用异常类+映射表） | 0 |
| API一致性 | 同步/异步混用 | 全异步 |
| 配置验证 | 分离在2个文件 | 统一在Settings类 |
| 测试隔离 | 仅1个组件支持reset | 全部支持 |

---

---

## 九、跨模块耦合问题（主服务 ↔ SG服务）

### R16. 【P1-重要】主服务与SG白名单默认值不一致

**位置**：
- `app/config/settings.py` — 主服务 `safety_gateway_whitelist_fields` 默认值：`['observations', 'status', 'request_id', 'session_id']`
- `safety_gateway/sg_config.py` — SG服务默认值：`['organ_name', 'anatomical_location', 'echogenicity', 'boundary', 'morphology', 'vascularity', 'calcification', 'lesion_size_measurements', 'observations']`

**影响**：如果环境变量未显式配置，主服务客户端和SG服务将使用**完全不同**的白名单进行降级输出过滤，可能导致敏感数据泄漏或合法数据被误过滤。

**重构建议**：将白名单定义提取为共享配置源，或SG服务作为白名单的唯一权威来源。

---

### R17. 【P1-重要】SafetyGatewayClient.check_output() 对503不重试但其他错误重试

**位置**：`app/services/safety_gateway_client.py:130-157`

**问题**：503（服务暂时不可用）恰恰是最应该重试的场景，但当前逻辑对503直接返回E4005错误，而对其他非200响应和TimeoutException/ConnectError反而重试。

**影响**：SG服务短暂过载返回503时，主服务不会重试而是直接拒绝诊断输出。

**重构建议**：对503响应也进入重试循环，与TimeoutException/ConnectError处理一致。

---

### R18. 【P2-一般】check_consistency() 和 check_contraindication() 无重试逻辑

**位置**：`app/services/safety_gateway_client.py:233-297`

**问题**：这两个方法在遇到任何异常时立即返回默认错误响应，而 `check_output()` 有完整的重试+指数退避+超时控制。

**重构建议**：统一客户端所有方法的重试策略，或提取通用请求方法。

---

### R19. 【P2-一般】SafetyGatewayClient 模块级实例化

**位置**：`app/services/safety_gateway_client.py:327`

**问题**：`safety_gateway_client = SafetyGatewayClient()` 在模块导入时即创建，读取的配置值无法在运行时替换。

**重构建议**：改为延迟初始化或通过依赖注入管理生命周期。

---

### R20. 【P2-一般】SafetyGatewayClient 不校验SG服务响应结构

**位置**：`app/services/safety_gateway_client.py:130-133`

**问题**：`check_output()` 收到200响应后直接 `response.json()` 返回，不校验响应体是否包含 `passed`、`consent_status`、`degraded_mode` 等必要字段。

**重构建议**：使用Pydantic模型校验SG响应，格式不匹配时返回E4005。

---

## 十、循环导入风险

### R21. 【P2-一般】__init__.py 急切导入checker的传递性依赖风险

**位置**：`safety_gateway/__init__.py`

**问题**：`__init__.py` 直接导入 `checker`，而 `checker.py` 传递性导入了8个模块。`import safety_gateway` 会触发整个SG模块树的加载。

**重构建议**：`__init__.py` 仅导出顶层API（如 `get_safety_gateway_checker`），不导入内部实现。

---

### R22. 【P2-一般】main.py 多处延迟导入暗示结构耦合

**位置**：`safety_gateway/main.py:197,210,223,297`

**问题**：4个端点处理函数内部使用延迟导入 `from safety_gateway.checker import _get_health_checker`，且 `_get_health_checker` 是以下划线开头的"内部"函数，被外部模块频繁使用违反封装原则。

**重构建议**：将 `_get_health_checker` 重命名为公开API，或在模块顶部导入。

---

## 十一、测试覆盖缺口

### R23. 【P1-重要】全部2753行测试集中在单一文件

**位置**：`tests/unit/sg/test_safety_gateway.py`

**问题**：40+个测试类、2753行测试代码全部集中在一个文件中，而源代码有17个独立模块。

**重构建议**：按源模块拆分测试文件：
```
tests/unit/sg/
├── test_desensitization.py
├── test_contraindication.py
├── test_threshold.py
├── test_checker.py
├── test_circuit_breaker.py
├── test_health.py
├── test_degraded_output.py
├── test_consent_checker.py
├── test_validation_result.py
├── test_sg_client.py
└── test_integration.py
```

---

### R24. 【P1-重要】sg_db.py 和 redis_pool.py 完全无测试覆盖

**问题**：`sg_db.py` 的 `_LazyAsyncEngine` 代理模式、`get_db_session()` 的commit/rollback行为，以及 `redis_pool.py` 的连接池管理、健康检查重置逻辑，均无任何单元测试。

**重构建议**：补充关键路径测试，特别是 `_LazyAsyncEngine.__getattr__` 代理、`get_db_session()` 事务边界、Redis连续失败3次重置。

---

### R25. 【P2-一般】sg_validator.py 13/15条验证规则无测试

**问题**：`validate_sg_config()` 包含15条验证规则，但测试中只有2条被间接覆盖。

**重构建议**：为每条验证规则添加参数化测试。

---

## 十二、错误处理与边界情况

### R26. 【P1-重要】_check_fusion_layer() 通用异常不设 _fusion_available=False

**位置**：`safety_gateway/health.py:441-444`

**问题**：`TimeoutException` 和 `ConnectError` 都设置了 `self._fusion_available = False`，但通用 `Exception` 分支没有。如果融合层之前是健康的，发生意外异常后 `_fusion_available` 仍保持 `True`，与实际检查结果不一致。

**重构建议**：在通用Exception分支也设置 `self._fusion_available = False`。

---

### R27. 【P2-一般】consent_checker._retry_query() 对所有Exception重试

**位置**：`safety_gateway/consent_checker.py:104`

**问题**：第二个 `except` 分支捕获所有 `Exception`，包括 `AttributeError`、`TypeError` 等编程错误，这些不应被重试。

**重构建议**：限制重试的异常类型为数据库相关异常（`SQLAlchemyError`、`asyncio.TimeoutError`），编程错误快速失败。

---

### R28. 【P2-一般】perform_full_check() 不校验 output_data 结构

**位置**：`safety_gateway/checker.py:612-613`

**问题**：`output_data` 缺少 `conclusions` 键时，阈值校验被静默跳过（`conclusions = output_data.get("conclusions", [])` → 空列表 → 无阈值检查）。

**重构建议**：对关键键（`organ`、`conclusions`）进行存在性校验，缺失时记录warning日志。

---

### R29. 【P2-一般】check_consistency() 失败返回语义矛盾结果

**位置**：`app/services/safety_gateway_client.py:257`

**问题**：失败时返回 `consistent=False` 但 `conflicts=[]`，语义矛盾。

**重构建议**：失败时返回 `consistent=None` 或在message中明确说明"校验请求失败，无法确定一致性"。

---

### R30. 【P2-一般】threshold订阅固定5s重试 vs 规则订阅指数退避

**位置**：`safety_gateway/threshold.py:454-456` vs `safety_gateway/main.py:130-153`

**问题**：两者实现相同的Redis Pub/Sub订阅模式，但重连策略不一致。

**重构建议**：统一为指数退避策略（O13通用Pub/Sub框架可解决）。

---

## 八、风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| 重构引入回归bug | 中 | 高（医疗安全系统） | 每个Phase完成后运行全部227个单元测试 |
| 脱敏引擎共享框架破坏异构冗余 | 低 | 高 | 基类仅共享框架，正则模式仍各自定义 |
| checker拆分改变校验顺序 | 低 | 高 | 保持phase注册顺序与原逻辑一致 |
| 单例注册表引入全局状态 | 中 | 中 | 注册表仅在SG进程内使用，不跨进程 |
| 配置验证迁移遗漏规则 | 低 | 中 | 对比sg_validator.py逐条迁移，添加测试 |
| 白名单不一致导致数据泄漏 | 低 | 高 | 重构后统一白名单来源，添加集成测试验证 |
| _check_fusion_layer异常处理遗漏 | 中 | 高 | 修复R26后添加测试覆盖 |
