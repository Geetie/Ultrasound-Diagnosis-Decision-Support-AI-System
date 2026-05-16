# SEC安全合规轨道 — 阶段2-6实施计划

## 前置条件

阶段1（P0-1~P0-7）和部分P1（P1-1~P1-3）已在上一轮完成。本计划覆盖剩余所有阶段。

---

## 阶段2: P0级测试补充

### 2.1 创建 `tests/unit/test_consent_api.py`

覆盖同意API全路径测试：
- `GET /{patient_id}` 无同意记录返回403(E1008)
- `GET /{patient_id}` 已撤回同意返回403(E1010)
- `GET /{patient_id}` 有效同意返回200
- `POST /` 创建同意记录成功
- `POST /` 撤回同意成功
- `POST /` 非授权角色返回403
- `POST /` 撤回同意触发Redis广播
- `POST /` 撤回同意写入安全事件表

### 2.2 创建 `tests/unit/test_auth_api.py`

覆盖认证API端点测试：
- `POST /login` 正确凭据返回access+refresh token
- `POST /login` 错误凭据返回401
- `POST /token` OAuth2标准端点返回access token（无refresh）
- `POST /refresh-token` 有效refresh token轮换成功
- `POST /refresh-token` 无效refresh token返回401
- `POST /refresh-token` 已撤销token重用触发家族撤销
- `POST /revoke-refresh-token` 成功撤销
- `POST /revoke-all-tokens` 撤销用户所有token
- `GET /verify-token` 有效token返回用户信息
- `GET /token-stats` 返回token统计

### 2.3 创建 `tests/unit/test_security_api.py`

覆盖安全API端点测试：
- `GET /desensitization/status` 需认证
- `GET /desensitization/rules` 需认证
- `POST /desensitization/rules/reload` 需SYSTEM_ADMIN角色
- `POST /desensitization/rules/check` 需SYSTEM_ADMIN或DEPARTMENT_HEAD角色
- `POST /desensitization/rules` 需SYSTEM_ADMIN角色
- `PUT /desensitization/rules/{name}` 需SYSTEM_ADMIN角色
- `DELETE /desensitization/rules/{name}` 需SYSTEM_ADMIN角色
- `POST /desensitization/test` 不返回original字段
- `PUT /logging/level` 需SYSTEM_ADMIN角色
- `GET /logging/level` 需认证

### 2.4 创建 `tests/unit/test_redis_refresh_token.py`

使用fakeredis覆盖RedisRefreshTokenStore：
- create/verify/revoke基本流程
- 家族撤销
- 用户级撤销
- 过期清理
- 重用检测（revoked token触发家族撤销）
- 并发会话限制

### 2.5 补充 `tests/unit/test_auth.py`

- JWT过期Token解码抛401
- JWT签名篡改验证失败
- 无效JWT格式解码抛异常
- get_current_user空sub字段抛401
- RoleChecker无角色用户抛403

### 2.6 补充 `tests/unit/test_hsm_basic.py`

- HSM encrypt/decrypt方法测试
- get_private_key禁止导出测试
- sign_jwt降级状态检查测试
- _get_encryption_key环境变量缺失测试

---

## 阶段3: P1级问题修复（P1-4~P1-10）

### P1-4: hsm.py sign_jwt添加降级状态和SoftHSM过期检查

**文件**: `app/security/hsm.py` L373-407

在 `sign_jwt` 方法 `with self._lock` 之前添加：
```python
if self._use_soft_hsm and self._check_soft_hsm_expiry():
    raise RuntimeError("SoftHSM has expired (90 days). Signing is prohibited until HSM is reconfigured.")
phase = self.get_degraded_phase()
if phase == DegradedPhase.EMERGENCY:
    raise RuntimeError("HSM in EMERGENCY degraded phase: signing is prohibited")
if phase == DegradedPhase.RESTRICTED:
    raise RuntimeError("HSM in RESTRICTED degraded phase: signing is prohibited")
```

### P1-5: auth.py RoleChecker返回user对象而非True

**文件**: `app/security/auth.py` L98-109

修改 `__call__` 返回值：
```python
def __call__(self, user: Dict[str, Any] = Depends(get_current_active_user)) -> Dict[str, Any]:
    user_role = user.get("role")
    if user_role not in self.allowed_roles:
        raise HTTPException(...)
    return user
```

### P1-6: consent.py 同意查询添加患者级授权检查

**文件**: `app/api/v1/consent.py` L26-66

在 `get_consent` 端点中添加角色检查：
- SYSTEM_ADMIN/QUALITY_CONTROL/DEPARTMENT_HEAD 可查询任意患者
- 超声科医生/急诊科医生仅可查询与自己诊断会话关联的患者
- 添加审计日志记录同意查询操作

### P1-7: auth.py Refresh Token撤销添加所有权验证

**文件**: `app/api/v1/auth.py` L231-247

在 `revoke_token` 端点中验证token属于当前用户：
```python
token_data = await verify_refresh_token(refresh_request.refresh_token)
if token_data and token_data.get("user_id") != (current_user.get("user_id") or current_user.get("username")):
    if "SYSTEM_ADMIN" not in (current_user.get("role"),):
        raise HTTPException(status_code=403, detail="Cannot revoke other user's token")
```

### P1-8: consent_repository.py 同意撤回广播失败添加补偿机制

**文件**: `app/repositories/consent_repository.py` L147-196

3次重试全部失败后：
1. 写入补偿表（`consent_withdrawal_pending`）记录待发送的撤回通知
2. 触发Critical告警
3. 标记同意记录为 `withdrawal_pending=True`
4. 不静默吞没，抛出 `ConsentWithdrawalBroadcastError` 异常

### P1-9: audit_service.py 审计哈希链并发问题

**文件**: `app/services/audit_service.py` L20-21, L50-61

使用数据库行锁保证链序：
- `_get_last_hash` 改用 `SELECT ... FOR UPDATE` 获取最新记录
- 在 `log_action` 中使用数据库事务保证原子性
- 移除实例级 `_last_hash` 缓存，每次从数据库获取

### P1-10: consent_checker.py 单例创建线程安全

**文件**: `safety_gateway/consent_checker.py` L195-202

添加线程锁：
```python
_singleton_lock = threading.Lock()

def get_consent_checker() -> ConsentChecker:
    global _singleton_instance
    if _singleton_instance is None:
        with _singleton_lock:
            if _singleton_instance is None:
                _singleton_instance = ConsentChecker()
    return _singleton_instance
```

---

## 阶段4: 性能验证

### 4.1 添加CI >= 1000吞吐量显式验证

**文件**: `tests/benchmark/test_safety_gateway_performance.py`

在 `test_concurrent_requests` 中添加吞吐量断言：
```python
throughput = iterations / duration
assert throughput >= 1000, f"CI throughput {throughput:.0f}/s < 1000/s"
```

**文件**: `tests/benchmark/test_desensitization_performance.py`

添加CI吞吐量测试：
```python
def test_ci_throughput():
    iterations = 5000
    start = time.perf_counter()
    for _ in range(iterations):
        engine.desensitize(test_text)
    duration = time.perf_counter() - start
    throughput = iterations / duration
    assert throughput >= 1000, f"CI throughput {throughput:.0f}/s < 1000/s"
```

**文件**: `tests/benchmark/test_revocation_performance.py`

添加sign_jwt吞吐量测试和CI验证。

### 4.2 添加同意撤回传播P99 < 500ms性能测试

**文件**: `tests/benchmark/test_revocation_performance.py`

新增测试：
```python
def test_consent_withdrawal_propagation_p99():
    # 测量从同意撤回到SafetyGateway检测到的端到端延迟
    # P99 < 500ms
```

### 4.3 添加脱敏主引擎P99延迟测试

**文件**: `tests/benchmark/test_desensitization_performance.py`

新增测试：
```python
def test_desensitization_p99_latency():
    # 1000次迭代，计算P99
    # P99 < 5ms
```

### 4.4 消除测试重复

- 删除 `tests/unit/test_hsm_basic.py` 中与 `test_security_compliance.py` 重复的测试类
- 删除 `tests/benchmark/test_desensitization_performance.py` 中与 `test_desensitization.py` 重复的性能测试
- 保留更完整的版本

### 4.5 修复恒真断言

**文件**: `tests/unit/test_security_compliance_complete.py` L238-253

替换 `TestConsentManagement` 中的mock断言为真实API调用测试。

---

## 阶段5: P2级问题修复+设计一致性修复

### P2-1: IP地址正则误匹配

**文件**: `app/security/desensitization.py`

修改IP正则，添加负向前瞻排除版本号：
```python
pattern=r'(?<!\d\.)(?<!\d)\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b(?!\.\d)',
```

### P2-2: study_instance_uid和hospital_id锚定正则

**文件**: `app/security/desensitization.py`

移除 `^...$` 锚定，改为非锚定匹配：
```python
pattern=r'(\d[\d.]{10,64})',  # study_instance_uid
pattern=r'(HOSP-[A-Z]{2,4}-\d{3,6})',  # hospital_id
```

### P2-3: patient_name_simple正则过度匹配

**文件**: `app/security/desensitization.py`

添加上下文约束，仅在"患者"关键字后匹配：
```python
pattern=r'(?:患者|病人|病人姓名)[是为：:]\s*([\u4e00-\u9fa5]{2,4})(?=，|,|$)',
```

### P2-4: all_passed判断逻辑修复

**文件**: `app/security/three_layer_desensitization.py` L166-170

修改为检查每层的 `modified` 字段：
```python
result.all_passed = all(
    not layer_result.get("modified", False)
    for layer_result in result.layer_results.values()
)
```

### P2-5: Redis密钥添加TTL

**文件**: `app/security/hsm.py` L629

为密钥元数据和加密私钥添加TTL：
```python
await redis_client.set(f"{HSM_KEYS_REDIS_PREFIX}{key_name}", json.dumps(key_data), ex=86400*365)
await redis_client.set(f"{HSM_KEYS_REDIS_PREFIX}{key_name}:private", encrypted_private_key, ex=86400*365)
```

### P2-6: Redis KEYS命令改为SCAN

**文件**: `app/security/refresh_token.py` L545

将 `redis_client.keys("refresh_token:*")` 改为 `redis_client.scan_iter("refresh_token:*", count=100)`

### P2-7: HSM降级事件重复检测修复

**文件**: `app/repositories/consent_repository.py` L42-43

检查最近事件的 `acknowledged` 状态和时间戳：
```python
if last_event and last_event.acknowledged:
    last_degraded_at = last_event.created_at
    if hsm_manager._degraded_at and hsm_manager._degraded_at > last_degraded_at:
        pass  # 新的降级事件，继续记录
    else:
        return False
```

### P2-8: check_consent_status返回值语义统一

**文件**: `safety_gateway/consent_checker.py`

使用NamedTuple替代三元组：
```python
class ConsentCheckResult(NamedTuple):
    status: ConsentStatus | None
    timeout: bool
    error: bool
```

### P2-9: desensitize_dict支持tuple/set

**文件**: `app/security/desensitization.py` L231-247

添加tuple和set处理分支：
```python
elif isinstance(value, (tuple, set)):
    result[key] = type(value)(
        self.desensitize(item) if isinstance(item, str)
        else self.desensitize_dict(item) if isinstance(item, dict)
        else item
        for item in value
    )
```

三个脱敏引擎均需同步修改。

### P2-10: 统一/token和/login端点行为

**文件**: `app/api/v1/auth.py`

为 `/token` 端点也创建refresh token，使其与 `/login` 行为一致。

---

## 阶段6: 数据保留策略实现

### 6.1 添加保留策略配置项

**文件**: `app/config/settings.py`

新增配置：
```python
data_retention_outpatient_years: int = 15
data_retention_inpatient_years: int = 30
data_retention_audit_years: int = 30
data_retention_operation_years: int = 5
session_archive_after_days: int = 7
```

### 6.2 实现自动归档定时任务

**新建文件**: `app/services/data_retention.py`

- `archive_expired_sessions()`: 将超过7天的sessions迁移到sessions_archive
- `purge_expired_archives()`: 删除超过保留期的归档数据
- `verify_retention_compliance()`: 验证数据保留合规性

### 6.3 实现过期数据自动清理逻辑

在 `data_retention.py` 中：
- 门诊数据15年后自动清理
- 住院数据30年后自动清理
- 操作日志5年后自动清理
- 审计日志按门诊/住院分类保留

### 6.4 实现合规保留期验证机制

- 启动时检查保留策略配置完整性
- 定期验证数据是否按策略归档/清理
- 验证结果写入审计日志

### 6.5 编写数据保留策略测试

**新建文件**: `tests/unit/test_data_retention.py`

- 归档任务正确迁移过期session
- 清理任务正确删除超期数据
- 保留期配置验证
- 合规性检查测试

---

## 执行顺序

1. **阶段3**（P1-4~P1-10代码修复）→ 先修代码，再写测试
2. **阶段2**（P0级测试补充）→ 基于修复后的代码写测试
3. **阶段4**（性能验证）→ 补充性能测试
4. **阶段5**（P2级修复+设计一致性）→ 低优先级修复
5. **阶段6**（数据保留策略）→ 独立模块，最后实现

每个阶段完成后运行 `pytest tests/unit/ tests/benchmark/ -v` 验证无回归。
