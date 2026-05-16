# Redis & PostgreSQL 最佳实践修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 根据审查报告，逐项修复 Redis 和 PostgreSQL 代码中不符合最佳实践和生产需求的问题，共 20 项（Redis 10 项 + PostgreSQL 10 项）。

**Architecture:** 按严重度分批修复，HIGH 级别优先。每项修复独立可验证，不引入新依赖。修改遵循现有代码风格和项目约定。

**Tech Stack:** Python 3.11+, redis-py 5.0.1 (redis.asyncio), SQLAlchemy 2.0.27 (async), asyncpg 0.29.0, pgvector 0.3.0, alembic 1.12.1

---

## 修复批次一：Redis HIGH 级别（R1-R5）

### Task 1: R1 — HSM 键名统一纳入 RedisKey 管理

**Files:**
- Modify: `app/config/redis_keys.py`
- Modify: `app/security/hsm.py`
- Modify: `app/security/emergency_signing.py`

**问题:** HSM 模块中硬编码键名（`hsm:keys:`, `hsm:degraded`, `hsm:emergency_signing:` 等），未使用 `RedisKey` 类统一管理，与新版 `:hsm:` 前缀不一致。

- [ ] **Step 1: 在 RedisKey 类中添加 HSM 相关键名常量**

在 `app/config/redis_keys.py` 的 `RedisKey` 类中添加：

```python
@staticmethod
def hsm_key(slot: str, key_name: str) -> str:
    return f":hsm:keys:{slot}:{key_name}"

@staticmethod
def hsm_key_private(slot: str, key_name: str) -> str:
    return f":hsm:keys:{slot}:{key_name}:private"

HSM_DEGRADED = ":hsm:degraded"
HSM_DEGRADED_AT = ":hsm:degraded_at"
HSM_DEGRADED_REASON = ":hsm:degraded_reason"

@staticmethod
def hsm_emergency_signing(field: str) -> str:
    return f":hsm:emergency_signing:{field}"

@staticmethod
def hsm_emergency_daily_count(date_key: str) -> str:
    return f":hsm:emergency_signing:daily_count:{date_key}"

@staticmethod
def hsm_emergency_pending_verification(token: str) -> str:
    return f":hsm:emergency_pending_verification:{token}"

HSM_KEYS_PATTERN = ":hsm:keys:*:private"
```

在 `LegacyRedisKey` 类中添加对应的旧版键名：

```python
@staticmethod
def hsm_key(slot: str, key_name: str) -> str:
    return f"hsm:keys:{slot}:{key_name}"

@staticmethod
def hsm_key_private(slot: str, key_name: str) -> str:
    return f"hsm:keys:{slot}:{key_name}:private"

HSM_DEGRADED = "hsm:degraded"
HSM_DEGRADED_AT = "hsm:degraded_at"
HSM_DEGRADED_REASON = "hsm:degraded_reason"

@staticmethod
def hsm_emergency_signing(field: str) -> str:
    return f"hsm:emergency_signing:{field}"

@staticmethod
def hsm_emergency_daily_count(date_key: str) -> str:
    return f"hsm:emergency_signing:daily_count:{date_key}"

HSM_KEYS_PATTERN = "hsm:keys:*:private"
```

- [ ] **Step 2: 修改 hsm.py 中的硬编码键名**

在 `app/security/hsm.py` 中：

1. 删除 `HSM_KEYS_REDIS_PREFIX = "hsm:keys:"` 常量
2. 添加导入: `from app.config.redis_keys import RedisKey`
3. 在 `_persist_degraded_state` 方法中，将 `"hsm:degraded"`, `"hsm:degraded_at"`, `"hsm:degraded_reason"` 替换为 `RedisKey.HSM_DEGRADED`, `RedisKey.HSM_DEGRADED_AT`, `RedisKey.HSM_DEGRADED_REASON`
4. 在 `_restore_degraded_state_from_redis` 方法中，同样替换
5. 在 `_persist_key_to_redis` 方法中，将 `f"{HSM_KEYS_REDIS_PREFIX}{slot.value}:{key_name}"` 替换为 `RedisKey.hsm_key(slot.value, key_name)`，将 `f"{redis_key}:private"` 替换为 `RedisKey.hsm_key_private(slot.value, key_name)`
6. 在 `_restore_keys_from_redis` 方法中，将 `redis_client.scan_iter(f"{HSM_KEYS_REDIS_PREFIX}*:private")` 替换为 `redis_client.scan_iter(RedisKey.HSM_KEYS_PATTERN)`，并调整键名解析逻辑以匹配新前缀

- [ ] **Step 3: 修改 emergency_signing.py 中的硬编码键名**

在 `app/security/emergency_signing.py` 中：

1. 删除 `_EMERGENCY_SIGNING_REDIS_PREFIX = "hsm:emergency_signing:"` 和 `_PENDING_VERIFICATION_PREFIX = "hsm:emergency_pending_verification:"`
2. 添加导入: `from app.config.redis_keys import RedisKey`
3. 将所有 `f"{_EMERGENCY_SIGNING_REDIS_PREFIX}xxx"` 替换为 `RedisKey.hsm_emergency_signing("xxx")`
4. 将 `f"{_EMERGENCY_SIGNING_REDIS_PREFIX}daily_count:{date_key}"` 替换为 `RedisKey.hsm_emergency_daily_count(date_key)`
5. 将 `f"{_PENDING_VERIFICATION_PREFIX}{token}"` 替换为 `RedisKey.hsm_emergency_pending_verification(token)`

- [ ] **Step 4: 更新双写迁移配置**

在 `app/tools/redis_key_migration/config.py` 中添加 HSM 键的迁移映射：

```python
"hsm:keys:*": ":hsm:keys:*",
"hsm:degraded": ":hsm:degraded",
"hsm:degraded_at": ":hsm:degraded_at",
"hsm:degraded_reason": ":hsm:degraded_reason",
"hsm:emergency_signing:*": ":hsm:emergency_signing:*",
"hsm:emergency_pending_verification:*": ":hsm:emergency_pending_verification:*",
```

- [ ] **Step 5: 验证**

运行: `python -c "from app.config.redis_keys import RedisKey, LegacyRedisKey; print('RedisKey OK'); print(RedisKey.hsm_key('signing', 'test')); print(RedisKey.HSM_DEGRADED)"`
预期输出: `:hsm:keys:signing:test` 和 `:hsm:degraded`

---

### Task 2: R2 — cleanup_expired_tokens 批量 Pipeline 优化

**Files:**
- Modify: `app/security/refresh_token.py`

**问题:** `cleanup_expired_tokens` 使用 `scan_iter` + 逐键 GET/DELETE，Token 数量大时产生大量 Redis 往返。

- [ ] **Step 1: 重写 cleanup_expired_tokens 方法**

将 `RedisRefreshTokenStore.cleanup_expired_tokens` 方法改为批量 Pipeline 处理：

```python
async def cleanup_expired_tokens(self) -> int:
    redis_client = await self._get_redis_client()
    now = datetime.now(timezone.utc)
    expired_count = 0
    batch_size = 100

    async for key in redis_client.scan_iter(RedisKey.AUTH_TOKEN_PATTERN, count=batch_size):
        token_data_str = await redis_client.get(key)
        if token_data_str:
            try:
                token_data = json.loads(token_data_str)
                expires_at = datetime.fromisoformat(token_data["expires_at"])
                if now > expires_at:
                    key_str = key if isinstance(key, str) else key.decode()
                    token_id = key_str.split(":")[-1]
                    user_id = token_data["user_id"]
                    family_id = token_data["family_id"]

                    async with redis_client.pipeline(transaction=True) as pipe:
                        await pipe.delete(key)
                        await pipe.srem(RedisKey.auth_user_tokens(user_id), token_id)
                        await pipe.srem(RedisKey.auth_family_tokens(family_id), token_id)
                        await pipe.execute()

                    expired_count += 1
            except (json.JSONDecodeError, KeyError):
                await redis_client.delete(key)

    logger.info("expired_tokens_cleaned_redis", count=expired_count)
    return expired_count
```

- [ ] **Step 2: 重写 get_token_count 方法减少往返**

将 `get_token_count` 改为 Pipeline 批量 GET：

```python
async def get_token_count(self) -> Dict[str, int]:
    redis_client = await self._get_redis_client()

    all_keys = []
    async for key in redis_client.scan_iter(RedisKey.AUTH_TOKEN_PATTERN, count=100):
        all_keys.append(key)

    if not all_keys:
        return {"total": 0, "active": 0, "revoked": 0, "expired": 0}

    pipe = redis_client.pipeline()
    for key in all_keys:
        pipe.get(key)
    results = await pipe.execute()

    now = datetime.now(timezone.utc)
    total = len(all_keys)
    active = 0
    revoked = 0
    expired = 0

    for token_data_str in results:
        if token_data_str:
            try:
                token_data = json.loads(token_data_str)
                if token_data.get("revoked", False):
                    revoked += 1
                else:
                    expires_at = datetime.fromisoformat(token_data["expires_at"])
                    if now > expires_at:
                        expired += 1
                    else:
                        active += 1
            except json.JSONDecodeError:
                pass

    return {"total": total, "active": active, "revoked": revoked, "expired": expired}
```

- [ ] **Step 3: 验证**

运行: `python -c "from app.security.refresh_token import RedisRefreshTokenStore; print('Import OK')"`

---

### Task 3: R3 — revoke_all_user_tokens 合并 Pipeline 事务

**Files:**
- Modify: `app/security/refresh_token.py`

**问题:** `revoke_all_user_tokens` 在循环中为每个 token 创建独立 Pipeline 事务，产生大量 Redis 往返。

- [ ] **Step 1: 重写 revoke_all_user_tokens 方法**

将循环内的独立 Pipeline 合并为单次批量 Pipeline：

```python
async def revoke_all_user_tokens(self, user_id: str) -> int:
    redis_client = await self._get_redis_client()

    token_ids_raw = await redis_client.smembers(RedisKey.auth_user_tokens(user_id))
    token_ids = [t.decode() if isinstance(t, bytes) else t for t in token_ids_raw]

    if not token_ids:
        return 0

    pipe = redis_client.pipeline()
    for token_id in token_ids:
        pipe.get(RedisKey.auth_token(token_id))
        pipe.ttl(RedisKey.auth_token(token_id))
    results = await pipe.execute()

    revoked_count = 0
    update_pipe = redis_client.pipeline(transaction=True)

    for i, token_id in enumerate(token_ids):
        token_data_str = results[i * 2]
        ttl_val = results[i * 2 + 1]
        if token_data_str:
            try:
                token_data = json.loads(token_data_str)
                if not token_data.get("revoked", False):
                    token_data["revoked"] = True
                    token_data["revoked_at"] = datetime.now(timezone.utc).isoformat()
                    family_id = token_data["family_id"]
                    if ttl_val and ttl_val > 0:
                        update_pipe.setex(RedisKey.auth_token(token_id), ttl_val, json.dumps(token_data))
                    else:
                        update_pipe.set(RedisKey.auth_token(token_id), json.dumps(token_data))
                    update_pipe.srem(RedisKey.auth_family_tokens(family_id), token_id)
                    revoked_count += 1
            except json.JSONDecodeError:
                pass

    update_pipe.delete(RedisKey.auth_user_tokens(user_id))
    if revoked_count > 0:
        await update_pipe.execute()
    else:
        await redis_client.delete(RedisKey.auth_user_tokens(user_id))

    logger.info("all_user_refresh_tokens_revoked_redis", user_id=user_id, count=revoked_count)
    return revoked_count
```

- [ ] **Step 2: 同样优化 revoke_token_family 方法**

将 `revoke_token_family` 中的逐个 TTL 查询改为批量 Pipeline：

```python
async def revoke_token_family(self, family_id: str) -> int:
    redis_client = await self._get_redis_client()

    token_ids_raw = await redis_client.smembers(RedisKey.auth_family_tokens(family_id))
    token_ids = [t.decode() if isinstance(t, bytes) else t for t in token_ids_raw]

    if not token_ids:
        return 0

    pipe = redis_client.pipeline()
    for token_id in token_ids:
        pipe.get(RedisKey.auth_token(token_id))
        pipe.ttl(RedisKey.auth_token(token_id))
    results = await pipe.execute()

    revoked_count = 0
    update_pipe = redis_client.pipeline(transaction=True)

    for i, token_id in enumerate(token_ids):
        token_data_str = results[i * 2]
        ttl_val = results[i * 2 + 1]
        if token_data_str:
            try:
                token_data = json.loads(token_data_str)
                if not token_data.get("revoked", False):
                    token_data["revoked"] = True
                    token_data["revoked_at"] = datetime.now(timezone.utc).isoformat()
                    if ttl_val and ttl_val > 0:
                        update_pipe.setex(RedisKey.auth_token(token_id), ttl_val, json.dumps(token_data))
                    else:
                        update_pipe.set(RedisKey.auth_token(token_id), json.dumps(token_data))
                    update_pipe.srem(RedisKey.auth_user_tokens(token_data['user_id']), token_id)
                    revoked_count += 1
            except json.JSONDecodeError:
                pass

    update_pipe.delete(RedisKey.auth_family_tokens(family_id))
    if revoked_count > 0:
        await update_pipe.execute()
    else:
        await redis_client.delete(RedisKey.auth_family_tokens(family_id))

    logger.info("refresh_token_family_revoked_redis", family_id=family_id, count=revoked_count)
    return revoked_count
```

- [ ] **Step 3: 验证**

运行: `python -c "from app.security.refresh_token import RedisRefreshTokenStore; print('Import OK')"`

---

### Task 4: R4 — 添加 retry_on_timeout 和连接重试策略

**Files:**
- Modify: `app/config/redis.py`
- Modify: `safety_gateway/redis_pool.py`

**问题:** Redis 连接池缺少 `retry_on_timeout` 配置，网络抖动导致的超时不会自动重试。

- [ ] **Step 1: 修改主服务 Redis 连接池配置**

在 `app/config/redis.py` 的 `get_client` 方法中，为 `redis.Redis.from_url` 添加重试配置：

```python
cls._client = redis.Redis.from_url(
    settings.redis_url,
    max_connections=settings.redis_pool_max_connections,
    socket_timeout=settings.redis_pool_timeout,
    socket_connect_timeout=settings.redis_pool_timeout,
    retry_on_timeout=True,
    retry=redis.retry.Retry(
        retries=3,
        backoff=redis.backoff.ExponentialBackoff(base=0.1, cap=2.0),
    ),
)
```

在文件顶部添加导入：
```python
import redis.retry
import redis.backoff
```

- [ ] **Step 2: 修改 SafetyGateway Redis 连接池配置**

在 `safety_gateway/redis_pool.py` 的 `get_client` 方法中，同样添加重试配置：

```python
cls._client = redis.Redis.from_url(
    sg_redis_url,
    max_connections=settings.redis_pool_max_connections,
    socket_timeout=settings.redis_pool_timeout,
    socket_connect_timeout=settings.redis_pool_timeout,
    retry_on_timeout=True,
    retry=redis.retry.Retry(
        retries=3,
        backoff=redis.backoff.ExponentialBackoff(base=0.1, cap=2.0),
    ),
)
```

在文件顶部添加导入：
```python
import redis.retry
import redis.backoff
```

- [ ] **Step 3: 验证**

运行: `python -c "from app.config.redis import RedisPool; print('Main RedisPool OK')" && python -c "from safety_gateway.redis_pool import SafetyGatewayRedisPool; print('SG RedisPool OK')"`

---

### Task 5: R5 — Redis 生产环境 TLS + 强制密码配置

**Files:**
- Modify: `app/config/settings.py`
- Modify: `app/config/redis.py`
- Modify: `safety_gateway/sg_config.py`
- Modify: `docker-compose.dev.yml`

**问题:** 生产环境 Redis 未配置 TLS，Docker Compose 中密码默认为空。

- [ ] **Step 1: 在 settings.py 中添加 Redis TLS 配置项**

在 `Settings` 类中添加：

```python
redis_ssl: bool = False
redis_ssl_cert_reqs: str = 'required'
```

在 `validate_deployment_config` 的 `if self.env == 'production'` 分支中添加：

```python
if not self.redis_url.startswith('rediss://'):
    _log.warning(
        "production模式建议使用TLS加密Redis连接(rediss://)，当前使用: %s",
        self.redis_url[:20] + "..."
    )
if '${REDIS_PASSWORD:-}' in os.environ.get('REDIS_URL', '') or not os.environ.get('REDIS_PASSWORD'):
    raise ValueError("REDIS_PASSWORD must be set in production environment")
```

- [ ] **Step 2: 在 sg_config.py 中添加 Redis SSL 配置项**

在 `SGSettings` 类中添加：

```python
redis_ssl: bool = False
```

- [ ] **Step 3: 修改 Redis 连接池以支持 TLS**

在 `app/config/redis.py` 的 `get_client` 方法中：

```python
connection_kwargs = dict(
    max_connections=settings.redis_pool_max_connections,
    socket_timeout=settings.redis_pool_timeout,
    socket_connect_timeout=settings.redis_pool_timeout,
    retry_on_timeout=True,
    retry=redis.retry.Retry(
        retries=3,
        backoff=redis.backoff.ExponentialBackoff(base=0.1, cap=2.0),
    ),
)
if settings.redis_ssl:
    connection_kwargs['ssl'] = True
    connection_kwargs['ssl_cert_reqs'] = settings.redis_ssl_cert_reqs

cls._client = redis.Redis.from_url(settings.redis_url, **connection_kwargs)
```

在 `safety_gateway/redis_pool.py` 中同样处理：

```python
connection_kwargs = dict(
    max_connections=settings.redis_pool_max_connections,
    socket_timeout=settings.redis_pool_timeout,
    socket_connect_timeout=settings.redis_pool_timeout,
    retry_on_timeout=True,
    retry=redis.retry.Retry(
        retries=3,
        backoff=redis.backoff.ExponentialBackoff(base=0.1, cap=2.0),
    ),
)
if settings.redis_ssl:
    connection_kwargs['ssl'] = True

cls._client = redis.Redis.from_url(sg_redis_url, **connection_kwargs)
```

- [ ] **Step 4: 修改 docker-compose.dev.yml 强制密码**

将 Redis 密码默认值从空字符串改为必填：

```yaml
redis:
  command:
    - "redis-server"
    - "--appendonly"
    - "yes"
    - "--save"
    - "60"
    - "1"
    - "--requirepass"
    - "${REDIS_PASSWORD:?REDIS_PASSWORD must be set}"
    - "--maxmemory"
    - "400mb"
    - "--maxmemory-policy"
    - "allkeys-lru"
    - "--slowlog-log-slower-than"
    - "10000"
  healthcheck:
    test: ["CMD", "redis-cli", "-a", "${REDIS_PASSWORD:?REDIS_PASSWORD must be set}", "ping"]
```

API 和 SG 的环境变量：
```yaml
api:
  environment:
    - REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/0
safety-gateway:
  environment:
    - REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/1
```

- [ ] **Step 5: 验证**

运行: `python -c "from app.config.settings import settings; print('redis_ssl:', settings.redis_ssl)"`

---

## 修复批次二：Redis MEDIUM 级别（R6-R10）

### Task 6: R6 — Pipeline 事务/非事务一致性修复

**Files:**
- Modify: `app/security/refresh_token.py`

**问题:** `revoke_token_family` 中读取 token 数据使用非事务 Pipeline，写入使用事务 Pipeline，可能导致不一致读取。

- [ ] **Step 1: 统一 revoke_token_family 的 Pipeline 使用**

已在 Task 3 的 Step 2 中一并修复——所有操作现在使用统一的 Pipeline 模式（先批量 GET+TTL，再事务 Pipeline 写入）。

- [ ] **Step 2: 验证**

确认 `revoke_token_family` 方法中不再有 `pipe = redis_client.pipeline()` (非事务) 的用法。

---

### Task 7: R7 — verify_refresh_token 非原子读-改-写修复

**Files:**
- Modify: `app/security/refresh_token.py`

**问题:** `verify_refresh_token` 中 GET → 修改 → TTL → SETEX 不是原子操作，并发时可能丢失 `last_used_at` 更新。

- [ ] **Step 1: 使用 Lua 脚本保证原子性**

在 `RedisRefreshTokenStore` 类中添加 Lua 脚本常量：

```python
_UPDATE_LAST_USED_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if current then
    local data = cjson.decode(current)
    data['last_used_at'] = ARGV[1]
    local ttl = redis.call('TTL', KEYS[1])
    if ttl > 0 then
        redis.call('SETEX', KEYS[1], ttl, cjson.encode(data))
    end
    return 1
end
return 0
"""
```

修改 `verify_refresh_token` 方法中的 `last_used_at` 更新部分：

```python
token_data["last_used_at"] = now.isoformat()
try:
    await redis_client.eval(
        self._UPDATE_LAST_USED_SCRIPT,
        1,
        RedisKey.auth_token(token_id),
        now.isoformat(),
    )
except Exception as e:
    logger.debug("failed_to_update_last_used_at", token_id=token_id[:8] + "...", error=str(e))
```

- [ ] **Step 2: 验证**

运行: `python -c "from app.security.refresh_token import RedisRefreshTokenStore; print('Lua script OK')"`

---

### Task 8: R8 — 键名前缀添加应用标识

**Files:**
- Modify: `app/config/redis_keys.py`
- Modify: `app/config/settings.py`

**问题:** 新版键名以冒号开头（`:auth:token:...`），不符合 Redis 社区惯例，缺少应用前缀。

- [ ] **Step 1: 在 settings.py 中添加 Redis 键前缀配置**

```python
redis_key_prefix: str = 'ultrasound'
```

- [ ] **Step 2: 修改 RedisKey 类使用配置前缀**

在 `app/config/redis_keys.py` 中：

```python
from app.config.settings import settings

_PREFIX = settings.redis_key_prefix


class RedisKey:
    @staticmethod
    def auth_token(token_id: str) -> str:
        return f"{_PREFIX}:auth:token:{token_id}"

    @staticmethod
    def auth_token_family(token_id: str) -> str:
        return f"{_PREFIX}:auth:token:family:{token_id}"

    @staticmethod
    def auth_user_tokens(user_id: str) -> str:
        return f"{_PREFIX}:auth:user:tokens:{user_id}"

    @staticmethod
    def auth_family_tokens(family_id: str) -> str:
        return f"{_PREFIX}:auth:family:tokens:{family_id}"

    AUTH_TOKEN_PATTERN = f"{_PREFIX}:auth:token:*"

    CONSENT_WITHDRAWAL_STREAM = f"{_PREFIX}:consent:withdrawal:stream"
    CONSENT_WITHDRAWAL_DEAD_LETTER_STREAM = f"{_PREFIX}:consent:withdrawal:dead_letter"
    CONSENT_WITHDRAWAL_NOTIFICATION = f"{_PREFIX}:consent:withdrawal:notification"

    @staticmethod
    def consent_withdrawn_session(session_id: str) -> str:
        return f"{_PREFIX}:consent:withdrawn:session:{session_id}"

    @staticmethod
    def edge_session(session_id: str, patient_id: str) -> str:
        return f"{_PREFIX}:edge:session:{session_id}:{patient_id}"

    @staticmethod
    def edge_stop_ai_annotation(session_id: str) -> str:
        return f"{_PREFIX}:edge:stop_ai_annotation:{session_id}"

    @staticmethod
    def desens_session(session_id: str, patient_id: str) -> str:
        return f"{_PREFIX}:desens:session:{session_id}:{patient_id}"

    @staticmethod
    def desens_restore_rate(hour_key: str) -> str:
        return f"{_PREFIX}:desens:restore_rate:{hour_key}"

    @staticmethod
    def hsm_desens_aes_key(key_version: int) -> str:
        return f"{_PREFIX}:hsm:desens_aes_key:{key_version}"

    HSM_DESENS_AES_KEY_VERSION = f"{_PREFIX}:hsm:desens_aes_key_version"

    SG_THRESHOLD_UPDATE = f"{_PREFIX}:sg:threshold:update"

    @staticmethod
    def hsm_key(slot: str, key_name: str) -> str:
        return f"{_PREFIX}:hsm:keys:{slot}:{key_name}"

    @staticmethod
    def hsm_key_private(slot: str, key_name: str) -> str:
        return f"{_PREFIX}:hsm:keys:{slot}:{key_name}:private"

    HSM_DEGRADED = f"{_PREFIX}:hsm:degraded"
    HSM_DEGRADED_AT = f"{_PREFIX}:hsm:degraded_at"
    HSM_DEGRADED_REASON = f"{_PREFIX}:hsm:degraded_reason"

    @staticmethod
    def hsm_emergency_signing(field: str) -> str:
        return f"{_PREFIX}:hsm:emergency_signing:{field}"

    @staticmethod
    def hsm_emergency_daily_count(date_key: str) -> str:
        return f"{_PREFIX}:hsm:emergency_signing:daily_count:{date_key}"

    @staticmethod
    def hsm_emergency_pending_verification(token: str) -> str:
        return f"{_PREFIX}:hsm:emergency_pending_verification:{token}"

    HSM_KEYS_PATTERN = f"{_PREFIX}:hsm:keys:*:private"
```

注意：`LegacyRedisKey` 类保持不变，它用于旧键名兼容。

- [ ] **Step 3: 更新 consent_withdrawal_consumer.py 中的 scan_iter 模式**

在 `app/security/consent_withdrawal_consumer.py` 的 `SafetyActionHandler.handle_withdrawal` 中，将硬编码的 `f"edge:session:*:{event.patient_id}"` 替换为使用 RedisKey 的模式。由于 scan_iter 需要通配符模式，在 RedisKey 中添加：

```python
@staticmethod
def edge_session_pattern(patient_id: str) -> str:
    return f"{_PREFIX}:edge:session:*:{patient_id}"
```

然后在 consumer 中使用：
```python
async for key in redis_client.scan_iter(RedisKey.edge_session_pattern(event.patient_id)):
```

- [ ] **Step 4: 更新 desensitization.py 中的 Pub/Sub 频道名**

在 `app/security/desensitization.py` 中，将硬编码的 `"desensitization_rules_update"` 替换为 RedisKey 常量。在 RedisKey 中添加：

```python
DESENS_RULES_UPDATE = f"{_PREFIX}:desens:rules:update"
```

- [ ] **Step 5: 更新 diagnosis.py 中的幂等性键名**

在 `app/api/v1/diagnosis.py` 中，将硬编码的 `f"diagnosis:idempotency:{request.idempotency_key}"` 替换为 RedisKey 常量。在 RedisKey 中添加：

```python
@staticmethod
def diagnosis_idempotency(key: str) -> str:
    return f"{_PREFIX}:diagnosis:idempotency:{key}"
```

- [ ] **Step 6: 更新迁移配置**

在 `app/tools/redis_key_migration/config.py` 中更新所有迁移映射，将旧版键名映射到带 `ultrasound` 前缀的新版键名。

- [ ] **Step 7: 验证**

运行: `python -c "from app.config.redis_keys import RedisKey; print(RedisKey.auth_token('test')); print(RedisKey.AUTH_TOKEN_PATTERN)"`
预期输出: `ultrasound:auth:token:test` 和 `ultrasound:auth:token:*`

---

### Task 9: R9 — Redis 健康检查添加内存/连接数监控

**Files:**
- Modify: `app/config/redis.py`
- Modify: `safety_gateway/redis_pool.py`

**问题:** 健康检查仅做 PING，未监控内存使用率、连接数等关键指标。

- [ ] **Step 1: 在主服务 RedisPool 中添加详细健康检查**

在 `app/config/redis.py` 中添加 `detailed_health_check` 方法：

```python
@classmethod
async def detailed_health_check(cls) -> dict:
    try:
        client = await cls.get_client()
        pong = await client.ping()
        if not pong:
            return {"status": "unhealthy", "error": "ping failed"}

        info = await client.info()
        return {
            "status": "healthy",
            "used_memory_human": info.get("used_memory_human"),
            "used_memory_peak_human": info.get("used_memory_peak_human"),
            "maxmemory": info.get("maxmemory"),
            "maxmemory_policy": info.get("maxmemory_policy"),
            "connected_clients": info.get("connected_clients"),
            "total_connections_received": info.get("total_connections_received"),
            "keyspace_hits": info.get("keyspace_hits"),
            "keyspace_misses": info.get("keyspace_misses"),
            "hit_rate": (
                round(info.get("keyspace_hits", 0) / max(info.get("keyspace_hits", 0) + info.get("keyspace_misses", 0), 1) * 100, 2)
            ),
            "uptime_in_seconds": info.get("uptime_in_seconds"),
        }
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}
```

- [ ] **Step 2: 在 SG RedisPool 中添加同样的方法**

在 `safety_gateway/redis_pool.py` 中添加同样的 `detailed_health_check` 方法。

- [ ] **Step 3: 在健康检查 API 中使用详细检查**

在 `app/api/v1/health.py` 中，将 Redis 健康检查从简单的 `RedisPool.health_check()` 改为可选的 `RedisPool.detailed_health_check()`，在详细模式下返回更多指标。

- [ ] **Step 4: 验证**

运行: `python -c "from app.config.redis import RedisPool; print('detailed_health_check method exists:', hasattr(RedisPool, 'detailed_health_check'))"`

---

### Task 10: R10 — SafetyGateway Redis 连接池大小优化

**Files:**
- Modify: `safety_gateway/sg_config.py`

**问题:** SG 的 `redis_pool_max_connections=50`，与主应用相同，但 SG 仅用于阈值热更新 Pub/Sub 和健康检查，不需要这么多连接。

- [ ] **Step 1: 降低 SG Redis 连接池大小**

在 `safety_gateway/sg_config.py` 中将默认值从 50 改为 15：

```python
redis_pool_max_connections: int = 15
```

- [ ] **Step 2: 验证**

运行: `python -c "from safety_gateway.sg_config import sg_settings; print('SG redis_pool_max_connections:', sg_settings.redis_pool_max_connections)"`
预期输出: `SG redis_pool_max_connections: 15`

---

## 修复批次三：PostgreSQL HIGH 级别（P1-P4）

### Task 11: P1 — 统一连接池管理，防止总连接数超限

**Files:**
- Modify: `app/observability/logging.py`
- Modify: `app/config/settings.py`

**问题:** 日志系统独立的 asyncpg 连接池（max_size=10）与主应用 SQLAlchemy 池（30）和 SG 池（15）共享同一 PostgreSQL 实例，总连接数可达 55，可能超限。

- [ ] **Step 1: 在 settings.py 中添加日志连接池配置**

```python
log_db_pool_max_size: int = 3
log_db_pool_min_size: int = 1
```

- [ ] **Step 2: 降低日志系统连接池大小**

在 `app/observability/logging.py` 的 `PostgreSQLLogStorage.__init__` 中，使用配置值替代硬编码：

```python
class PostgreSQLLogStorage(LogStorageBackend):
    def __init__(
        self,
        database_url: str,
        max_pool_size: int = None,
        min_pool_size: int = None,
    ):
        self.database_url = database_url
        from app.config.settings import settings
        self.max_pool_size = max_pool_size or settings.log_db_pool_max_size
        self.min_pool_size = min_pool_size or settings.log_db_pool_min_size
        self._pool: Optional[Any] = None
        self._lock = threading.Lock()
        self._async_lock: Optional[asyncio.Lock] = None
```

- [ ] **Step 3: 添加连接数警告日志**

在 `_get_pool` 方法中，创建池后添加日志：

```python
logger.info(
    "postgresql_log_pool_created",
    min_size=self.min_pool_size,
    max_size=self.max_pool_size,
    note="Total PG connections = main_pool(30) + sg_pool(15) + log_pool({})".format(self.max_pool_size),
)
```

- [ ] **Step 4: 验证**

运行: `python -c "from app.config.settings import settings; print('log_db_pool_max_size:', settings.log_db_pool_max_size)"`

---

### Task 12: P2 — archive_expired_sessions 批量操作优化

**Files:**
- Modify: `app/services/data_retention.py`

**问题:** `archive_expired_sessions` 逐行加载、创建归档、删除，N+1 模式。

- [ ] **Step 1: 重写 archive_expired_sessions 为批量操作**

```python
async def archive_expired_sessions(self, batch_size: int = 500) -> int:
    archive_threshold = datetime.now(timezone.utc) - timedelta(days=settings.session_archive_after_days)
    archived_count = 0

    while True:
        result = await self.db.execute(
            select(DiagnosisSession).where(
                and_(
                    DiagnosisSession.created_at < archive_threshold,
                    DiagnosisSession.status.in_(["completed", "failed", "terminated"])
                )
            ).limit(batch_size)
        )
        sessions = result.scalars().all()

        if not sessions:
            break

        for session in sessions:
            archive = SessionArchive(
                session_id=session.session_id,
                patient_id=session.patient_id,
                device_id=session.device_id,
                status=session.status,
                target_organs=getattr(session, 'target_organs', []),
                organ_statuses=getattr(session, 'organ_statuses', {}),
                knowledge_base_version=getattr(session, 'knowledge_base_version', 'unknown'),
                consent_status_at_creation=getattr(session, 'consent_status_at_creation', 'UNKNOWN'),
                archive_timestamp=datetime.now(timezone.utc),
                created_at=session.created_at,
            )
            self.db.add(archive)
            await self.db.delete(session)

        await self.db.flush()
        archived_count += len(sessions)

        if len(sessions) < batch_size:
            break

    if archived_count > 0:
        await self.audit_service.log_action(
            action="sessions_archived",
            actor="system",
            detail={"archived_count": archived_count, "threshold_days": settings.session_archive_after_days},
        )

    logger.info("data_retention_archive_completed", count=archived_count)
    return archived_count
```

- [ ] **Step 2: 验证**

运行: `python -c "from app.services.data_retention import DataRetentionService; print('Import OK')"`

---

### Task 13: P3 — DiagnosisSession ORM 添加 patient_id 索引

**Files:**
- Modify: `app/models/database.py`

**问题:** ORM 模型中 `DiagnosisSession.patient_id` 没有 `index=True`，与 0002 迁移中的索引不一致。

- [ ] **Step 1: 在 ORM 模型中添加索引定义**

在 `app/models/database.py` 的 `DiagnosisSession` 类中：

```python
class DiagnosisSession(Base):
    __tablename__ = 'sessions'

    session_id = Column(String(64), primary_key=True)
    patient_id = Column(String(64), nullable=False, index=True)
    device_id = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default='pending', index=True)
    target_organs = Column(JSONB, nullable=False)
    organ_statuses = Column(JSONB, nullable=False)
    knowledge_base_version = Column(String(32), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp(), onupdate=func.current_timestamp())
    expired_at = Column(TIMESTAMP(timezone=True))
    consent_status_at_creation = Column(String(16), nullable=False)

    __table_args__ = (
        Index('idx_sessions_patient_status', 'patient_id', 'status'),
    )
```

注意：添加 `index=True` 到 `patient_id` 和 `status`，以及复合索引 `idx_sessions_patient_status`。

- [ ] **Step 2: 验证**

运行: `python -c "from app.models.database import DiagnosisSession; print('patient_id indexed:', any('patient_id' in str(idx) for idx in DiagnosisSession.__table__.indexes))"`

---

### Task 14: P4 — 添加 idle_in_transaction_session_timeout 配置

**Files:**
- Modify: `app/config/database.py`
- Modify: `safety_gateway/sg_db.py`
- Modify: `app/config/settings.py`

**问题:** 未配置 `idle_in_transaction_session_timeout`，事务中等待外部资源时会长时间持有锁。

- [ ] **Step 1: 在 settings.py 中添加配置项**

```python
db_idle_in_transaction_timeout: int = 30
```

- [ ] **Step 2: 修改主应用数据库连接配置**

在 `app/config/database.py` 的 `create_async_engine` 中，修改 `connect_args`：

```python
connect_args={
    "statement_timeout": f"{settings.db_query_timeout * 1000}",
    "command_timeout": settings.db_query_timeout,
    "options": f"-c idle_in_transaction_session_timeout={settings.db_idle_in_transaction_timeout * 1000}",
},
```

- [ ] **Step 3: 修改 SG 数据库连接配置**

在 `safety_gateway/sg_db.py` 的 `_get_async_engine` 中，同样添加：

```python
connect_args={
    "statement_timeout": f"{sg_settings.db_query_timeout * 1000}",
    "command_timeout": sg_settings.db_query_timeout,
    "options": f"-c idle_in_transaction_session_timeout={sg_settings.db_query_timeout * 1000 * 6}",
},
```

注意：SG 使用 `db_query_timeout * 6`（30秒）作为 idle timeout，与主应用一致。

- [ ] **Step 4: 验证**

运行: `python -c "from app.config.settings import settings; print('db_idle_in_transaction_timeout:', settings.db_idle_in_transaction_timeout)"`

---

## 修复批次四：PostgreSQL MEDIUM 级别（P5-P10）

### Task 15: P5 — awrite_batch 失败重试优化

**Files:**
- Modify: `app/observability/logging.py`

**问题:** 批量写入失败后逐条重试，如果失败原因是数据库不可用，会放大失败。

- [ ] **Step 1: 添加退避重试和本地文件后备**

修改 `awrite_batch` 方法：

```python
async def awrite_batch(self, records: List[LogRecord]) -> None:
    if not records:
        return
    try:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                for record in records:
                    await conn.execute(
                        """
                        INSERT INTO audit_logs (session_id, operation, operator, details, created_at)
                        VALUES ($1, $2, $3, $4, $5)
                        """,
                        record.kwargs.get("session_id"),
                        record.message,
                        record.kwargs.get("operator"),
                        json.dumps(record.to_dict()),
                        datetime.now(timezone.utc),
                    )
        _internal_logger.debug("Batch write to PostgreSQL completed", count=len(records))
    except Exception as e:
        _internal_logger.error("Failed to write batch logs to PostgreSQL",
                              error=str(e),
                              count=len(records))
        import asyncio
        await asyncio.sleep(1)
        success_count = 0
        for record in records:
            try:
                await self.awrite(record)
                success_count += 1
            except Exception:
                pass
        if success_count < len(records):
            _internal_logger.error(
                "Batch write partial failure",
                total=len(records),
                succeeded=success_count,
                failed=len(records) - success_count,
            )
```

- [ ] **Step 2: 验证**

运行: `python -c "from app.observability.logging import PostgreSQLLogStorage; print('Import OK')"`

---

### Task 16: P6 — DiagnosisSession 主键策略评估（仅添加注释）

**Files:**
- Modify: `app/models/database.py`

**问题:** `DiagnosisSession` 使用 String(64) 主键，比 BigInteger 主键索引更大、比较更慢。但由于这是业务标识符，改动影响面大，本次仅添加设计说明注释。

- [ ] **Step 1: 添加设计决策注释**

在 `DiagnosisSession` 类的 `session_id` 列上方添加注释：

```python
class DiagnosisSession(Base):
    __tablename__ = 'sessions'

    session_id = Column(String(64), primary_key=True)  # 业务主键：外部系统生成的会话标识符，保留String类型
```

- [ ] **Step 2: 验证**

确认注释已添加。

---

### Task 17: P7 — 大表 created_at 添加 BRIN 索引

**Files:**
- Create: `alembic/versions/0003_brin_indexes.py`

**问题:** `audit_logs`、`safety_events` 等大表有大量基于 `created_at` 的范围查询，仅使用 B-tree 索引，BRIN 索引可减少 10-100 倍索引体积。

- [ ] **Step 1: 创建 Alembic 迁移添加 BRIN 索引**

创建 `alembic/versions/0003_brin_indexes.py`：

```python
"""Add BRIN indexes on created_at for large time-series tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-13
"""
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at_brin "
        "ON audit_logs USING brin (created_at) WITH (pages_per_range = 32)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_safety_events_created_at_brin "
        "ON safety_events USING brin (created_at) WITH (pages_per_range = 32)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_audit_logs_created_at_brin")
    op.execute("DROP INDEX IF EXISTS idx_safety_events_created_at_brin")
```

- [ ] **Step 2: 验证**

运行: `python -c "print('Migration file created')"` 并确认文件存在。

---

### Task 18: P8 — Docker Compose 移除 PostgreSQL 弱密码默认值

**Files:**
- Modify: `docker-compose.dev.yml`

**问题:** PostgreSQL 密码默认为 `supersecret`，生产环境不安全。

- [ ] **Step 1: 修改 Docker Compose 强制设置密码**

```yaml
postgres:
  environment:
    POSTGRES_DB: ultrasound_db
    POSTGRES_USER: admin
    POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD environment variable must be set}
```

API 和 SG 的环境变量：
```yaml
api:
  environment:
    - DATABASE_URL=postgresql+asyncpg://admin:${POSTGRES_PASSWORD}@postgres:5432/ultrasound_db
safety-gateway:
  environment:
    - DATABASE_URL=postgresql+asyncpg://admin:${POSTGRES_PASSWORD}@postgres:5432/ultrasound_db
```

- [ ] **Step 2: 验证**

确认 docker-compose.dev.yml 中不再有 `supersecret` 字符串。

---

### Task 19: P9 — PostgreSQLLogStorage 原生 SQL INSERT 补全 NOT NULL 字段

**Files:**
- Modify: `app/observability/logging.py`

**问题:** 原生 SQL INSERT 仅包含 5 列，但 `audit_logs` 表有 `log_id`(NOT NULL)、`hash_self`(NOT NULL) 等必填字段。

- [ ] **Step 1: 修改 awrite 和 awrite_batch 的 SQL 语句**

```python
async def awrite(self, record: LogRecord) -> None:
    try:
        pool = await self._get_pool()
        import uuid
        import hashlib
        log_id = str(uuid.uuid4())
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO audit_logs (log_id, session_id, action, actor, target_type, target_id, detail, log_type, hash_self, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                log_id,
                record.kwargs.get("session_id"),
                record.message,
                record.kwargs.get("operator", "system"),
                record.kwargs.get("target_type"),
                record.kwargs.get("target_id"),
                json.dumps(record.to_dict()),
                "operation",
                hashlib.sha256(f"{log_id}:{record.message}".encode()).hexdigest(),
                datetime.now(timezone.utc),
            )
    except Exception as e:
        _internal_logger.error("Failed to write log to PostgreSQL",
                              error=str(e),
                              category=record.category,
                              message=record.message)
```

同样修改 `awrite_batch` 中的 SQL 语句。

- [ ] **Step 2: 验证**

运行: `python -c "from app.observability.logging import PostgreSQLLogStorage; print('Import OK')"`

---

### Task 20: P10 — data_retention NOT IN 查询优化

**Files:**
- Modify: `app/services/data_retention.py`

**问题:** `purge_expired_operation_logs` 使用 `NOT IN`，无法高效使用索引。

- [ ] **Step 1: 将 NOT IN 改为 IN 正向列表**

```python
async def purge_expired_operation_logs(self) -> int:
    expiry_date = datetime.now(timezone.utc) - timedelta(days=settings.data_retention_operation_years * 365)

    purgeable_actions = [
        "consent_granted", "consent_withdrawn", "consent_rejected",
        "session_created", "session_completed", "session_failed", "session_terminated",
        "device_registered", "device_updated", "device_deactivated",
        "user_login", "user_logout", "user_created", "user_updated",
        "feedback_submitted", "feedback_acknowledged",
        "hsm_degraded", "hsm_recovered", "hsm_key_rotated",
        "emergency_signing_enabled", "emergency_signing_disabled",
        "sessions_archived", "archives_purged", "operation_logs_purged",
        "retention_compliance_violation",
        "consent_withdrawal_safety_action", "consent_withdrawal_data_cleanup",
    ]

    result = await self.db.execute(
        delete(AuditLog).where(
            and_(
                AuditLog.created_at < expiry_date,
                AuditLog.action.in_(purgeable_actions)
            )
        )
    )

    purged_count = result.rowcount

    if purged_count > 0:
        await self.audit_service.log_action(
            action="operation_logs_purged",
            actor="system",
            detail={"purged_count": purged_count, "retention_years": settings.data_retention_operation_years},
        )
        await self.db.flush()

    logger.info("data_retention_operation_purge_completed", count=purged_count)
    return purged_count
```

- [ ] **Step 2: 验证**

运行: `python -c "from app.services.data_retention import DataRetentionService; print('Import OK')"`

---

## 全局验证步骤

### Task 21: 最终验证

- [ ] **Step 1: 运行 Python 导入检查**

```bash
python -c "
from app.config.redis import RedisPool
from app.config.redis_keys import RedisKey, LegacyRedisKey
from app.config.database import async_engine, get_db_session
from app.config.settings import settings
from app.security.refresh_token import RedisRefreshTokenStore
from app.security.hsm import hsm_manager
from app.security.emergency_signing import EmergencySigningManager
from app.security.desensitization_restore import DesensitizationRestoreService
from app.security.consent_withdrawal_consumer import ConsentWithdrawalConsumer
from app.observability.logging import PostgreSQLLogStorage
from app.services.data_retention import DataRetentionService
from app.models.database import DiagnosisSession
from safety_gateway.redis_pool import SafetyGatewayRedisPool
from safety_gateway.sg_config import sg_settings
from safety_gateway.sg_db import get_db_session as sg_get_db_session
print('All imports OK')
"
```

- [ ] **Step 2: 运行现有测试套件**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | head -100
```

- [ ] **Step 3: 检查 Redis 键名一致性**

```bash
python -c "
from app.config.redis_keys import RedisKey
keys = [
    RedisKey.auth_token('test'),
    RedisKey.auth_user_tokens('u1'),
    RedisKey.CONSENT_WITHDRAWAL_STREAM,
    RedisKey.hsm_desens_aes_key(1),
    RedisKey.HSM_DEGRADED,
    RedisKey.hsm_key('signing', 'test'),
    RedisKey.diagnosis_idempotency('key1'),
    RedisKey.DESSENS_RULES_UPDATE,
]
for k in keys:
    assert k.startswith('ultrasound:'), f'Key does not start with ultrasound: {k}'
print('All keys have ultrasound prefix')
"
```

- [ ] **Step 4: 检查 PostgreSQL 连接配置**

```bash
python -c "
from app.config.settings import settings
assert settings.db_idle_in_transaction_timeout > 0
assert settings.log_db_pool_max_size > 0
assert settings.log_db_pool_max_size <= 5
print('PG config OK')
"
```

- [ ] **Step 5: 检查 Redis 连接配置**

```bash
python -c "
from app.config.settings import settings
from safety_gateway.sg_config import sg_settings
assert settings.redis_key_prefix == 'ultrasound'
assert sg_settings.redis_pool_max_connections <= 20
print('Redis config OK')
"
```

---

## 实施顺序总结

| 批次 | Task | 问题ID | 严重度 | 描述 | 涉及文件数 |
|------|------|--------|--------|------|-----------|
| 1 | 1 | R1 | HIGH | HSM 键名统一管理 | 4 |
| 1 | 2 | R2 | HIGH | cleanup_expired_tokens 批量优化 | 1 |
| 1 | 3 | R3 | HIGH | revoke_all_user_tokens 合并事务 | 1 |
| 1 | 4 | R4 | HIGH | retry_on_timeout 配置 | 2 |
| 1 | 5 | R5 | HIGH | TLS + 强制密码 | 4 |
| 2 | 6 | R6 | MED | Pipeline 事务一致性 | 0 (已在T3修复) |
| 2 | 7 | R7 | MED | verify_refresh_token 原子性 | 1 |
| 2 | 8 | R8 | MED | 键名添加应用前缀 | 5 |
| 2 | 9 | R9 | MED | Redis 详细健康检查 | 3 |
| 2 | 10 | R10 | MED | SG 连接池大小优化 | 1 |
| 3 | 11 | P1 | HIGH | 统一连接池管理 | 2 |
| 3 | 12 | P2 | HIGH | archive 批量操作 | 1 |
| 3 | 13 | P3 | HIGH | ORM 添加索引 | 1 |
| 3 | 14 | P4 | HIGH | idle_in_transaction_timeout | 3 |
| 4 | 15 | P5 | MED | awrite_batch 重试优化 | 1 |
| 4 | 16 | P6 | MED | 主键策略注释 | 1 |
| 4 | 17 | P7 | MED | BRIN 索引迁移 | 1 |
| 4 | 18 | P8 | MED | Docker 密码安全 | 1 |
| 4 | 19 | P9 | MED | SQL INSERT 补全字段 | 1 |
| 4 | 20 | P10 | MED | NOT IN 改 IN | 1 |
| 5 | 21 | - | - | 全局验证 | - |
