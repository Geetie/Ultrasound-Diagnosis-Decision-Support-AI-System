# LLM轨道验证机制有效性审查报告

**审查日期**：2026-05-10
**审查范围**：LLM轨道全部8个核心模块的源码与测试代码
**测试基线**：396个单元测试全部通过（65.64s）
**审查原则**：独立、客观、深入、批判性

---

## 一、执行摘要

### 整体评估结论：**有条件通过**

LLM轨道的验证框架架构设计合理，模块划分清晰，核心验证逻辑基本正确。但存在若干关键缺陷，主要集中在**测试数据代表性严重不足**、**临床边界定义与文档不一致**、**部分验证机制覆盖不完整**三个方面。这些问题可能导致验证结果无法充分反映实际应用场景中的风险。

**关键数据**：
- 源码总量：~4,000行（8个模块）
- 测试总量：~3,300行（8个测试文件），396个测试用例
- Critical级发现：3项
- High级发现：5项
- Medium级发现：4项
- Low级发现：2项
- 文档一致性偏差：6项

---

## 二、各模块审查结果

### 2.1 一致性验证模块（consistency.py / test_consistency.py）

| 维度 | 评分 | 说明 |
|------|------|------|
| 设计规范 | 7/10 | Protocol接口使用正确，但临床边界硬编码与文档不一致 |
| 代码质量 | 7/10 | _percentile重复，FalseStats仅基于Engine A |
| 代码风格 | 8/10 | 结构清晰，日志完善，dataclass使用合理 |
| 功能正确性 | 6/10 | 边界跨越逻辑与verification_config存在语义差异 |

**详细发现**：

1. **[Critical] 临床边界定义硬编码与文档不一致**
   - `_grades_cross_clinical_boundary`（[consistency.py:135](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/consistency.py#L135)）使用 `critical_boundaries = [3.0]` 作为唯一临界值
   - 文档01-系统架构设计§5.7明确定义了三个临床决策边界：TI-RADS 3↔4a、LI-RADS 3↔4、BI-RADS 3↔4a
   - 当前实现仅能检测跨越数值3.0的边界，无法正确处理LI-RADS和BI-RADS的边界跨越
   - 同时，`verification_config.py`的`get_default_boundary_config()`已定义了完整的边界对配置（thyroid 28对、liver 6对、cardiac 12对），但`_grades_cross_clinical_boundary`完全未使用此配置
   - **影响**：可能导致LI-RADS 3↔4和BI-RADS 3↔4a的关键临床边界跨越未被检测到

2. **[Critical] 边界病例测试数据严重不足**
   - 文档07-数据标注与模型评估§4要求≥300例边界病例（TI-RADS/LI-RADS/BI-RADS各≥100例）
   - 测试中仅使用2-3例，数据代表性严重不足
   - `test_verify_with_mock_engines`仅3例，`test_verify_with_inconsistent_engines`仅2例
   - **影响**：无法验证一致性验证在大规模数据上的统计可靠性

3. **[High] _percentile函数重复实现**
   - [consistency.py:32-41](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/consistency.py#L32)和[benchmark.py:17-26](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/benchmark.py#L17)各有一个完全相同的`_percentile`函数
   - 应提取为共享工具函数

4. **[Medium] FalseStats仅基于Engine A**
   - `_compute_report`中FalseStats计算仅使用Engine A的幻觉检测结果（[consistency.py:521-546](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/consistency.py#L521)）
   - 未同时考虑Engine B的检测结果，可能导致统计偏差

5. **[Medium] cross_validate未测试边界情况**
   - 当n_folds > len(cases)时，fold_size=0，可能导致空子集验证
   - 测试仅用20例3折，未覆盖此边界情况

---

### 2.2 幻觉检测模块（hallucination_detector.py / test_hallucination_detector.py）

| 维度 | 评分 | 说明 |
|------|------|------|
| 设计规范 | 7/10 | 五策略覆盖较全面，但cardiac支持不完整 |
| 代码质量 | 7/10 | 全局实例线程安全风险，MedicalCommonSense误报风险 |
| 代码风格 | 8/10 | 策略模式清晰，配置驱动良好 |
| 功能正确性 | 7/10 | 否定语境窗口过小，置信度计算可优化 |

**详细发现**：

6. **[High] 否定语境窗口过小**
   - `_is_negated`方法（[hallucination_detector.py:76-79](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/hallucination_detector.py#L76)）仅检查前后10字符窗口
   - 中文否定语境如"未见明显肺癌转移征象"中，"未见"与"肺癌"距离可能>10字符
   - **影响**：可能导致否定语境下的禁忌词被误报为幻觉

7. **[High] ContextAwareDetector不支持cardiac**
   - [hallucination_detector.py:252](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/hallucination_detector.py#L252)中 `if organ not in ("thyroid", "liver"): return issues`
   - 文档要求三器官支持（thyroid/liver/cardiac），cardiac返回空结果
   - **影响**：心脏超声报告的描述-结论矛盾和过度反应无法被检测

8. **[High] MedicalCommonSenseDetector误报风险**
   - [hallucination_detector.py:159-168](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/hallucination_detector.py#L159)中，良性+恶性关键词同时出现即判定矛盾
   - 但"良性结节，需排除恶性可能"是合理临床表述，当前实现会误报
   - **影响**：正常临床推理表述可能被错误标记为幻觉

9. **[Medium] 全局默认检测器线程安全**
   - `_default_detector`全局变量（[hallucination_detector.py:419](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/hallucination_detector.py#L419)）无锁保护
   - 多线程环境下可能重复创建检测器实例

10. **[Low] 幻觉率增量验证缺失**
    - 文档要求"量化后幻觉率增量≤0.5%"，但测试中无量化前后对比测试
    - 测试未区分严重/轻微幻觉，文档要求"严重幻觉率≤0.5%"

---

### 2.3 模型基准测试模块（benchmark.py / test_benchmark.py）

| 维度 | 评分 | 说明 |
|------|------|------|
| 设计规范 | 7/10 | winner判定逻辑与文档基本对齐，但阈值来源不明确 |
| 代码质量 | 8/10 | 结构清晰，并发处理正确 |
| 代码风格 | 8/10 | 代码简洁，dataclass使用合理 |
| 功能正确性 | 7/10 | 默认不检测幻觉，测试数据规模不足 |

**详细发现**：

11. **[Medium] 基准测试默认不检测幻觉**
    - `_benchmark_single`中 `is_hallucination_check = case.get("check_hallucination", False)`（[benchmark.py:247](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/benchmark.py#L247)）
    - 默认不检测幻觉，需测试用例显式启用
    - 文档要求幻觉率是核心验证指标，默认行为应更安全

12. **[Low] 测试数据规模不足**
    - 测试仅使用1-3例，文档要求常规≥200例+边界≥300例
    - 器官分解统计仅测试thyroid和liver，未测试cardiac

---

### 2.4 漂移检测模块（drift_detector.py / test_drift_detector.py）

| 维度 | 评分 | 说明 |
|------|------|------|
| 设计规范 | 8/10 | Wilson置信区间+sigma混合策略合理，幻觉率绝对阈值与文档对齐 |
| 代码质量 | 8/10 | 统计计算正确，基线持久化完善 |
| 代码风格 | 8/10 | 枚举使用规范，日志完善 |
| 功能正确性 | 8/10 | 零标准差处理合理，器官隔离正确 |

**详细发现**：

13. **幻觉率绝对阈值与文档对齐确认**
    - `hallucination_rate_warning=0.01, hallucination_rate_critical=0.02`（[verification_config.py:113-114](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/verification_config.py#L113)）
    - 文档00-项目总览定义严重幻觉率≤0.5%（Critical），整体幻觉率≤5%（Critical）
    - 漂移检测的warning=1%和critical=2%是相对于基线的增量阈值，与文档的绝对阈值是不同维度
    - **需确认**：漂移检测阈值是否应与文档的绝对幻觉率阈值关联

---

### 2.5 GPU显存监控模块（gpu_monitor.py / test_gpu_monitor.py）

| 维度 | 评分 | 说明 |
|------|------|------|
| 设计规范 | 7/10 | 碎片化阈值与文档对齐，但碎片化计算仅为启发式估算 |
| 代码质量 | 7/10 | 跨平台支持完善，但pynvml碎片化计算非真正度量 |
| 代码风格 | 8/10 | ABC模式清晰，工厂模式正确 |
| 功能正确性 | 7/10 | 缺少4小时持续运行验证，调度策略超时逻辑简化 |

**详细发现**：

14. **[High] 碎片化计算仅为启发式估算**
    - `_calculate_with_pynvml`（[gpu_monitor.py:165-190](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/gpu_monitor.py#L165)）基于使用率的启发式估算，代码注释已承认"并非真正的碎片化度量"
    - `_calculate_fallback`（[gpu_monitor.py:192-203](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/gpu_monitor.py#L192)）同样是线性映射
    - 文档07-数据标注与模型评估§4要求"碎片化率<30%（Warning阈值）"，但当前计算方法无法准确反映真实碎片化程度
    - **影响**：碎片化验证结果可能与实际GPU显存碎片化状况不符

15. **[Low] 4小时持续运行验证缺失**
    - 文档要求"持续运行≥4小时后测量碎片化率"
    - `run_continuous_monitor`方法支持4小时监控，但测试中无此场景

---

### 2.6 推理引擎与工厂模式（factory.py / protocols.py / test_inference.py / test_factory.py）

| 维度 | 评分 | 说明 |
|------|------|------|
| 设计规范 | 9/10 | Protocol接口定义与文档§5.7对齐，compact/full模式强制正确 |
| 代码质量 | 8/10 | 工厂模式清晰，参数传递完整 |
| 代码风格 | 8/10 | 类型注解完善，错误处理规范 |
| 功能正确性 | 8/10 | 端口配置与文档对齐，重试/熔断参数传递正确 |

**详细发现**：

16. **端口配置与文档对齐确认**
    - llama.cpp默认8001、vLLM默认8002（[factory.py:9,11](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/factory.py#L9)）
    - 与06-开发环境与进度规划§5配置项完全对齐

17. **compact模式max_tokens限制与文档对齐确认**
    - llama.cpp强制max_tokens≤1024（[factory.py:36](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/factory.py#L36)）
    - 与06-开发环境与进度规划§5对齐

---

### 2.7 验证配置模块（verification_config.py / test_verification_config.py）

| 维度 | 评分 | 说明 |
|------|------|------|
| 设计规范 | 8/10 | 配置结构完整，Pydantic验证合理 |
| 代码质量 | 8/10 | 单例模式正确，JSON序列化/反序列化完善 |
| 代码风格 | 8/10 | Pydantic BaseModel使用规范 |
| 功能正确性 | 7/10 | 允许无效配置值（负权重、空分级），缺乏输入验证 |

**详细发现**：

18. **[Medium] 配置验证不严格**
    - `BoundaryPairConfig`允许负权重和空分级字符串
    - `StrategyWeightConfig`允许负权重和零权重
    - 测试`test_negative_weight`和`test_empty_grade_strings`确认了这些行为，但未验证是否应在创建时拒绝

---

## 三、文档一致性偏差

### 偏差1：临床边界差异率阈值不一致 [Critical]

| 维度 | 文档值 | 代码值 | 偏差 |
|------|--------|--------|------|
| clinical_boundary_diff_rate | ≤1%（07-数据标注与模型评估§4） | 0.01=1%（verification_config.py:109） | ✅ 一致 |
| clinical_boundary_diff_rate（ConsistencyReport.passed） | ≤1% | 0.005=0.5%（实际使用consistency_thresholds） | ❌ 不一致 |

**详细分析**：`ConsistencyThresholdConfig.clinical_boundary_diff_rate`默认值为0.01（1%），与文档一致。但需确认`ConsistencyReport.passed`实际读取的阈值是否为0.01。

经核实：[verification_config.py:109](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/verification_config.py#L109)中 `clinical_boundary_diff_rate: float = Field(0.01)`，与文档≤1%一致。✅

### 偏差2：_grades_cross_clinical_boundary与verification_config边界定义语义不同 [Critical]

- `_grades_cross_clinical_boundary`使用数值比较（`critical_boundaries = [3.0]`），仅检测跨越数值3.0的边界
- `verification_config`的`get_default_boundary_config()`定义了完整的from_grade→to_grade边界对
- 两者语义不同：前者是数值边界检测，后者是分级字符串匹配
- **建议**：`_grades_cross_clinical_boundary`应改为从verification_config加载边界配置，使用分级字符串匹配而非数值比较

### 偏差3：幻觉率差异阈值不一致 [High]

| 维度 | 文档值 | 代码值 | 偏差 |
|------|--------|--------|------|
| hallucination_rate_diff | ≤0.5%（07-数据标注与模型评估§4"量化后幻觉率增量≤0.5%"） | 0.005=0.5%（verification_config.py:110） | ✅ 一致 |
| ConsistencyReport.passed使用 | hallucination_rate_diff | 0.003=0.3%（test_consistency.py:258测试值） | ❌ 测试值与配置默认值不一致 |

**详细分析**：测试中`test_passed_when_all_criteria_met`使用`hallucination_rate_diff=0.003`，但`ConsistencyThresholdConfig`默认值为0.005。测试值比配置更严格，可能导致测试通过但实际配置下不通过的情况未被覆盖。

### 偏差4：text_consistency_rate阈值不一致 [Medium]

| 维度 | 文档值 | 代码值 |
|------|--------|--------|
| 完整文本一致率 | ≥90%（01-系统架构设计§5.7） | 0.90=90%（verification_config.py:112） |

✅ 一致。但`ConsistencyReport.passed`中的`text_consistency_rate`检查使用`thresholds.text_consistency_rate`，默认0.90，与文档对齐。

### 偏差5：GPU碎片化计算方法与文档要求不匹配 [High]

- 文档要求"碎片化率<30%（Warning阈值）"，暗示需要真正的碎片化度量
- 代码实现仅为基于使用率的启发式估算，源码注释已承认"并非真正的碎片化度量"
- **建议**：当pynvml支持更详细的显存分配信息时，替换为基于最大连续可用块占比的精确计算

### 偏差6：边界病例测试集规模与文档要求严重不符 [Critical]

| 维度 | 文档要求 | 实际测试 |
|------|----------|----------|
| 边界病例 | ≥300例（TI-RADS/LI-RADS/BI-RADS各≥100例） | 2-3例 |
| 常规病例 | ≥200例 | 1-3例 |
| 高风险病种 | ≥100例 | 0例 |

---

## 四、关键发现汇总（按严重程度排序）

### Critical（3项）

| # | 发现 | 模块 | 影响 |
|---|------|------|------|
| C1 | 临床边界定义硬编码`[3.0]`与文档三器官边界不一致 | consistency.py | LI-RADS/BI-RADS边界跨越可能未被检测 |
| C2 | 边界病例测试数据严重不足（2-3例 vs 文档≥300例） | 全部测试 | 验证结果统计可靠性无法保证 |
| C3 | _grades_cross_clinical_boundary与verification_config边界定义语义不同 | consistency.py | 两套边界逻辑可能产生矛盾结果 |

### High（5项）

| # | 发现 | 模块 | 影响 |
|---|------|------|------|
| H1 | 否定语境窗口过小（10字符） | hallucination_detector.py | 中文否定语境下禁忌词误报 |
| H2 | ContextAwareDetector不支持cardiac | hallucination_detector.py | 心脏超声描述-结论矛盾无法检测 |
| H3 | MedicalCommonSenseDetector误报风险 | hallucination_detector.py | 合理临床表述被误判为幻觉 |
| H4 | GPU碎片化计算仅为启发式估算 | gpu_monitor.py | 碎片化验证结果可能与实际不符 |
| H5 | _percentile函数重复实现 | consistency.py/benchmark.py | 代码重复，维护风险 |

### Medium（4项）

| # | 发现 | 模块 | 影响 |
|---|------|------|------|
| M1 | FalseStats仅基于Engine A | consistency.py | 统计偏差 |
| M2 | 全局默认检测器线程安全 | hallucination_detector.py | 多线程重复创建 |
| M3 | 配置验证不严格 | verification_config.py | 无效配置可能引入运行时错误 |
| M4 | cross_validate未测试边界情况 | test_consistency.py | n_folds > len(cases)时可能异常 |

### Low（2项）

| # | 发现 | 模块 | 影响 |
|---|------|------|------|
| L1 | 基准测试默认不检测幻觉 | benchmark.py | 幻觉率可能被遗漏 |
| L2 | 4小时持续运行验证缺失 | test_gpu_monitor.py | 文档要求未覆盖 |

---

## 五、改进建议（按优先级排序）

### P0 — 必须修复（影响验证有效性）

1. **统一临床边界检测逻辑**：将`_grades_cross_clinical_boundary`改为从`verification_config`加载边界配置，使用分级字符串匹配替代数值比较。删除`critical_boundaries = [3.0]`硬编码。

2. **扩充测试数据集**：
   - 边界病例：至少达到文档要求的≥300例（TI-RADS/LI-RADS/BI-RADS各≥100例）
   - 常规病例：≥200例
   - 高风险病种：≥100例
   - 建议创建`tests/fixtures/`目录存放标准测试数据集

3. **添加真实双引擎集成测试**：当前全部使用MockInferenceEngine，需添加llama.cpp + vLLM真实引擎的集成测试（标记为`@pytest.mark.integration`）

### P1 — 应当修复（影响验证可靠性）

4. **扩大否定语境窗口**：将`_is_negated`的窗口从10字符扩大到20-30字符，或改用正则表达式匹配否定模式

5. **添加cardiac器官支持**：为ContextAwareDetector添加cardiac的描述-结论矛盾检测和过度反应检测规则

6. **优化MedicalCommonSenseDetector**：添加否定语境检查，"良性...排除恶性"不应判定为矛盾

7. **改进GPU碎片化计算**：当pynvml支持时，使用基于最大连续可用块占比的精确计算；当前启发式方法应在文档中明确标注为估算值

8. **提取共享工具函数**：将`_percentile`提取到`app/inference/utils.py`

### P2 — 建议修复（提升代码质量）

9. **添加配置输入验证**：BoundaryPairConfig拒绝负权重和空分级字符串；StrategyWeightConfig拒绝负权重

10. **添加线程安全保护**：`get_default_detector`使用`threading.Lock`保护全局实例创建

11. **FalseStats双引擎统计**：同时考虑Engine A和Engine B的幻觉检测结果

12. **cross_validate边界处理**：当n_folds > len(cases)时抛出ValueError

---

## 六、核心参考文档更新建议

以下文档内容需根据更优编码实践更新：

1. **01-系统架构设计 §5.7**：建议明确`_grades_cross_clinical_boundary`应使用配置驱动的边界检测而非硬编码，与verification_config对齐

2. **07-数据标注与模型评估 §4**：GPU碎片化计算当前为启发式估算，建议文档标注"一期使用基于使用率的启发式估算，二期升级为基于最大连续可用块占比的精确计算"

---

## 七、审查方法说明

本次审查采用以下方法：
1. **静态代码审查**：逐行阅读8个模块的源码和测试代码
2. **测试执行**：运行396个单元测试，确认全部通过
3. **文档对照**：将代码实现与5份核心参考文档逐一比对
4. **批判性分析**：从设计规范、代码质量、代码风格、功能正确性四维度评估
5. **跨模块一致性检查**：验证模块间接口调用和数据流的正确性
