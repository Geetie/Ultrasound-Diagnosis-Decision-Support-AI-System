# Redis & PostgreSQL 代码审查计划

## 审查范围

对超声诊疗决策支持AI系统中所有涉及 Redis 和 PostgreSQL 的代码进行系统性审查，依据 `redis-development` 和 `supabase-postgres-best-practices` 两个 Skill 的最佳实践规则，识别不符合生产需求的代码模式，并给出具体修复建议。

---

## 第一部分：Redis 代码审查

### 1.1 连接管理 (conn-*)

| # | 问题 | 严重度 | 文件 | 规则 |
|---|------|--------|------|------|
| R-01 | **RedisPool 无并发初始化保护** — `app/config/redis.py` 的 `get_client()` 无 asyncio.Lock，多协程并发调用时可能创建多个连接实例 | HIGH | `app/config/redis.py:14-21` | `conn-pooling` |
| R-02 | **未配置 decode_responses** — 所有 Redis 返回值为 bytes，全代码库充斥 `.decode()` 调用，增加出错概率和代码冗余 | MEDIUM | `app/config/redis.py:16-20` | `data-choose-structure` |
| R-03 | **未配置 socket_connect_timeout** — 仅设置 `socket_timeout=5`，连接建立阶段无超时保护，DNS 解析或 TCP 握手可能无限阻塞 | HIGH | `app/config/redis.py:16-20` | `conn-timeouts` |
| R-04 | **健康检查失败后直接置空客户端** — `RedisPool.health_check()` 在失败时 `cls._client = None`，无并发保护，可能导致后续请求获取到 None | MEDIUM | `app/config/redis.py:30-38` | `conn-pooling` |

### 1.2 数据结构与键命名 (data-*)

| # | 问题 | 严重度 | 文件 | 规则 |
|---|------|--------|------|------|
| R-05 | **Token 数据使用 JSON String 而非 Hash** — `refresh_token:{token_id}` 存储完整 JSON 字符串，每次读取/更新需全量序列化/反序列化，部分更新（如 last_used_at）效率极低 | HIGH | `app/security/refresh_token.py:434` | `data-choose-structure` |
| R-06 | **scan_iter 生产环境性能风险** — `refresh_token.py` 的 `cleanup_expired_tokens()`、`get_token_count()` 使用 `scan_iter("refresh_token:*")` 全库扫描，Token 数量大时阻塞事件循环 | HIGH | `app/security/refresh_token.py:606,635` | `conn-blocking` |
| R-07 | **hsm.py 的 scan_iter 无 COUNT 限制** — `_restore_keys_from_redis()` 和 `_migrate_legacy_keys()` 使用 `scan_iter` 无 count 参数，大量密钥时单次 SCAN 返回过多键 | MEDIUM | `app/security/hsm.py:934,1000` | `conn-blocking` |
| R-08 | **consent_withdrawal_consumer 的 scan_iter** — `SafetyActionHandler` 和 `DataCleanupHandler` 使用 `scan_iter(f"edge:session:*:{patient_id}")` 和 `scan_iter(f"desens:session:*:{patient_id}")`，模式匹配效率低 | MEDIUM | `app/security/consent_withdrawal_consumer.py:85,138` | `conn-blocking` |
| R-09 | **emergency_signing 的 scan_iter** — `get_pending_verification_reports()` 使用 `scan_iter` 遍历待验证报告，应使用 Redis Set 或 List 维护索引 | MEDIUM | `app/security/emergency_signing.py:250` | `data-choose-structure` |
| R-10 | **键命名不一致** — 部分键使用 `:` 分隔（`hsm:keys:`, `consent:withdrawn:`），部分使用 `_`（`user_tokens:`, `token_family:`），缺乏统一命名规范 | LOW | 多文件 | `data-key-naming` |

### 1.3 TTL 与内存管理 (ram-*)

| # | 问题 | 严重度 | 文件 | 规则 |
|---|------|--------|------|------|
| R-11 | **user_tokens Set 无 TTL** — `user_tokens:{user_id}` Redis Set 永不过期，即使用户所有 Token 已过期/撤销，Set 仍残留，造成内存泄漏 | HIGH | `app/security/refresh_token.py:436` | `ram-ttl` |
| R-12 | **family_tokens Set 无 TTL** — `family_tokens:{family_id}` 同样永不过期 | HIGH | `app/security/refresh_token.py:438` | `ram-ttl` |
| R-13 | **token_family String 无 TTL** — `token_family:{token_id}` 映射键无过期时间 | MEDIUM | `app/security/refresh_token.py:437` | `ram-ttl` |
| R-14 | **HSM 密钥 TTL 过长** — 密钥 TTL 设为 `86400*365`（1年），对于安全关键数据过长，且与 SoftHSM 90天过期策略不一致 | MEDIUM | `app/security/hsm.py:914,918` | `ram-ttl` |
| R-15 | **Docker 未配置 maxmemory-policy** — `docker-compose.dev.yml` 中 Redis 未设置 `maxmemory` 和 `maxmemory-policy`，内存满时默认策略为 `noeviction`，将导致写入失败 | HIGH | `docker-compose.dev.yml` | `ram-limits` |
| R-16 | **incr + expire 竞态条件** — `emergency_signing.py` 的 `_increment_signature_count_persistent()` 先 `incr` 再 `expire`，若 `incr` 后进程崩溃，键将永不过期；`desensitization_restore.py` 同样存在此问题 | HIGH | `app/security/emergency_signing.py:65-66`, `app/security/desensitization_restore.py:171-173` | `ram-ttl` |

### 1.4 Pipeline 与原子性 (conn-pipelining)

| # | 问题 | 严重度 | 文件 | 规则 |
|---|------|--------|------|------|
| R-17 | **verify_refresh_token 非原子更新** — 先 `get` 读取 Token 数据，修改 `last_used_at` 后单独 `set` 写回，两个操作之间可能被其他请求覆盖 | MEDIUM | `app/security/refresh_token.py:495-496` | `conn-pipelining` |
| R-18 | **revoke_all_user_tokens 逐个操作** — 循环中对每个 Token 单独 `get` + `set` + `srem`，应使用 Pipeline 批量处理 | HIGH | `app/security/refresh_token.py:572-594` | `conn-pipelining` |
| R-19 | **revoke_token_family 中 get + pipeline 混用** — 先循环 `get` 每个 Token 数据，再批量 Pipeline 写入，读取阶段未使用 Pipeline | MEDIUM | `app/security/refresh_token.py:534-564` | `conn-pipelining` |
| R-20 | **emergency_signing enable 多次独立 set** — `enable_emergency_signing()` 连续 5 次 `redis.set()`，应使用 Pipeline | MEDIUM | `app/security/emergency_signing.py:100-127` | `conn-pipelining` |
| R-21 | **emergency_signing disable 多次独立 delete** — `disable_emergency_signing()` 连续 5 次 `redis.delete()`，应使用 Pipeline | LOW | `app/security/emergency_signing.py:151-155` | `conn-pipelining` |

### 1.5 Stream 使用 (stream-*)

| # | 问题 | 严重度 | 文件 | 规则 |
|---|------|--------|------|------|
| R-22 | **Stream 无 MAXLEN 限制** — `consent:withdrawal` 和 `consent:withdrawal:dead_letter` Stream 无 XTRIM/MAXLEN，将无限增长 | HIGH | `app/security/consent_withdrawal_consumer.py`, `app/repositories/consent_repository.py` | `stream-choosing-pattern` |
| R-23 | **xadd 未指定 MAXLEN** — `consent_repository.py` 的 `_publish_consent_withdrawal()` 和 `consent_withdrawal_consumer.py` 的 `_handle_processing_error()` 调用 `xadd` 时未设置 `maxlen` | HIGH | `app/repositories/consent_repository.py:164`, `app/security/consent_withdrawal_consumer.py:319` | `stream-choosing-pattern` |
| R-24 | **无 XAUTOCLAIM 机制** — 消费者崩溃后，已读取但未 ACK 的消息不会被自动重新分配，可能导致消息永久挂起 | HIGH | `app/security/consent_withdrawal_consumer.py` | `stream-choosing-pattern` |
| R-25 | **xgroup_create 异常静默吞没** — 消费者组已存在时抛出异常被 `except Exception: pass` 吞没，可能掩盖其他错误 | MEDIUM | `app/security/consent_withdrawal_consumer.py:208-216` | `observe-commands` |

### 1.6 Pub/Sub (stream-*)

| # | 问题 | 严重度 | 文件 | 规则 |
|---|------|--------|------|------|
| R-26 | **Pub/Sub 无消息确认机制** — `desensitization.py` 的 `subscribe_rules_updates()` 和 `threshold.py` 的 `_handle_threshold_update()` 使用 Pub/Sub，消息无持久化保证，订阅者离线期间的消息将丢失 | MEDIUM | `app/security/desensitization.py`, `safety_gateway/threshold.py` | `stream-choosing-pattern` |
| R-27 | **Pub/Sub 无消息去重** — 阈值更新和脱敏规则更新消息无 idempotency key，重复消息可能导致重复持久化 | LOW | `safety_gateway/threshold.py:429-511` | `stream-choosing-pattern` |

### 1.7 安全 (security-*)

| # | 问题 | 严重度 | 文件 | 规则 |
|---|------|--------|------|------|
| R-28 | **Redis 无认证配置** — `docker-compose.dev.yml` 未配置 `requirepass`，任何客户端可直连 Redis | CRITICAL | `docker-compose.dev.yml` | `security-auth` |
| R-29 | **Redis 无 TLS** — 连接使用明文 `redis://` 协议，生产环境密钥数据（HSM 密钥、AES 密钥）在网络上明文传输 | CRITICAL | `app/config/settings.py:10`, `docker-compose.dev.yml` | `security-network` |
| R-30 | **HSM 私钥存于 Redis** — 即使经 Fernet 加密，将 HSM 私钥存储在 Redis 中仍违反最小权限原则，任何 Redis 访问者可获取加密后的私钥 | HIGH | `app/security/hsm.py:914-918` | `security-acls` |
| R-31 | **无 Redis ACL 配置** — 所有服务共享同一 Redis 实例的完整权限，应按服务/模块配置最小权限 ACL | HIGH | `docker-compose.dev.yml` | `security-acls` |

### 1.8 可观测性 (observe-*)

| # | 问题 | 严重度 | 文件 | 规则 |
|---|------|--------|------|------|
| R-32 | **无 Redis 慢查询日志配置** — Docker 未配置 `slowlog-log-slower-than` | LOW | `docker-compose.dev.yml` | `observe-commands` |
| R-33 | **无 Redis 监控指标采集** — 未配置 Redis Exporter 或 Prometheus 指标 | LOW | `docker-compose.dev.yml` | `observe-metrics` |

### 1.9 优雅降级与错误处理

| # | 问题 | 严重度 | 文件 | 规则 |
|---|------|--------|------|------|
| R-34 | **RedisPool.get() 返回 None 语义模糊** — 无法区分"键不存在"和"Redis 错误"，调用方（如 diagnosis.py 幂等性检查）可能误判 | HIGH | `app/config/redis.py:41-51`, `app/api/v1/diagnosis.py:52-55` | — |
| R-35 | **HSM _safe_run_async_task 即发即弃** — 密钥持久化/恢复操作通过 `create_task` 异步执行，任务失败仅记录日志，不向上层传播错误 | HIGH | `app/security/hsm.py:174-197` | — |
| R-36 | **RedisPool.get/setex 异常时置空客户端** — `health_check` 失败后置空 `_client`，但 `get/setex` 失败时未重置客户端，可能导致后续请求持续使用断开的连接 | MEDIUM | `app/config/redis.py:30-51` | `conn-pooling` |

---

## 第二部分：PostgreSQL 代码审查

### 2.1 连接管理 (conn-*)

| # | 问题 | 严重度 | 文件 | 规则 |
|---|------|--------|------|------|
| P-01 | **未配置 pool_recycle** — SQLAlchemy 引擎未设置 `pool_recycle`，长连接可能因 PostgreSQL `idle_in_transaction_session_timeout` 或网络中断而失效 | HIGH | `app/config/database.py:8-16`, `safety_gateway/sg_db.py:14-22` | `conn-pooling` |
| P-02 | **未配置语句超时** — 引擎未通过 `connect_args={"statement_timeout": "..."}` 设置语句级超时，依赖应用层 `db_query_timeout` 但未在数据库层强制执行 | HIGH | `app/config/database.py:8-16` | `conn-timeouts` |
| P-03 | **get_db_session 自动 commit** — `yield session` 后自动 `commit()`，多操作场景下部分成功部分失败时可能提交不完整数据 | HIGH | `app/config/database.py:31-39` | — |
| P-04 | **sg_db.py 懒加载无并发保护** — `_get_async_engine()` 和 `_get_async_session_local()` 使用全局变量 + `if is None` 模式，多线程/协程并发时可能创建多个引擎实例 | MEDIUM | `safety_gateway/sg_db.py:11-36` | `conn-pooling` |
| P-05 | **主服务连接池过大** — compact 部署模式下 `pool_size=20, max_overflow=10` 最多 30 个连接，对于资源受限环境可能过多 | MEDIUM | `app/config/settings.py:29-30` | `conn-pooling` |

### 2.2 Schema 设计 (schema-*)

| # | 问题 | 严重度 | 文件 | 规则 |
|---|------|--------|------|------|
| P-06 | **缺少外键约束** — 所有表之间无 FK 约束（如 `DiagnosisSession.patient_id` 不引用 `Patient.patient_id`），数据完整性完全依赖应用层 | CRITICAL | `app/models/database.py` | `schema-design` |
| P-07 | **JSONB 列无 GIN 索引** — `DiagnosisSession.target_organs`、`DiagnosisSession.organ_statuses`、`DiagnosisResult.findings`、`SafetyEvent.payload`、`AuditLog.detail` 等 JSONB 列均未创建 GIN 索引，JSON 内查询将全表扫描 | HIGH | `app/models/database.py` | `schema-partial-indexes` |
| P-08 | **DiagnosisSession 缺少关键索引** — `patient_id` 和 `status` 列频繁用于 WHERE 条件但无独立索引（仅 `session_id` 为主键） | HIGH | `app/models/database.py:40-54` | `query-missing-indexes` |
| P-09 | **ConsentRecord 缺少 created_at 索引** — `get_latest_consent()` 使用 `ORDER BY created_at DESC LIMIT 1`，但 `created_at` 无索引，每次查询需文件排序 | HIGH | `app/models/database.py:23-37`, `app/repositories/consent_repository.py:69-75` | `query-missing-indexes` |
| P-10 | **无部分索引优化** — 常见查询模式如"活跃会话"（status IN ('pending', 'in_progress')）无部分索引，每次扫描全表 | MEDIUM | `app/models/database.py` | `schema-partial-indexes` |
| P-11 | **缺少 updated_at 触发器** — 所有 `updated_at` 列依赖 SQLAlchemy `onupdate` 参数，但数据库层面无触发器，直接 SQL 更新将不刷新时间戳 | LOW | `app/models/database.py` | `schema-design` |
| P-12 | **DesensitizationMapping 未纳入 Alembic** — `desensitization_mapping.py` 的模型使用独立 Base，不在 Alembic 迁移管理范围内 | HIGH | `app/models/desensitization_mapping.py`, `alembic/env.py` | `schema-design` |

### 2.3 查询性能 (query-*)

| # | 问题 | 严重度 | 文件 | 规则 |
|---|------|--------|------|------|
| P-13 | **audit_service verify_chain 全表加载** — `verify_chain()` 无 session_id 时加载全部审计日志到 Python 内存，O(n) 内存消耗，日志量大时 OOM 风险 | CRITICAL | `app/services/audit_service.py:120-155` | `query-optimization` |
| P-14 | **verify_all_chains N+1 查询** — 先查所有 distinct session_id，再逐个调用 `verify_chain()`，每个 session_id 执行一次全量查询 | HIGH | `app/services/audit_service.py:157-173` | `query-optimization` |
| P-15 | **archive_expired_sessions 逐行处理** — 加载所有过期会话到内存后逐个创建 Archive + 删除 Session，应使用批量 INSERT...SELECT + DELETE | HIGH | `app/services/data_retention.py:21-61` | `query-optimization` |
| P-16 | **re_encrypt_with_new_key 无分页提交** — 虽有 `batch_size=1000`，但 while 循环中每次重新查询，且 `commit()` 在循环内，可能导致长事务 | MEDIUM | `app/security/desensitization_restore.py:294-348` | `query-optimization` |
| P-17 | **get_latest_consent 无覆盖索引** — 查询 `WHERE patient_id=? ORDER BY created_at DESC LIMIT 1`，当前仅有 `idx_consent_patient_status(patient_id, status)` 复合索引，无法利用索引排序 | HIGH | `app/repositories/consent_repository.py:69-75` | `query-missing-indexes` |
| P-18 | **DiagnosisSession 状态查询无索引** — `consent_withdrawal_consumer.py` 查询 `WHERE patient_id=? AND status IN (...)` 无复合索引支持 | HIGH | `app/security/consent_withdrawal_consumer.py:46-49` | `query-missing-indexes` |

### 2.4 事务管理 (lock-*)

| # | 问题 | 严重度 | 文件 | 规则 |
|---|------|--------|------|------|
| P-19 | **consent_withdrawal_consumer 混合 DB+Redis 操作** — `SafetyActionHandler.handle_withdrawal()` 在同一逻辑中混合 DB 写入和 Redis 操作，无统一事务边界，DB 成功但 Redis 失败将导致不一致 | HIGH | `app/security/consent_withdrawal_consumer.py:34-96` | `lock-*` |
| P-20 | **threshold.py 独立会话持久化** — `_persist_threshold_to_db()` 打开独立数据库会话，与调用方不在同一事务中，可能内存更新成功但持久化失败 | MEDIUM | `safety_gateway/threshold.py:288-349` | `lock-*` |
| P-21 | **audit_service 链锁无超时** — `_get_chain_lock()` 获取的 asyncio.Lock 无获取超时，高并发下可能长时间阻塞 | MEDIUM | `app/services/audit_service.py:23-35` | `lock-*` |
| P-22 | **无 SELECT FOR UPDATE** — 关键更新操作（如同意状态变更、会话状态更新）未使用悲观锁，并发更新可能导致丢失更新 | HIGH | `app/repositories/consent_repository.py`, `app/security/consent_withdrawal_consumer.py` | `lock-*` |

### 2.5 安全与 RLS (security-*)

| # | 问题 | 严重度 | 文件 | 规则 |
|---|------|--------|------|------|
| P-23 | **无 Row-Level Security** — 多租户/多角色场景下无 RLS 策略，数据隔离完全依赖应用层查询条件 | HIGH | `app/models/database.py` | `security-rls` |
| P-24 | **硬编码数据库凭据** — `alembic.ini` 包含 `postgresql+asyncpg://admin:supersecret@localhost:5432/ultrasound_db`，`docker-compose.dev.yml` 同样硬编码密码 | CRITICAL | `alembic.ini:63`, `docker-compose.dev.yml` | `security-auth` |
| P-25 | **API Key 无盐值哈希** — `Device.api_key` 使用 SHA256 无盐值哈希，彩虹表可破解 | HIGH | `app/repositories/device_repository.py` | `security-auth` |
| P-26 | **无数据库级审计日志** — 依赖应用层 AuditLog，无法捕获直接 SQL 操作，可被绕过 | MEDIUM | — | `security-rls` |

### 2.6 迁移与 Schema 一致性

| # | 问题 | 严重度 | 文件 | 规则 |
|---|------|--------|------|------|
| P-27 | **双轨初始化机制** — `scripts/init_db.sql` 和 Alembic 迁移并存，表结构可能不一致，`init_db.sql` 为早期版本 | HIGH | `scripts/init_db.sql`, `alembic/versions/0001_initial_migration.py` | `schema-design` |
| P-28 | **新增表无迁移脚本** — `Device`、`FeedbackRecord`、`User`、`OrganDiseaseThreshold`、`DesensitizationMapping` 等表在 ORM 中定义但无对应 Alembic 迁移 | HIGH | `alembic/versions/` | `schema-design` |
| P-29 | **SafetyGateway 独立 Base 无迁移** — `sg_models.py` 使用独立 `Base`，其表变更完全不在 Alembic 管控范围内 | MEDIUM | `safety_gateway/sg_models.py`, `alembic/env.py` | `schema-design` |

---

## 第三部分：实施计划

### 阶段 1：CRITICAL 级别修复（安全合规）

1. **R-28/R-29** — Redis 添加认证和 TLS 配置
2. **P-24** — 移除硬编码凭据，使用环境变量
3. **P-06** — 评估添加外键约束的可行性（需考虑性能影响和迁移策略）
4. **P-13** — 重构 `verify_chain()` 为流式验证，避免全表加载

### 阶段 2：HIGH 级别修复（生产稳定性）

5. **R-01** — RedisPool 添加 asyncio.Lock 并发保护
6. **R-03** — 配置 socket_connect_timeout
7. **R-05** — Token 数据结构从 JSON String 迁移到 Redis Hash
8. **R-11/R-12/R-13** — 为 user_tokens/family_tokens/token_family 添加 TTL
9. **R-15** — Docker 配置 maxmemory 和 maxmemory-policy
10. **R-16** — 使用 Lua 脚本或 Pipeline 实现原子 incr+expire
11. **R-22/R-23** — Stream 添加 MAXLEN 限制
12. **R-24** — 实现 XAUTOCLAIM 消息恢复机制
13. **R-34** — 区分"键不存在"和"Redis 错误"的返回语义
14. **R-35** — HSM 持久化操作添加错误回调和重试
15. **P-01** — 配置 pool_recycle
16. **P-02** — 配置语句超时
17. **P-07** — 为 JSONB 列添加 GIN 索引
18. **P-08/P-09/P-17/P-18** — 添加缺失的数据库索引
19. **P-12/P-28** — 将所有模型纳入 Alembic 迁移管理
20. **P-19** — 重构 consent_withdrawal_consumer 的事务边界
21. **P-22** — 关键更新操作添加 SELECT FOR UPDATE
22. **P-25** — API Key 哈希添加盐值

### 阶段 3：MEDIUM 级别修复（性能优化）

23. **R-02** — 配置 decode_responses=True
24. **R-06/R-07/R-08/R-09** — 优化 scan_iter 使用或替换为更高效的数据结构
25. **R-17/R-18/R-19/R-20** — 使用 Pipeline 批量操作
26. **P-03** — 评估 get_db_session 自动 commit 策略
27. **P-04** — sg_db.py 添加并发保护
28. **P-10** — 添加部分索引
29. **P-14** — 优化 verify_all_chains 为单次查询
30. **P-15** — 使用批量 SQL 替代逐行操作
31. **P-20** — 统一 threshold.py 的事务边界
32. **P-27** — 移除 init_db.sql，统一使用 Alembic

### 阶段 4：LOW 级别修复（代码质量）

33. **R-10** — 统一键命名规范
34. **R-21** — Pipeline 批量 delete
35. **R-25** — 细化 xgroup_create 异常处理
36. **R-27** — Pub/Sub 消息添加幂等性标识
37. **R-32/R-33** — 配置 Redis 监控
38. **P-11** — 添加 updated_at 数据库触发器
39. **P-26** — 评估数据库级审计日志

---

## 审查方法

每个修复项的实施步骤：

1. **读取相关 Skill 规则文件** — 获取详细的最佳实践说明和代码示例
2. **编写/修改代码** — 按规则要求修改
3. **运行现有测试** — 确保修改不破坏现有功能
4. **验证修复** — 确认修改符合最佳实践

## 预期产出

- 每个修复项的代码变更
- 新增的索引迁移脚本
- Docker 配置更新
- 测试验证结果
