# LLM轨道验证机制有效性审查 — 实施计划

## 审查范围

LLM轨道涉及以下核心模块及其测试代码：

| 模块 | 源码 | 测试 |
|------|------|------|
| 一致性验证 | `app/inference/consistency.py` | `tests/unit/test_consistency.py` |
| 幻觉检测 | `app/inference/hallucination_detector.py` | `tests/unit/test_hallucination_detector.py` |
| 模型基准测试 | `app/inference/benchmark.py` | `tests/unit/test_benchmark.py` |
| 漂移检测 | `app/inference/drift_detector.py` | `tests/unit/test_drift_detector.py` |
| GPU显存监控 | `app/inference/gpu_monitor.py` | `tests/unit/test_gpu_monitor.py` |
| 推理引擎工厂 | `app/inference/factory.py` | `tests/unit/test_factory.py` |
| 验证配置 | `app/inference/verification_config.py` | `tests/unit/test_verification_config.py` |
| 推理引擎协议/实现 | `app/inference/protocols.py`, `llama_cpp.py`, `vllm.py` | `tests/unit/test_inference.py` |

## 核心参考文档对照

| 优先级 | 文档 | 关键验证标准 |
|--------|------|-------------|
| P0 | 01-系统架构设计 §5.7 | InferenceEngine Protocol接口、vLLM vs llama.cpp架构差异、compact/full模式设计 |
| P0 | 06-开发环境与进度规划 §5+§7 | INFERENCE_ENGINE配置、端口8001/8002、GPU显存分配策略、Gate 0A一致性验证 |
| P1 | 07-数据标注与模型评估 §4 | 分级一致率≥95%、临床决策边界差异率≤1%、幻觉率增量≤0.5%、边界病例≥300例 |
| P2 | 02-核心流程与知识库 | 推理流程与知识库缓存策略交互 |
| P2 | 00-项目总览与核心指标 | 准确率≥89%基层/≥92%三甲、严重幻觉率≤0.5% |

---

## 审查步骤

### 步骤1：运行现有测试套件，建立基线

**目标**：确认所有测试当前是否通过，建立审查基线

**操作**：
1. 执行 `pytest tests/unit/ -v --tb=short` 运行全部LLM轨道单元测试
2. 记录通过/失败/跳过数量
3. 检查测试覆盖率（如可获取）

**验证**：测试结果记录完整，基线数据可追溯

---

### 步骤2：一致性验证模块审查

**源码**：`app/inference/consistency.py`（846行）
**测试**：`tests/unit/test_consistency.py`（669行）

#### 2.1 设计规范审查

- [ ] **InferenceEngine Protocol一致性**：ConsistencyVerifier是否正确使用Protocol接口（而非具体实现），与01-系统架构设计§5.7对齐
- [ ] **分级一致率阈值**：ConsistencyReport.passed使用的阈值（grading_consistency_rate≥0.95, clinical_boundary_diff_rate≤0.005, hallucination_rate_diff≤0.003, text_consistency_rate≥0.91）是否与07-数据标注与模型评估§4的"分级一致率≥95%"对齐
- [ ] **临床边界跨越检测**：`_grades_cross_clinical_boundary`方法仅使用`critical_boundaries = [3.0]`作为唯一临界值——验证这是否覆盖了所有临床决策边界（TI-RADS 3↔4a、LI-RADS 3↔4、BI-RADS 3↔4a）
- [ ] **边界权重计算**：`_compute_boundary_weight`是否正确从verification_config加载边界配置并应用权重

#### 2.2 代码质量审查

- [ ] **_percentile函数重复**：`consistency.py`和`benchmark.py`各有一个`_percentile`函数实现完全相同——应提取为共享工具函数
- [ ] **detect_hallucination兼容函数**：`consistency.py`中的`detect_hallucination`仅为兼容性包装器，检查是否有遗留调用者需要此兼容层
- [ ] **cross_validate方法**：K折交叉验证的fold划分逻辑是否正确处理了不能整除的情况
- [ ] **FalseStats计算**：仅基于Engine A的幻觉检测结果计算假阳性/假阴性——是否应同时考虑Engine B

#### 2.3 测试有效性审查

- [ ] **边界跨越测试覆盖不足**：`test_boundary_crossing_4_to_5`和`test_boundary_crossing_4a_to_4c`验证了不跨越边界的情况，但缺少LI-RADS 3↔4、BI-RADS 3↔4a的边界跨越测试
- [ ] **临床边界定义测试缺失**：`_grades_cross_clinical_boundary`的`critical_boundaries = [3.0]`硬编码未在测试中验证其覆盖范围是否与文档对齐
- [ ] **一致性验证端到端测试**：当前测试使用MockInferenceEngine，缺少真实双引擎（llama.cpp + vLLM）的集成测试
- [ ] **边界病例测试集规模**：文档要求≥300例边界病例（TI-RADS/LI-RADS/BI-RADS各≥100例），测试中仅使用2-3例——测试数据代表性严重不足
- [ ] **cross_validate测试**：仅用20例3折验证，未测试数据量不足时的行为（如n_folds > len(cases)）
- [ ] **并发安全性**：ConsistencyVerifier使用asyncio.Semaphore控制并发，但测试未验证并发安全性

---

### 步骤3：幻觉检测模块审查

**源码**：`app/inference/hallucination_detector.py`（441行）
**测试**：`tests/unit/test_hallucination_detector.py`（497行）

#### 3.1 设计规范审查

- [ ] **五策略覆盖完整性**：FORBIDDEN_TERMS、CONTRADICTORY_GRADING、MEDICAL_COMMON_SENSE、NUMERICAL_VALIDITY、CONTEXT_AWARE是否覆盖了07-数据标注与模型评估§1中"严重幻觉率≤0.5%"的验证需求
- [ ] **禁忌词否定检测**：`_is_negated`方法仅检查前后10字符窗口——验证此窗口是否足够捕获中文否定语境（如"未见明显肺癌转移征象"中"未见"与"肺癌"距离可能>10字符）
- [ ] **ContextAwareDetector器官覆盖**：仅支持thyroid和liver，cardiac返回空——验证这是否与文档要求的三器官支持对齐
- [ ] **置信度计算合理性**：当前置信度=加权严重性/最大可能严重性，但最大可能严重性固定为3*weight——验证此计算是否合理

#### 3.2 代码质量审查

- [ ] **全局默认检测器线程安全**：`_default_detector`全局变量无锁保护——多线程环境下可能重复创建
- [ ] **MedicalCommonSenseDetector误报风险**：良性+恶性关键词同时出现即判定矛盾，但"良性结节，需排除恶性可能"是合理临床表述——当前实现会误报
- [ ] **NumericalValidityDetector单位转换**：mm→cm转换逻辑正确，但未处理μm等更小单位

#### 3.3 测试有效性审查

- [ ] **否定语境测试缺失**：未测试"未见肺癌转移"等否定语境下的正确行为
- [ ] **ContextAwareDetector心脏支持缺失**：测试`test_cardiac_not_supported`确认了cardiac返回空，但未验证这是否为设计意图
- [ ] **幻觉率增量验证缺失**：文档要求"量化后幻觉率增量≤0.5%"，但测试中无量化前后对比测试
- [ ] **严重幻觉率验证缺失**：文档要求"严重幻觉率≤0.5%"，但测试未区分严重/轻微幻觉
- [ ] **假阳性/假阴性率测试缺失**：幻觉检测器本身的假阳性率和假阴性率未在测试中评估
- [ ] **多策略交互测试不足**：`test_mixed_issues`仅验证多策略同时触发，未验证策略间是否会产生矛盾判定

---

### 步骤4：模型基准测试模块审查

**源码**：`app/inference/benchmark.py`（408行）
**测试**：`tests/unit/test_benchmark.py`（325行）

#### 4.1 设计规范审查

- [ ] **winner判定逻辑**：`determine_winner`使用accuracy≥0.85、p99_latency≤3000ms、hallucination_increment≤阈值三重条件——验证这些阈值是否与06-开发环境与进度规划§7.1对齐
- [ ] **幻觉检测方法**：`_detect_hallucination`使用统一幻觉检测模块，但`check_hallucination`标志由测试用例控制——验证默认行为是否安全（默认不检测幻觉）
- [ ] **warmup_rounds**：默认3轮warmup，但测试中设为0——验证warmup轮数是否影响结果稳定性

#### 4.2 测试有效性审查

- [ ] **基准测试数据规模**：测试仅使用1-3例，文档要求常规≥200例+边界≥300例——测试数据代表性严重不足
- [ ] **器官分解统计测试**：仅测试thyroid和liver，未测试cardiac
- [ ] **量化幻觉率增量测试**：`test_quantized_hallucination_increment`仅测试数据模型计算，未测试实际量化前后对比
- [ ] **敏感性分析测试**：仅验证返回结构，未验证分析结果的统计合理性

---

### 步骤5：漂移检测模块审查

**源码**：`app/inference/drift_detector.py`（527行）
**测试**：`tests/unit/test_drift_detector.py`（444行）

#### 5.1 设计规范审查

- [ ] **Wilson置信区间**：`compute_baseline`对accuracy和grading_consistency使用Wilson置信区间+sigma的max策略——验证此混合策略的统计合理性
- [ ] **幻觉率绝对阈值**：幻觉率使用绝对阈值（warning=0.01, critical=0.02）而非统计阈值——验证这与文档"严重幻觉率≤0.5%"的对应关系
- [ ] **最小样本量**：要求30个历史数据点才计算基线——验证此最小样本量是否满足统计显著性要求

#### 5.2 测试有效性审查

- [ ] **基线计算统计效力**：测试使用30个数据点，但未验证不同分布（偏态、双峰）下的基线计算准确性
- [ ] **零标准差处理**：`test_zero_std_dev`验证了零标准差不崩溃，但未验证漂移检测在零标准差下的行为是否合理（任何偏离都触发alert）
- [ ] **器官隔离测试**：`test_organ_isolation`验证了不同器官基线独立，但未测试跨器官漂移传播
- [ ] **基线文件损坏测试**：`test_load_corrupted_json`和`test_load_partial_json`验证了容错，但未验证损坏数据是否会影响其他正常基线

---

### 步骤6：GPU显存监控模块审查

**源码**：`app/inference/gpu_monitor.py`
**测试**：`tests/unit/test_gpu_monitor.py`（454行）

#### 6.1 设计规范审查

- [ ] **碎片化阈值**：warning=0.30, critical=0.40——验证这与07-数据标注与模型评估§4"显存碎片化率<30%（Warning阈值）"对齐
- [ ] **compact模式内存预算**：budget_limit_mb=22048——验证这与06-开发环境与进度规划§5"12-22GB占用"对齐
- [ ] **GPU调度策略**：LLM与检索互斥占用——验证这与01-系统架构设计§5.7"compact模式llama.cpp常驻~3GB"对齐

#### 6.2 测试有效性审查

- [ ] **FragmentationCalculator测试**：仅测试fallback计算，未测试真实GPU碎片化计算路径
- [ ] **GPU快照测试**：`test_take_snapshot_no_gpu`在无GPU环境下测试，未验证真实GPU环境下的数据准确性
- [ ] **调度策略超时测试**：`test_timeout_checks`使用100s超时，未测试实际超时释放行为
- [ ] **4小时持续运行验证缺失**：文档要求"持续运行≥4小时后测量碎片化率"，测试中无此场景

---

### 步骤7：推理引擎与工厂模式审查

**源码**：`app/inference/factory.py`（124行）、`protocols.py`、`llama_cpp.py`、`vllm.py`
**测试**：`tests/unit/test_factory.py`（125行）、`tests/unit/test_inference.py`（1345行）

#### 7.1 设计规范审查

- [ ] **端口配置**：llama.cpp默认8001、vLLM默认8002——验证与06-开发环境与进度规划§5对齐
- [ ] **compact模式max_tokens限制**：llama.cpp强制max_tokens≤1024——验证与06-开发环境与进度规划§5"compact模式"对齐
- [ ] **vLLM GPU显存预分配**：full模式0.90、compact切换vLLM时0.80——验证配置传递链完整性
- [ ] **InferenceEngine Protocol**：验证接口定义与01-系统架构设计§5.7对齐

#### 7.2 测试有效性审查

- [ ] **重试与熔断测试缺失**：test_factory仅验证参数传递，未测试重试逻辑和熔断器的实际行为
- [ ] **连接池配置测试缺失**：pool_max_connections和pool_keepalive_expiry仅验证参数传递
- [ ] **vLLM require_model测试**：仅测试空model时的行为，未测试model不存在时的错误处理
- [ ] **Settings验证测试**：compact强制llama.cpp、full强制vLLM的测试覆盖良好，但端口冲突检测仅覆盖部分场景

---

### 步骤8：验证配置模块审查

**源码**：`app/inference/verification_config.py`
**测试**：`tests/unit/test_verification_config.py`（472行）

#### 8.1 设计规范审查

- [ ] **一致性阈值配置**：grading_consistency_rate=0.95, clinical_boundary_diff_rate=0.005, hallucination_rate_diff=0.003, text_consistency_rate=0.91——逐一与文档对齐
- [ ] **边界配置完整性**：thyroid 28个边界对、liver 6个边界对——验证是否覆盖所有临床决策边界
- [ ] **禁忌词配置完整性**：thyroid 18个、liver 13个、cardiac 12个——验证覆盖范围

#### 8.2 测试有效性审查

- [ ] **配置验证缺失**：BoundaryPairConfig允许负权重和空分级字符串（test_negative_weight, test_empty_grade_strings），但未验证这些无效配置是否应在创建时被拒绝
- [ ] **StrategyWeightConfig验证缺失**：允许负权重和零权重，但未验证权重总和是否应归一化
- [ ] **并发安全测试**：`test_concurrent_get_verification_config`仅验证10线程读取，未验证读写并发
- [ ] **自适应阈值测试**：compute_adaptive_threshold测试覆盖良好，但未测试极端历史值（如全部0.0或全部1.0）

---

### 步骤9：跨模块一致性审查

- [ ] **consistency.py ↔ hallucination_detector.py**：一致性验证中的幻觉检测调用链是否正确（consistency.py → unified_detect_hallucination → hallucination_detector.py）
- [ ] **benchmark.py ↔ verification_config.py**：基准测试的阈值是否从verification_config正确加载
- [ ] **drift_detector.py ↔ verification_config.py**：漂移检测的幻觉率绝对阈值是否从verification_config正确加载
- [ ] **factory.py ↔ settings.py**：推理引擎创建参数是否与Settings配置正确映射

---

### 步骤10：核心参考文档一致性审查

- [ ] **01-系统架构设计 §5.7**：InferenceEngine Protocol接口定义与实际实现对齐
- [ ] **06-开发环境与进度规划 §5**：所有配置项（INFERENCE_ENGINE, LLAMA_CPP_PORT, VLLM_PORT, VLLM_GPU_MEMORY_UTILIZATION）与代码默认值对齐
- [ ] **06-开发环境与进度规划 §7.1**：Gate 0A-Tech P0子门控验证标准与测试覆盖对齐
- [ ] **07-数据标注与模型评估 §4**：一致性验证详细要求（分级一致率≥95%、临床决策边界差异率≤1%、幻觉率增量≤0.5%）与代码阈值对齐
- [ ] **07-数据标注与模型评估 §4**：边界病例测试集要求（≥300例）与测试数据对齐

---

### 步骤11：生成审查报告

**输出**：在项目根目录生成审查报告，包含：

1. **执行摘要**：整体评估结论（通过/有条件通过/不通过）
2. **各模块审查结果**：按设计规范、代码质量、代码风格、功能正确性四维度评分
3. **关键发现**：按严重程度排序（Critical/High/Medium/Low）
4. **文档一致性偏差**：代码实现与核心参考文档的偏差清单
5. **改进建议**：按优先级排序的具体改进措施

---

## 预期关键发现（基于预审查）

### Critical

1. **边界病例测试数据严重不足**：文档要求≥300例边界病例，测试仅用2-3例
2. **临床边界定义硬编码**：`_grades_cross_clinical_boundary`仅用`[3.0]`一个临界值，未覆盖LI-RADS 3↔4、BI-RADS 3↔4a
3. **一致性验证缺少真实双引擎集成测试**：全部使用Mock引擎

### High

4. **幻觉检测否定语境窗口过小**：10字符窗口可能遗漏中文否定语境
5. **ContextAwareDetector不支持cardiac**：与三器官支持要求不一致
6. **MedicalCommonSenseDetector误报风险**：合理临床表述会被误判为幻觉
7. **GPU碎片化4小时持续运行验证缺失**：文档明确要求但测试未覆盖
8. **_percentile函数重复实现**：consistency.py和benchmark.py各一份

### Medium

9. **幻觉检测器全局实例线程安全**：`_default_detector`无锁保护
10. **重试与熔断器行为未测试**：仅验证参数传递
11. **配置验证不严格**：允许负权重、空分级等无效配置
12. **cross_validate未测试边界情况**：如n_folds > len(cases)

### Low

13. **测试数据版本管理**：test_data_version硬编码为"2.0"，未与实际数据版本关联
14. **benchmark默认不检测幻觉**：check_hallucination默认为False

---

## 执行顺序

步骤1→2→3→4→5→6→7→8→9→10→11

步骤2-8可并行审查（各模块独立），步骤9-10依赖2-8的结果，步骤11汇总所有发现。
