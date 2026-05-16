# SG安全网关待修复项实施计划

## 修复项总览

| 编号 | 严重程度 | 目标文件 | 修复内容 |
|------|---------|---------|---------|
| SG-FIX-08 | P2-Medium | `consent_checker.py` | Redis缓存策略对齐"直查PostgreSQL"文档要求 |
| SG-FIX-09 | P2-Medium | `health.py` + `sg_config.py` | compact模式判断改用配置项 |
| SG-FIX-10 | P2-Medium | `sg_db.py` | SQLAlchemy deprecated API迁移 |
| SG-FIX-11 | P2-Medium | `validation_result.py` | Pydantic V2 ConfigDict迁移 |
| SG-FIX-12 | P2-Medium | `checker.py` | `perform_full_check`方法拆分重构 |
| SG-FIX-13 | P3-Low | `main.py` + `health.py` | `/health`端点增加oom_score_adj验证 |
| SG-FIX-14 | P3-Low | `desensitization.py` | 扩大默认人名字典fallback |

---

## SG-FIX-08: consent_checker.py Redis缓存策略对齐

### 问题
文档明确要求"同意状态已取消Redis双写（直写PostgreSQL，直查PostgreSQL）"，但代码在PG查询成功后仍写入Redis缓存（TTL=10s），导致同意撤回场景下存在10s窗口期。

### 修复步骤
1. **移除PG成功路径的Redis写入**：删除 `check_consent_status` 中 `await self._cache_consent_to_redis(patient_id, consent_status)` 调用（L164）
2. **保留Redis缓存读取作为降级fallback**：当PG不可用时仍尝试读取Redis缓存（可能包含旧数据），但不再主动写入新缓存
3. **使用配置项替代硬编码TTL**：将 `REDIS_CONSENT_TTL_SECONDS = 10` 改为读取 `settings.sg_consent_cache_ttl_seconds`
4. **更新模块docstring**：反映"直查PostgreSQL"设计，说明Redis仅作为PG不可用时的只读降级缓存
5. **验证**：运行 `test_safety_gateway.py` 确认所有测试通过

### 代码变更
```python
# consent_checker.py L154-165 修改前:
async def check_consent_status(self, patient_id: str) -> ConsentCheckResult:
    ...
    try:
        consent_status = await breaker.execute(self._retry_query, patient_id)
        await self._cache_consent_to_redis(patient_id, consent_status)  # 删除此行
        return ConsentCheckResult(consent_status, False, False)

# 修改后:
async def check_consent_status(self, patient_id: str) -> ConsentCheckResult:
    ...
    try:
        consent_status = await breaker.execute(self._retry_query, patient_id)
        return ConsentCheckResult(consent_status, False, False)
```

```python
# L33-34 修改前:
REDIS_CONSENT_KEY_PREFIX = "sg:consent:"
REDIS_CONSENT_TTL_SECONDS = 10

# 修改后:
REDIS_CONSENT_KEY_PREFIX = "sg:consent:"
```

---

## SG-FIX-09: health.py compact模式判断改用配置项

### 问题
`health.py` L56 使用 `os.environ.get("DEPLOYMENT_PROFILE")` 直接读取环境变量，绕过了配置管理系统。

### 修复步骤
1. **sg_config.py 添加 `deployment_profile` 配置项**：默认值 `"compact"`，支持环境变量 `DEPLOYMENT_PROFILE`
2. **health.py 替换环境变量读取**：`os.environ.get("DEPLOYMENT_PROFILE")` → `settings.deployment_profile`
3. **移除 health.py 中未使用的 `import os`**（若os仅用于此用途则移除，否则保留）
4. **验证**：运行测试确认

### 代码变更
```python
# sg_config.py 添加:
deployment_profile: str = "compact"

# health.py L56 修改前:
if redis_status["status"] == "unhealthy" and os.environ.get("DEPLOYMENT_PROFILE") == "compact":

# 修改后:
if redis_status["status"] == "unhealthy" and settings.deployment_profile == "compact":
```

---

## SG-FIX-10: sg_db.py SQLAlchemy deprecated API迁移

### 问题
使用 `sqlalchemy.ext.declarative.declarative_base()` 触发 `MovedIn20Warning`。

### 修复步骤
1. **修改导入路径**：`from sqlalchemy.ext.declarative import declarative_base` → `from sqlalchemy.orm import declarative_base`
2. **验证**：运行测试确认无warning

### 代码变更
```python
# sg_db.py L2 修改前:
from sqlalchemy.ext.declarative import declarative_base

# 修改后:
from sqlalchemy.orm import declarative_base
```

---

## SG-FIX-11: validation_result.py Pydantic V2 ConfigDict迁移

### 问题
使用 `class Config: use_enum_values = True` 触发 `PydanticDeprecatedSince20`。

### 修复步骤
1. **导入ConfigDict**：`from pydantic import BaseModel` → `from pydantic import BaseModel, ConfigDict`
2. **替换Config类**：`class Config: use_enum_values = True` → `model_config = ConfigDict(use_enum_values=True)`
3. **验证**：运行测试确认无warning

### 代码变更
```python
# validation_result.py L9 修改前:
from pydantic import BaseModel

# 修改后:
from pydantic import BaseModel, ConfigDict

# L56-59 修改前:
    class Config:
        use_enum_values = True

# 修改后:
    model_config = ConfigDict(use_enum_values=True)
```

---

## SG-FIX-12: checker.py perform_full_check方法拆分重构

### 问题
`perform_full_check` 方法约320行，包含同意/脱敏/禁忌/阈值/一致性/降级/组合故障等所有逻辑，可读性和可维护性差。

### 修复步骤
1. **提取 `_check_consent_phase` 方法**：封装同意状态校验逻辑（L277-355），返回 `CheckResult | None`（None表示继续下一阶段）
2. **提取 `_check_desensitization_phase` 方法**：封装脱敏校验逻辑（L376-414），返回 `CheckResult | None`
3. **提取 `_check_contraindication_and_threshold_phase` 方法**：封装禁忌+阈值+组合故障逻辑（L416-505），返回 `CheckResult | None`
4. **提取 `_check_consistency_phase` 方法**：封装一致性校验逻辑（L526-549），返回 `CheckResult | None`
5. **重构 `perform_full_check`**：调用各阶段方法，保持原有控制流和延迟记录
6. **验证**：运行完整测试套件确认所有201个测试通过

### 设计原则
- 每个阶段方法返回 `CheckResult | None`：返回CheckResult表示该阶段有结论（通过/失败/降级），返回None表示继续下一阶段
- 保持 `try/finally` 中的延迟记录逻辑不变
- 保持快速拒绝逻辑在 `perform_full_check` 中（入口前置判断）
- 不改变任何业务逻辑，仅做结构重组

### 代码变更概要
```python
async def perform_full_check(self, ...) -> CheckResult:
    start_time = datetime.now(timezone.utc)
    health_checker = _get_health_checker()

    # 快速拒绝（入口前置）
    if consent_status_at_input is not None and consent_status_at_input != "granted":
        return self._fast_reject_consent(patient_id, consent_status_at_input, start_time)

    try:
        # 阶段1: 同意状态校验
        result = await self._check_consent_phase(patient_id, consent_status_at_input, fusion_available, start_time)
        if result is not None:
            return result

        consent_status, is_timeout, is_db_error = result._consent_data  # 通过属性传递

        # 阶段2: 脱敏校验
        result = await self._check_desensitization_phase(...)
        if result is not None:
            return result

        # 阶段3: 禁忌+阈值+组合故障
        result = await self._check_contraindication_threshold_phase(...)
        if result is not None:
            return result

        # 阶段4: 一致性校验
        result = await self._check_consistency_phase(...)
        if result is not None:
            return result

        # 全部通过
        return CheckResult(status=CheckResultStatus.PASSED, ...)
    finally:
        total_elapsed = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
        health_checker.record_latency(total_elapsed)
```

> **注意**：此重构最复杂，需确保阶段间数据传递（consent_status、is_timeout等）正确。采用内部数据类 `_PhaseContext` 传递阶段间共享状态。

---

## SG-FIX-13: main.py /health端点增加oom_score_adj验证

### 问题
应用层未验证OOM保护是否实际生效，`main.py` 中仅记录日志备注。

### 修复步骤
1. **health.py 的 `check_all` 方法增加 `oom_protection` 组件**：读取 `/proc/self/oom_score_adj`（Linux）或返回 `"unknown"`（非Linux）
2. **验证逻辑**：oom_score_adj == -1000 → "protected"，其他值 → "warning"
3. **main.py 移除日志备注**：删除 `oom_score_adj_note=...` 日志行（功能已迁移至health检查）
4. **验证**：运行测试确认

### 代码变更
```python
# health.py 新增方法:
def _check_oom_protection(self) -> dict[str, Any]:
    try:
        with open(f"/proc/{os.getpid()}/oom_score_adj") as f:
            value = int(f.read().strip())
            return {
                "status": "protected" if value == -1000 else "warning",
                "oom_score_adj": value,
                "expected": -1000,
            }
    except Exception:
        return {"status": "unknown", "oom_score_adj": None}
```

---

## SG-FIX-14: desensitization.py 扩大默认人名字典

### 问题
默认人名字典仅包含张/李/王/刘/陈5个姓氏的15个组合，覆盖率极低。

### 修复步骤
1. **扩展默认人名字典**：增加赵/黄/周/吴/徐/孙/马/朱/胡/林/郭/何/高/罗等常见姓氏，覆盖前20大姓氏
2. **增加职称变体**：除医生/主任/教授外，增加副主任医师/主治医师/护士长等
3. **验证**：运行测试确认

### 代码变更
```python
# desensitization.py _get_default_person_name_set 修改:
def _get_default_person_name_set(self) -> set[str]:
    surnames = "张李王刘陈赵黄周吴徐孙马朱胡林郭何高罗"
    titles = ["医生", "主任", "教授", "副主任医师", "主治医师", "护士长", "院长", "科长"]
    return {f"{s}{t}" for s in surnames for t in titles}
```

---

## 执行顺序

1. SG-FIX-10 (sg_db.py) — 最简单，1行改动
2. SG-FIX-11 (validation_result.py) — 简单，2处改动
3. SG-FIX-09 (health.py + sg_config.py) — 简单，2个文件
4. SG-FIX-08 (consent_checker.py) — 中等，需理解降级逻辑
5. SG-FIX-14 (desensitization.py) — 简单，扩展字典
6. SG-FIX-13 (main.py + health.py) — 中等，新增组件检查
7. SG-FIX-12 (checker.py) — 最复杂，大规模重构
8. **最终验证**: 运行完整测试套件 + 性能基准测试

## 验证标准

- 所有单元测试通过（201+ passed, 0 failed）
- 性能基准测试通过（6 passed）
- 无 PydanticDeprecatedSince20 警告
- 无 MovedIn20Warning 警告
- 无 RuntimeWarning 警告
