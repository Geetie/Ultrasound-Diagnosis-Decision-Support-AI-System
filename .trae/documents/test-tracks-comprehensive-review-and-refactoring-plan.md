# OBS/LLM/SG/API 四轨道测试体系全面审查与重构计划

## 一、现状总览

### 1.1 四轨道规模对比

| 轨道 | 单元测试文件 | 集成测试文件 | 基准测试文件 | 测试方法总数 | conftest.py | 进度可视化 |
|------|-------------|-------------|-------------|-------------|-------------|-----------|
| **OBS** | 9 | 0 | 1 | ~192 | ❌ 无 | ❌ 无 |
| **LLM** | 7 | 5 | 0 | ~302 | ❌ 无(依赖全局) | ❌ 无 |
| **SG** | 5 | 2 | 1 | ~218 | ❌ 无 | ❌ 无 |
| **API** | 12 | 0 | 0 | ~340 | ❌ 无 | ❌ 无 |
| **SEC(参考)** | 29 | 0 | 4 | ~809 | ✅ 有 | ✅ SECProgressReporter |

### 1.2 关键问题汇总

| 问题 | OBS | LLM | SG | API |
|------|-----|-----|----|-----|
| 无专用 conftest.py | ✅ | ✅ | ✅ | ✅ |
| 无进度可视化 | ✅ | ✅ | ✅ | ✅ |
| 无源码变更检测 | ✅ | N/A | ✅ | N/A |
| Fixture 重复定义 | 5文件各定义 | 全局conftest | 2文件重复sg_available | 7-8次重复test_client/manager |
| sleep 瓶颈 | 3.5s+(benchmark) | 无(单元) | 6.1s+(熔断器+Redis) | 0.05s |
| 测试内容重复 | 低 | 低 | 中(同名类覆盖) | 高(9类重复) |
| 大文件需拆分 | 无 | test_consistency.py(2689行) | test_safety_gateway.py(2949行) | test_api.py(856行) |
| 废弃API使用 | 无 | 无 | 无 | asyncio.get_event_loop() |

### 1.3 SEC 参考模型核心特征（需对齐）

1. **SECProgressReporter** — pytest 插件，单行刷新进度条 + ETA + 阶段图标 + 会话汇总柱状图
2. **源码变更检测** — SHA-256 哈希 + 持久化缓存 + 环境变量控制智能跳过
3. **Session 级共享密钥** — 预生成 RSA 密钥对，避免每个测试重复生成
4. **Redis Mock** — session 级 patch 消除外部连接
5. **轻量级对象构造** — `__new__` + 手动属性初始化绕过完整初始化
6. **阶段自动分类** — 14 个关键词自动匹配，语义化进度报告
7. **覆盖率对比工具** — baseline/compare/report 三模式
8. **专用 conftest.py** — 覆盖全局 autouse fixture，减少不必要开销

---

## 二、优化目标

| 指标 | 优化前 | 优化目标 |
|------|--------|----------|
| OBS 运行时间 | 基准值 | 减少 40%+ |
| LLM 单元测试运行时间 | 基准值 | 减少 30%+ |
| SG 运行时间 | 基准值 | 减少 35%+ |
| API 运行时间 | 基准值 | 减少 30%+ |
| 四轨道进度可视化 | 均无 | 全部实现 SEC 级进度报告器 |
| 四轨道中间结果展示 | 均无 | 全部实现步骤级追踪 |
| conftest.py | 均无 | 全部创建专用 conftest |
| 测试有效性 | 未评估 | 建立评估指标并验证 |
| SEC 级精准执行 | 仅 SEC | 四轨道全部对齐 |

---

## 三、分阶段实施计划

### 阶段 1：建立基准（只读操作）

**目标**：获取四轨道的性能和覆盖率基线数据

#### 1.1 运行时间基准

对每个轨道独立运行，记录总时间和最慢测试：

```bash
# OBS
pytest tests/unit/obs/ tests/benchmark/obs/ --durations=0 -v > .trae/baseline_obs_durations.txt
pytest tests/unit/obs/ tests/benchmark/obs/ --durations=50 -q > .trae/baseline_obs_top50.txt

# LLM（仅单元测试，集成测试需要 GPU）
pytest tests/unit/llm/ --durations=0 -v > .trae/baseline_llm_durations.txt
pytest tests/unit/llm/ --durations=50 -q > .trae/baseline_llm_top50.txt

# SG
pytest tests/unit/sg/ tests/benchmark/sg/ --durations=0 -v > .trae/baseline_sg_durations.txt
pytest tests/unit/sg/ tests/benchmark/sg/ --durations=50 -q > .trae/baseline_sg_top50.txt

# API
pytest tests/unit/api/ --durations=0 -v > .trae/baseline_api_durations.txt
pytest tests/unit/api/ --durations=50 -q > .trae/baseline_api_top50.txt
```

#### 1.2 覆盖率基准

```bash
pytest tests/unit/obs/ --cov=app.obs --cov-report=term-missing -v > .trae/baseline_obs_coverage.txt
pytest tests/unit/llm/ --cov=app.inference --cov-report=term-missing -v > .trae/baseline_llm_coverage.txt
pytest tests/unit/sg/ --cov=safety_gateway --cov-report=term-missing -v > .trae/baseline_sg_coverage.txt
pytest tests/unit/api/ --cov=app.api --cov-report=term-missing -v > .trae/baseline_api_coverage.txt
```

#### 1.3 保存基准 JSON

创建 `tests/.benchmark_baselines/` 目录，保存各轨道的基准数据：
- `{track}_baseline.json`：包含总时间、各文件时间、最慢 50 个测试、覆盖率数据

**验证**：基准数据文件完整，包含时间 + 覆盖率数据

---

### 阶段 2：创建四轨道专用 conftest.py

**目标**：为每个轨道创建 conftest.py，提取共享 fixture，实现 SEC 级进度可视化

#### 2.1 创建通用进度报告器基类

**文件**：`tests/utils/track_progress_reporter.py`

提取 SEC 的 `SECProgressReporter` 为通用 `TrackProgressReporter` 基类：

```python
class TrackProgressReporter:
    """通用轨道进度报告器"""

    PHASE_ICONS = {}  # 子类覆盖

    def __init__(self, track_name="TEST"):
        self.track_name = track_name
        self.total = 0
        self.completed = 0
        self.start_time = None
        self.current_phase = ""
        self.phase_counts = {}
        self._last_print_len = 0

    def _classify_phase(self, nodeid):
        # 通用阶段分类逻辑
        ...

    # 四个 pytest hook（同 SEC 实现）
    def pytest_collection_modifyitems(self, session, config, items): ...
    def pytest_runtest_logstart(self, nodeid, location): ...
    def pytest_runtest_logfinish(self, nodeid, location): ...
    def pytest_sessionfinish(self, session, exitstatus): ...
```

#### 2.2 OBS conftest.py

**文件**：`tests/unit/obs/conftest.py`

**内容**：
1. **OBSProgressReporter** — 继承 `TrackProgressReporter`，定义 OBS 阶段图标：
   - 📊 metrics, 📝 logging, 🚨 alert, 📈 cusum, 💾 cache, 🔗 audit, 📡 collector, 🔍 tracing
2. **共享 fixture 提取**（从 5 个文件中提取）：
   - `reset_memory_monitor_singleton`（autouse）
   - `reset_log_storage_singleton`（autouse）
   - `reset_cache_monitor_singleton`（autouse）
   - `reset_metrics_collector_singleton`（autouse）
   - `reset_tracing_context`（autouse）
   - `default_cusum_config` — `CUSUMConfig(target_rate=0.005, decision_threshold=0.5, delta=0.005)`
3. **pytest_configure** — 注册 OBSProgressReporter + marker

#### 2.3 LLM conftest.py

**文件**：`tests/unit/llm/conftest.py`

**内容**：
1. **LLMProgressReporter** — 定义 LLM 阶段图标：
   - 🧠 inference, ✅ consistency, 👻 hallucination, 🏭 factory, 📉 drift, ⚙ config, 📊 benchmark
2. **共享 fixture**：
   - `mock_inference_engine` — `MockInferenceEngine` 实例（从 test_consistency.py 提取）
   - `reset_verification_config`（autouse）— 覆盖全局 autouse，减少非 LLM 测试开销
3. **pytest_configure** — 注册 LLMProgressReporter + marker

#### 2.4 SG conftest.py

**文件**：`tests/unit/sg/conftest.py`

**内容**：
1. **SGProgressReporter** — 定义 SG 阶段图标：
   - 🛡 desensitization, ⚕ contraindication, 📏 threshold, 🔍 checker, 🔌 circuit, 📋 health, 🔒 consent, ⚠ degraded, 🔄 redis
2. **共享 fixture 提取**：
   - `mock_sg_fusion_health_check`（autouse）— 从 test_safety_gateway.py 提取
   - `sg_available`（session）— 从两个集成测试文件提取
3. **pytest_configure** — 注册 SGProgressReporter + marker
4. **源码变更检测**（对齐 SEC）：
   - `SG_SOURCE_FILES`：`safety_gateway/checker/orchestrator.py`, `safety_gateway/checker/desensitization_engine.py`
   - 环境变量：`SKIP_SG_UNCHANGED`

#### 2.5 API conftest.py

**文件**：`tests/unit/api/conftest.py`

**内容**：
1. **APIProgressReporter** — 定义 API 阶段图标：
   - 🌐 endpoint, 🔐 auth, 📋 consent, 🏥 diagnosis, 📷 imaging, 🔌 websocket, ⏱ rate_limit, ❌ error, 📡 dicom, 🛡 safety
2. **共享 fixture 提取**（从 7-8 个重复定义中提取）：
   - `test_client` — FastAPI TestClient + 依赖覆盖
   - `manager` — ConnectionManager 实例
   - `mock_db_session` — AsyncMock 数据库会话
   - `mock_consent_repo` — ConsentRepository mock
   - `mock_session_repo` — SessionRepository mock
   - `reset_rate_limit_buckets`（autouse）— 清理全局 `_buckets`
3. **pytest_configure** — 注册 APIProgressReporter + marker

**验证**：每个 conftest.py 创建后，运行对应轨道测试确认通过

---

### 阶段 3：性能优化

**目标**：消除关键瓶颈，实现可衡量的性能提升

#### 3.1 OBS 性能优化

| 优化项 | 文件 | 优化方式 | 预估节省 |
|--------|------|---------|---------|
| 消除 benchmark sleep | test_logging_performance.py | 用 `asyncio.wait_for` + 回调替代 `sleep(0.05)x100` | ~5s |
| 缩短 warmup 等待 | test_logging_performance.py | `sleep(0.5)` → `sleep(0.1)` | ~0.4s |
| 缩短 throughput 等待 | test_logging_performance.py | `sleep(1.0)` → `sleep(0.2)` + 事件通知 | ~0.8s |
| CUSUM 配置共享 | test_cusum.py | 提取为 conftest fixture，避免 8+ 处重复构造 | 微量 |
| Monitor 实例复用 | test_metrics.py | 将 `VictoriaMetricsMemoryMonitor` 改为 class scope fixture | 微量 |

#### 3.2 LLM 性能优化

| 优化项 | 文件 | 优化方式 | 预估节省 |
|--------|------|---------|---------|
| 覆盖全局 autouse | tests/conftest.py | LLM conftest 覆盖 `reset_desensitization_engine` 和 `reset_verification_config` | 每测试 ~1ms |
| MockInferenceEngine 共享 | test_consistency.py | 提取到 conftest，class scope | 微量 |
| 标记慢速测试 | test_consistency.py | `TestBoundaryCaseValidation` 等已有 `@pytest.mark.slow`，确认 CI 默认跳过 | CI: 大幅 |

#### 3.3 SG 性能优化

| 优化项 | 文件 | 优化方式 | 预估节省 |
|--------|------|---------|---------|
| 消除熔断器 sleep | test_safety_gateway.py | `sleep(1.1)` → mock `time.monotonic` 或调整 cooldown_period 为 0.01s | ~1.1s |
| 消除 Redis 恢复 sleep | test_sg_degradation_e2e.py | `sleep(5)` → 轮询 + 超时（max 1s） | ~4s |
| 源码变更检测 | conftest.py | SG 源码未变时跳过重型测试 | 重复运行: 大幅 |
| 修复重复类名 | test_safety_gateway.py | 重命名第二个 `TestRedisFailoverDegradation` | 恢复 4 个丢失测试 |

#### 3.4 API 性能优化

| 优化项 | 文件 | 优化方式 | 预估节省 |
|--------|------|---------|---------|
| 消除 time.sleep | test_api.py | mock `TokenBucket.last_refill` 时间戳 | ~0.05s |
| 缩小 PixelData | test_api_integration.py | `b'\x00' * (480*640)` → `b'\x00' * 100` | 微量 |
| 依赖覆盖封装 | test_api_integration.py | 提取为 conftest fixture，避免 10+ 次重复 setup/teardown | 每测试 ~2ms |
| 修复废弃 API | test_api_integration.py | `asyncio.get_event_loop().run_until_complete()` → `@pytest.mark.asyncio` | 兼容性 |
| 清理全局 _buckets | test_rate_limit.py | conftest autouse fixture 清理 | 并行安全 |

#### 3.5 通用优化

| 优化项 | 实施方式 | 适用轨道 |
|--------|---------|---------|
| pytest-xdist 并行 | `pyproject.toml` 添加 `-n auto --dist=loadfile` | 全部 |
| 测试分层标记 | `@pytest.mark.slow` / `@pytest.mark.integration` / `@pytest.mark.benchmark` | 全部 |
| CI 快速路径 | `pytest -m "not slow and not integration"` | 全部 |

**验证**：每项优化后运行对应轨道测试，确认时间减少且测试通过

---

### 阶段 4：测试去重与有效性审查

**目标**：消除重复测试，提升断言质量，建立有效性指标

#### 4.1 API 去重（最严重）

**重复内容与处理方案**：

| 重复内容 | 出现文件 | 处理 |
|---------|---------|------|
| TokenBucket 基础测试 | test_api.py + test_rate_limit.py | 保留 test_rate_limit.py（专项），删除 test_api.py 中的 TestRateLimitMiddleware |
| CRC32 计算 | test_api.py + test_websocket.py | 保留 test_websocket.py（DICOM 协议上下文），删除 test_api.py 中的 TestCRC32Calculation |
| WS 关闭码 | test_api.py + test_websocket.py + test_api_integration.py | 保留 test_websocket.py（专项），删除其余两处 |
| ConnectionManager 令牌 | test_api.py + test_api_phase0a.py + test_websocket.py + test_api_integration.py | 保留 test_websocket.py（最全），其余仅保留增量测试 |
| DICOM 格式校验 | test_api.py + test_imaging.py | 保留 test_imaging.py（专项），删除 test_api.py 中的 TestDICOMValidation |
| 脱敏函数 | test_api.py + test_dicom_upload.py + test_imaging.py | 保留 test_imaging.py（专项），删除其余重复 |
| 异常类属性 | test_api.py + test_errors.py + test_error_code_consistency.py | 保留 test_errors.py（最全），删除 test_api.py 中的异常测试 |
| 限流配置 | test_api.py + test_rate_limit.py + test_api_integration.py | 保留 test_rate_limit.py + test_api_integration.py 的集成测试 |

**test_api.py 拆分方案**：

将 test_api.py（856行/22类/62方法）按功能域拆分：

| 新文件 | 来源类 | 预估方法数 |
|--------|--------|-----------|
| test_repositories.py | TestConsentRepository, TestSessionRepository, TestDiagnosisRepository, TestDeviceRepository | 9 |
| test_services.py | TestAuditService, TestSafetyService | 2 |
| test_schemas_enums.py | TestSchemas, TestEnums, TestIdempotencyTTL | 7 |
| test_exceptions.py | TestErrorHandling, TestCoreExceptionClasses, TestMissingDICOMTagException | 12 |
| test_websocket_core.py | TestConnectionManager, TestWebSocketCloseCodes, TestBinaryFrameFlags, TestDiagnosisResultPush | 12 |
| test_dicom_core.py | TestDICOMValidation, TestCRC32Calculation, TestDICOMTagValidation | 13 |
| test_rate_limit_core.py | TestRateLimitMiddleware | 7 |

**删除 test_api.py 原文件**，所有内容已迁移到专项文件。

#### 4.2 SG 去重

| 重复内容 | 处理 |
|---------|------|
| 重复类名 `TestRedisFailoverDegradation` | 重命名第二个为 `TestRedisFailoverDegradationAdvanced` |
| 集成测试 `sg_available` fixture | 提取到 `tests/integration/sg/conftest.py` |

#### 4.3 LLM 去重

LLM 单元测试重复度低，主要处理：
- `MockInferenceEngine` 在 test_consistency.py 和 test_benchmark.py 中各定义一次 → 提取到 conftest

#### 4.4 OBS 去重

OBS 重复度最低，主要处理：
- 5 个文件的 autouse singleton 重置 fixture → 已在阶段 2 提取到 conftest

#### 4.5 断言质量审查

对每个轨道审查以下断言模式：

| 问题模式 | 检测方法 | 修复方式 |
|---------|---------|---------|
| 存在性断言（仅验证属性存在） | 搜索 `assert hasattr` / `assert ... is not None` | 替换为值断言 |
| 恒真断言（测试永远通过） | 搜索 `assert True` / 无 assert 的测试 | 删除或补充有效断言 |
| 过宽断言（`assert result` 而非具体值） | 代码审查 | 细化为具体值断言 |
| 异常吞没（try/except pass） | 搜索 `except.*:.*pass` | 确认测试意图或补充断言 |

#### 4.6 测试有效性指标

为每个轨道建立以下指标：

| 指标 | 计算方式 | 目标 |
|------|---------|------|
| 代码覆盖率 | `pytest --cov` | ≥ 优化前基线 |
| 变异测试得分 | `mutmut run`（抽样） | ≥ 70% |
| 断言密度 | 断言数 / 测试方法数 | ≥ 2.0 |
| 去重率 | 删除方法数 / 原方法数 | OBS: ≤10%, LLM: ≤5%, SG: ≤10%, API: ≤25% |
| 失败诊断率 | 失败测试能定位到具体 bug 的比例 | 定性评估 |

**验证**：去重后运行覆盖率对比，确认不降低

---

### 阶段 5：可视化增强

**目标**：实现 SEC 级实时进度展示和中间结果输出

#### 5.1 通用进度报告器

**文件**：`tests/utils/track_progress_reporter.py`

基于 SEC 的 `SECProgressReporter` 提取通用基类，支持：
- 单行刷新进度条：`[completed/total] pct% | 当前测试 | ETA | 总耗时`
- 阶段图标自动分类
- 会话汇总柱状图
- ETA 预估

#### 5.2 四轨道进度报告器

每个轨道的 conftest.py 中定义子类，设置各自的 `PHASE_ICONS`：

| 轨道 | 阶段图标数 | 示例 |
|------|-----------|------|
| OBS | 8 | 📊 metrics, 📝 logging, 🚨 alert, 📈 cusum, 💾 cache, 🔗 audit, 📡 collector, 🔍 tracing |
| LLM | 7 | 🧠 inference, ✅ consistency, 👻 hallucination, 🏭 factory, 📉 drift, ⚙ config, 📊 benchmark |
| SG | 9 | 🛡 desensitization, ⚕ contraindication, 📏 threshold, 🔍 checker, 🔌 circuit, 📋 health, 🔒 consent, ⚠ degraded, 🔄 redis |
| API | 10 | 🌐 endpoint, 🔐 auth, 📋 consent, 🏥 diagnosis, 📷 imaging, 🔌 websocket, ⏱ rate_limit, ❌ error, 📡 dicom, 🛡 safety |

#### 5.3 步骤级追踪增强

**文件**：`tests/conftest_step_display.py`（增强现有实现）

新增功能：
- 每个测试的 SETUP/CALL/TEARDOWN 阶段耗时细分
- 关键检查点结果输出（通过 `StepTracker.log_checkpoint()` API）
- 慢测试警告（超过 1 秒的测试标记为 ⚠️）

#### 5.4 中间结果输出机制

在 conftest.py 中提供 `checkpoint` fixture：

```python
@pytest.fixture
def checkpoint(request):
    """关键检查点结果输出"""
    def _checkpoint(name, result, detail=""):
        status = "✅" if result else "❌"
        print(f"  {status} CHECKPOINT: {name} | {detail}")
    return _checkpoint
```

测试中使用：
```python
def test_auth_flow(self, checkpoint):
    token = generate_token()
    checkpoint("token_generated", bool(token), f"length={len(token)}")
    verified = verify_token(token)
    checkpoint("token_verified", verified)
    assert verified
```

#### 5.5 执行时间统计

增强 `run_track_tests.py`，添加 `--report` 模式：

```bash
python scripts/run_track_tests.py obs --report
# 输出：
# 📊 OBS 轨道测试报告
# ━━━━━━━━━━━━━━━━━━━━━━━━
# 总测试数: 192 | 通过: 190 | 失败: 0 | 跳过: 2
# 总耗时: 12.3s
#
# 📁 按文件统计:
#   test_metrics.py       28 方法  2.1s  ████████
#   test_logging.py       32 方法  3.5s  ██████████████
#   ...
#
# 🐢 最慢 10 个测试:
#   1. test_actual_write_latency  5.2s
#   2. test_throughput            1.8s
#   ...
```

**验证**：运行各轨道测试，确认进度条和报告正常输出

---

### 阶段 6：精准执行能力对齐

**目标**：四轨道实现与 SEC 一致的精准执行能力

#### 6.1 增强 run_track_tests.py

当前 `run_track_tests.py` 仅支持按目录运行，需增加以下能力：

| 能力 | 当前 | 目标 |
|------|------|------|
| 按文件运行 | ❌ | `python scripts/run_track_tests.py obs --file test_metrics.py` |
| 按测试类运行 | ❌ | `python scripts/run_track_tests.py obs --class TestMemoryThreshold` |
| 按方法运行 | ❌ | `python scripts/run_track_tests.py obs --test test_memory_warning` |
| 按阶段运行 | ❌ | `python scripts/run_track_tests.py obs --phase metrics` |
| 排除慢速测试 | ❌ | `python scripts/run_track_tests.py obs --skip-slow` |
| 排除基准测试 | 部分 | `python scripts/run_track_tests.py obs --skip-benchmark` |
| 执行报告 | ❌ | `python scripts/run_track_tests.py obs --report` |
| 基准对比 | ❌ | `python scripts/run_track_tests.py obs --compare-baseline` |
| 仅变更测试 | ❌ | `python scripts/run_track_tests.py obs --changed-only` |

#### 6.2 源码变更检测（对齐 SEC）

为 OBS 和 SG 实现源码变更检测：

**OBS 变更检测**：
- 监控文件：`app/obs/metrics.py`, `app/obs/logging.py`, `app/obs/alert_escalation.py`
- 环境变量：`SKIP_OBS_UNCHANGED`
- Marker：`obs_heavy`

**SG 变更检测**：
- 监控文件：`safety_gateway/checker/orchestrator.py`, `safety_gateway/checker/desensitization_engine.py`
- 环境变量：`SKIP_SG_UNCHANGED`
- Marker：`sg_heavy`

#### 6.3 覆盖率对比工具（对齐 SEC）

为每个轨道创建覆盖率对比脚本：

| 轨道 | 脚本路径 | 覆盖源码范围 |
|------|---------|-------------|
| OBS | `tests/unit/obs/coverage_compare.py` | `app.obs` |
| LLM | `tests/unit/llm/coverage_compare.py` | `app.inference` |
| SG | `tests/unit/sg/coverage_compare.py` | `safety_gateway` |
| API | `tests/unit/api/coverage_compare.py` | `app.api` |

每个脚本支持三种模式：
```bash
python tests/unit/obs/coverage_compare.py --baseline   # 保存基线
python tests/unit/obs/coverage_compare.py --compare    # 对比当前
python tests/unit/obs/coverage_compare.py --report     # 完整报告
```

**验证**：使用增强后的 run_track_tests.py 运行各轨道各种模式

---

### 阶段 7：最终验证与交付

#### 7.1 运行完整测试套件

```bash
pytest tests/unit/obs/ tests/benchmark/obs/ -v --cov=app.obs
pytest tests/unit/llm/ -v --cov=app.inference
pytest tests/unit/sg/ tests/benchmark/sg/ -v --cov=safety_gateway
pytest tests/unit/api/ -v --cov=app.api
```

#### 7.2 覆盖率对比

使用各轨道的 `coverage_compare.py --compare` 确认覆盖率不低于优化前。

#### 7.3 性能对比

使用 `--durations=0` 重新运行，与阶段 1 基准对比，确认：
- OBS：减少 40%+
- LLM：减少 30%+
- SG：减少 35%+
- API：减少 30%+

#### 7.4 交付成果清单

| 交付物 | 路径 |
|--------|------|
| 通用进度报告器 | `tests/utils/track_progress_reporter.py` |
| OBS conftest + 进度报告器 | `tests/unit/obs/conftest.py` |
| LLM conftest + 进度报告器 | `tests/unit/llm/conftest.py` |
| SG conftest + 进度报告器 | `tests/unit/sg/conftest.py` |
| API conftest + 进度报告器 | `tests/unit/api/conftest.py` |
| SG 集成 conftest | `tests/integration/sg/conftest.py` |
| 增强版 run_track_tests.py | `scripts/run_track_tests.py` |
| 四轨道覆盖率对比脚本 | `tests/unit/{obs,llm,sg,api}/coverage_compare.py` |
| 基准数据 | `tests/.benchmark_baselines/` |
| 性能改进报告 | `.trae/documents/test-optimization-performance-report.md` |
| 测试有效性评估报告 | `.trae/documents/test-effectiveness-assessment.md` |

---

## 四、执行顺序与依赖关系

```
阶段1(基准) ──→ 阶段2(conftest) ──→ 阶段3(性能优化) ──→ 阶段7(终验)
                  │                        │
                  └──→ 阶段4(去重+有效性) ──┘
                  │
                  └──→ 阶段5(可视化) ──→ 阶段6(精准执行) ──→ 阶段7(终验)
```

**关键路径**：阶段1 → 阶段2 → 阶段3 → 阶段7

**可并行**：阶段4、5、6 在阶段2 完成后可并行推进

---

## 五、风险控制

| 风险 | 缓解措施 |
|------|---------|
| 覆盖率下降 | 每步运行覆盖率对比，丢失则保留或补充 |
| 合并引入 bug | 逐文件操作，每步运行测试验证 |
| 并行测试冲突 | 确保测试独立无共享状态；xdist 用 `--dist=loadfile` |
| 进度插件影响性能 | 插件仅做轻量统计，无 I/O 密集操作 |
| conftest fixture 冲突 | 专用 conftest 覆盖全局 autouse，避免重复执行 |
| API 拆分遗漏 | 拆分前记录 test_api.py 所有类/方法，拆分后逐一核对 |

---

## 六、各轨道优化前后预估对比

### OBS

| 指标 | 优化前 | 优化后 |
|------|--------|--------|
| conftest.py | 无 | 有（进度+共享fixture+变更检测） |
| 进度可视化 | 无 | SEC级实时进度+ETA+阶段图标 |
| sleep 瓶颈 | ~3.5s(benchmark) | ~0.5s |
| 重复 fixture | 5文件各定义 | conftest 统一 |
| 预估运行时间减少 | - | ~40% |

### LLM

| 指标 | 优化前 | 优化后 |
|------|--------|--------|
| conftest.py | 无(依赖全局) | 有（进度+共享fixture+覆盖全局autouse） |
| 进度可视化 | 无 | SEC级实时进度+ETA+阶段图标 |
| MockInferenceEngine | 2文件各定义 | conftest 统一 |
| 全局autouse开销 | 每测试~1ms | 覆盖后0ms |
| 预估运行时间减少 | - | ~30% |

### SG

| 指标 | 优化前 | 优化后 |
|------|--------|--------|
| conftest.py | 无 | 有（进度+共享fixture+变更检测） |
| 进度可视化 | 无 | SEC级实时进度+ETA+阶段图标 |
| sleep 瓶颈 | ~6.1s | ~0.5s |
| 丢失测试 | 4个(重复类名) | 恢复 |
| sg_available重复 | 2文件各定义 | conftest 统一 |
| 预估运行时间减少 | - | ~35% |

### API

| 指标 | 优化前 | 优化后 |
|------|--------|--------|
| conftest.py | 无 | 有（进度+共享fixture+全局状态清理） |
| 进度可视化 | 无 | SEC级实时进度+ETA+阶段图标 |
| 重复测试 | 9类重复 | 去重后无重复 |
| test_api.py | 856行/22类 | 拆分为7个专项文件 |
| 重复 fixture | 7-8次 | conftest 统一 |
| 预估运行时间减少 | - | ~30% |
