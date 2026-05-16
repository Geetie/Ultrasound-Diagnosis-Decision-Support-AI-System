# Phase0A 测试有效性改善计划

## 一、现状诊断

### 1.1 推理引擎实现状态：✅ 完全实现

| 组件 | 状态 | 说明 |
|------|------|------|
| InferenceEngine Protocol | ✅ | protocols.py - 4个抽象方法 + 完整数据模型 |
| BaseHttpInferenceEngine | ✅ | base.py - HTTP客户端 + 断路器 + 指数退避重试 |
| LlamaCppEngine | ✅ | llama_cpp.py - /v1/completions 适配 |
| VLLMEngine | ✅ | vllm.py - /v1/chat/completions 适配 |
| llama_cpp_server.py | ✅ | 支持CPU，默认Qwen2.5-1.5B GGUF |
| vllm_server.py | ✅ | 支持 `--device cpu`，vLLM>=0.5 CPU模式 |
| start_dual_engine_test.py | ✅ | 一键启动双引擎，支持 `--device cpu` |
| consistency_verify.py | ✅ | 完整的一致性验证CLI脚本 |
| ConsistencyVerifier | ✅ | consistency.py - 分级/边界/幻觉/延迟全指标 |
| 310条测试用例 | ✅ | consistency_test_cases.json |
| 本地模型文件 | ✅ | GGUF: models/qwen2.5-1.5b-instruct-q4_k_m.gguf |
| 本地模型文件 | ✅ | HF: models/Qwen2.5-1.5B-Instruct/ |
| llama-cpp-python | ✅ | 已安装 v0.3.22 |
| vllm | ✅ | 已安装 v0.20.1 |

### 1.2 测试代码问题诊断：❌ 严重不足

| 问题 | 严重程度 | 说明 |
|------|----------|------|
| **单元测试全部使用Mock** | P0-Critical | test_consistency.py 用 httpx.MockTransport 模拟HTTP，从未调用真实LLM |
| **集成测试用例极少** | P0-Critical | test_dual_engine_consistency.py 仅5个测试用例，远低于Phase0A要求的≥100例/器官 |
| **集成测试静默跳过** | P0-Critical | skipif机制导致服务不可用时测试静默SKIP，无法发现真实问题 |
| **无服务生命周期管理** | P1-High | 测试框架没有启动/停止LLM服务的fixture，依赖手动启动 |
| **无CPU模式自动化** | P1-High | 虽然vllm_server.py支持--device cpu，但测试框架未利用 |
| **无Gate判定集成测试** | P1-High | ConsistencyReport.gate_status在集成测试中从未被验证 |
| **无器官维度分解验证** | P2-Medium | Phase0A要求按thyroid/liver/cardiac分别统计，但集成测试未覆盖 |
| **无延迟性能验证** | P2-Medium | P50/P99延迟在集成测试中从未被实际测量 |
| **无幻觉检测端到端验证** | P2-Medium | 幻觉检测器只在单元测试中用硬编码文本验证 |

## 二、改善目标

### 2.1 Phase0A Gate 0A-Tech-P0 验证要求（来源：06-开发环境与进度规划.md）

| 验证项 | 通过标准 | 当前状态 |
|--------|----------|----------|
| vLLM推理验证 | 推理延迟<3s | ❌ 无真实测试 |
| llama.cpp推理验证 | 2并发P99<8s | ❌ 无真实测试 |
| vLLM与llama.cpp一致性验证 | 分级一致率≥95% | ❌ 无真实测试 |
| 临床决策边界差异率 | ≤1% | ❌ 无真实测试 |
| 幻觉率差异 | ≤0.5% | ❌ 无真实测试 |
| 文本一致率 | ≥90% | ❌ 无真实测试 |
| 延迟差异百分比 | ≤20% | ❌ 无真实测试 |

### 2.2 改善后的目标

1. **集成测试调用真实本地LLM**（CPU模式，无需GPU）
2. **覆盖Phase0A全部验证指标**
3. **使用310条测试用例**（≥100例/器官）
4. **自动化服务生命周期**（pytest fixture管理启动/停止）
5. **Gate判定端到端验证**（PASSED/CONDITIONAL/FAILED）
6. **可重复、可CI集成**

## 三、实施计划

### Step 1: 创建LLM服务管理pytest fixture

**文件**: `tests/conftest.py`（修改）

添加session级别的fixture，自动管理llama.cpp和vLLM服务的启动和停止：

```python
@pytest.fixture(scope="session")
def llama_cpp_service():
    """启动llama.cpp服务（session级别）"""
    # 使用subprocess启动 python scripts/llama_cpp_server.py
    # 或 python -m llama_cpp.server --model models/qwen2.5-1.5b-instruct-q4_k_m.gguf
    # 等待/health返回200
    yield base_url
    # terminate进程

@pytest.fixture(scope="session")
def vllm_service():
    """启动vLLM服务（session级别，CPU模式）"""
    # 使用subprocess启动 python scripts/vllm_server.py --device cpu
    # --model models/Qwen2.5-1.5B-Instruct
    # 等待/health返回200
    yield base_url
    # terminate进程
```

关键设计：
- **session级别**：整个测试会话只启动一次，避免重复加载模型
- **CPU模式**：vLLM使用 `--device cpu`，llama.cpp天然支持CPU
- **环境变量控制**：`LLM_TEST_MODE=local|skip`，skip时退回原有skipif行为
- **超时保护**：启动超时120秒，单个请求超时60秒（CPU推理较慢）

### Step 2: 创建Phase0A集成测试套件

**文件**: `tests/integration/test_phase0a_consistency.py`（新建）

这是核心改进文件，包含以下测试类：

#### 2.1 TestEngineSmokeTest - 引擎冒烟测试
- `test_llama_cpp_health_check`: llama.cpp /health返回200
- `test_vllm_health_check`: vLLM /health返回200
- `test_llama_cpp_generate`: llama.cpp能生成非空文本
- `test_vllm_generate`: vLLM能生成非空文本
- `test_llama_cpp_grading_extraction`: llama.cpp输出包含可提取的TI-RADS分级
- `test_vllm_grading_extraction`: vLLM输出包含可提取的TI-RADS分级

#### 2.2 TestGradingConsistency - 分级一致性验证（Phase0A核心）
- `test_grading_consistency_rate`: 分级一致率≥95%（使用310条用例）
- `test_semantic_grading_consistency_rate`: 语义分级一致率≥98%
- `test_organ_grading_consistency`: 按器官维度分解（thyroid/liver/cardiac各≥100例）

#### 2.3 TestClinicalBoundary - 临床边界跨越检测
- `test_clinical_boundary_diff_rate`: 临床决策边界差异率≤1%
- `test_boundary_diff_rate_ci_upper`: 边界差异率95%CI上界≤2%
- `test_no_critical_boundary_crossing`: 无关键边界跨越（4↔5）
- `test_weighted_boundary_diff_rate`: 加权边界差异率

#### 2.4 TestHallucinationDetection - 幻觉检测验证
- `test_hallucination_rate_diff`: 双引擎幻觉率差异≤0.5%
- `test_hallucination_rate_absolute`: 绝对幻觉率≤2%
- `test_hallucination_false_stats`: 假阳性/假阴性/F1统计

#### 2.5 TestLatencyPerformance - 延迟性能验证
- `test_llama_cpp_p99_latency`: llama.cpp P99延迟<8s（CPU模式放宽）
- `test_vllm_p99_latency`: vLLM P99延迟<15s（CPU模式放宽）
- `test_latency_diff_pct`: 双引擎延迟差异≤20%
- `test_organ_latency_breakdown`: 按器官维度延迟分解

#### 2.6 TestGateDetermination - Gate判定端到端验证
- `test_gate_status_report`: 生成完整ConsistencyReport并验证gate_status
- `test_gate_passed_criteria`: 验证PASSED条件（分级≥95% + 边界≤1% + 幻觉≤0.5%）
- `test_report_json_output`: 验证report_to_json输出完整性

#### 2.7 TestOrganBreakdown - 器官维度分解验证
- `test_thyroid_breakdown`: 甲状腺≥100例完整统计
- `test_liver_breakdown`: 肝脏≥100例完整统计
- `test_cardiac_breakdown`: 心脏≥100例完整统计

### Step 3: 创建CPU模式启动辅助脚本

**文件**: `scripts/start_local_test_services.py`（新建）

简化版启动脚本，专门用于本地CPU测试：

```bash
python scripts/start_local_test_services.py
# 自动检测本地模型，启动llama.cpp(8001) + vLLM CPU模式(8002)
# 等待健康检查通过后输出服务地址
```

关键特性：
- 自动检测 `models/` 目录下的模型文件
- 默认CPU模式
- 输出环境变量供pytest使用
- 信号处理优雅关闭

### Step 4: 添加pytest配置和标记

**文件**: `pyproject.toml`（修改）

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
markers = [
    "slow: marks tests as slow",
    "integration: marks tests as integration tests requiring external services",
    "phase0a: marks tests as Phase0A gate verification tests",
    "requires_llm: marks tests that require real LLM services",
]
addopts = "-m 'not requires_llm' --timeout=300"
```

新增 `phase0a` 和 `requires_llm` 标记，默认跳过LLM测试（避免CI失败）。

### Step 5: 更新conftest.py添加LLM服务fixture

**文件**: `tests/conftest.py`（修改）

添加：
- `llama_cpp_service` fixture（session级别）
- `vllm_service` fixture（session级别）
- `dual_engine_services` fixture（组合上述两个）
- `consistency_test_cases` fixture（加载310条测试用例）
- `llama_cpp_engine_real` fixture（真实引擎实例）
- `vllm_engine_real` fixture（真实引擎实例）

### Step 6: 验证和调试

1. 启动本地LLM服务（CPU模式）
2. 运行集成测试套件
3. 根据实际LLM输出调整阈值（CPU模式下延迟阈值需放宽）
4. 确认Gate判定逻辑与实际结果一致
5. 生成一致性验证报告

## 四、文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `tests/conftest.py` | 修改 | 添加LLM服务管理fixture |
| `tests/integration/test_phase0a_consistency.py` | 新建 | Phase0A完整集成测试套件 |
| `scripts/start_local_test_services.py` | 新建 | CPU模式本地测试服务启动脚本 |
| `pyproject.toml` | 修改 | 添加phase0a/requires_llm标记 |

## 五、CPU模式性能预估

基于Qwen2.5-1.5B-Instruct模型在CPU上的推理性能：

| 指标 | GPU模式 | CPU模式（预估） |
|------|---------|-----------------|
| llama.cpp单次推理 | ~1-3s | ~5-15s |
| vLLM单次推理 | ~1-2s | ~10-30s |
| 310条用例双引擎验证 | ~15-30min | ~2-4小时 |
| 内存占用 | ~3-5GB | ~8-16GB |

CPU模式下的调整：
- 请求超时从30s放宽到120s
- P99延迟阈值从3s放宽到30s（llama.cpp）/ 60s（vLLM）
- 并发数从4降低到1（避免CPU争抢）
- 可选择只运行部分用例（如每器官30例）进行快速验证

## 六、执行顺序

1. **Step 1**: 修改conftest.py添加LLM服务fixture
2. **Step 2**: 创建test_phase0a_consistency.py完整测试套件
3. **Step 3**: 创建start_local_test_services.py辅助脚本
4. **Step 4**: 更新pyproject.toml配置
5. **Step 5**: 启动本地LLM服务并运行测试
6. **Step 6**: 根据实际结果调整阈值和测试用例

## 七、风险和缓解

| 风险 | 缓解措施 |
|------|----------|
| CPU推理太慢导致测试超时 | 分层测试：冒烟测试(6例) → 快速验证(30例/器官) → 完整验证(100例/器官) |
| vLLM CPU模式不稳定 | 优先验证llama.cpp，vLLM CPU作为可选 |
| 模型输出格式不一致 | 先运行冒烟测试确认输出格式，再调整分级提取正则 |
| 内存不足 | 串行运行（concurrency=1），避免同时加载两个模型 |
