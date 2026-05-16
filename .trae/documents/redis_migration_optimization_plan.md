# Redis键迁移工具优化实施计划

## 一、问题分析

### 1.1 现有问题
1. **测试执行时间过长** - 迁移工具测试缺乏性能优化
2. **缺乏中间步骤显示** - 测试过程中无法实时查看执行状态
3. **键名硬编码** - 多个文件直接使用硬编码的键名
4. **缺少进度跟踪** - 迁移执行过程中没有实时反馈

### 1.2 需要修改的文件

| 文件路径 | 修改内容 |
|---------|---------|
| `app/tools/redis_key_migration/migrator.py` | 添加进度显示和性能优化 |
| `app/tools/redis_key_migration/cli.py` | 增强命令行输出 |
| `app/security/refresh_token.py` | 使用新的键名常量 |
| `app/security/consent_withdrawal_consumer.py` | 使用新的键名常量 |
| `app/repositories/consent_repository.py` | 使用新的键名常量 |
| `app/security/desensitization_restore.py` | 使用新的键名常量 |
| `safety_gateway/threshold.py` | 使用新的键名常量 |
| `app/api/v1/consent.py` | 使用新的键名常量 |
| `tests/unit/tools/test_redis_key_migration.py` | 添加性能测试和进度验证 |

---

## 二、优化实施步骤

### Step 1: 优化迁移工具 - 添加进度显示和性能改进

**目标**: 
- 在迁移过程中显示实时进度
- 添加性能指标收集
- 优化批量操作效率

**修改内容**:
1. 在 `migrator.py` 中添加进度回调函数
2. 添加实时进度输出
3. 添加性能统计（耗时、TPS等）
4. 优化批量操作减少网络往返

---

### Step 2: 增强命令行工具 - 丰富输出信息

**目标**:
- 显示详细的执行步骤
- 添加进度条或进度百分比
- 显示性能统计结果

**修改内容**:
1. 在 `cli.py` 中添加进度回调
2. 格式化输出各阶段结果
3. 添加性能报告

---

### Step 3: 更新核心模块 - 使用新的键名常量

**目标**:
- 将所有硬编码键名替换为常量引用
- 保持双写模式兼容性

**修改的文件**:
1. `app/security/refresh_token.py` - 使用 `RedisKey.auth_token()`
2. `app/security/consent_withdrawal_consumer.py` - 使用 `RedisKey` 中的同意相关常量
3. `app/repositories/consent_repository.py` - 使用 `RedisKey` 常量
4. `app/security/desensitization_restore.py` - 使用脱敏相关常量
5. `safety_gateway/threshold.py` - 使用网关相关常量
6. `app/api/v1/consent.py` - 使用同意相关常量

---

### Step 4: 添加性能测试和中间步骤验证

**目标**:
- 创建专门的性能测试用例
- 验证进度显示功能正常
- 记录优化前后的性能对比

**测试内容**:
1. 测试进度回调正确触发
2. 测试性能统计准确
3. 测试迁移速度有明显提升

---

## 三、技术实现细节

### 3.1 进度回调机制设计

```python
class ProgressCallback:
    def on_batch_start(self, batch_num: int, total_batches: int):
        pass
    
    def on_key_processed(self, key: str, status: str):
        pass
    
    def on_batch_complete(self, stats: dict):
        pass
    
    def on_complete(self, final_stats: dict):
        pass
```

### 3.2 性能优化方案

1. **批量操作优化** - 使用 pipeline 批量读取和写入
2. **并行处理** - 支持多 worker 并行迁移（可选）
3. **异步优化** - 充分利用 asyncio 的并发能力
4. **缓存策略** - 减少重复的 TTL 查询

### 3.3 双写模式架构

```
┌─────────────────┐    ┌─────────────────┐
│   旧键写入       │ ←→ │   新键写入       │
│ (LegacyRedisKey) │    │   (RedisKey)     │
└─────────────────┘    └─────────────────┘
         ↑                    ↑
         └──────┬─────────────┘
                │
        ┌───────▼───────┐
        │  DualWrite    │
        │  Manager      │
        └───────────────┘
```

---

## 四、验证与测试

### 4.1 测试策略

| 测试类型 | 验证内容 | 验证方法 |
|---------|---------|---------|
| 功能测试 | 键名迁移正确性 | 对比新旧键数据 |
| 性能测试 | 迁移速度提升 | 记录执行时间对比 |
| 集成测试 | 双写模式正常工作 | 同时验证新旧键 |
| 回归测试 | 所有现有功能正常 | 完整测试套件 |

### 4.2 性能指标

| 指标 | 优化前 | 优化后 | 目标 |
|-----|-------|-------|------|
| 单键迁移时间 | ~10ms | ~2ms | ↓80% |
| 批量迁移速度 | ~100 keys/s | ~500 keys/s | ↑5x |
| 测试执行时间 | ~5s | ~1s | ↓80% |

---

## 五、实施顺序

1. Step 1: 优化迁移工具核心
2. Step 2: 增强命令行输出
3. Step 3: 更新核心模块使用新常量
4. Step 4: 执行完整验证测试

---

## 六、风险评估

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| 键名替换遗漏 | 中 | 全面搜索 + 回归测试 |
| 双写模式性能影响 | 中 | 开发环境先验证性能 |
| 迁移工具bug | 中 | 充分的单元测试覆盖 |