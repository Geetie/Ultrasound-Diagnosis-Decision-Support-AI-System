# P2 修复收尾计划

## 审计结果

24项P2修复中 **23项已落地**，**2项待修复**：

### 待修复项

#### 1. P2-21：P99 硬约束限流未写入 main.py
- **文件**: `safety_gateway/main.py`
- **位置**: `perform_safety_check()` 函数（第291行）
- **问题**: 内存超预算检查后缺少 P99 限流检查
- **修复**: 在内存检查之后添加 `_get_health_checker()._latency_tracker.is_throttling` 检查

当前代码（第291-299行）：
```python
async def perform_safety_check(request: SafetyCheckRequest):
    from safety_gateway.health.memory_monitor import MemoryMonitor
    from safety_gateway.sg_config import sg_settings as _settings

    if MemoryMonitor.is_over_budget():
        raise ServiceUnavailableError(
            error_message="安全网关内存超预算，拒绝新请求（E4005）",
            detail="sg_memory_over_budget",
        )
```

修改为：
```python
async def perform_safety_check(request: SafetyCheckRequest):
    from safety_gateway.health.memory_monitor import MemoryMonitor
    from safety_gateway.sg_config import sg_settings as _settings
    from safety_gateway.checker import _get_health_checker

    if MemoryMonitor.is_over_budget():
        raise ServiceUnavailableError(
            error_message="安全网关内存超预算，拒绝新请求（E4005）",
            detail="sg_memory_over_budget",
        )

    health_checker = _get_health_checker()
    if health_checker._latency_tracker.is_throttling:
        raise ServiceUnavailableError(
            error_message="安全网关P99延迟持续超限，拒绝新请求（E4005）",
            detail="sg_p99_throttling",
        )
```

#### 2. 测试修复：scan_iter mock → scan mock
- **文件**: `tests/unit/sec/test_consent_withdrawal_consumer.py`
- **问题**: 测试仍 mock `scan_iter`，但生产代码已改为 `scan()`
- **修复**: 4处 `scan_iter` mock 改为 `scan` mock

| 行号 | 旧代码 | 新代码 |
|------|--------|--------|
| 68 | `mock_redis.scan_iter = AsyncMock(return_value=[])` | `mock_redis.scan = AsyncMock(return_value=(0, []))` |
| 223 | `mock_redis.scan_iter = AsyncMock(return_value=[])` | `mock_redis.scan = AsyncMock(return_value=(0, []))` |
| 226 | `mock_redis.scan_iter.assert_called()` | `mock_redis.scan.assert_called()` |
| 251 | `mock_redis.scan_iter = AsyncMock(return_value=[])` | `mock_redis.scan = AsyncMock(return_value=(0, []))` |
| 280 | `mock_redis.scan_iter = AsyncMock(return_value=[])` | `mock_redis.scan = AsyncMock(return_value=(0, []))` |

### 预存失败（非P2修复引起，不在本次范围）
- `test_health_check_imports_redis_pool` — health_check 端点未导入 RedisPool
- `test_health_check_imports_async_engine` — health_check 端点未导入 async_engine
- `test_default_whitelist_fields` — 白名单字段不匹配（measurement vs observations）

## 实施步骤

| 步骤 | 操作 | 文件 |
|------|------|------|
| 1 | P2-21 补漏：添加 P99 限流检查 | safety_gateway/main.py |
| 2 | 测试修复：scan_iter → scan mock | tests/unit/sec/test_consent_withdrawal_consumer.py |
| 3 | 运行测试验证 | - |
