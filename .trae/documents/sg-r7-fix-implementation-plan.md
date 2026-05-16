# SG安全网关R7审查修复实施计划

**依据文档**：sg-safety-gateway-audit-report-round7.md
**修复原则**：逐项修复，每项修复后验证，确保不引入新问题

---

## 修复项清单（19项，按优先级排序）

### P0级（4项，含R6遗留2项）

#### FIX-01: SG-FIX-R7-01 — three_layer_desensitization.py键名不匹配 + 参数缺失

**文件**：`app/security/three_layer_desensitization.py`

**问题1**：第120行 `check_result.get("degraded_mode", False)` → 客户端返回键名是`degraded`
**问题2**：第183-185行 `check_result.get("passed", True)` → 客户端返回键名是`allowed`
**问题3**：第112-118行 `check_output()`未传递`desensitization_degraded`参数

**修复方案**：

1. 第9行：`from typing import Dict, Any, Optional, Tuple` → `from typing import Any`（移除未使用的导入）
2. 第28行：`self.layer_results: Dict[DesensitizationLayer, Dict[str, Any]] = {}` → `self.layer_results: dict[DesensitizationLayer, dict[str, Any]] = {}`
3. 第29行：`self.final_data: Optional[Dict[str, Any]] = None` → `self.final_data: dict[str, Any] | None = None`
4. 第120行：`check_result.get("degraded_mode", False)` → `check_result.get("degraded", False)`
5. 第112-118行：添加`desensitization_degraded`参数传递
6. 第183-185行：`check_result.get("passed", True)` → `check_result.get("allowed", True)`
7. 第138行：`Optional[Dict[str, Any]]` → `dict[str, Any] | None`
8. 第49行：`Tuple[Dict[str, Any], Dict[str, Any]]` → `tuple[dict[str, Any], dict[str, Any]]`
9. 第85行：同上
10. 第109行：同上
11. 第199行：`Optional[ThreeLayerDesensitizationManager]` → `ThreeLayerDesensitizationManager | None`

**验证**：运行`pytest tests/unit/test_safety_gateway.py -x -v`确认无回归

---

#### FIX-02: SG-FIX-R7-02 — SafetyGatewayClient并发竞态条件

**文件**：`app/services/safety_gateway_client.py`

**问题**：`_consecutive_failures`和`_get_client()`在并发场景下存在竞态

**修复方案**：

1. 添加`import asyncio`
2. 在`__init__`中添加`self._lock = asyncio.Lock()`
3. `_get_client()`使用`self._lock`保护：
```python
async def _get_client(self) -> httpx.AsyncClient:
    if self._client is None or self._client.is_closed:
        async with self._lock:
            if self._client is None or self._client.is_closed:
                self._client = httpx.AsyncClient(...)
    return self._client
```
4. `check_output()`中`_consecutive_failures`的读写使用`self._lock`保护
5. `_e4005_response()`不需要锁（纯函数，无共享状态）

**验证**：运行`pytest tests/unit/ -x -v -k safety`确认无回归

---

#### FIX-03: SG-FIX-R6-01 — main.py SGError异常未映射HTTP状态码

**文件**：`safety_gateway/main.py`

**问题**：第268行`except SGError: raise`，E4005B/E4005C返回HTTP 500

**修复方案**：

在main.py中添加FastAPI异常处理器：

```python
from safety_gateway.errors import (
    SafetyGatewayError,
    ServiceUnavailableError,
    ConsentSafetyViolationError,
    DatabaseUnavailableError,
    CombinedUnavailableError,
)

@app.exception_handler(SafetyGatewayError)
async def sg_error_handler(request, exc: SafetyGatewayError):
    if isinstance(exc, (ConsentSafetyViolationError, DatabaseUnavailableError, CombinedUnavailableError)):
        status_code = 503
    elif isinstance(exc, ServiceUnavailableError):
        status_code = 503
    else:
        status_code = 500
    return JSONResponse(
        status_code=status_code,
        content=exc.to_dict(),
    )
```

**验证**：运行`pytest tests/unit/test_safety_gateway.py -x -v -k "E4005 or error"`确认

---

#### FIX-04: SG-FIX-R6-02 — contraindication.py和threshold.py单例线程安全

**文件1**：`safety_gateway/contraindication.py` 第222-229行
**文件2**：`safety_gateway/threshold.py` 第546-553行

**问题**：简单全局变量检查，无double-checked locking

**修复方案**：

contraindication.py：
```python
_sg_contraindication_engine: ContraindicationEngine | None = None
_sg_contraindication_lock = threading.Lock()

def get_contraindication_engine() -> ContraindicationEngine:
    global _sg_contraindication_engine
    if _sg_contraindication_engine is None:
        with _sg_contraindication_lock:
            if _sg_contraindication_engine is None:
                _sg_contraindication_engine = ContraindicationEngine()
    return _sg_contraindication_engine
```

threshold.py同理，添加`_sg_threshold_lock = threading.Lock()`和double-checked locking。

需在两个文件中添加`import threading`。

**验证**：运行`pytest tests/unit/test_safety_gateway.py -x -v`确认

---

### P1级（5项）

#### FIX-05: SG-FIX-R7-03 — medical_person_names.yaml known_combinations声明清理

**文件**：`medical_person_names.yaml`

**修复方案**：删除第10行注释`#   known_combinations: 已知的人名+称谓组合（高优先级匹配）`

**验证**：YAML语法检查

---

#### FIX-06: SG-FIX-R7-04 — desensitization_rules_safety.yaml独立姓名脱敏规则补充

**文件**：`desensitization_rules_safety.yaml`

**修复方案**：在`sg_patient_name_standalone`规则后添加一条无需前缀的独立中文姓名脱敏规则：

```yaml
- description: "独立患者姓名脱敏（异构：上下文无关匹配，无前缀要求）"
  enabled: true
  name: sg_patient_name_simple
  pattern: "(?<![\\u4e00-\\u9fa5])[\\u4e00-\\u9fa5]{2,4}(?![\\u4e00-\\u9fa5])"
  priority: 10
  replacement: '[REDACTED_NAME_SG]'
```

注意：此规则使用负向前瞻/后顾排除中文上下文，减少误匹配。优先级设为10（最低），确保其他更精确的规则优先匹配。

**验证**：运行脱敏引擎测试确认新规则生效且无误匹配

---

#### FIX-07: SG-FIX-R7-05 — Dockerfile与supervisor HOME目录一致性

**文件**：`deploy/supervisor/safety-gateway.conf` 第19行

**修复方案**：将`environment=HOME="/home/sguser",USER="sguser"`改为`environment=HOME="/app",USER="sguser"`

**验证**：配置文件语法检查

---

#### FIX-08: SG-FIX-R7-06 — SafetyGatewayClient E4005响应区分子类型

**文件**：`app/services/safety_gateway_client.py`

**问题**：`_e4005_response()`统一返回`error_code="E4005"`，丢失E4005A/B/C诊断信息

**修复方案**：

1. 修改`_e4005_response()`接受可选的`sub_code`参数：
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

2. 在`check_output()`中，当服务端返回非200状态码时，尝试从响应体中提取`error_code`：
```python
if response.status_code == 503:
    try:
        body = response.json()
        server_error_code = body.get("code", "E4005")
        if server_error_code in ("E4005A", "E4005B", "E4005C"):
            self._consecutive_failures += 1
            return self._e4005_response(sub_code=server_error_code)
    except Exception:
        pass
```

3. 在`check_output()`的HTTP 200成功路径中，透传服务端返回的`error_code`（如果有）：
```python
if response.status_code == 200:
    self._consecutive_failures = 0
    result = response.json()
    if result.get("degraded_mode") and result.get("degraded_reason"):
        reason = result["degraded_reason"]
        if reason in ("DESENSITIZATION_DEGRADED_SG_UNAVAILABLE", "SG_UNAVAILABLE_CONSENT_WITHDRAWN"):
            result.setdefault("error_code", "E4005A")
    return result
```

**验证**：运行`pytest tests/unit/ -x -v -k "E4005 or e4005"`确认

---

#### FIX-09: SG-FIX-R7-07 — supervisor startretries限制

**文件**：`deploy/supervisor/safety-gateway.conf`

**修复方案**：
1. 将`startretries=5`改为`startretries=100`
2. 添加`exitcodes=0,2`（仅正常退出码和启动错误码不重启）
3. 添加日志轮转配置（同时修复SG-FIX-R7-12）：
   - `stdout_logfile_maxbytes=50MB`
   - `stdout_logfile_backups=5`
   - `stderr_logfile_maxbytes=50MB`
   - `stderr_logfile_backups=5`

**验证**：配置文件语法检查

---

### P2级（10项，含R6遗留5项类型注解 + 1项__init__.py + 4项独立修复）

#### FIX-10: SG-FIX-R6-04 — redis_pool.py类型注解风格

**文件**：`safety_gateway/redis_pool.py`

**修复方案**：
1. 第15行：`from typing import Optional, Any` → `from typing import Any`
2. 第25行：`_client: Optional[redis.Redis] = None` → `_client: redis.Redis | None = None`
3. 第61行：`async def get(cls, key: str) -> Optional[bytes]:` → `async def get(cls, key: str) -> bytes | None:`

---

#### FIX-11: SG-FIX-R6-05 — sg_config.py和sg_validator.py类型注解风格

**文件1**：`safety_gateway/sg_config.py`
1. 第1行：`from typing import List` → 删除
2. 第15行：`safety_gateway_whitelist_fields: List[str]` → `safety_gateway_whitelist_fields: list[str]`
3. 第44行：`sg_cors_allowed_origins: List[str]` → `sg_cors_allowed_origins: list[str]`

**文件2**：`safety_gateway/sg_validator.py`
1. 第1行：`from typing import List` → 删除
2. 第7行：`def __init__(self, errors: List[str]):` → `def __init__(self, errors: list[str]):`
3. 第12行：`def validate_sg_config() -> List[str]:` → `def validate_sg_config() -> list[str]:`
4. 第13行：`errors: List[str] = []` → `errors: list[str] = []`

---

#### FIX-12: SG-FIX-R6-06 — consent_checker.py类型注解风格

**文件**：`safety_gateway/consent_checker.py`

1. 第16行：`from typing import NamedTuple, Optional` → `from typing import NamedTuple`
2. 第40行：`status: Optional[ConsentStatus]` → `status: ConsentStatus | None`

---

#### FIX-13: SG-FIX-R6-07 — three_layer_desensitization.py类型注解风格

**文件**：`app/security/three_layer_desensitization.py`

已在FIX-01中一并修复（移除`from typing import Dict, Any, Optional, Tuple`，统一使用Python 3.10+风格）。

---

#### FIX-14: SG-FIX-R6-08 — __init__.py公共API导出

**文件**：`safety_gateway/__init__.py`

**修复方案**：添加公共API导出

```python
from safety_gateway.sg_types import ConsentStatus, CheckResultStatus, DegradedReason
from safety_gateway.errors import (
    SafetyGatewayError,
    ServiceUnavailableError,
    ConsentSafetyViolationError,
    DatabaseUnavailableError,
    CombinedUnavailableError,
)
from safety_gateway.checker import SafetyGatewayChecker, get_safety_gateway_checker
from safety_gateway.sg_config import sg_settings

__all__ = [
    "ConsentStatus",
    "CheckResultStatus",
    "DegradedReason",
    "SafetyGatewayError",
    "ServiceUnavailableError",
    "ConsentSafetyViolationError",
    "DatabaseUnavailableError",
    "CombinedUnavailableError",
    "SafetyGatewayChecker",
    "get_safety_gateway_checker",
    "sg_settings",
]
```

---

#### FIX-15: SG-FIX-R7-09 — 性能测试time.time()→time.perf_counter()

**文件**：`tests/benchmark/test_safety_gateway_performance.py`

**修复方案**：将所有`time.time()`替换为`time.perf_counter()`

涉及行：
1. 第79行：`start = time.time()` → `start = time.perf_counter()`
2. 第81行：`elapsed = (time.time() - start) * 1000` → `elapsed = (time.perf_counter() - start) * 1000`
3. 第113行：同上
4. 第115行：同上
5. 第135行：同上
6. 第137行：同上
7. 第156行：同上
8. 第158行：同上
9. 第191行：同上
10. 第193行：同上

---

#### FIX-16: SG-FIX-R7-10 — medical_person_names.yaml suffixes移除"患者"

**文件**：`medical_person_names.yaml`

**修复方案**：从suffixes列表中删除`- 患者`（第121行）

---

#### FIX-17: SG-FIX-R7-11 — start_safety_gateway.sh添加set -e

**文件**：`scripts/start_safety_gateway.sh`

**修复方案**：在第1行`#!/bin/bash`后添加`set -e`

---

#### FIX-18: SG-FIX-R7-12 — supervisor日志轮转配置

**文件**：`deploy/supervisor/safety-gateway.conf`

**修复方案**：已在FIX-09中一并修复。

---

#### FIX-19: SG-FIX-R6-01补充 — main.py中SGError异常处理器需导入SGError子类

**文件**：`safety_gateway/main.py`

**修复方案**：已在FIX-03中一并修复。

---

## 执行顺序

按依赖关系和风险等级排序：

1. **FIX-01**（P0）：three_layer_desensitization.py — 最关键的安全缺陷
2. **FIX-02**（P0）：safety_gateway_client.py并发竞态
3. **FIX-03**（P0）：main.py SGError→HTTP状态码映射
4. **FIX-04**（P0）：contraindication.py和threshold.py单例线程安全
5. **FIX-05**（P1）：medical_person_names.yaml注释清理
6. **FIX-06**（P1）：desensitization_rules_safety.yaml规则补充
7. **FIX-07**（P1）：supervisor HOME目录修复
8. **FIX-08**（P1）：客户端E4005子类型区分
9. **FIX-09**（P1）：supervisor startretries + 日志轮转
10. **FIX-10**（P2）：redis_pool.py类型注解
11. **FIX-11**（P2）：sg_config.py + sg_validator.py类型注解
12. **FIX-12**（P2）：consent_checker.py类型注解
13. **FIX-13**（P2）：three_layer_desensitization.py类型注解（已在FIX-01中完成）
14. **FIX-14**（P2）：__init__.py公共API导出
15. **FIX-15**（P2）：性能测试time.time()→time.perf_counter()
16. **FIX-16**（P2）：medical_person_names.yaml移除"患者"
17. **FIX-17**（P2）：start_safety_gateway.sh添加set -e

## 验证策略

每完成一个P0/P1级修复后运行：
```bash
pytest tests/unit/test_safety_gateway.py tests/unit/test_security_compliance.py tests/unit/test_sec_craft_fixes.py -x -v
```

全部修复完成后运行完整测试套件：
```bash
pytest tests/unit/ tests/benchmark/ -x -v
```
