# SEC测试套件全面优化计划

## 一、现状分析

### 1.1 测试套件规模

| 类别 | 文件数 | 测试方法数 | 总行数 |
|------|--------|-----------|--------|
| 核心功能测试 | 9 | 141 | 1,608 |
| 深度/补充测试 | 4 | 252 | 2,349 |
| 审查修复验证测试 | 8 | 248 | 3,481 |
| 回归/一致性测试 | 3 | 102 | 794 |
| 集成/E2E测试 | 2 | 66 | 984 |
| **合计** | **27** | **~809** | **~9,216** |

### 1.2 关键性能瓶颈识别

**最大阻塞项（按测试方法数排序）：**

| 排名 | 文件 | 方法数 | 行数 | 主要问题 |
|------|------|--------|------|----------|
| 1 | test_sec_token_consent_audit_deep.py | 111 | 736 | 大量与supplementary/core重复 |
| 2 | test_audit_fixes.py | 72 | 658 | 21个类、大量存在性断言 |
| 3 | test_sec_supplementary.py | 51 | 720 | 与deep文件大面积重叠 |
| 4 | test_sec_gap_fill.py | 50 | 306 | 与其他文件边界测试重叠 |
| 5 | test_security_compliance_complete.py | 47 | 697 | 与core文件功能重叠 |
| 6 | test_sec_desensitization_deep.py | 50 | 519 | 参数化密集但部分与core重叠 |
| 7 | test_white_fixes.py | 45 | 661 | 文件I/O（YAML读取） |
| 8 | test_sec_audit_regression.py | 45 | 325 | 与p0_critical重叠 |
| 9 | test_sec_hsm_deep.py | 40 | 374 | 与hsm_basic/hsm_key_slot重叠 |

### 1.3 重复测试映射（核心问题）

以下功能点在3个以上文件中重复测试：

| 功能点 | 重复文件数 | 涉及文件 |
|--------|-----------|---------|
| HSM降级状态机 | 7 | hsm_basic, security_compliance, hsm_deep, supplementary, gap_fill, white_fixes, white_round2 |
| Token家族撤销 | 5 | refresh_token, token_consent_audit_deep, supplementary, k1_fixes, p0_critical |
| 脱敏规则覆盖 | 5 | desensitization, desensitization_deep, compliance_complete, k1_fixes, e2e_flow |
| 错误码验证 | 4 | k1_fixes, token_consent_audit_deep, supplementary, audit_fixes |
| 私钥导出禁止 | 3 | hsm_basic, hsm_deep, token_consent_audit_deep |
| 应急签名日限制 | 3 | emergency_signing, hsm_deep, supplementary |
| 审计链完整性 | 3 | token_consent_audit_deep, supplementary, white_fixes |
| 三层脱敏集成 | 3 | e2e_flow, desensitization_deep, supplementary |
| 配置验证 | 3 | token_consent_audit_deep, supplementary, config_doc_consistency |

### 1.4 可视化反馈缺失

- 当前无测试执行进度显示
- 无中间步骤输出
- 无预计剩余时间
- 无阶段/模块级别进度指示

---

## 二、优化目标

| 指标 | 优化前 | 优化目标 |
|------|--------|----------|
| 测试方法总数 | ~809 | ~450-500（减少40%+） |
| 测试文件数 | 27 | 15-18 |
| 整体运行时间 | 基准值 | 减少50%+ |
| 代码覆盖率 | 基准值 | 维持或提升 |
| 可视化反馈 | 无 | 实时进度+阶段+ETA |
| 并行执行 | 无 | pytest-xdist自动并行 |

---

## 三、优化实施步骤

### 阶段1：建立基准（只读操作）

**步骤1.1：运行覆盖率基准**
- 执行 `pytest tests/unit/sec/ --cov=app.security --cov=safety_gateway --cov-report=term-missing --cov-report=html -v` 获取当前覆盖率基线
- 记录每个测试文件的执行时间：`pytest tests/unit/sec/ --durations=0`
- 保存基准数据到 `tests/unit/sec/.benchmark_baseline.json`

**步骤1.2：运行时间基准**
- 执行 `pytest tests/unit/sec/ --durations=50` 获取最慢50个测试
- 记录总运行时间

### 阶段2：测试去重与必要性审查

**步骤2.1：合并深度测试到核心测试文件**

将深度测试文件中有价值的测试合并到对应的核心测试文件，删除冗余测试：

| 操作 | 源文件 | 目标文件 | 说明 |
|------|--------|----------|------|
| 合并 | test_sec_hsm_deep.py → | test_hsm_basic.py + test_hsm_key_slot.py | 保留参数化降级状态机(8时间点)、并发签名测试；删除与core重复的槽位隔离/私钥导出/应急签名基础测试 |
| 合并 | test_sec_desensitization_deep.py → | test_desensitization.py | 保留参数化PII上下文(8×5参数)、嵌套结构测试、SG异构测试；删除与core重复的基础脱敏测试 |
| 合并 | test_sec_token_consent_audit_deep.py → | test_refresh_token.py + 新建test_audit_deep.py | Token家族深度测试合并到refresh_token；审计链深度测试移到audit_deep；删除与supplementary重复的配置/模型/错误码测试 |
| 合并 | test_sec_supplementary.py → | 各核心文件 | 并发测试合并到对应核心文件；删除与deep文件重复的降级边界/Token链/审计链/配置测试 |

**步骤2.2：合并审查修复验证测试**

| 操作 | 文件 | 说明 |
|------|------|------|
| 保留 | test_k1_fixes.py | 最新一轮修复，保留全部测试 |
| 合并到core | test_sec_craft_fixes.py | HSM填充修复→hsm_basic；Token重用检测→refresh_token；降级语义→hsm_key_slot；删除回归测试(已被core覆盖) |
| 合并到core | test_white_fixes.py | YAML规则同步→desensitization；Redis原子性→refresh_token；审计哈希链→audit_deep；HSM降级事件→hsm_basic；删除回归测试 |
| 合并到core | test_white_round2_fixes.py | 审计链验证→audit_deep；detail序列化→audit_deep；YAML转义→desensitization；删除回归测试 |
| 删除 | test_security_compliance.py | 全部测试已被hsm_basic/hsm_key_slot/refresh_token/desensitization覆盖 |
| 精简 | test_security_compliance_complete.py | 保留三层脱敏集成(10方法)和E2E边缘场景(8方法)；删除与core重复的Token/HSM/降级测试(29方法) |

**步骤2.3：合并回归/缺口测试**

| 操作 | 文件 | 说明 |
|------|------|------|
| 合并到core | test_sec_audit_regression.py | RS256强制→auth；患者姓名哈希→auth；ConsentStatus→refresh_token；Redis PubSub→desensitization；删除与其他文件重复的HSM/Token测试 |
| 合并到core | test_sec_gap_fill.py | HSM降级E2E→hsm_basic；脱敏还原边界→desensitization_restore；数据保留→config_doc_consistency；删除重复的JWT/SG配置测试 |
| 保留 | test_config_doc_consistency.py | 唯一性测试，保留 |

**步骤2.4：删除冗余测试的具体规则**

以下类型的测试标记为可删除：
1. **存在性断言**：仅验证方法/属性存在（如`test_method_exists`），已被功能测试隐含覆盖
2. **重复回归测试**：每个fix文件末尾的`TestRegression`类，与core功能测试完全重复
3. **重叠边界测试**：supplementary中的降级边界(4:59/5:01)已被hsm_deep的参数化测试(8时间点)更全面覆盖
4. **重复配置验证**：token_consent_audit_deep中的`TestConfigValidation`(9方法)与config_doc_consistency重叠
5. **重复模型字段检查**：token_consent_audit_deep中的`TestDesensitizationMappingModel`(7方法)与supplementary中的`TestDesensitizationMappingKeySpec`(2方法)重叠，保留后者更精简的版本

### 阶段3：性能优化

**步骤3.1：配置pytest-xdist并行执行**

在 `pyproject.toml` 中添加：
```toml
[tool.pytest.ini_options]
addopts = "-n auto -q --tb=short"
```

安装依赖：`pip install pytest-xdist`

**步骤3.2：优化测试隔离开销**

- 将 `reset_desensitization_engine` fixture 从 `autouse` 改为按需使用（仅脱敏相关测试文件使用）
- 将 `reset_verification_config` fixture 从 `autouse` 改为按需使用
- 在 `tests/unit/sec/conftest.py` 中定义SEC专用fixture，覆盖全局autouse fixture

**步骤3.3：优化重型测试**

| 文件 | 优化项 | 说明 |
|------|--------|------|
| test_white_fixes.py | YAML文件缓存 | 将YAML文件读取结果缓存到模块级变量，避免每个测试重复读取 |
| test_sec_p0_critical.py | FakeRedis替代AsyncMock | 使用redis_token_store_integration.py中的FakeRedisClient替代大量AsyncMock设置 |
| test_sec_supplementary.py | 删除time.sleep(0.01) | 用mock时间替代真实sleep |
| test_audit_fixes.py | 删除time.sleep(0.01) | 同上 |

**步骤3.4：参数化优化**

- test_sec_desensitization_deep.py 的20个parametrize装饰器保持不变（参数化本身不增加开销，反而减少fixture重复）
- test_sec_hsm_deep.py 的6个parametrize保持不变

### 阶段4：可视化反馈实现

**步骤4.1：创建pytest进度插件**

创建 `tests/unit/sec/conftest.py`，实现实时进度展示：

```python
import pytest
import time

class SECProgressReporter:
    """SEC测试套件实时进度报告器"""

    def __init__(self):
        self.total = 0
        self.completed = 0
        self.start_time = None
        self.current_file = None
        self.phase_start = None

    def pytest_collection_modifyitems(self, session, config, items):
        self.total = len(items)

    def pytest_runtest_logstart(self, nodeid, location):
        self.current_file = location[0]
        if self.start_time is None:
            self.start_time = time.time()
        self.phase_start = time.time()

    def pytest_runtest_logfinish(self, nodeid, location):
        self.completed += 1
        elapsed = time.time() - self.start_time
        avg = elapsed / self.completed
        remaining = avg * (self.total - self.completed)
        pct = self.completed / self.total * 100

        print(
            f"\r  [{self.completed}/{self.total}] {pct:5.1f}% | "
            f"当前: {location[2][:40]:<40} | "
            f"ETA: {remaining:5.1f}s | "
            f"总耗时: {elapsed:5.1f}s",
            end="", flush=True
        )

    def pytest_sessionfinish(self, session, exitstatus):
        if self.start_time:
            total = time.time() - self.start_time
            print(f"\n  ✓ 完成: {self.completed}/{self.total} 测试, 总耗时: {total:.1f}s")
```

**步骤4.2：添加阶段级进度**

在conftest.py中按测试类名自动归类阶段：
- 🔐 认证与授权 (auth)
- 🔑 HSM密钥管理 (hsm)
- 🔄 Token管理 (token/refresh)
- 🛡️ 脱敏引擎 (desensitization)
- 📋 审计与合规 (audit/compliance)
- 🚨 应急签发 (emergency)
- 🔒 同意管理 (consent)

**步骤4.3：配置pytest输出**

在 `pyproject.toml` 中添加：
```toml
[tool.pytest.ini_options]
addopts = "-v --tb=short --no-header"
```

### 阶段5：覆盖率验证与报告

**步骤5.1：创建覆盖率对比脚本**

创建 `tests/unit/sec/coverage_compare.py`：
- 运行优化前覆盖率 → 保存基线
- 运行优化后覆盖率 → 对比
- 输出差异报告（新增/删除/变化的覆盖行）

**步骤5.2：创建测试结果分析报告模板**

报告包含：
1. 性能优化前后对比（总运行时间、各文件运行时间、最慢10个测试）
2. 测试覆盖率变化（总体覆盖率、各模块覆盖率、新增/丢失覆盖行）
3. 测试数量变化（优化前/后方法数、文件数、参数化用例数）
4. 可视化反馈效果评估（进度显示截图、ETA准确度）
5. 去重详情（删除的测试列表及删除理由）

### 阶段6：最终验证

**步骤6.1：运行完整测试套件**
- `pytest tests/unit/sec/ -v --cov=app.security --cov=safety_gateway --cov-report=term-missing`
- 确认所有测试通过

**步骤6.2：覆盖率对比**
- 确认覆盖率不低于优化前
- 如有覆盖率下降，补充针对性测试

**步骤6.3：性能对比**
- 确认运行时间减少50%+
- 记录优化前后数据

---

## 四、优化后文件结构

```
tests/unit/sec/
├── __init__.py
├── conftest.py                          # 新建：SEC专用fixture + 进度报告器
├── test_auth.py                         # 保留 + 合入audit_regression的RS256/姓名哈希
├── test_desensitization.py              # 保留 + 合入deep的参数化PII/嵌套/SG异构 + craft的降级语义 + white的YAML同步 + gap_fill的脱敏还原边界
├── test_hsm_basic.py                    # 保留 + 合入deep的参数化降级状态机/并发 + craft的HSM填充 + white的降级事件 + gap_fill的降级E2E
├── test_hsm_key_slot.py                 # 保留 + 合入deep的槽位隔离参数化
├── test_refresh_token.py                # 保留 + 合入deep的Token家族深度 + craft的重用检测 + white的Redis原子性 + audit_regression的ConsentStatus
├── test_emergency_signing.py            # 保留（不变）
├── test_desensitization_restore.py      # 保留 + 合入gap_fill的还原边界测试
├── test_consent_withdrawal_consumer.py  # 保留（不变）
├── test_security_api.py                 # 保留（不变）
├── test_e2e_security_flow.py            # 保留（不变）
├── test_k1_fixes.py                     # 保留（最新修复，不变）
├── test_audit_deep.py                   # 新建：合入deep的审计链 + white的哈希链/序列化 + supplementary的审计完整性
├── test_config_doc_consistency.py       # 保留 + 合入gap_fill的数据保留边界
├── test_redis_token_store_integration.py # 保留（不变）
├── test_security_fixes.py               # 保留（不变）
├── coverage_compare.py                  # 新建：覆盖率对比工具
└── .benchmark_baseline.json             # 新建：基准数据
```

**删除的文件（9个）：**
- test_security_compliance.py（全部重复）
- test_security_compliance_complete.py（大部分重复，有价值部分合入e2e）
- test_sec_craft_fixes.py（合入core）
- test_white_fixes.py（合入core）
- test_white_round2_fixes.py（合入core/audit_deep）
- test_sec_hsm_deep.py（合入hsm_basic/hsm_key_slot）
- test_sec_token_consent_audit_deep.py（合入refresh_token/audit_deep）
- test_sec_desensitization_deep.py（合入desensitization）
- test_sec_supplementary.py（合入各core文件）
- test_sec_audit_regression.py（合入auth/refresh_token等）
- test_sec_gap_fill.py（合入各core文件）
- test_sec_p0_critical.py（合入refresh_token/hsm_basic）
- test_audit_fixes.py（合入各core文件，保留有价值的API Key哈希/DICOM存储等）

**优化后：27个文件 → 17个文件，~809个方法 → ~450-500个方法**

---

## 五、风险控制

| 风险 | 缓解措施 |
|------|----------|
| 覆盖率下降 | 每次删除前运行覆盖率对比，丢失覆盖则保留或补充 |
| 合并引入bug | 逐文件合并，每步运行测试验证 |
| 并行测试冲突 | 确保所有测试独立无共享状态；xdist用`--dist=loadfile`按文件分配 |
| 进度插件影响性能 | 插件仅做轻量统计，无I/O操作 |
| 删除重要测试 | 仅删除覆盖率分析确认被其他测试隐含覆盖的测试 |

---

## 六、执行顺序

1. 阶段1（基准）→ 验证：基准数据完整
2. 阶段2（去重）→ 验证：每步运行测试通过 + 覆盖率不降
3. 阶段3（性能）→ 验证：并行执行无冲突
4. 阶段4（可视化）→ 验证：进度显示正常
5. 阶段5（报告）→ 验证：报告数据准确
6. 阶段6（终验）→ 验证：运行时间减少50%+ 且覆盖率不降
