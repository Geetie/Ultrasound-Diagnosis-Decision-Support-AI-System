# SG安全网关R8审查修复计划

**来源**：sg-safety-gateway-audit-report-round8.md
**修复项**：9项（P0×2 + P1×4 + P2×3）
**原则**：最小改动、逐项验证、不引入新缺陷

---

## 修复项总览

| 序号 | 修复项 | 优先级 | 文件 | 类型 |
|------|--------|--------|------|------|
| 1 | SG-FIX-R8-01 | P0 | three_layer_desensitization.py | 键名兼容 |
| 2 | SG-FIX-R8-02 | P0 | three_layer_desensitization.py | 语义修正 |
| 3 | SG-FIX-R8-06 | P1 | safety_gateway_client.py | E4005子类型区分 |
| 4 | SG-FIX-R8-04 | P1 | sg_config.py + consent_checker.py | Redis TTL对齐 |
| 5 | SG-FIX-R8-05 | P1 | desensitization_rules_safety.yaml | 规则补充 |
| 6 | SG-FIX-R8-03 | P1 | safety_gateway_client.py | fusion_available清理 |
| 7 | SG-FIX-R8-07 | P2 | three_layer_desensitization.py | 类型注解风格 |
| 8 | SG-FIX-R8-08 | P2 | sg_db.py | 类型注解风格 |
| 9 | SG-FIX-R8-09 | P2 | circuit_breaker.py | 线程安全文档化 |

---

## 修复步骤

### 步骤1：SG-FIX-R8-01 — 三层脱敏键名兼容（P0）

**问题**：SG服务端SafetyCheckResponse返回`degraded_mode`/`passed`，客户端_e4005_response返回`degraded`/`allowed`。three_layer_desensitization.py仅检查`degraded`/`allowed`，遗漏服务端键名。

**文件**：`app/security/three_layer_desensitization.py`

**当前代码（第127行）**：
```python
degraded_mode = check_result.get("degraded", False)
```

**修改为**：
```python
degraded_mode = check_result.get("degraded_mode", check_result.get("degraded", False))
```

**当前代码（第199行）**：
```python
allowed = check_result.get("allowed", True)
```

**修改为**：
```python
allowed = check_result.get("passed", check_result.get("allowed", True))
```

**验证**：
- 确认SG服务端SafetyCheckResponse返回`degraded_mode`和`passed`字段（main.py第48-59行已确认）
- 确认客户端_e4005_response返回`degraded`和`allowed`字段（client.py第285-293行已确认）
- 修改后两种响应格式的键名均可正确读取

---

### 步骤2：SG-FIX-R8-02 — desensitization_degraded语义修正（P0）

**问题**：`layer1_degraded = layer1_result.get("modified", False)`将"脱敏引擎正常替换"等同于"降级"。

**文件**：`app/security/three_layer_desensitization.py`

**当前代码（第163行、第169行）**：
```python
layer1_degraded = False
...
layer1_degraded = layer1_result.get("modified", False)
```

**修改为**：
```python
layer1_degraded = False
...
layer1_degraded = layer1_result.get("degraded", False)
```

**分析**：process_standardization_layer()返回的result字典（第75-79行）包含`modified`键，不包含`degraded`键。因此`layer1_result.get("degraded", False)`将正确返回`False`——标准化层脱敏引擎没有降级状态，只有"是否做了替换"。

**同步修改**：process_standardization_layer()的result字典应添加`degraded`键以保持语义完整：
```python
result = {
    "layer": DesensitizationLayer.STANDARDIZATION,
    "desensitized": desensitized_data,
    "modified": data != desensitized_data,
    "degraded": False,  # 标准化层脱敏引擎无降级状态
}
```

同理，process_fusion_layer()的result字典也应添加：
```python
result = {
    "layer": DesensitizationLayer.FUSION,
    "desensitized": desensitized_data,
    "modified": data != desensitized_data,
    "degraded": False,  # 融合层脱敏引擎降级状态由调用方传入
}
```

**验证**：
- 确认`desensitization_degraded`参数传递给SG服务端时，值正确反映脱敏服务是否降级
- 确认标准化层正常替换敏感数据时，`desensitization_degraded=False`

---

### 步骤3：SG-FIX-R8-06 — 客户端E4005子类型区分（P1，R7-06遗留）

**问题**：`_e4005_response()`统一返回`error_code="E4005"`，不区分E4005A/B/C。

**文件**：`app/services/safety_gateway_client.py`

**当前代码（第131-134行）**：
```python
if response.status_code == 200:
    async with self._lock:
        self._consecutive_failures = 0
    return response.json()
```

**修改为**：
```python
if response.status_code == 200:
    async with self._lock:
        self._consecutive_failures = 0
    return response.json()
elif response.status_code == 503:
    async with self._lock:
        self._consecutive_failures += 1
        current_failures = self._consecutive_failures
    try:
        error_body = response.json()
        sub_code = error_body.get("code", "E4005")
    except Exception:
        sub_code = "E4005"
    if current_failures >= self.FAST_FAIL_THRESHOLD:
        return self._e4005_response(sub_code=sub_code)
    logger.error(
        "safety_gateway_check_failed_503",
        status_code=503,
        error_code=sub_code,
        session_id=session_id,
    )
    return self._e4005_response(sub_code=sub_code)
```

**当前代码（第285-293行）**：
```python
def _e4005_response(self, sub_code: str | None = None) -> dict[str, Any]:
    error_code = sub_code if sub_code else "E4005"
    return {
        "allowed": False,
        "error_code": error_code,
        "consent_status_at_output": "unknown_blocked",
        "degraded": True,
        "message": "安全校验服务不可用，请转为人工诊断",
    }
```

**修改为**：
```python
def _e4005_response(self, sub_code: str | None = None) -> dict[str, Any]:
    error_code = sub_code if sub_code else "E4005"
    messages = {
        "E4005A1": "脱敏防线降级且检测到敏感数据，请转为人工诊断",
        "E4005A2": "安全校验不可用且患者同意状态异常，请转为人工诊断",
        "E4005B": "核心数据服务不可用，请转为人工诊断",
        "E4005C": "系统安全校验不可用，请转为人工诊断",
    }
    return {
        "allowed": False,
        "error_code": error_code,
        "consent_status_at_output": "unknown_blocked",
        "degraded": True,
        "message": messages.get(error_code, "安全校验服务不可用，请转为人工诊断"),
    }
```

同时需要修改原有的`else`分支（非200/503状态码），使其不再被503触发：
```python
else:
    async with self._lock:
        self._consecutive_failures += 1
        current_failures = self._consecutive_failures
    if current_failures >= self.FAST_FAIL_THRESHOLD:
        logger.error(...)
        return self._e4005_response()
    logger.error(...)
```

**验证**：
- 确认SG服务端503响应中`code`字段为E4005A1/A2/B/C之一（errors.py已确认）
- 确认客户端正确提取并传递子类型

---

### 步骤4：SG-FIX-R8-04 — Redis缓存TTL对齐设计文档（P1）

**问题**：`sg_consent_cache_ttl_seconds=5`（当前值，审计报告记录为10但实际代码为5），设计文档要求TTL 60s。

**文件1**：`safety_gateway/sg_config.py` 第47行

**当前代码**：
```python
sg_consent_cache_ttl_seconds: int = 5
```

**修改为**：
```python
sg_consent_cache_ttl_seconds: int = 60
```

**文件2**：`safety_gateway/consent_checker.py` 第79行

**当前TTL检查逻辑**：`if ttl > self._consent_cache_ttl_seconds` → 当Redis key剩余TTL > 60s时拒绝使用。

**分析**：修改sg_consent_cache_ttl_seconds=60后，consent_checker.py的TTL检查逻辑变为：
- Redis key剩余TTL > 60s → 拒绝使用（缓存"太新鲜"，可能包含主服务未及时更新的数据）
- Redis key剩余TTL ≤ 60s且 > 0 → 接受使用

如果主服务写入Redis时设置TTL=60s，则：
- 刚写入时TTL≈60s → 60 ≤ 60 → SG接受使用 ✅
- 写入50s后TTL≈10s → 10 ≤ 60 → SG接受使用 ✅
- 写入60s后TTL=0 → 已过期 → SG拒绝使用 ✅

这与设计文档"PostgreSQL不可用时降级为Redis只读缓存（TTL 60s）"完全一致。

**无需修改consent_checker.py的TTL检查逻辑**，仅需修改sg_config.py的配置值。

**验证**：
- 确认sg_consent_cache_ttl_seconds=60与设计文档一致
- 确认TTL检查逻辑在修改后正确工作

---

### 步骤5：SG-FIX-R8-05 — 独立姓名脱敏规则补充（P1）

**问题**：`sg_patient_name_simple`规则仍要求"姓名/名为/名叫"前缀，不带前缀的独立中文姓名不会被SG脱敏。

**文件**：`desensitization_rules_safety.yaml`

**当前sg_patient_name_simple规则（第47-52行）**：
```yaml
- description: "独立患者姓名脱敏（异构：医疗上下文关键词定位，无前缀要求）"
  enabled: true
  name: sg_patient_name_simple
  pattern: "(?:姓名[：:]\\s*|名为\\s*|名叫\\s*)[\\u4e00-\\u9fa5]{2,4}(?![\\u4e00-\\u9fa5])"
  priority: 10
  replacement: '[REDACTED:NAME]'
```

**修改为**（对齐主规则`patient_name_simple`的异构版本）：
```yaml
- description: "独立患者姓名脱敏（异构：行首2-4字中文姓名+标点/行尾定位）"
  enabled: true
  name: sg_patient_name_simple
  pattern: "^[\\u4e00-\\u9fa5]{2,4}(?=[，,。.\\s]|$)"
  priority: 10
  replacement: '[REDACTED:NAME]'
```

**异构冗余说明**：
- 主规则`patient_name_simple`：`^([\u4e00-\u9fa5]{2,4})(?=，|,|$)` — 使用捕获组+前瞻
- SG规则`sg_patient_name_simple`：`^[\u4e00-\u9fa5]{2,4}(?=[，,。\s]|$)` — 非捕获组+扩展标点集（增加句号和空白）

两者模式不同（捕获方式、标点集），实现异构冗余。

**验证**：
- 确认"张三，"可被sg_patient_name_simple匹配
- 确认"李四"（行尾）可被匹配
- 确认"王五。"可被匹配
- 确认与主规则模式不同（异构冗余）

---

### 步骤6：SG-FIX-R8-03 — fusion_available字段清理（P1）

**问题**：客户端发送`fusion_available`字段被SG服务端SafetyCheckRequest忽略。

**文件**：`app/services/safety_gateway_client.py`

**当前代码（第121-129行）**：
```python
json={
    "session_id": session_id,
    "patient_id": patient_id,
    "output_data": output_data,
    "consent_status_at_input": consent_status_at_input,
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "fusion_available": fusion_available,
    "desensitization_degraded": desensitization_degraded,
},
```

**修改为**：
```python
json={
    "session_id": session_id,
    "patient_id": patient_id,
    "output_data": output_data,
    "consent_status_at_input": consent_status_at_input,
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "desensitization_degraded": desensitization_degraded,
},
```

同时移除`check_output()`方法签名中的`fusion_available`参数和`process_safety_gateway_layer()`中的`fusion_available`参数传递。

**涉及文件**：
1. `app/services/safety_gateway_client.py`：移除`check_output()`的`fusion_available`参数和请求体中的字段
2. `app/security/three_layer_desensitization.py`：移除`process_safety_gateway_layer()`的`fusion_available`参数和`process_full_pipeline()`的`fusion_available`参数

**验证**：
- 确认客户端不再发送`fusion_available`字段
- 确认SG服务端SafetyCheckRequest无此字段（已确认）
- 确认SG服务端通过HealthChecker自主检测融合层状态（已确认）

---

### 步骤7：SG-FIX-R8-07 — three_layer_desensitization.py类型注解风格（P2）

**文件**：`app/security/three_layer_desensitization.py`

**当前代码（第9-10行）**：
```python
from typing import Any
from enum import Enum
```

**修改为**：
```python
from enum import Enum
```

移除`from typing import Any`，将所有`dict[str, Any]`改为`dict[str, Any]`... 等等，`Any`仍需从typing导入，因为Python没有内置的`Any`类型。因此保留`from typing import Any`。

**重新评估**：`Any`在Python 3.10+中仍需从`typing`导入，无内置替代。`Enum`从`enum`导入是标准做法。因此此修复项无需修改。

**结论**：SG-FIX-R8-07无需修改——`from typing import Any`和`from enum import Enum`在Python 3.10+中是必要的导入，不存在替代方案。

---

### 步骤8：SG-FIX-R8-08 — sg_db.py类型注解风格（P2）

**文件**：`safety_gateway/sg_db.py`

**当前代码（第3行）**：
```python
from typing import AsyncGenerator
```

**修改为**：
```python
from collections.abc import AsyncGenerator
```

**验证**：Python 3.10+支持`collections.abc.AsyncGenerator`，无需从typing导入。

---

### 步骤9：SG-FIX-R8-09 — circuit_breaker.py线程安全文档化（P2）

**文件**：`safety_gateway/circuit_breaker.py`

**当前代码（第70-73行）**：
```python
def _update_state(self) -> None:
    # NOTE: 此方法在asyncio单线程模型下是安全的。state/is_available属性
    # 在execute()等async方法中被调用，与record_failure/record_success
    # 持有_lock的代码不会并发执行。若未来引入多线程调用，需添加同步保护。
```

**修改为**（增强文档声明）：
```python
def _update_state(self) -> None:
    """更新熔断器状态（检查冷却时间是否到期）。

    ⚠️ asyncio单线程约束：此方法修改self._state等可变状态，
    依赖asyncio事件循环的单线程特性保证线程安全。
    state/is_available属性在execute()等async方法中被调用，
    与record_failure/record_success持有_lock的代码不会并发执行。
    若未来引入多线程调用（如run_in_executor），必须添加同步保护。
    """
```

**验证**：确认类文档字符串中包含asyncio单线程约束声明。

---

## 执行顺序

1. **步骤1**（R8-01）+ **步骤2**（R8-02）：同一文件three_layer_desensitization.py，合并修改
2. **步骤3**（R8-06）+ **步骤6**（R8-03）：同一文件safety_gateway_client.py，合并修改
3. **步骤4**（R8-04）：sg_config.py单行修改
4. **步骤5**（R8-05）：desensitization_rules_safety.yaml规则修改
5. **步骤7**（R8-07）：评估后无需修改
6. **步骤8**（R8-08）：sg_db.py单行修改
7. **步骤9**（R8-09）：circuit_breaker.py文档增强

## 验证计划

每步修改后：
1. 运行`python -m pytest tests/unit/test_safety_gateway.py -v --tb=short -x`确认核心测试通过
2. 全部修改完成后运行`python -m pytest tests/unit/ -k "safety or desensitization or security" -v --tb=short`
3. 检查类型注解：`python -m py_compile`各修改文件
