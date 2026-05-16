# Step 5: Redis 键命名规范统一 - 详细执行计划

## 一、项目概述

### 1.1 背景
当前代码库中 Redis 键命名存在不一致的问题，影响代码可维护性和可扩展性。本计划旨在统一键命名规范，提升代码质量和开发效率。

### 1.2 目标
- 统一所有 Redis 键命名规范
- 实现平滑的数据迁移（双写 + 后台迁移）
- 确保开发环境迁移后的功能完整性
- 准备生产环境迁移方案

### 1.3 当前键命名分析

| 当前键模式 | 使用位置 | 数量 | 新键模式 |
|-----------|---------|------|---------|
| `refresh_token:{token_id}` | refresh_token.py | 30+ | `:auth:token:{token_id}` |
| `user_tokens:{user_id}` | refresh_token.py | 15+ | `:auth:user:tokens:{user_id}` |
| `family_tokens:{family_id}` | refresh_token.py | 10+ | `:auth:family:tokens:{family_id}` |
| `consent:withdrawal` | 多个文件 | 5+ | `:consent:withdrawal:stream` |
| `consent:withdrawal:dead_letter` | consent_withdrawal_consumer.py | 1 | `:consent:withdrawal:dead_letter` |
| `consent:withdrawn:session:{id}` | consent_withdrawal_consumer.py | 1 | `:consent:withdrawn:session:{id}` |
| `consent:withdrawal:notification` | consent_withdrawal_consumer.py | 1 | `:consent:withdrawal:notification` |
| `edge:session:*:{patient_id}` | consent_withdrawal_consumer.py | 1 | `:edge:session:{session_id}:{patient_id}` |
| `edge:stop_ai_annotation:{id}` | consent_withdrawal_consumer.py | 1 | `:edge:stop_ai_annotation:{id}` |
| `desens:session:*:{patient_id}` | consent_withdrawal_consumer.py | 1 | `:desens:session:{session_id}:{patient_id}` |
| `desens:restore_rate:{hour_key}` | desensitization_restore.py | 1 | `:desens:restore_rate:{hour_key}` |
| `sg:threshold:update` | threshold.py | 1 | `:sg:threshold:update` |

---

## 二、键名迁移策略设计

### 2.1 统一命名规范

```
{namespace}:{category}:{entity}:{id}

说明：
- 使用冒号 `:` 作为分隔符
- 前缀为 `:`（空命名空间）或特定命名空间
- category: 业务类别（auth, consent, edge, desens, sg）
- entity: 实体名称（token, user, family, session 等）
- id: 唯一标识符
```

### 2.2 双写策略方案

#### 2.2.1 阶段划分

| 阶段 | 描述 | 持续时间 |
|------|------|---------|
| **Phase 1** | 双写阶段：同时写入新旧键 | 开发环境测试 |
| **Phase 2** | 只读新键：停止写入旧键，只读新键 | 验证阶段 |
| **Phase 3** | 清理旧键：删除所有旧键数据 | 最终清理 |

#### 2.2.2 双写实现模式

```python
# 双写装饰器模式
def dual_write(old_key_func, new_key_func):
    def decorator(func):
        async def wrapper(*args, **kwargs):
            # 获取新旧键名
            old_key = old_key_func(*args, **kwargs)
            new_key = new_key_func(*args, **kwargs)
            
            # 执行原始操作（新键）
            result = await func(*args, **kwargs)
            
            # 同时写入旧键（兼容阶段）
            if hasattr(func, '_dual_write_op'):
                await _sync_to_old_key(old_key, new_key)
            
            return result
        return wrapper
    return decorator
```

### 2.3 后台迁移程序架构

```
┌─────────────────────────────────────────────────────────────┐
│                    后台迁移程序                              │
├─────────────────────────────────────────────────────────────┤
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │  扫描模块    │ -> │  迁移模块    │ -> │  验证模块    │  │
│  │ (Scan)       │    │ (Migrate)    │    │ (Verify)     │  │
│  └──────────────┘    └──────────────┘    └──────────────┘  │
│         │                   │                   │           │
│         v                   v                   v           │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │  进度追踪    │    │  错误重试    │    │  日志记录    │  │
│  │  (Progress)  │    │  (Retry)     │    │  (Logging)   │  │
│  └──────────────┘    └──────────────┘    └──────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

#### 2.3.1 迁移流程

```
1. 扫描阶段：SCAN 遍历所有旧键
   - 使用 COUNT 参数控制每次扫描数量（默认 100）
   - 分批处理，避免阻塞

2. 迁移阶段：
   - GET/MGET 获取旧键数据
   - SET/SETEX 设置新键（保留 TTL）
   - 记录迁移状态

3. 验证阶段：
   - 比对新旧键数据一致性
   - 记录不一致项

4. 清理阶段（可选）：
   - 删除已验证的旧键
```

#### 2.3.2 性能优化方案

| 优化项 | 方案 | 预期效果 |
|--------|------|---------|
| 批量操作 | 使用 Pipeline 批量获取/设置 | 减少网络往返 |
| 限流控制 | 每秒最多处理 1000 个键 | 避免 Redis 压力过大 |
| 分片处理 | 按键前缀分片并行处理 | 提高迁移速度 |
| 增量迁移 | 监控新增键实时迁移 | 确保数据一致性 |

### 2.4 数据一致性保障

1. **原子操作**: 使用 Pipeline 和事务确保操作原子性
2. **幂等设计**: 迁移操作可重复执行，不会产生副作用
3. **断点续传**: 记录迁移进度，支持中断后继续
4. **数据校验**: 迁移前后对比数据完整性
5. **监控告警**: 设置迁移状态监控和异常告警

---

## 三、开发环境迁移实施

### 3.1 键名迁移工具开发

#### 3.1.1 工具架构

```
app/
└── tools/
    └── redis_key_migration/
        ├── __init__.py
        ├── config.py          # 迁移配置
        ├── mapper.py          # 键名映射器
        ├── scanner.py         # 键扫描器
        ├── migrator.py        # 迁移执行器
        ├── validator.py       # 验证器
        └── cli.py             # 命令行接口
```

#### 3.1.2 配置文件示例

```yaml
# app/tools/redis_key_migration/config.yaml
mappings:
  - old_pattern: "refresh_token:*"
    new_pattern: ":auth:token:*"
    ttl_preserve: true
  
  - old_pattern: "user_tokens:*"
    new_pattern: ":auth:user:tokens:*"
    ttl_preserve: true
  
  - old_pattern: "family_tokens:*"
    new_pattern: ":auth:family:tokens:*"
    ttl_preserve: true
  
  - old_pattern: "consent:withdrawal"
    new_pattern: ":consent:withdrawal:stream"
    is_stream: true

migration:
  batch_size: 100
  rate_limit: 1000
  parallel_workers: 4
  progress_file: .migrate_progress.json
```

#### 3.1.3 键名映射器实现

```python
class KeyMapper:
    """键名映射器 - 负责新旧键名转换"""
    
    def __init__(self, config: dict):
        self.mappings = config['mappings']
    
    def get_new_key(self, old_key: str) -> Optional[str]:
        """根据旧键名获取新键名"""
        for mapping in self.mappings:
            pattern = mapping['old_pattern']
            if self._matches_pattern(old_key, pattern):
                return self._transform_key(old_key, mapping)
        return None
    
    def _matches_pattern(self, key: str, pattern: str) -> bool:
        """检查键是否匹配模式"""
        # 简化实现：支持 * 通配符
        pattern_re = pattern.replace('*', '.*')
        return re.match(pattern_re, key) is not None
    
    def _transform_key(self, old_key: str, mapping: dict) -> str:
        """转换旧键名为新键名"""
        old_pattern = mapping['old_pattern']
        new_pattern = mapping['new_pattern']
        
        # 提取变量部分
        old_prefix = old_pattern[:old_pattern.index('*')] if '*' in old_pattern else old_pattern
        new_prefix = new_pattern[:new_pattern.index('*')] if '*' in new_pattern else new_pattern
        
        if '*' in old_pattern:
            suffix = old_key[len(old_prefix):]
            return new_pattern.replace('*', suffix)
        return new_pattern
```

#### 3.1.4 迁移执行器实现

```python
class RedisKeyMigrator:
    """Redis 键迁移执行器"""
    
    def __init__(self, redis_client: redis.Redis, mapper: KeyMapper, config: dict):
        self.redis_client = redis_client
        self.mapper = mapper
        self.batch_size = config.get('batch_size', 100)
        self.rate_limit = config.get('rate_limit', 1000)
        self.progress_file = config.get('progress_file', '.migrate_progress.json')
    
    async def migrate(self) -> dict:
        """执行完整迁移流程"""
        stats = {
            'scanned': 0,
            'migrated': 0,
            'failed': 0,
            'skipped': 0,
            'start_time': datetime.now(),
        }
        
        cursor = 0
        start_time = time.time()
        processed_in_second = 0
        
        while cursor != 0 or stats['scanned'] == 0:
            # 限流控制
            if processed_in_second >= self.rate_limit:
                elapsed = time.time() - start_time
                if elapsed < 1:
                    await asyncio.sleep(1 - elapsed)
                start_time = time.time()
                processed_in_second = 0
            
            # SCAN 扫描
            cursor, keys = await self.redis_client.scan(
                cursor=cursor,
                count=self.batch_size,
            )
            
            for key in keys:
                key_str = key.decode() if isinstance(key, bytes) else key
                
                # 获取新键名
                new_key = self.mapper.get_new_key(key_str)
                if not new_key:
                    stats['skipped'] += 1
                    continue
                
                # 执行迁移
                try:
                    await self._migrate_key(key_str, new_key)
                    stats['migrated'] += 1
                except Exception as e:
                    stats['failed'] += 1
                    logger.error("key_migration_failed", old_key=key_str, error=str(e))
                
                stats['scanned'] += 1
                processed_in_second += 1
            
            # 保存进度
            await self._save_progress(stats)
        
        stats['end_time'] = datetime.now()
        return stats
    
    async def _migrate_key(self, old_key: str, new_key: str) -> None:
        """迁移单个键"""
        # 获取旧键数据和 TTL
        ttl = await self.redis_client.ttl(old_key)
        data = await self.redis_client.get(old_key)
        
        if data is None:
            return
        
        # 设置新键（保留 TTL）
        if ttl > 0:
            await self.redis_client.setex(new_key, ttl, data)
        else:
            await self.redis_client.set(new_key, data)
```

### 3.2 开发环境部署与测试

#### 3.2.1 部署步骤

1. **安装依赖**: 确保 Redis 连接配置正确
2. **配置映射**: 更新 `config.yaml` 中的键名映射
3. **运行迁移**: 
   ```bash
   python -m app.tools.redis_key_migration.cli migrate
   ```
4. **验证结果**:
   ```bash
   python -m app.tools.redis_key_migration.cli verify
   ```

#### 3.2.2 测试验证

| 测试项 | 测试方法 | 预期结果 |
|--------|---------|---------|
| 键迁移正确性 | 对比新旧键数据 | 数据完全一致 |
| TTL 保留 | 检查新键 TTL | 与旧键相同 |
| 错误处理 | 模拟网络中断 | 断点续传正常 |
| 性能影响 | 监控 Redis CPU/内存 | 无明显波动 |

---

## 四、回归测试计划

### 4.1 测试用例设计

#### 4.1.1 单元测试

| 测试模块 | 测试内容 | 用例数量 |
|---------|---------|---------|
| KeyMapper | 键名映射正确性 | 10+ |
| RedisKeyMigrator | 迁移逻辑 | 15+ |
| TokenStore | 令牌 CRUD 操作 | 20+ |

#### 4.1.2 集成测试

| 测试场景 | 测试内容 | 验证点 |
|---------|---------|-------|
| 令牌创建 | 创建令牌后检查新旧键 | 双写成功 |
| 令牌验证 | 验证令牌时读取新键 | 只读新键正常 |
| 令牌吊销 | 吊销令牌时删除新旧键 | 双删成功 |
| 同意撤回 | 撤回事件发布与消费 | 流操作正常 |

#### 4.1.3 系统测试

| 测试场景 | 测试内容 | 验证标准 |
|---------|---------|---------|
| 完整认证流程 | 登录 -> 刷新 -> 吊销 | 全流程无错误 |
| 同意撤回流程 | 发起撤回 -> 处理撤回 | 数据一致性 |
| 高并发场景 | 模拟 1000+ 并发请求 | 无数据丢失 |

### 4.2 测试执行流程

```
1. 单元测试 -> 代码覆盖率 >= 80%
2. 集成测试 -> 所有用例通过
3. 系统测试 -> 全流程验证通过
4. 性能测试 -> 响应时间符合预期
```

### 4.3 测试通过标准

| 阶段 | 通过标准 |
|------|---------|
| 单元测试 | 所有用例通过，覆盖率 >= 80% |
| 集成测试 | 所有用例通过，无数据不一致 |
| 系统测试 | 所有流程正常，无错误日志 |
| 性能测试 | P95 响应时间 < 100ms |

### 4.4 问题修复流程

```
测试失败 -> 定位问题 -> 修复代码 -> 更新测试 -> 重新执行
```

---

## 五、文档与准备工作

### 5.1 迁移方案文档

**文档结构**:
- 1. 项目背景与目标
- 2. 键名规范设计
- 3. 迁移策略说明
- 4. 工具使用指南
- 5. 验证方法
- 6. 故障排除

### 5.2 开发环境验证清单

```
☐ 所有 Redis 键已成功迁移
☐ 新旧键数据完全一致
☐ 所有单元测试通过
☐ 所有集成测试通过
☐ 系统测试全流程验证通过
☐ 性能测试符合预期
☐ 日志无错误记录
```

### 5.3 生产环境迁移规划

#### 5.3.1 平滑过渡策略

```
Day 1: 部署双写代码（同时写新旧键）
Day 2: 运行后台迁移程序
Day 3: 验证迁移完整性
Day 4: 切换为只读新键
Day 5: 清理旧键数据
```

#### 5.3.2 回滚计划

| 回滚阶段 | 操作 |
|---------|------|
| 双写阶段 | 回滚代码到单写旧键 |
| 只读新键阶段 | 临时恢复双写，切换回读旧键 |
| 清理阶段 | 从备份恢复旧键数据 |

---

## 六、任务排期与资源分配

### 6.1 任务分解

| 任务 | 描述 | 负责人 | 预估时间 |
|------|------|--------|---------|
| T1 | 键名映射器开发 | Dev1 | 1 天 |
| T2 | 迁移执行器开发 | Dev1 | 2 天 |
| T3 | CLI 工具开发 | Dev2 | 1 天 |
| T4 | 单元测试编写 | Dev1 | 1 天 |
| T5 | 开发环境迁移测试 | Dev2 | 1 天 |
| T6 | 集成测试编写 | Dev1 | 2 天 |
| T7 | 文档编写 | Dev2 | 1 天 |
| T8 | 回归测试执行 | QA | 2 天 |

### 6.2 资源需求

| 资源类型 | 需求 |
|---------|------|
| 开发人员 | 2 人 |
| 测试人员 | 1 人 |
| Redis 测试环境 | 1 台（开发环境） |
| 测试数据 | 模拟生产规模数据 |

### 6.3 进度跟踪

- **每日站会**: 同步进度和阻塞问题
- **进度看板**: 使用项目管理工具跟踪任务状态
- **周报**: 汇总每周进展和风险

---

## 七、风险评估

| 风险 | 等级 | 应对措施 |
|------|------|---------|
| 数据不一致 | 高 | 双写 + 数据校验 |
| 性能影响 | 中 | 限流 + 分批处理 |
| 迁移中断 | 中 | 断点续传 + 进度保存 |
| 回滚困难 | 高 | 保留旧键直到验证完成 |

---

## 八、后续工作

1. 完成开发环境迁移后，进行代码审查
2. 准备生产环境迁移文档
3. 制定生产环境迁移时间窗口
4. 准备回滚方案和数据备份