# LLM推理轨道 — 全面重构需求分析与优化建议报告

## 一、代码规模概览

| 模块 | 文件 | 行数 | 职责 |
|------|------|------|------|
| 一致性验证 | consistency.py | 1312 | 双引擎一致性验证、分级提取、Wilson置信区间、器官分解、交叉验证、报告生成 |
| 验证配置 | verification_config.py | 729 | 验证配置管理、4器官边界/禁忌词/数值范围默认配置、JSON加载/保存、自适应阈值 |
| GPU监控 | gpu_monitor.py | 693 | GPU内存监控、互斥调度、系统内存分析、VRAM碎片化估算 |
| 推理基类 | base.py | 472 | HTTP推理引擎基类、熔断器、指数退避重试、模型信息缓存 |
| 幻觉检测 | hallucination_detector.py | 465 | 5策略幻觉检测、禁忌词/矛盾分级/医学常识/数值有效性/上下文感知 |
| 漂移检测 | drift_detector.py | 463 | SPC统计过程控制、基线计算/保存/加载、漂移检测与报告 |
| HTTP客户端基类 | http_client_base.py | 440 | HTTP客户端公共基类、GET/POST/重试/熔断/连接池 |
| 基准测试 | benchmark.py | 350 | 模型选型基准测试、敏感性分析、胜者判定 |
| 协议定义 | protocols.py | 249 | 推理引擎ABC、请求/响应模型、错误码、Token统计 |
| vLLM引擎 | vllm.py | 184 | vLLM后端适配器、Chat Completions API |
| 引擎工厂 | factory.py | 119 | 引擎创建/配置、从Settings创建 |
| VRAM碎片化 | vram_fragmentation.py | 109 | VRAM碎片化验证、pynvml集成 |
| llama.cpp引擎 | llama_cpp.py | 134 | llama.cpp后端适配器、Completions API |
| 分级定义 | grading_definitions.py | 90 | 分级正则模式、层级定义、LVEF映射、分级提取 |
| 工具函数 | utils.py | 21 | 百分位数计算 |
| **合计** | **15个源文件** | **~5840** | — |

辅助文件：
| 文件 | 行数 | 职责 |
|------|------|------|
| app/config/settings.py (LLM部分) | ~80 | LLM配置项（引擎类型、端口、超时、熔断等） |
| scripts/vllm_server.py | ~130 | vLLM服务启动脚本 |
| scripts/model_benchmark.py | ~100 | 模型选型基准测试脚本 |
| app/inference/data/*.json | 3文件 | 边界配置、禁忌词、415例一致性测试用例 |

---

## 二、重构需求分析（按严重程度排序）

### R1. 【P0-严重】consistency.py 过度膨胀 — 上帝类反模式

**问题**：`consistency.py`（1312行）是整个LLM轨道最大的文件，承担了过多职责：
- 双引擎一致性验证编排（`verify`、`_verify_single_case`）
- 分级语义等价判断（`grading_semantic_equivalent`、`_check_boundary_match`）
- 文本相似度计算（`text_similarity`、`text_consistency_with_grading`）
- Wilson置信区间计算（`wilson_confidence_interval`）
- 假阳性/假阴性统计（`_compute_false_stats`、`FalseStats`）
- 器官级别统计分解（`_compute_organ_breakdown`）
- 失败分析与建议生成（`_generate_failure_analysis`）
- 报告序列化（`report_to_dict`、`report_to_json`）
- 交叉验证（`cross_validate`、`CrossValidationResult`）
- 三档门控判定（`ConsistencyReport.gate_status`）
- 延迟百分位数统计

**影响**：
- 修改任一统计逻辑都可能影响其他统计
- 测试集中在一个文件中（16个测试类），难以定位失败原因
- 新增统计指标需要修改此文件，违反开闭原则
- `_generate_failure_analysis`（160行）、`_compute_organ_breakdown`（117行）等超长方法难以理解和维护

**重构建议**：将consistency.py拆分为验证编排器+独立统计/分析组件，详见优化建议O1。

---

### R2. 【P0-严重】base.py generate方法过度膨胀 — 单方法上帝函数

**问题**：`BaseHttpInferenceEngine.generate`方法（约188行，L311-L499）包含了完整的推理流程：
- 断路器状态检查与更新
- 请求构建
- 重试循环（含指数退避+抖动）
- HTTP错误处理（状态码判断）
- JSON解析错误处理
- 超时处理
- 连接错误处理
- 通用异常处理
- 结果解析与响应构建

**影响**：
- 修改重试逻辑可能影响断路器逻辑
- 添加新的错误处理策略需要在188行方法中插入代码
- 测试需要覆盖所有分支路径，组合爆炸
- 3-4层嵌套的try/except结构难以阅读

**重构建议**：将generate拆分为多个私有方法，详见优化建议O2。

---

### R3. 【P0-严重】全局单例管理不一致且缺乏统一机制

**问题**：LLM轨道内至少有3处独立的单例实现，风格各异：

| 组件 | 单例实现方式 | 线程安全 | 重置支持 | 位置 |
|------|-------------|---------|---------|------|
| VerificationConfig | 全局变量+threading.Lock | ✅ | ✅ reset | verification_config.py L771-798 |
| HallucinationDetector | 全局变量+threading.Lock | ✅ | ❌ | hallucination_detector.py L528-539 |
| vllm_server.llm | 模块级变量（无延迟） | ❌ | ❌ | scripts/vllm_server.py L12 |

**影响**：
- 每个单例的初始化/重置/线程安全策略不一致
- 测试中需要分别处理每个单例的重置逻辑
- `verification_config`的`config_path`参数只在首次调用时生效，后续调用传入不同路径会被忽略
- `HallucinationDetector`无重置方法，测试间可能泄漏状态

**重构建议**：统一到SG轨道已实现的`SGComponentRegistry`或创建LLM轨道专用注册表，详见优化建议O3。

---

### R4. 【P1-重要】verification_config.py 默认配置硬编码 — 数据与逻辑混合

**问题**：`verification_config.py`（729行）中约440行是硬编码的默认配置数据：
- `get_default_boundary_config`（87行，L208-294）：4个器官的边界对配置
- `get_default_forbidden_terms_config`（357行，L297-654）：4个器官的禁忌词配置
- `get_default_numerical_validation_config`（约50行）：数值验证配置

这些配置数据与配置管理逻辑（加载/保存/验证/单例管理）混在同一文件中。

**影响**：
- 修改默认配置需要在729行文件中定位
- 配置数据无法独立测试和验证
- 新增器官类型需要修改此文件
- 代码审查时配置数据干扰逻辑审查

**重构建议**：将默认配置数据外置为JSON/YAML文件或独立模块，详见优化建议O4。

---

### R5. 【P1-重要】http_client_base.py 三方法重复错误处理 — DRY违反

**问题**：`http_client_base.py`中`_do_get`（L199-287）、`_do_post_json`（L289-377）、`_do_post_multipart`（L379-469）三个方法的错误处理逻辑几乎完全相同：
- `httpx.TimeoutException` → `_create_timeout_error`
- `httpx.ConnectError` → `_create_connection_error`
- `Exception` → `_create_unexpected_error`
- 非200状态码 → `_create_http_error`
- `json.JSONDecodeError` → `_create_json_decode_error`

约70行错误处理代码重复了3次。

**影响**：
- 修改错误处理策略需要同步3处
- 新增HTTP方法（如PUT/DELETE）需要再次复制错误处理逻辑
- 错误响应截断长度`500`在3处重复硬编码

**重构建议**：提取错误处理为装饰器或模板方法，详见优化建议O5。

---

### R6. 【P1-重要】幻觉检测器与一致性验证器对全局配置的运行时紧耦合

**问题**：
- `HallucinationDetector`（hallucination_detector.py L419）在`__init__`中默认调用`get_verification_config()`获取全局单例
- 所有`BaseDetector`子类（L66-67）也在`__init__`中默认获取全局配置
- `ConsistencyReport.gate_status`属性（consistency.py L356-358）在运行时调用`get_verification_config()`
- `ModelBenchmark.__init__`（benchmark.py L116-117）通过延迟导入获取全局配置

**影响**：
- 无法在测试中为不同场景使用不同阈值
- `ConsistencyReport`的行为依赖于全局状态，无法隔离测试
- 修改配置需要通过全局单例，无法实现请求级别的配置覆盖

**重构建议**：通过依赖注入替代全局单例依赖，详见优化建议O6。

---

### R7. 【P1-重要】consistency.py 自适应阈值逻辑完全无效

**问题**：`ConsistencyReport.gate_status`属性（consistency.py L368-378）中的自适应阈值逻辑：
```python
for at in config.adaptive_thresholds:
    rates = []  # 始终为空列表！
    if at.metric_name in ("grading_consistency_rate", ...):
        rates = []  # 仍然为空！
    adjusted = compute_adaptive_threshold(at, rates)  # rates为空时直接返回base_threshold
```
`rates`始终为空列表，`compute_adaptive_threshold`在`rates`为空时直接返回`base_threshold`，因此自适应阈值调整逻辑完全无效。L369-371的`if`判断无任何作用。

**影响**：
- 自适应阈值功能名存实亡，给维护者造成误解
- `AdaptiveThresholdConfig`标注为"预留接口，尚未集成到验证流程"，但实际已被调用（只是无效）
- 无法根据历史数据动态调整验证阈值

**重构建议**：要么正确实现自适应阈值（传入历史数据），要么移除无效代码，详见优化建议O7。

---

### R8. 【P1-重要】多处宽泛异常捕获可能掩盖严重错误

**问题**：LLM轨道中至少有7处宽泛的`except Exception`：

| 位置 | 文件 | 行为 |
|------|------|------|
| base.py L470 | generate方法 | 捕获所有异常转为InferenceError |
| consistency.py L724/L746 | _verify_single_case | 吞掉引擎错误，记录到error字段 |
| benchmark.py L150 | warmup | `except Exception: pass` 完全静默 |
| gpu_monitor.py L248/273/296 | 内存读取 | 仅warning，返回空快照 |
| drift_detector.py L171/L224 | 基线保存/加载 | 仅error日志，返回False/None |
| verification_config.py L750/L766 | 配置加载/保存 | 吞掉所有错误 |
| gpu_monitor.py L707 | GPU指标读取 | 仅warning，返回空快照 |

**最严重的问题**：
- `consistency.py`中两个引擎都失败时，空结果对（`text=""`）会被当作有效结果参与统计，可能导致一致率虚高
- `benchmark.py`的warmup异常完全静默，服务不可用时仍会执行基准测试
- `base.py`的`except Exception`可能捕获`KeyboardInterrupt`等不应被捕获的异常

**重构建议**：细化异常处理，区分可恢复/不可恢复错误，详见优化建议O8。

---

### R9. 【P2-一般】hallucination_detector.py 重复检测逻辑

**问题**：
- `ForbiddenTermsDetector.detect`（L96-136）中主术语检测（L103-L117）与变体检测（L119-L134）逻辑几乎完全重复
- `ContextAwareDetector`中`_check_description_conclusion_conflict`（L335-355）与`_check_cardiac_description_conclusion_conflict`（L368-388）结构完全一致
- `_check_overreaction`（L357-366）与`_check_cardiac_overreaction`（L390-399）也是相同模式

**影响**：
- 修改检测逻辑需要同步多处
- 新增器官类型需要复制检测代码

**重构建议**：提取参数化检测方法，详见优化建议O9。

---

### R10. 【P2-一般】gpu_monitor.py 平台内存读取器重复代码

**问题**：`WindowsMemoryReader`（L258-280）和`DarwinMemoryReader`（L283-303）的`read_memory`实现几乎完全相同，都使用`psutil.virtual_memory()`，仅Windows版本多读取了`cached_mb`和`buffers_mb`。两者的`_log_warning`方法也完全重复。

**影响**：
- 新增平台支持需要复制代码
- 修改内存读取逻辑需要同步多处

**重构建议**：提取基类或参数化工厂方法，详见优化建议O10。

---

### R11. 【P2-一般】11处硬编码魔术数字

**问题**：LLM轨道中至少有11处硬编码的魔术数字：

| 位置 | 值 | 含义 |
|------|-----|------|
| base.py L142/150/165/175等 | `"retry_after_5s"`/`"no_retry"` | 重试策略字符串 |
| base.py L351, http_client_base.py L260/350/442 | `500` | 错误响应截断长度 |
| consistency.py L553/554/883 | `0.5`/`0.8` | 文本相似度阈值 |
| consistency.py L423/431/440/449 | `10` | 偏差百分比阈值 |
| consistency.py L1264 | `0.005` | 临床边界差异率阈值 |
| drift_detector.py L275/280 | `1.96`/`2.576` | Wilson z值 |
| gpu_monitor.py L110/716 | `22048.0` | 内存预算限制 |
| vram_fragmentation.py L133 | `0.3` | 碎片化估算系数 |
| consistency.py L700/706, benchmark.py L238-244 | `512`/`0.0`/`0.05`/`0.95` | 验证请求参数 |
| hallucination_detector.py L80/81/139 | `50` | 上下文窗口大小 |
| benchmark.py L304 | `0.05` | 幻觉率安全阈值 |

**重构建议**：提取为命名常量或配置项，详见优化建议O11。

---

### R12. 【P2-一般】vram_fragmentation.py 在async函数中使用同步sleep

**问题**：`vram_fragmentation.py` L78在`async def verify`方法中使用了同步的`time.sleep(remaining)`，会阻塞整个事件循环。

**影响**：
- 在异步环境中阻塞事件循环，影响其他协程的执行
- 可能导致超时检测不准确

**重构建议**：替换为`await asyncio.sleep(remaining)`，详见优化建议O12。

---

### R13. 【P2-一般】gpu_monitor.py acquire_with_timeout 逻辑错误

**问题**：`GPUScheduleStrategy.acquire_with_timeout`（gpu_monitor.py L474-483）：
```python
if resource_type == "llm":
    await asyncio.wait_for(self._llm_available.wait(), timeout=remaining)
    async with self._lock:
        if self._llama_cpp_active:  # release_llm已将其设为False，此条件永远不成立
            return True
```
当`_llm_available` Event被set时（在`release_llm`中），代码尝试获取锁并检查`_llama_cpp_active`。但`release_llm`会将`_llama_cpp_active`设为`False`，所以这个检查永远不会返回True。

**影响**：
- GPU资源释放后无法被等待的请求正确获取
- 可能导致LLM推理请求超时失败

**重构建议**：修复资源获取逻辑，详见优化建议O13。

---

### R14. 【P2-一般】consistency.py 性能浪费 — 重复排序

**问题**：`_compute_organ_breakdown`（consistency.py L1014-1017）对同一列表调用了两次`sorted()`：
```python
organ_latency_a_p50 = _percentile(sorted(organ_latencies_a), 50)
organ_latency_a_p99 = _percentile(sorted(organ_latencies_a), 99)
```
对每个器官的延迟列表都排序了2次（engine_a + engine_b = 4次排序/器官），4个器官共16次冗余排序。

**重构建议**：排序一次后复用排序结果，详见优化建议O14。

---

### R15. 【P3-低】factory.py 参数传递冗余

**问题**：`create_inference_engine_from_settings`（factory.py L77-124）中每个参数都使用了`x if x is not None else settings.x`的模式重复了约20次，且`create_inference_engine`本身的参数列表长达17个。

**重构建议**：使用配置对象模式替代逐参数传递，详见优化建议O15。

---

### R16. 【P3-低】consistency.py 术语不一致

**问题**：`cross_validate`方法中`CrossValidationResult`的字段名为`accuracy_diff`，但实际计算的是`grading_consistency_rate`的差异，而非准确率（accuracy）。日志中也使用了`train_accuracy`/`validation_accuracy`，术语不一致可能导致混淆。

**重构建议**：统一术语为`consistency_diff`/`train_consistency`/`validation_consistency`，详见优化建议O16。

---

## 三、优化建议（按优先级排序）

### O1. 【P0-严重】consistency.py 拆分 — 验证编排器+独立统计组件

**目标**：将1312行的consistency.py拆分为职责清晰的子模块。

**拆分方案**：

```
app/inference/consistency/
├── __init__.py              # 统一导出（保持向后兼容）
├── verifier.py              # ConsistencyVerifier 验证编排器
├── report.py                # ConsistencyReport, ConsistencyPair, GradingResult, FalseStats
├── statistics.py            # wilson_confidence_interval, text_similarity, grading_semantic_equivalent
├── organ_breakdown.py       # _compute_organ_breakdown, _compute_false_stats
├── failure_analysis.py      # _generate_failure_analysis
├── cross_validation.py      # cross_validate, CrossValidationResult
├── gate.py                  # gate_status 判定逻辑 + 自适应阈值
└── serialization.py         # report_to_dict, report_to_json
```

**关键设计**：
- `ConsistencyVerifier`仅保留编排逻辑（`verify`、`_verify_single_case`）
- 统计计算函数移入`statistics.py`，作为纯函数便于测试
- `ConsistencyReport.gate_status`移入`gate.py`，接收配置参数而非依赖全局单例
- `__init__.py`重新导出所有公共接口，确保现有导入路径不受影响

**风险与回滚**：
- 风险：拆分后导入路径变化可能影响外部调用
- 回滚：保留原`consistency.py`作为薄代理层，逐步迁移
- 验证：运行全量LLM单元测试+集成测试确认无回归

---

### O2. 【P0-严重】base.py generate方法拆分 — 模板方法+策略模式

**目标**：将188行的`generate`方法拆分为可独立测试和替换的子步骤。

**拆分方案**：

```python
class BaseHttpInferenceEngine(BaseHttpClient, InferenceEngine, ABC):
    async def generate(self, request: InferenceRequest) -> InferenceResponse:
        self._check_circuit_breaker()
        payload = self._build_payload(request)
        response_data = await self._execute_with_retry(payload)
        return self._parse_response(response_data, request)

    def _check_circuit_breaker(self) -> None:
        if self._circuit_breaker_state == CircuitBreakerState.OPEN:
            ...

    async def _execute_with_retry(self, payload: dict) -> dict:
        for attempt in range(self._max_retries + 1):
            try:
                return await self._single_request(payload)
            except InferenceError as e:
                await self._handle_retryable_error(e, attempt)
        ...

    async def _single_request(self, payload: dict) -> dict:
        response = await self._do_post_json(self._generate_endpoint(), payload)
        return response

    async def _handle_retryable_error(self, error: InferenceError, attempt: int) -> None:
        if not error.retryable or attempt == self._max_retries:
            raise error
        await self._wait_retry(attempt)
```

**关键设计**：
- `_check_circuit_breaker`：断路器检查（纯判断逻辑）
- `_execute_with_retry`：重试循环（含退避策略）
- `_single_request`：单次HTTP请求
- `_handle_retryable_error`：可重试错误处理
- 重试策略字符串提取为枚举`RetryStrategy`

**风险与回滚**：
- 风险：拆分可能改变异常传播路径
- 回滚：保留原方法作为注释参考，逐步替换
- 验证：对比拆分前后的异常处理行为

---

### O3. 【P0-严重】统一单例管理 — LLM组件注册表

**目标**：将LLM轨道的3处独立单例实现统一到注册表模式。

**方案**：

```python
class LLMComponentRegistry:
    def __init__(self) -> None:
        self._instances: dict[str, Any] = {}
        self._factories: dict[str, Callable[[], Any]] = {}
        self._lock = threading.Lock()

    def register(self, name: str, factory: Callable[[], Any]) -> None: ...
    def get(self, name: str) -> Any: ...
    def reset(self, name: str | None = None) -> None: ...
    def override(self, name: str, instance: Any) -> None: ...
```

**迁移清单**：
1. `VerificationConfig` → `registry.register("verification_config", lambda: _create_config())`
2. `HallucinationDetector` → `registry.register("hallucination_detector", HallucinationDetector)`
3. `vllm_server.llm` → `registry.register("vllm_llm", lambda: LLM(...))`

**关键设计**：
- 与SG轨道的`SGComponentRegistry`保持一致的API
- 支持`override`用于测试注入
- 支持`reset`用于测试清理

**风险与回滚**：
- 风险：迁移过程中可能遗漏某些获取路径
- 回滚：保留原获取函数作为兼容层
- 验证：搜索所有`get_verification_config`/`get_default_detector`调用点

---

### O4. 【P1-重要】verification_config.py 默认配置外置

**目标**：将约440行的硬编码配置数据外置为JSON文件。

**方案**：

```
app/inference/data/
├── boundary_config.json          # 已存在
├── forbidden_terms.json          # 已存在
├── numerical_validation.json     # 新增：数值验证配置
└── default_verification.json     # 新增：完整默认验证配置
```

`verification_config.py`瘦身至约290行（仅保留配置管理逻辑）：
- `VerificationConfig`等Pydantic模型定义
- `load_config_from_json`/`save_config_to_json`
- `get_verification_config`单例管理
- `compute_adaptive_threshold`

**关键设计**：
- `get_default_verification_config`改为从JSON文件加载
- 保留代码内默认值作为JSON文件缺失时的回退
- JSON文件与Pydantic模型双向验证

**风险与回滚**：
- 风险：JSON文件缺失或格式错误导致启动失败
- 回滚：代码内默认值作为回退，确保零配置可运行
- 验证：测试JSON文件缺失/损坏/格式错误的场景

---

### O5. 【P1-重要】http_client_base.py 错误处理去重

**目标**：消除3个`_do_*`方法中约70行重复的错误处理代码。

**方案A — 模板方法**：
```python
async def _execute_request(self, method: str, url: str, **kwargs) -> dict:
    try:
        response = await self._client.request(method, url, **kwargs)
        if response.status_code != 200:
            raise self._create_http_error(response)
        try:
            return response.json()
        except json.JSONDecodeError as e:
            raise self._create_json_decode_error(str(e), response.text[:self._max_error_body_length])
    except httpx.TimeoutException as e:
        raise self._create_timeout_error(str(e))
    except httpx.ConnectError as e:
        raise self._create_connection_error(str(e))
    except Exception as e:
        raise self._create_unexpected_error(str(e))

async def _do_get(self, path: str, params: dict | None = None) -> dict:
    return await self._execute_request("GET", self._base_url + path, params=params)

async def _do_post_json(self, path: str, data: dict) -> dict:
    return await self._execute_request("POST", self._base_url + path, json=data)
```

**方案B — 装饰器**：
```python
def _handle_http_errors(func):
    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        try:
            return await func(self, *args, **kwargs)
        except httpx.TimeoutException as e: ...
        except httpx.ConnectError as e: ...
        except Exception as e: ...
    return wrapper
```

**推荐方案A**（模板方法），因为错误处理逻辑与请求执行紧密耦合，模板方法更直观。

**关键设计**：
- 魔术数字`500`提取为`_max_error_body_length`类属性
- 三个`_do_*`方法简化为`_execute_request`的薄包装

---

### O6. 【P1-重要】依赖注入替代全局单例依赖

**目标**：消除`HallucinationDetector`和`ConsistencyReport`对全局配置单例的运行时依赖。

**方案**：

```python
class HallucinationDetector:
    def __init__(self, config: VerificationConfig | None = None) -> None:
        self._config = config or get_verification_config()  # 延迟回退到全局

class ConsistencyReport:
    def __init__(self, ..., config: VerificationConfig | None = None) -> None:
        self._config = config

    @property
    def gate_status(self) -> str:
        config = self._config or get_verification_config()  # 延迟回退
        ...
```

**关键设计**：
- 构造函数接受可选的`config`参数
- 默认回退到全局配置（保持向后兼容）
- 测试中可传入自定义配置，无需修改全局状态

**风险与回滚**：
- 风险：需要修改所有构造调用点
- 回滚：默认参数确保现有调用无需修改
- 验证：搜索所有`HallucinationDetector()`和`ConsistencyReport()`调用点

---

### O7. 【P1-重要】自适应阈值逻辑修复或移除

**目标**：解决自适应阈值逻辑完全无效的问题。

**方案A — 正确实现**：
```python
@property
def gate_status(self) -> str:
    config = self._config or get_verification_config()
    for at in config.adaptive_thresholds:
        rates = self._get_historical_rates(at.metric_name)  # 从历史数据获取
        adjusted = compute_adaptive_threshold(at, rates)
        ...
```

**方案B — 移除无效代码**：
- 移除`gate_status`中的自适应阈值循环
- 将`AdaptiveThresholdConfig`标注为`@deprecated`
- 在`verification_config.py`中添加注释说明此功能计划在未来版本实现

**推荐方案B**（移除无效代码），原因：
- 正确实现需要历史数据存储，当前架构不支持
- 无效代码比缺失代码更危险（给维护者造成误解）
- 可在未来版本中重新实现

---

### O8. 【P1-重要】细化异常处理

**目标**：消除宽泛的`except Exception`，区分可恢复/不可恢复错误。

**方案**：

1. **base.py generate**：将`except Exception`改为`except (httpx.HTTPError, ValueError, KeyError)`，让`KeyboardInterrupt`/`SystemExit`正常传播

2. **consistency.py _verify_single_case**：当两个引擎都失败时，标记`pair`为无效结果，不参与统计：
```python
if pair.engine_a_result.error and pair.engine_b_result.error:
    pair.is_valid = False  # 新增字段
    continue
```

3. **benchmark.py warmup**：至少记录warning日志：
```python
except Exception as e:
    logger.warning("Warmup failed: %s", e)
```

4. **drift_detector.py**：区分文件不存在和其他错误：
```python
except FileNotFoundError:
    logger.info("Baseline file not found: %s", path)
    return None
except json.JSONDecodeError as e:
    logger.error("Baseline file corrupted: %s", e)
    return None
except Exception as e:
    logger.error("Failed to load baseline: %s", e)
    return None
```

---

### O9. 【P2-一般】hallucination_detector.py 检测逻辑去重

**目标**：消除`ForbiddenTermsDetector`和`ContextAwareDetector`中的重复检测逻辑。

**方案**：

```python
class ForbiddenTermsDetector(BaseDetector):
    def _detect_term_in_text(self, text: str, term: str, variants: list[str], ...) -> list[HallucinationDetail]:
        results = []
        for t in [term] + variants:
            pos = text.find(t)
            while pos != -1:
                if not self._is_negated(text, pos, len(t)):
                    results.append(...)
                pos = text.find(t, pos + 1)
        return results

    def detect(self, text: str, organ: str) -> list[HallucinationDetail]:
        results = []
        for term_config in organ_terms:
            results.extend(self._detect_term_in_text(text, term_config.term, term_config.variants, ...))
        return results
```

```python
class ContextAwareDetector(BaseDetector):
    def _check_description_conclusion_conflict(self, text: str, organ: str,
                                                 benign_keywords: set, malignant_keywords: set) -> HallucinationDetail | None:
        ...

    def detect(self, text: str, organ: str) -> list[HallucinationDetail]:
        results = []
        if organ == "cardiac":
            results.append(self._check_description_conclusion_conflict(text, organ, CARDIAC_BENIGN, CARDIAC_MALIGNANT))
            results.append(self._check_overreaction(text, organ, CARDIAC_SEVERE))
        else:
            results.append(self._check_description_conclusion_conflict(text, organ, STANDARD_BENIGN, STANDARD_MALIGNANT))
            results.append(self._check_overreaction(text, organ, STANDARD_SEVERE))
        return [r for r in results if r is not None]
```

---

### O10. 【P2-一般】gpu_monitor.py 平台内存读取器统一

**目标**：消除`WindowsMemoryReader`和`DarwinMemoryReader`的重复代码。

**方案**：

```python
class BaseMemoryReader(ABC):
    def read_memory(self) -> MemorySnapshot:
        try:
            return self._read_psutil()
        except Exception as e:
            self._log_warning(str(e))
            return MemorySnapshot()

    def _read_psutil(self) -> MemorySnapshot:
        mem = psutil.virtual_memory()
        snapshot = MemorySnapshot(
            total_mb=mem.total / (1024 * 1024),
            available_mb=mem.available / (1024 * 1024),
            used_percent=mem.percent,
        )
        self._enrich_snapshot(snapshot)
        return snapshot

    def _enrich_snapshot(self, snapshot: MemorySnapshot) -> None:
        pass  # 子类可选覆盖

    def _log_warning(self, msg: str) -> None:
        logger.warning(...)

class WindowsMemoryReader(BaseMemoryReader):
    def _enrich_snapshot(self, snapshot: MemorySnapshot) -> None:
        snapshot.cached_mb = ...
        snapshot.buffers_mb = ...

class DarwinMemoryReader(BaseMemoryReader):
    pass  # 使用默认实现
```

---

### O11. 【P2-一般】魔术数字提取为命名常量

**目标**：将11处硬编码的魔术数字提取为命名常量或配置项。

**方案**：

```python
class InferenceConstants:
    ERROR_BODY_MAX_LENGTH = 500
    RETRY_STRATEGY_WAIT_5S = "retry_after_5s"
    RETRY_STRATEGY_NO_RETRY = "no_retry"
    CONTEXT_WINDOW_SIZE = 50

class ConsistencyConstants:
    TEXT_SIMILARITY_THRESHOLD_MODERATE = 0.5
    TEXT_SIMILARITY_THRESHOLD_HIGH = 0.8
    DEVIATION_PERCENT_THRESHOLD = 10
    BOUNDARY_DIFF_RATE_THRESHOLD = 0.005

class GPUConstants:
    MEMORY_BUDGET_MB = 22048.0
    VRAM_FRAGMENTATION_ESTIMATE_FACTOR = 0.3
```

或更好的方案：将阈值类常量移入`VerificationConfig`，使它们可配置化。

---

### O12. 【P2-一般】vram_fragmentation.py async sleep修复

**目标**：将同步`time.sleep`替换为异步`asyncio.sleep`。

**方案**：
```python
# 修改前
time.sleep(remaining)

# 修改后
await asyncio.sleep(remaining)
```

**验证**：确认`verify`方法已经是`async def`，无需额外修改签名。

---

### O13. 【P2-一般】gpu_monitor.py acquire_with_timeout 逻辑修复

**目标**：修复资源释放后无法重新获取的bug。

**方案**：
```python
async def acquire_with_timeout(self, resource_type: str, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        try:
            async with asyncio.timeout(remaining):
                if resource_type == "llm":
                    await self._llm_available.wait()
                    # 尝试获取资源，而非检查状态
                    async with self._lock:
                        if not self._llama_cpp_active:
                            self._llama_cpp_active = True
                            self._llm_available.clear()
                            return True
                elif resource_type == "retrieval":
                    await self._retrieval_available.wait()
                    async with self._lock:
                        if not self._retrieval_active:
                            self._retrieval_active = True
                            self._retrieval_available.clear()
                            return True
        except asyncio.TimeoutError:
            return False
    return False
```

---

### O14. 【P2-一般】consistency.py 排序优化

**目标**：消除`_compute_organ_breakdown`中的重复排序。

**方案**：
```python
# 修改前
organ_latency_a_p50 = _percentile(sorted(organ_latencies_a), 50)
organ_latency_a_p99 = _percentile(sorted(organ_latencies_a), 99)

# 修改后
sorted_latencies_a = sorted(organ_latencies_a) if organ_latencies_a else []
organ_latency_a_p50 = _percentile(sorted_latencies_a, 50) if sorted_latencies_a else 0.0
organ_latency_a_p99 = _percentile(sorted_latencies_a, 99) if sorted_latencies_a else 0.0
```

---

### O15. 【P3-低】factory.py 配置对象模式

**目标**：简化`create_inference_engine`的17参数列表和`create_inference_engine_from_settings`的20次`x if x is not None else settings.x`。

**方案**：

```python
@dataclass
class EngineConfig:
    engine_type: str
    host: str = "localhost"
    port: int = 8001
    model: str | None = None
    max_tokens: int = 2048
    temperature: float = 0.1
    top_p: float = 0.95
    timeout: float = 30.0
    max_retries: int = 3
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_recovery_timeout: float = 30.0
    circuit_breaker_half_open_max_calls: int = 3
    pool_max_connections: int = 10

def create_inference_engine(config: EngineConfig) -> InferenceEngine:
    if config.engine_type == "llama.cpp":
        return LlamaCppEngine(...)
    elif config.engine_type == "vllm":
        return VLLMEngine(...)

def create_engine_config_from_settings(settings: Settings, **overrides) -> EngineConfig:
    config = EngineConfig(
        engine_type=overrides.get("engine_type", settings.inference_engine),
        host=overrides.get("host", settings.llama_cpp_host),
        ...
    )
    return config
```

---

### O16. 【P3-低】consistency.py 术语统一

**目标**：将`accuracy_diff`/`train_accuracy`/`validation_accuracy`统一为`consistency_diff`/`train_consistency`/`validation_consistency`。

**方案**：
- `CrossValidationResult.accuracy_diff` → `consistency_diff`
- 日志中`train_accuracy` → `train_consistency`
- 日志中`validation_accuracy` → `validation_consistency`
- 添加向后兼容属性：`@property def accuracy_diff(self): return self.consistency_diff`

---

## 四、重构实施路线图

### Phase 1：紧急修复（P0级，影响正确性）

| 编号 | 任务 | 风险 | 预计影响范围 |
|------|------|------|-------------|
| O12 | vram_fragmentation.py async sleep修复 | 低 | 1文件1行 |
| O13 | gpu_monitor.py acquire_with_timeout逻辑修复 | 中 | 1文件1方法 |
| O7 | 自适应阈值无效代码移除 | 低 | 2文件 |

### Phase 2：架构拆分（P0级，影响可维护性）

| 编号 | 任务 | 风险 | 预计影响范围 |
|------|------|------|-------------|
| O1 | consistency.py拆分 | 高 | 1文件→8文件 |
| O2 | base.py generate方法拆分 | 中 | 1文件 |
| O3 | 统一单例管理 | 中 | 3文件 |

### Phase 3：质量提升（P1级，影响可靠性）

| 编号 | 任务 | 风险 | 预计影响范围 |
|------|------|------|-------------|
| O4 | verification_config.py默认配置外置 | 中 | 1文件+新增JSON |
| O5 | http_client_base.py错误处理去重 | 低 | 1文件 |
| O6 | 依赖注入替代全局单例 | 中 | 3文件 |
| O8 | 细化异常处理 | 中 | 6文件 |

### Phase 4：代码清理（P2-P3级，影响可读性）

| 编号 | 任务 | 风险 | 预计影响范围 |
|------|------|------|-------------|
| O9 | hallucination_detector.py检测逻辑去重 | 低 | 1文件 |
| O10 | gpu_monitor.py平台读取器统一 | 低 | 1文件 |
| O11 | 魔术数字提取为常量 | 低 | 6文件 |
| O14 | consistency.py排序优化 | 低 | 1文件 |
| O15 | factory.py配置对象模式 | 低 | 1文件 |
| O16 | consistency.py术语统一 | 低 | 1文件 |

---

## 五、测试覆盖现状与补充建议

### 现状

| 维度 | 评估 |
|------|------|
| 单元测试文件 | 7个，覆盖10/15个源模块 |
| 集成测试文件 | 2个，依赖真实LLM服务，默认跳过 |
| 覆盖率配置 | **缺失** — `pyproject.toml`中`coverage.run.source`仅包含`safety_gateway`，LLM模块覆盖率数据不会被收集 |
| 未覆盖模块 | `grading_definitions.py`、`utils.py`无直接测试 |
| 性能测试 | 缺失 |

### 补充建议

1. **修复覆盖率配置**：在`pyproject.toml`中添加`app.inference`到`coverage.run.source`
2. **补充未覆盖模块测试**：为`grading_definitions.py`和`utils.py`添加单元测试
3. **补充factory.py边界测试**：配置缺失、引擎类型无效等场景
4. **补充base.py重试/熔断测试**：当前仅测试ABC继承，需补充`BaseHttpInferenceEngine`的核心功能测试
5. **添加性能基准测试**：在`tests/benchmark/llm/`下添加独立性能测试

---

## 六、风险应对与回滚策略

### 通用回滚策略

1. **Git分支策略**：每个Phase在独立分支开发，合并前全量测试
2. **向后兼容层**：拆分后的`__init__.py`重新导出所有公共接口
3. **渐进式迁移**：保留原接口作为兼容层，标记为`@deprecated`，逐步迁移
4. **全量回归测试**：每个Phase完成后运行全量LLM单元测试+集成测试

### 高风险操作专项应对

| 操作 | 风险 | 应对 |
|------|------|------|
| consistency.py拆分 | 导入路径变化 | `__init__.py`重新导出+兼容层 |
| base.py generate拆分 | 异常传播路径变化 | 对比拆分前后异常行为测试 |
| 单例统一迁移 | 遗漏获取路径 | 全局搜索调用点+兼容层 |
| acquire_with_timeout修复 | 调度行为变化 | 专项并发测试 |

---

## 七、总结

| 维度 | 现状 | 目标 |
|------|------|------|
| 最大文件行数 | 1312行（consistency.py） | <400行/文件 |
| 最大方法行数 | 188行（generate） | <50行/方法 |
| 单例实现方式 | 3种不同实现 | 统一注册表 |
| 硬编码魔术数字 | 11处 | 0处 |
| 宽泛异常捕获 | 7处 | 0处 |
| 重复代码块 | 6处 | 0处 |
| 测试覆盖率配置 | 未包含LLM模块 | 包含+≥80% |
| 自适应阈值 | 无效代码 | 正确实现或移除 |
| async中同步sleep | 1处 | 0处 |
| 资源获取逻辑bug | 1处 | 0处 |
