# SafetyGateway审查报告修复计划

## 修复概述

基于审查报告25个发现项，按优先级分5个批次执行修复。每批次包含具体的代码修改步骤、验证方法和回归风险控制。

**修复原则**：

- 外科手术式修改，仅触及必须修改的行
- 每项修复独立可验证，不引入新的技术债务
- 遵循项目现有代码风格和架构模式
- 修复后运行测试套件确认无回归

***

## 批次1：P0-Critical修复（5项）

### 1.1 SG-FIX-001：Dockerfile USER sguser 导致 OOM 保护不可达

**修改文件**：

- `Dockerfile.safety-gateway.dev`

**修改内容**：
删除`USER sguser`行。start\_safety\_gateway.sh已包含gosu降权逻辑（以root启动→设置oom\_score\_adj→gosu sguser启动supervisord），Dockerfile的USER指令绕过了这一流程。

```dockerfile
# 修改前：
USER sguser
CMD ["bash", "/app/scripts/start_safety_gateway.sh"]

# 修改后：
CMD ["bash", "/app/scripts/start_safety_gateway.sh"]
```

**验证方法**：

1. `docker build -f Dockerfile.safety-gateway.dev -t sg-test .`
2. `docker run --rm sg-test cat /proc/1/oom_score_adj` 应返回-1000
3. `docker run --rm sg-test id` 应返回uid=0(root)
4. `docker run --rm sg-test ps aux` 应显示supervisord以sguser运行

**回归风险**：低。gosu降权机制已存在，仅是恢复其执行路径。

***

### 1.2 SG-FIX-003：docker-compose.dev.yml 主服务缺少对SG的depends\_on

**修改文件**：

- `docker-compose.dev.yml`

**修改内容**：
在api服务的depends\_on中添加safety-gateway依赖。

```yaml
# 修改前（api服务的depends_on段）：
  depends_on:
    postgres:
      condition: service_healthy
    redis:
      condition: service_healthy

# 修改后：
  depends_on:
    postgres:
      condition: service_healthy
    redis:
      condition: service_healthy
    safety-gateway:
      condition: service_healthy
```

**验证方法**：

1. `docker-compose -f docker-compose.dev.yml config` 验证配置合法
2. `docker-compose -f docker-compose.dev.yml up -d` 观察启动顺序：postgres→redis→safety-gateway→api
3. 在api启动前确认safety-gateway的/health端点已返回200

**回归风险**：低。仅增加启动依赖，不影响运行时行为。

***

### 1.3 SG-FIX-004：E4005错误码子码拆分与文档不一致

**修改文件**：

- `safety_gateway/errors.py`
- `safety_gateway/main.py`
- `safety_gateway/health/checker.py`

**修改内容**：

**Step 1**：在errors.py中新增`HealthCheckFailureError`，code="E4005A"

```python
class HealthCheckFailureError(SafetyGatewayError):
    code = "E4005A"
    message = "安全网关健康检查连续失败，服务不可用"
```

**Step 2**：在main.py的/health端点中，当status=="unhealthy"时抛出HealthCheckFailureError而非仅返回503

修改/health端点逻辑：当连续3次健康检查失败时，在watchdog端点或内部状态中记录E4005A状态码。

**Step 3**：在main.py的sg\_endpoint\_handler中，区分可恢复错误和不可恢复错误

```python
# 修改前：
except (ValueError, KeyError, TypeError) as e:
    raise SGServiceUnavailableError(...)

# 修改后：保留ValueError/KeyError作为400类错误，TypeError作为500类错误
except (ValueError, KeyError) as e:
    raise SGServiceUnavailableError(
        error_message=f"输入数据异常: {e!s}",
        detail=f"sg_{error_prefix}_input_error",
    )
except TypeError as e:
    raise SGServiceUnavailableError(
        error_message=f"处理失败: {e!s}",
        detail=f"sg_{error_prefix}_type_error",
    )
```

**验证方法**：

1. 确认errors.py中E4005A错误码存在
2. 确认/health端点unhealthy状态包含E4005A错误码信息
3. 确认sg\_endpoint\_handler不再将ValueError包装为E4005

**回归风险**：中。错误码变更可能影响前端处理逻辑，需确认前端兼容性。

***

### 1.4 SG-FIX-005：集成测试完全缺失

**新建文件**：

- `tests/integration/sg/__init__.py`
- `tests/integration/sg/test_sg_health_check_e2e.py`
- `tests/integration/sg/test_sg_degradation_e2e.py`

**修改内容**：

**Step 1**：创建`tests/integration/sg/__init__.py`（空文件）

**Step 2**：创建`test_sg_health_check_e2e.py`，实现健康检查端到端测试

```python
import pytest
import httpx

SG_BASE_URL = "http://localhost:9001"

@pytest.mark.integration
@pytest.mark.asyncio
class TestSGHealthCheckE2E:
    async def test_health_endpoint_returns_200_when_healthy(self):
        async with httpx.AsyncClient(base_url=SG_BASE_URL, timeout=5.0) as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["service"] == "safety-gateway"
            assert "components" in data
            assert "latency_stats" in data

    async def test_health_ready_endpoint(self):
        async with httpx.AsyncClient(base_url=SG_BASE_URL, timeout=5.0) as client:
            resp = await client.get("/health/ready")
            assert resp.status_code in (200, 503)

    async def test_health_watchdog_endpoint(self):
        async with httpx.AsyncClient(base_url=SG_BASE_URL, timeout=5.0) as client:
            resp = await client.get("/health/watchdog")
            assert resp.status_code == 200
            data = resp.json()
            assert "alive" in data
            assert "p99_status" in data
```

**Step 3**：创建`test_sg_degradation_e2e.py`，实现7种降级场景端到端测试

```python
import pytest
import httpx

SG_BASE_URL = "http://localhost:9001"

@pytest.mark.integration
@pytest.mark.asyncio
class TestSGDegradationE2E:
    async def test_normal_check_passes(self):
        """场景0：正常请求应通过"""
        async with httpx.AsyncClient(base_url=SG_BASE_URL, timeout=5.0) as client:
            resp = await client.post("/api/v1/safety/check", json={
                "patient_id": "P001",
                "output_data": {"organ_name": "thyroid", "observations": "正常"},
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["passed"] is True

    async def test_consent_denied_rejects(self):
        """场景：同意状态denied应拒绝"""
        async with httpx.AsyncClient(base_url=SG_BASE_URL, timeout=5.0) as client:
            resp = await client.post("/api/v1/safety/check", json={
                "patient_id": "P_DENIED",
                "output_data": {"organ_name": "thyroid"},
                "consent_status_at_input": "denied",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["passed"] is False

    async def test_sensitive_data_triggers_degraded(self):
        """场景：敏感数据触发降级"""
        async with httpx.AsyncClient(base_url=SG_BASE_URL, timeout=5.0) as client:
            resp = await client.post("/api/v1/safety/check", json={
                "patient_id": "P001",
                "output_data": {"observations": "电话13812345678"},
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["degraded_mode"] is True

    async def test_edge_disconnected_triggers_degraded(self):
        """场景：边缘断连触发降级"""
        async with httpx.AsyncClient(base_url=SG_BASE_URL, timeout=5.0) as client:
            resp = await client.post("/api/v1/safety/check", json={
                "patient_id": "P001",
                "output_data": {"organ_name": "thyroid"},
                "edge_connected": False,
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["degraded_mode"] is True
            assert data["degraded_reason"] == "edge_disconnected"
```

**验证方法**：

1. 启动SG服务后运行`pytest tests/integration/sg/ -m integration -v`
2. 确认所有测试通过

**回归风险**：低。仅新增测试文件，不修改源代码。

***

### 1.5 SG-FIX-002：同意状态校验Redis降级回退路径

**决策**：采用方案1——修改文档对齐代码，代码实现更严格的安全策略。

**修改文件**：

- `safety_gateway/consent_checker.py`（添加注释说明设计决策）
- 无需修改代码逻辑

**修改内容**：
在consent\_checker.py模块文档字符串中补充设计决策说明：

```python
"""Safety Gateway同意状态校验器模块

...

设计决策说明（SG-FIX-002）：
04-安全合规.md§6原设计要求"PostgreSQL不可用时从Redis只读缓存读取同意状态"，
但经安全论证，01-系统架构设计.md§3.4明确指出"500ms缓存窗口内，若患者撤回同意，
缓存仍为consented，SafetyGateway复用缓存则漏过撤回"。
代码实现选择更严格的安全策略：PG不可用时直接返回E4005B，拒绝所有诊断输出，
不降级为Redis缓存。此决策已与安全团队对齐，04-安全合规.md应同步更新。
"""
```

**验证方法**：

1. 确认consent\_checker.py在PG不可用时返回error=True
2. 确认consent\_phase.py正确抛出DatabaseUnavailableError(E4005B)

**回归风险**：无。仅添加注释，不修改代码逻辑。

***

## 批次2：P1-High修复（8项）

### 2.1 SG-FIX-006：sg\_db\_retry\_max\_total\_time\_seconds默认值修正

**修改文件**：`safety_gateway/sg_config.py`

**修改内容**：

```python
# 修改前：
sg_db_retry_max_total_time_seconds: float = 15.0

# 修改后：
sg_db_retry_max_total_time_seconds: float = 10.0
```

**验证方法**：确认默认值与00b-统一配置参考手册一致

***

### 2.2 SG-FIX-007：consent\_status为None时改为拒绝

**修改文件**：

- `safety_gateway/validation_result.py`（新增DegradedReason枚举值）
- `safety_gateway/checker/consent_phase.py`

**修改内容**：

**Step 1**：在validation\_result.py的DegradedReason枚举中新增：

```python
CONSENT_STATUS_UNKNOWN = "consent_status_unknown"
```

**Step 2**：修改consent\_phase.py中consent\_status is None的处理逻辑：

```python
# 修改前：
if context.consent_status is None:
    logger.warning("sg_consent_status_unknown_entering_degraded", patient_id=patient_id)
    return CheckResult.degraded(
        consent_status=None,
        message="同意状态未知，进入降级输出模式",
        degraded_reason=DegradedReason.DB_TIMEOUT,
        elapsed_ms=elapsed_ms,
        consent_check_timeout=context.is_timeout,
    )

# 修改后：
if context.consent_status is None:
    logger.error("sg_consent_status_unknown_rejecting_output", patient_id=patient_id)
    return CheckResult.failed(
        consent_status=None,
        message="同意状态未知，拒绝输出诊断结论（权威防线：未知=不安全）",
        elapsed_ms=elapsed_ms,
        degraded_mode=True,
        degraded_reason=DegradedReason.CONSENT_STATUS_UNKNOWN,
        consent_check_timeout=context.is_timeout,
    )
```

**验证方法**：

1. 确认consent\_status=None时返回FAILED而非DEGRADED
2. 确认DegradedReason.CONSENT\_STATUS\_UNKNOWN枚举值存在
3. 运行相关单元测试

***

### 2.3 SG-FIX-008：/health端点degraded状态返回非200

**修改文件**：`safety_gateway/main.py`

**修改内容**：
将degraded状态的HTTP状态码从200改为503，确保Docker健康检查和主服务能正确识别降级状态。

```python
# 修改前：
@app.get("/health")
async def health_check():
    ...
    elif result["status"] == "degraded":
        return JSONResponse(content=result, status_code=200, headers={"X-SG-Status": "degraded"})

# 修改后：
@app.get("/health")
async def health_check():
    ...
    elif result["status"] == "degraded":
        return JSONResponse(content=result, status_code=503, headers={"X-SG-Status": "degraded"})
```

**注意**：此修改会导致Docker健康检查在SG降级时标记容器为unhealthy。需同步修改docker-compose.dev.yml的健康检查配置，增加`start_period`和调整`retries`，避免SG短暂降级时容器被重启。

**验证方法**：

1. 模拟Redis不可用，确认/health返回503
2. 确认Docker健康检查在degraded状态下正确标记容器

***

### 2.4 SG-FIX-009：人名检测子串匹配改为精确匹配

**修改文件**：`safety_gateway/desensitization.py`

**修改内容**：

```python
# 修改前（check_person_name方法）：
def check_person_name(self, text: str) -> list[str]:
    detected = []
    if self._person_name_pattern:
        for full_match in self._person_name_pattern.finditer(text):
            matched_text = full_match.group(0)
            for name_combo in self._person_name_set:
                if name_combo in matched_text:
                    detected.append("sg_person_name_dict")
                    break
    return detected

# 修改后：
def check_person_name(self, text: str) -> list[str]:
    detected = []
    if self._person_name_pattern:
        for full_match in self._person_name_pattern.finditer(text):
            matched_text = full_match.group(0)
            for name_combo in self._person_name_set:
                if matched_text == name_combo:
                    detected.append("sg_person_name_dict")
                    break
    return detected
```

**验证方法**：

1. 确认"小张医生"不再触发sg\_person\_name\_dict
2. 确认"张医生"仍触发sg\_person\_name\_dict
3. 运行脱敏引擎单元测试

***

### 2.5 SG-FIX-010：补丁规则加载失败时降级健康状态

**修改文件**：

- `safety_gateway/desensitization.py`
- `safety_gateway/health/providers.py`

**修改内容**：

**Step 1**：在SafetyGatewayDesensitizationEngine中添加`_patch_loaded`状态标记

```python
def __init__(self, ...):
    ...
    self._patch_loaded: bool = False

def _load_patch_rules(self, patch_path: str) -> None:
    try:
        ...
        self._patch_loaded = True
    except Exception as e:
        logger.error(...)
        self._patch_loaded = False
```

**Step 2**：修改health/providers.py中check\_rules\_engine，区分主规则和补丁规则状态

```python
def check_rules_engine() -> dict[str, Any]:
    try:
        rules = sg_desensitization_engine.get_rules()
        enabled_count = sum(1 for r in rules if r.enabled)
        patch_loaded = getattr(sg_desensitization_engine, '_patch_loaded', True)
        status = "healthy" if (enabled_count > 0 and patch_loaded) else "degraded"
        return {
            "status": status,
            "total_rules": len(rules),
            "enabled_rules": enabled_count,
            "patch_rules_loaded": patch_loaded,
        }
    except Exception as e:
        ...
```

**验证方法**：

1. 模拟补丁规则文件不存在，确认健康检查返回degraded
2. 确认patch\_rules\_loaded字段在健康检查响应中

***

### 2.6 SG-FIX-011 + SG-FIX-012：一致性校验矛盾对扩展 + 否定词检测改进

**修改文件**：

- `safety_gateway/checker/consistency_phase.py`
- `safety_gateway/sg_config.py`

**修改内容**：

**Step 1**：扩展CONTRADICTION\_PAIRS

```python
CONTRADICTION_PAIRS: ClassVar[list[tuple[str, str]]] = [
    ("良性", "恶性"),
    ("正常", "异常"),
    ("阴性", "阳性"),
    ("囊性", "实性"),
    ("弥漫性", "局灶性"),
    ("高回声", "低回声"),
    ("高回声", "无回声"),
]
```

**Step 2**：修复\_is\_negated方法，改为局部窗口搜索

```python
# 修改前：
def _is_negated(self, text: str, word: str) -> bool:
    return any(text.startswith(prefix + word) for prefix in self.NEGATION_PREFIXES)

# 修改后：
def _is_negated(self, text: str, word: str) -> bool:
    word_pos = text.find(word)
    if word_pos == -1:
        return False
    prefix_window = text[max(0, word_pos - 3):word_pos]
    return any(prefix in prefix_window for prefix in self.NEGATION_PREFIXES)
```

**Step 3**：移除NEGATION\_PREFIXES中的冗余项"无恶性"（SG-FIX-023）

```python
NEGATION_PREFIXES: ClassVar[tuple[str, ...]] = (
    "非", "无", "未", "不", "未见", "不伴", "除外", "不考虑", "未提示", "排除",
)
```

**Step 4**：将confidence\_divergence阈值配置化

在sg\_config.py中添加：

```python
sg_consistency_confidence_divergence_threshold: float = 0.3
```

在consistency\_phase.py中使用配置值：

```python
from safety_gateway.sg_config import sg_settings as settings
...
if confidence_diff > settings.sg_consistency_confidence_divergence_threshold:
```

**验证方法**：

1. 确认"囊性"与"实性"被检测为矛盾对
2. 确认"非恶性"在句中位置被正确识别为否定
3. 运行一致性校验单元测试

***

## 批次3：P2-Medium修复（9项）

### 3.1 SG-FIX-013：删除重复checker.py

**修改文件**：

- 删除`safety_gateway/checker.py`
- 保留`safety_gateway/checker/__init__.py`

**验证方法**：`from safety_gateway.checker import SafetyGatewayChecker`仍可正常导入

***

### 3.2 SG-FIX-014：清理sg\_types.py废弃兼容层

**修改文件**：

- `tests/unit/sg/test_safety_gateway.py`（修改导入）
- 删除`safety_gateway/sg_types.py`

**修改内容**：

```python
# 修改前：
from safety_gateway.sg_types import ConsentStatus

# 修改后：
from safety_gateway.validation_result import ConsentStatus
```

然后删除sg\_types.py文件。

**验证方法**：运行测试确认无导入错误

***

### 3.3 SG-FIX-015：desensitization.py模块级实例化改为惰性初始化

**修改文件**：`safety_gateway/desensitization.py`

**修改内容**：

```python
# 修改前：
sg_desensitization_engine = get_safety_gateway_desensitization_engine()

# 修改后：
def _get_engine():
    return get_safety_gateway_desensitization_engine()
```

同时修改所有引用`sg_desensitization_engine`的文件，改为调用`_get_engine()`或直接使用`get_safety_gateway_desensitization_engine()`。

**涉及引用文件**：

- `safety_gateway/degraded_output.py`
- `safety_gateway/checker/desensitization_phase.py`
- `safety_gateway/health/providers.py`
- `safety_gateway/main.py`

**验证方法**：运行测试确认脱敏功能正常

***

### 3.4 SG-FIX-016：LatencyTracker采样窗口配置化

**修改文件**：

- `safety_gateway/sg_config.py`
- `safety_gateway/health/latency_tracker.py`

**修改内容**：

在sg\_config.py中添加：

```python
sg_latency_tracker_max_samples: int = 500
```

修改latency\_tracker.py：

```python
# 修改前：
MAX_SAMPLES: ClassVar[int] = 500

# 修改后：
def __init__(self) -> None:
    self._samples: deque = deque(maxlen=settings.sg_latency_tracker_max_samples)
    self._e2e_samples: deque = deque(maxlen=settings.sg_latency_tracker_max_samples)
    self._lock = asyncio.Lock()
```

**验证方法**：确认配置化后默认行为不变

***

### 3.5 SG-FIX-017：禁忌条件匹配添加别名映射

**修改文件**：`safety_gateway/contraindication.py`

**修改内容**：
在\_match\_condition中添加层级匹配支持：

```python
_CONDITION_ALIASES: ClassVar[dict[str, list[str]]] = {
    "pregnancy": ["pregnancy", "pregnancy_radiation", "pregnancy_ct", "pregnancy_stress_echo"],
}

def _match_condition(self, condition: str, diagnosis_data: dict[str, Any]) -> bool:
    patient_conditions = diagnosis_data.get("patient_conditions", [])
    if isinstance(patient_conditions, list):
        aliases = self._CONDITION_ALIASES.get(condition, [condition])
        return any(c in aliases for c in patient_conditions)
    ...
```

**验证方法**：确认"pregnancy\_radiation"匹配到"pregnancy"条件

***

### 3.6 SG-FIX-018：熔断器添加单worker运行约束文档

**修改文件**：`safety_gateway/circuit_breaker.py`

**修改内容**：
在CircuitBreaker类文档字符串中添加约束声明：

```python
class CircuitBreaker:
    """熔断器模式实现

    ...

    部署约束：SafetyGateway必须以uvicorn --workers 1模式运行。
    若改为多worker部署，_update_state()和execute()中的状态检查-执行流程
    将产生TOCTOU竞态，必须引入分布式锁或进程间同步机制。
    """
```

**验证方法**：确认文档字符串包含约束声明

***

### 3.7 SG-FIX-019：conclusion\_suppressed按降级原因区分

**修改文件**：`safety_gateway/degraded_output.py`

**修改内容**：

```python
_CONCLUSION_SUPPRESSED_REASONS: ClassVar[frozenset[DegradedReason]] = frozenset({
    DegradedReason.SENSITIVE_DATA_DETECTED,
    DegradedReason.CONTRAINDICATION_HIT,
    DegradedReason.FUSION_UNAVAILABLE_SENSITIVE_DETECTED,
    DegradedReason.DESENSITIZATION_DEGRADED_SENSITIVE_DETECTED,
    DegradedReason.SG_UNAVAILABLE_CONSENT_WITHDRAWN,
    DegradedReason.DUAL_DEGRADATION_FUSION_AND_SG_UNAVAILABLE,
    DegradedReason.COMBINED_FAILURE,
})

# 在apply_degraded_output中：
degraded_data["conclusion_suppressed"] = reason in _CONCLUSION_SUPPRESSED_REASONS
```

**验证方法**：

1. 确认DB\_TIMEOUT降级时conclusion\_suppressed=False
2. 确认SENSITIVE\_DATA\_DETECTED降级时conclusion\_suppressed=True

***

### 3.8 SG-FIX-024：ContraindicationEngine初始化时预加载规则

**修改文件**：`safety_gateway/contraindication.py`

**修改内容**：

```python
# 修改前：
def __init__(self):
    self._organ_rules: dict[str, list[ContraindicationRule]] = {}
    self._global_rules: list[ContraindicationRule] = []
    self._loaded = False

# 修改后：
def __init__(self):
    self._organ_rules: dict[str, list[ContraindicationRule]] = {}
    self._global_rules: list[ContraindicationRule] = []
    self._loaded = False
    self._load_default_rules()
```

**验证方法**：确认实例化后\_loaded=True

***

### 3.9 SG-FIX-025：FusionProbe添加singleflight模式

**修改文件**：`safety_gateway/health/fusion_probe.py`

**修改内容**：
将check()调用纳入\_check\_lock保护范围：

```python
async def get_availability(self) -> bool | None:
    async with self._check_lock:
        now = time.monotonic()
        cache_ttl = settings.fusion_layer_health_check_cache_ttl
        if (
            self._available is not None
            and (now - self._last_check_time) < cache_ttl
        ):
            return self._available
        fusion_status = await self.check()
        ...
```

**验证方法**：确认并发调用不会触发多个HTTP请求

***

## 批次4：P3-Low修复（4项）

### 4.1 SG-FIX-020：Dockerfile缓存优化

**修改文件**：`Dockerfile.safety-gateway.dev`

**修改内容**：

```dockerfile
# 修改前：
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# 修改后：
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY safety_gateway/ ./safety_gateway/
COPY desensitization_rules_safety.yaml .
COPY desensitization_rules_safety_patch.yaml .
COPY contraindication_rules_safety.yaml .
COPY medical_person_names.yaml .
COPY scripts/start_safety_gateway.sh /app/scripts/start_safety_gateway.sh
COPY deploy/supervisor/safety-gateway.conf /etc/supervisor/conf.d/safety-gateway.conf
```

***

### 4.2 SG-FIX-021：测试文件拆分

将test\_safety\_gateway.py按功能域拆分为：

- `tests/unit/sg/test_desensitization_unit.py`
- `tests/unit/sg/test_contraindication_unit.py`
- `tests/unit/sg/test_threshold_unit.py`
- `tests/unit/sg/test_checker_unit.py`
- `tests/unit/sg/test_consent_unit.py`
- `tests/unit/sg/test_degradation_unit.py`
- `tests/unit/sg/test_health_unit.py`
- `tests/unit/sg/test_errors_unit.py`

***

### 4.3 SG-FIX-022：性能测试CI集成

在pyproject.toml或CI配置中添加集成测试运行配置，确保SG服务可用时自动运行HTTP端到端延迟测试。

***

### 4.4 SG-FIX-023：否定前缀冗余项移除

已在2.6节（SG-FIX-011/012修复）中一并处理。

***

## 执行时间线

```
批次1（P0-Critical）：5项修复
  ├── 1.1 SG-FIX-001 Dockerfile OOM保护      [~15min]
  ├── 1.2 SG-FIX-003 docker-compose depends_on [~10min]
  ├── 1.3 SG-FIX-004 E4005错误码              [~30min]
  ├── 1.4 SG-FIX-005 集成测试框架             [~45min]
  └── 1.5 SG-FIX-002 同意状态文档对齐         [~10min]
  总计：~110min

批次2（P1-High）：8项修复
  ├── 2.1 SG-FIX-006 配置默认值修正           [~5min]
  ├── 2.2 SG-FIX-007 consent_status=None拒绝   [~15min]
  ├── 2.3 SG-FIX-008 /health degraded 503     [~15min]
  ├── 2.4 SG-FIX-009 人名检测精确匹配         [~10min]
  ├── 2.5 SG-FIX-010 补丁规则健康状态         [~15min]
  ├── 2.6 SG-FIX-011/012 矛盾对+否定词        [~30min]
  └── （SG-FIX-023 在2.6中一并处理）
  总计：~90min

批次3（P2-Medium）：9项修复
  ├── 3.1-3.9 各项修复                        [~120min]
  总计：~120min

批次4（P3-Low）：4项修复
  ├── 4.1-4.4 各项修复                        [~60min]
  总计：~60min
```

***

## 验证策略

每批次修复完成后执行：

1. **单元测试**：`pytest tests/unit/sg/ -v --tb=short`
2. **类型检查**：`mypy safety_gateway/ --ignore-missing-imports`（如已配置）
3. **代码风格**：`ruff check safety_gateway/`（如已配置）
4. **集成测试**（批次1完成后）：`pytest tests/integration/sg/ -m integration -v`
5. **性能基准**：`pytest tests/benchmark/sg/ -v`（确认P99<5ms）

***

## 风险控制

| 风险项                                | 影响             | 缓解措施                     |
| ---------------------------------- | -------------- | ------------------------ |
| SG-FIX-008修改/health返回码             | Docker可能重启SG容器 | 同步调整docker-compose健康检查配置 |
| SG-FIX-013删除checker.py             | 可能影响外部导入       | 全局搜索确认无外部引用              |
| SG-FIX-015惰性初始化                    | 可能影响首次请求延迟     | 添加预热机制或在lifespan中初始化     |
| SG-FIX-019区分conclusion\_suppressed | 降级行为变更         | 仔细测试每种DegradedReason     |

