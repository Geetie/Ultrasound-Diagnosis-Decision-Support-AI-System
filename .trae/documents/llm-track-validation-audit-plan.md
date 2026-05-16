# LLM轨道验证机制全面有效性审查计划

## 一、审查范围

本审查覆盖LLM轨道全部8个单元测试文件、8个验证脚本、3个JSON验证数据文件、1个根目录快速验证脚本，以及对应的10个核心源代码模块。

### 审查对象清单

| 类别 | 文件 | 测试类数 | 测试方法数 |
|------|------|----------|-----------|
| 推理引擎测试 | test_inference.py | 11 | 73 |
| 一致性验证测试 | test_consistency.py | 6 | 34 |
| 幻觉检测测试 | test_hallucination_detector.py | 9 | 30 |
| 漂移检测测试 | test_drift_detector.py | 7 | 28 |
| 基准测试测试 | test_benchmark.py | 4 | 7 |
| 工厂模式测试 | test_factory.py | 2 | 10 |
| GPU监控测试 | test_gpu_monitor.py | 12 | 37 |
| 验证配置测试 | test_verification_config.py | 14 | 37 |

---

## 二、审查发现：按严重程度分级

### 🔴 严重（CRITICAL）— 验证机制存在根本性缺失

#### C1. 一致性验证核心功能未测试

**位置**: [test_consistency.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_consistency.py)

**问题**: 源代码 [consistency.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/consistency.py) 中实现了以下关键功能，但测试代码完全未覆盖：

| 未测试功能 | 源代码行号 | 风险说明 |
|-----------|-----------|---------|
| `cross_validate()` 5折交叉验证 | L651-L736 | 核心验证方法，无测试意味着交叉验证可能静默失败 |
| `FalseStats` precision/recall/F1 计算 | L103-L129 | 假阳性/假阴性统计是幻觉检测质量的核心指标 |
| `_generate_failure_analysis()` 失败分析 | L548-L641 | 追溯链和建议生成无测试，可能输出错误建议 |
| `organ_breakdown` 器官明细统计 | L508-L522 | 按器官分组的一致性统计无测试 |
| 延迟统计 (p50/p99) | L482-L505 | 延迟百分位数计算无测试 |
| `weighted_boundary_diff_rate` 加权边界差异率 | L437 | 权重边界差异率无测试 |

**影响**: 一致性验证是LLM轨道最核心的质量保障机制。以上未测试功能占源代码约40%的逻辑，任何回归都无法被检测。

#### C2. 基准测试核心执行路径未测试

**位置**: [test_benchmark.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_benchmark.py)

**问题**: 源代码 [benchmark.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/benchmark.py) 中以下关键方法无测试：

| 未测试功能 | 源代码行号 | 风险说明 |
|-----------|-----------|---------|
| `benchmark_candidate()` 实际基准执行 | L121-L227 | 核心评测逻辑完全未测试 |
| `analyze_threshold_sensitivity()` 敏感性分析 | L327-L394 | 阈值敏感性分析无测试 |
| 预热轮次逻辑 | L141-L153 | 预热可能影响后续评测结果 |
| 并发执行（Semaphore） | L155-L168 | 并发控制无测试 |

**影响**: 基准测试是模型选型的决策依据，核心执行路径无测试意味着选型结果可能不可靠。

#### C3. 持续验证脚本存在导入错误

**位置**: [continuous_verify.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/scripts/continuous_verify.py) L41

**问题**: 脚本使用 `from app.inference.factory import create_engine`，但 [factory.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/factory.py) 中导出的是 `create_inference_engine` 和 `create_inference_engine_from_settings`，不存在 `create_engine` 函数。此脚本在真实引擎环境下会直接报错，仅因 Mock 回退逻辑才不会崩溃。

**影响**: CI/CD 持续验证流水线在真实环境下无法运行，验证结果不可信。

---

### 🟠 高（HIGH）— 验证机制设计存在显著缺陷

#### H1. 幻觉检测器关键参数未测试

**位置**: [test_hallucination_detector.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_hallucination_detector.py)

**问题**:
- `severity_threshold` 参数（控制幻觉判断的最低严重性）无测试，默认值为1意味着所有问题都触发幻觉，可能过于敏感
- `DetectionResult.execution_time_ms` 无测试
- `DetectionResult.test_data_version` 无测试
- 置信度计算仅验证 `> 0 and <= 1.0`，未验证具体计算逻辑的正确性

**影响**: 幻觉检测的灵敏度和特异度无法被验证，可能产生过多假阳性或假阴性。

#### H2. 漂移检测统计方法存在正确性问题

**位置**: [drift_detector.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/drift_detector.py) L258-L259

**问题**: `compute_baseline` 使用总体方差（除以N）而非样本方差（除以N-1）。对于小样本（如最小10个数据点），这会系统性低估标准差，导致2-sigma和3-sigma阈值偏紧，增加假阳性漂移警报的概率。

```python
variance = sum((x - mean) ** 2 for x in historical_values) / len(historical_values)  # 应为 / (len-1)
```

**影响**: 漂移检测可能产生过多假阳性警报，降低验证结果的可信度。

#### H3. 幻觉率指标方向分类可能不当

**位置**: [drift_detector.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/drift_detector.py) L264

**问题**: `HALLUCINATION_RATE` 被分类为"越小越好"指标，使用 `mean + 2*sigma` 作为警告阈值。但幻觉率通常非常小（如0.005），标准差也很小，导致基于sigma的阈值可能不合理。例如，如果基线幻觉率为0.005，标准差为0.002，则警告阈值为0.009，临界阈值为0.011——这些阈值在临床上可能没有意义。

**影响**: 漂移检测对幻觉率指标的敏感性可能过高或过低。

#### H4. 数值有效性检测器未测试结节大小检测

**位置**: [test_hallucination_detector.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_hallucination_detector.py)

**问题**: `NumericalValidityDetector` 源代码实现了 `_check_nodule_size` 方法（支持cm/mm单位转换），但测试仅覆盖了LVEF检测，甲状腺结节大小和肝脏占位大小的检测完全未测试。

**影响**: 结节/占位大小异常值可能无法被检测到。

---

### 🟡 中（MEDIUM）— 验证机制存在设计或覆盖不足

#### M1. 验证阈值硬编码且不可配置

**位置**: 
- [consistency.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/consistency.py) L176-L181: `grading_consistency_rate >= 0.95`, `clinical_boundary_diff_rate <= 0.01`, `hallucination_rate_diff <= 0.005`
- [benchmark.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/benchmark.py) L104-L106: `_ACCURACY_THRESHOLD = 0.85`, `_LATENCY_THRESHOLD_MS = 3000.0`, `_HALLUCINATION_INCREMENT_THRESHOLD = 0.005`

**问题**: 虽然 `VerificationConfig` 提供了 `AdaptiveThresholdConfig`，但 `ConsistencyReport.passed` 和 `ModelBenchmark.determine_winner` 中的阈值是硬编码的，未使用配置系统。测试也未验证不同阈值下的行为。

**影响**: 无法根据临床需求或部署环境调整验证标准。

#### M2. 测试数据生成器缺乏多样性和对抗性

**位置**: [generate_test_cases.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/scripts/generate_test_cases.py)

**问题**:
- 每个分级仅有5个模板，通过随机选择生成，导致测试用例模式单一
- 无对抗性测试用例（如边界模糊的描述、多器官同时提及、罕见分级组合）
- 无临床困难病例（如TI-RADS 4a与4b的鉴别诊断）
- 心脏测试用例的LVEF值是离散的（25, 22, 20...），不是连续分布
- 幻觉测试用例仅10例（占310例的3.2%），比例偏低

**影响**: 验证数据不能充分代表真实临床场景的复杂性。

#### M3. 工厂测试存在疑似逻辑错误

**位置**: [test_factory.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_factory.py) L40-L43

**问题**: `test_create_llama_cpp_compact_max_tokens` 传入 `max_tokens=4096` 但断言 `engine._default_max_tokens == 1024`。查看 [factory.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/factory.py)，llama.cpp引擎创建时忽略了传入的max_tokens参数。这可能是：
- 一个bug（应该使用传入的max_tokens）
- 一个设计决策（compact模式强制使用1024）
- 但无论哪种情况，测试都没有验证这个行为的合理性

**影响**: 工厂方法的参数传递行为不明确。

#### M4. GPU监控测试未覆盖实际监控循环

**位置**: [test_gpu_monitor.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_gpu_monitor.py)

**问题**: `GPUMemoryMonitor` 的持续监控循环（定时采样、碎片化趋势分析）无测试。`ScheduledRequest` 数据结构无测试。

**影响**: GPU资源监控的可靠性无法验证。

#### M5. 验证配置测试未覆盖异常输入

**位置**: [test_verification_config.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_verification_config.py)

**问题**: 
- 无负权重测试
- 无空字符串测试
- 无配置版本迁移测试
- 无并发访问单例的测试

**影响**: 配置系统的鲁棒性未经验证。

#### M6. 持续验证脚本的Mock回退逻辑不合理

**位置**: [continuous_verify.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/scripts/continuous_verify.py) L57-L65

**问题**: Mock引擎始终返回 `"TI-RADS 3类"`，无论输入是什么。这意味着：
- 当真实引擎不可用时，验证会使用Mock引擎"通过"
- 两个Mock引擎输出完全相同，一致性率必然为100%
- 这使得验证结果毫无意义，但脚本仍会输出"PASS"

**影响**: 在引擎不可用时，CI/CD可能错误地报告验证通过。

---

### 🟢 低（LOW）— 验证机制存在轻微不足

#### L1. 推理引擎测试未覆盖并发和流式场景

**位置**: [test_inference.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_inference.py)

**问题**: 
- 无并发请求测试
- 无流式响应测试
- 无大payload测试
- VLLMEngine部分测试未使用fixture（不一致）

#### L2. 幻觉检测器未测试变体检测和自定义配置

**位置**: [test_hallucination_detector.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_hallucination_detector.py)

**问题**: 
- 未测试禁忌词变体检测（如"肺恶性肿瘤"应被检测为"肺癌"的变体）
- 未测试使用自定义VerificationConfig的检测器行为

#### L3. 漂移检测器未测试基线文件并发访问和损坏

**问题**: 
- 无并发文件读写测试
- 无损坏JSON文件处理测试
- 无GRADING_CONSISTENCY指标类型测试

#### L4. 边界配置中cardiac边界为空

**位置**: [verification_config.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/verification_config.py) L179

**问题**: `cardiac` 器官的边界配置为空列表 `[]`，意味着心脏LVEF的分级变化不会被检测为边界跨越。但心脏LVEF有明确的临床分级边界（如正常→轻度降低、轻度→中度降低），应配置相应边界对。

---

## 三、验证机制设计合理性评估

### 3.1 整体架构评价

| 维度 | 评分 | 说明 |
|------|------|------|
| 验证层次完整性 | ★★★★☆ | 覆盖了推理引擎、一致性、幻觉、漂移、基准5个层次，但缺少端到端集成验证 |
| 验证策略多样性 | ★★★★★ | 幻觉检测5种策略设计合理，覆盖了词法、语义、数值、上下文多个维度 |
| 配置可扩展性 | ★★★☆☆ | VerificationConfig提供了配置框架，但核心阈值仍硬编码 |
| 数据代表性 | ★★★☆☆ | 310例测试数据覆盖3个器官，但模板化生成导致多样性不足 |
| CI/CD集成度 | ★★★☆☆ | 有持续验证脚本，但存在导入错误和Mock回退问题 |

### 3.2 验证指标科学性评估

| 指标 | 当前阈值 | 科学性评估 |
|------|---------|-----------|
| 分级一致性率 ≥ 95% | 合理，但缺乏临床验证依据。建议引用文献或通过专家评审确认 |
| 临床边界差异率 ≤ 1% | 偏严格。310例中仅允许3例边界跨越，统计波动可能导致偶发失败 |
| 幻觉率差异 ≤ 0.5% | 过于严格。310例中差异不超过1.55例，统计上几乎不可能精确测量 |
| 模型准确率 ≥ 85% | 合理，作为最低准入门槛 |
| P99延迟 ≤ 3000ms | 合理，但应区分compact/full模式 |
| 幻觉率 ≤ 0.5% | 合理，但应区分器官类型 |

### 3.3 验证结果可靠性评估

| 风险因素 | 影响程度 | 说明 |
|---------|---------|------|
| Mock依赖过重 | 高 | 核心验证逻辑（一致性、基准）依赖Mock引擎，无法验证真实LLM行为 |
| 统计方法缺陷 | 中 | 总体方差替代样本方差、小样本阈值偏紧 |
| 阈值硬编码 | 中 | 无法根据场景调整验证标准 |
| 测试数据单一 | 中 | 模板化生成缺乏临床多样性 |
| Mock回退误通过 | 高 | 持续验证脚本在引擎不可用时可能误报通过 |

---

## 四、改进建议（按优先级排序）

### P0 — 必须立即修复

1. **修复 continuous_verify.py 导入错误**
   - 将 `create_engine` 改为 `create_inference_engine`
   - 移除或修正Mock回退逻辑，引擎不可用时应报错而非静默通过

2. **补充一致性验证核心功能测试**
   - 添加 `cross_validate()` 测试
   - 添加 `FalseStats` precision/recall/F1 测试
   - 添加 `failure_analysis` 测试
   - 添加 `organ_breakdown` 测试
   - 添加延迟统计测试

3. **补充基准测试核心执行路径测试**
   - 添加 `benchmark_candidate()` 测试（使用Mock引擎）
   - 添加 `analyze_threshold_sensitivity()` 测试
   - 添加预热逻辑测试

### P1 — 应尽快完成

4. **修复漂移检测统计方法**
   - 将总体方差改为样本方差（除以N-1）
   - 或使用 `statistics.stdev()` 标准库函数

5. **补充幻觉检测器关键参数测试**
   - 测试 `severity_threshold` 不同值的行为
   - 验证置信度计算的具体逻辑
   - 测试结节大小检测（甲状腺/肝脏）

6. **将硬编码阈值迁移到配置系统**
   - `ConsistencyReport.passed` 使用 `VerificationConfig` 中的阈值
   - `ModelBenchmark` 使用可配置的阈值
   - 添加不同阈值下的测试用例

7. **补充cardiac边界配置**
   - 添加LVEF分级边界对（normal↔mild↔moderate↔severe）

### P2 — 应计划完成

8. **增强测试数据多样性**
   - 添加对抗性测试用例（边界模糊描述、多器官提及）
   - 增加幻觉测试用例比例（从3.2%提升到10%+）
   - 添加临床困难病例
   - 使用连续分布生成LVEF值

9. **添加集成测试层**
   - 使用真实LLM引擎的端到端验证测试
   - 标记为 `@pytest.mark.integration` 可选执行
   - 验证Mock测试与真实行为的对应关系

10. **改进持续验证流水线**
    - 引擎不可用时明确报错退出（exit code 2）
    - 添加验证结果趋势分析
    - 支持部分验证（仅验证特定器官）

11. **添加性能/负载测试**
    - 并发推理请求测试
    - 长时间运行稳定性测试
    - 内存泄漏检测

---

## 五、实施步骤

### 阶段1：紧急修复（1-2天）

1. 修复 `continuous_verify.py` 导入错误和Mock回退逻辑
2. 修复 `drift_detector.py` 统计方法（总体方差→样本方差）
3. 添加 `cardiac` 边界配置

### 阶段2：核心测试补充（3-5天）

4. 为 `consistency.py` 补充 `cross_validate`、`FalseStats`、`failure_analysis`、`organ_breakdown`、延迟统计测试
5. 为 `benchmark.py` 补充 `benchmark_candidate`、`analyze_threshold_sensitivity` 测试
6. 为 `hallucination_detector.py` 补充 `severity_threshold`、置信度计算、结节大小检测测试

### 阶段3：架构改进（5-7天）

7. 将硬编码阈值迁移到 `VerificationConfig`
8. 增强测试数据生成器
9. 添加集成测试框架

### 阶段4：持续改进（持续）

10. 添加性能/负载测试
11. 改进持续验证流水线
12. 建立验证结果趋势分析

---

## 六、验证指标覆盖矩阵

| 验证维度 | 源代码实现 | 单元测试覆盖 | 集成测试覆盖 | CI/CD覆盖 |
|---------|-----------|-------------|-------------|----------|
| 推理引擎协议 | ✅ | ✅ 完整 | ❌ | ❌ |
| 推理引擎HTTP交互 | ✅ | ✅ 完整(Mock) | ❌ | ❌ |
| 分级提取 | ✅ | ✅ 完整 | ❌ | ❌ |
| 一致性验证核心 | ✅ | ⚠️ 部分 | ❌ | ⚠️ 有脚本但有bug |
| 交叉验证 | ✅ | ❌ 未覆盖 | ❌ | ❌ |
| 幻觉检测5策略 | ✅ | ✅ 完整 | ❌ | ❌ |
| 幻觉检测参数 | ✅ | ⚠️ 部分 | ❌ | ❌ |
| 漂移检测 | ✅ | ✅ 完整 | ❌ | ⚠️ 有脚本 |
| 漂移统计方法 | ⚠️ 有缺陷 | ✅ 测试通过但逻辑有误 | ❌ | ❌ |
| 基准测试核心 | ✅ | ❌ 未覆盖 | ❌ | ❌ |
| 基准胜者判定 | ✅ | ✅ 完整 | ❌ | ❌ |
| 验证配置管理 | ✅ | ✅ 完整 | ❌ | ❌ |
| GPU资源监控 | ✅ | ⚠️ 部分 | ❌ | ❌ |
| 测试数据生成 | ✅ | ❌ 无测试 | ❌ | ❌ |

---

## 七、自反思记录

```
CONTEXT: LLM轨道验证机制全面审查
REFLECTION: 审查发现核心验证功能（交叉验证、基准执行、失败分析）完全无测试覆盖，
            这意味着当前测试体系给人一种"覆盖良好"的错觉，实际上最关键的验证逻辑
            恰恰是最缺乏保障的。同时，持续验证脚本存在导入错误，说明CI/CD流水线
            从未在真实环境下成功运行过。
LESSON: 测试覆盖率数字可能具有误导性——高覆盖率不等于高质量覆盖。
        应优先确保核心业务逻辑的测试，而非追求表面覆盖率。
        对于医疗AI系统，验证机制本身的可靠性应被视为最高优先级。
```
