# LLM轨道Phase0A验证机制全面有效性审查报告

**审查日期**: 2026-05-13  
**审查范围**: LLM轨道Phase0A阶段全部验证环节  
**测试结果**: 279个单元测试全部通过（0.70s）  
**审查原则**: 独立、客观、深入、批判性

---

## 一、总体评价

LLM轨道Phase0A验证体系整体设计**严谨且完整**，覆盖了分级一致性、临床边界检测、幻觉检测、性能验证、失败分析五大核心维度，门控机制与核心参考文档高度一致。代码质量整体较高，模块解耦合理，测试覆盖充分。

**但存在以下需要关注的问题**，按严重程度分级：

---

## 二、Critical级问题（3项）

### C1. 幻觉检测 `allow_if_context` 机制存在严重误报风险

**位置**: [hallucination_detector.py:139-143](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/hallucination_detector.py#L139-L143)

**问题**: `_is_context_allowed()` 仅检查上下文窗口（±50字符）中是否包含 `allow_if_context` 关键词。例如甲状腺禁忌词配置中，"骨转移"的 `allow_if_context="甲状腺"`，这意味着文本"甲状腺癌伴肺癌骨转移"中，"骨转移"因附近存在"甲状腺"而被跳过检测，但"肺癌"本身也是甲状腺禁忌词且应被检出。

更严重的是，"甲状腺癌伴肺癌"这样的文本中，"肺癌"会被独立检出（无 allow_if_context），但"骨转移"被跳过——这导致同一文本中的不同禁忌词检测标准不一致。

**影响**: 可能导致跨器官术语在特定上下文组合下漏检，降低幻觉检测的召回率。

**建议**: `allow_if_context` 应仅允许与当前器官直接相关的转移模式（如"甲状腺癌骨转移"），而不应允许同一上下文中出现的其他禁忌词。需要增加上下文相关性验证逻辑。

### C2. `organ_breakdown.py` 文本一致率阈值硬编码与全局逻辑不一致

**位置**: [organ_breakdown.py:49-53](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/consistency/organ_breakdown.py#L49-L53)

**问题**: 器官维度分解中，文本一致率使用硬编码阈值 `0.8`：
```python
text_similarity(p.engine_a_result.text, p.engine_b_result.text) >= 0.8
```
而全局文本一致率计算使用 `text_consistency_with_grading()`，该函数在分级语义等价时阈值为 `0.5`，不等价时为 `0.8`。

**影响**: 器官维度的文本一致率会系统性低于全局文本一致率（因为语义等价用例在全局用0.5阈值但在器官维度用0.8阈值），导致器官维度报告与全局报告不一致，可能误导优化方向。

**建议**: 器官维度应复用 `text_consistency_with_grading()` 函数，保持阈值逻辑一致。

### C3. `weighted_boundary_diff_rate` 计算分母不合理

**位置**: [verifier.py:284-300](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/consistency/verifier.py#L284-L300)

**问题**: `max_possible_weight` 的计算方式是对每个用例取其器官的最大边界权重并累加：
```python
for p in pairs:
    boundaries = organ_boundaries.get(p.organ, [])
    if boundaries:
        organ_max = max(b.weight for b in boundaries)
        max_possible_weight += organ_max
```
这假设每个用例都可能跨越最严重边界（如3→5，权重3.0），但实际上大部分用例的分级是一致的，不会产生任何边界跨越。这导致分母被严重高估，`weighted_boundary_diff_rate` 被系统性低估。

**影响**: 加权边界差异率指标可能无法有效反映临床风险的真实严重程度。

**建议**: 分母应改为"实际发生边界跨越的用例的最大可能权重之和"，或改用"边界跨越用例数/总用例数"作为主指标，加权版本作为补充。

---

## 三、Warning级问题（8项）

### W1. 心脏器官分级提取优先级设计存在临床风险

**位置**: [grading_definitions.py:83-101](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/grading_definitions.py#L83-L101)

**问题**: `extract_grading()` 对cardiac器官"中文描述优先于LVEF数值"。当文本为"LVEF: 60%，收缩功能中度减低"时，提取结果为"moderate"而非"normal"。虽然代码注释解释"中文描述通常由医生综合判断后给出"，但在双引擎一致性验证场景下，如果两个引擎对同一输入的中文描述和LVEF数值的侧重不同，会导致分级提取结果不一致，而这种不一致并非引擎推理差异，而是提取逻辑的优先级设计导致的。

**建议**: 在一致性验证报告中，对cardiac器官增加"LVEF-描述矛盾"标记，区分引擎推理差异和提取优先级导致的差异。

### W2. 语义等价判定 `4a↔4c` 不等价可能过于严格

**位置**: [statistics.py:30-42](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/consistency/statistics.py#L30-L42)

**问题**: `grading_semantic_equivalent()` 中 `abs(level_a[1] - level_b[1]) <= 1`，即4a(4,1)与4b(4,2)等价，但4a(4,1)与4c(4,3)不等价。从临床角度看，4a/4b/4c都属于4类（可疑恶性），临床处理均为"建议穿刺活检"，差异仅在穿刺优先级。将4a与4c判定为不等价可能导致语义一致率偏低。

**建议**: 考虑将子级别差异阈值放宽到2（即4a与4c等价），或引入"临床处理等价"概念（4类内部所有子级别临床处理一致）。

### W3. `percentile()` 函数文档与实际行为不一致

**位置**: [utils.py:5-26](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/utils.py#L5-L26)

**问题**: 函数文档说明"无需预排序，函数内部自动排序"，但 `verifier.py` 中调用时传入的是已排序列表（`sorted_a = sorted(latencies_a)`），导致重复排序。同时文档说"与numpy的默认方法（最近秩法）不同"，但实际使用线性插值法，对于小样本（<30个）P99差异可能较大。

**建议**: 移除调用处的预排序（函数内部已排序），或在函数签名中明确要求传入未排序数据。更新文档说明插值方法。

### W4. `FalseStats` 顶层字段全为0的设计可能造成混淆

**位置**: [report.py:110-136](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/consistency/report.py#L110-L136), [verifier.py:401-409](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/consistency/verifier.py#L401-L409)

**问题**: 顶层 `FalseStats` 的 TP/FP/FN/TN 全为0，仅通过 `engine_a_stats`/`engine_b_stats` 子对象存储实际数据。`precision`/`recall`/`f1_score` 属性因此返回0.0，可能误导使用者认为检测性能为零。

**建议**: 要么将顶层 FalseStats 改为两引擎的汇总统计，要么在序列化时明确标注"汇总数据请参考 engine_a_stats/engine_b_stats"。

### W5. 边界对配置双向定义冗余

**位置**: [verification_config.py:210-233](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/verification_config.py#L210-L233)

**问题**: 甲状腺边界对配置中，`3→4` 和 `4→3` 分别定义为独立的 `BoundaryPairConfig`，共22对中11对是方向相反的重复。这增加了配置维护成本，且容易导致方向权重不一致。

**建议**: 将 `BoundaryPairConfig` 改为无向边设计（`is_directional=False` 默认），减少配置条目数量。

### W6. `_is_negated()` 否定词窗口可能不足

**位置**: [hallucination_detector.py:79-94](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/hallucination_detector.py#L79-L94)

**问题**: 否定词检测窗口为±50字符。在中文医疗文本中，否定词与术语之间可能隔着较长的描述性文字（如"未见明显异常回声，各叶形态规则，被膜完整，……，肺癌"），50字符窗口可能无法覆盖。

**建议**: 将窗口扩大到±100字符，或改为以句号/分号为界的句子级检测。

### W7. 集成测试中 `concurrency=1` 不反映真实场景

**位置**: [test_phase0a_consistency.py:186-187](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/integration/llm/test_phase0a_consistency.py#L186-L187)

**问题**: Gate标准层测试中 `ConsistencyVerifier` 的 `concurrency=1`，而文档要求验证2并发场景（06-开发环境与进度规划.md §7 "2并发P99 < 8s"）。单并发下的延迟和一致性结果可能与2并发场景有显著差异。

**建议**: Gate标准层测试应增加 `concurrency=2` 的测试用例，验证并发场景下的一致性和延迟。

### W8. 幻觉 ground truth 用例仅32例，F1统计意义不足

**位置**: [boundary_test_cases.py:231-587](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/fixtures/boundary_test_cases.py#L231-L587)

**问题**: `generate_hallucination_ground_truth_cases()` 每器官仅8例（4阳性+4阴性），4器官共32例。对于精确率/召回率/F1统计，32例的样本量不足以产生稳定的估计，特别是当某个器官的阳性率较低时。

**建议**: 将每器官用例数增加到至少30例（15阳性+15阴性），使F1统计的95%置信区间宽度小于±20%。

---

## 四、Low级问题（6项）

### L1. `text_similarity()` 中文bigram噪声问题

中文无空格分词，bigram切分会产生大量无意义字符对（如"甲状"、"状腺"），降低相似度区分度。建议考虑使用字符trigram或基于jieba分词的词级Jaccard。

### L2. Levenshtein距离对长文本的O(n*m)复杂度

当LLM输出文本较长时（如500+字符），Levenshtein距离计算可能成为性能瓶颈。建议对超长文本截断或使用近似算法。

### L3. `benchmark.py` 中 `logger` 变量名错误

**位置**: [benchmark.py:151](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/benchmark.py#L151)

代码中使用 `logger.warning(...)` 但模块级日志变量名为 `self._log`，会导致 `NameError`。应改为 `self._log.warning(...)`。

### L4. `drift_detector.py` 基线计算的最小样本量30与Wilson CI的适用性

Wilson置信区间在样本量≥5时即可使用，但 `compute_baseline()` 要求≥30。对于低频指标（如幻觉率），30个历史数据点可能难以收集。建议对比例指标降低最小样本量要求至10。

### L5. `serialization.py` 缺少 `text_consistency_rate_strict` 字段

`report_to_dict()` 序列化中缺少 `text_consistency_rate_strict` 字段，导致JSON报告中无法查看严格文本一致率。

### L6. `verification_phase` 字段无运行时自动设置机制

`ConsistencyThresholdConfig.verification_phase` 默认为 `"phase1"`，但没有根据实际验证时间或用例数量自动切换到 `"phase2"` 的机制，需要手动设置。

---

## 五、文档一致性审查结果

### 5.1 阈值对照（全部一致 ✓）

| 代码字段 | 代码值 | 文档要求 | 一致性 |
|---------|-------|---------|--------|
| `grading_consistency_rate` | 0.95 | ≥95% | ✓ |
| `clinical_boundary_diff_rate` | 0.01 | ≤1% | ✓ |
| `hallucination_rate_diff` | 0.005 | ≤0.5% | ✓ |
| `text_consistency_rate` | 0.90 | ≥90% | ✓ |
| `semantic_grading_consistency_rate` | 0.98 | ≥98% | ✓ |
| `phase2_boundary_ci_upper_threshold` | 0.02 | ≤2% | ✓ |
| `phase2_min_cases_per_organ` | 300 | ≥300例 | ✓ |
| `hallucination_increment` | 0.005 | ≤0.5% | ✓ |
| `accuracy` | 0.89 | ≥89% | ✓ |
| `latency_p99_ms` | 3000.0 | <3s | ✓ |

### 5.2 条件性门控规则对照

**文档规则**（01-系统架构设计.md §5.7）：
- 分级一致率≥95% 且 边界差异率≤1% → PASSED
- 分级一致率90%-95% 或 边界差异率1%-2% → CONDITIONAL
- 分级一致率<90% 或 边界差异率>2% → FAILED

**代码实现**（report.py gate_status属性）：
- PASSED条件：gc≥0.95 AND sgc≥0.98 AND bd≤0.01 AND ci_upper≤0.02 AND hd≤0.005 AND tc≥0.90 AND ld≤20%
- CONDITIONAL条件：gc≥0.90 AND ci_upper≤0.02 AND hd≤0.01 AND tc≥0.80 AND ld≤30%
- FAILED：其余

**差异**: 代码的PASSED/CONDITIONAL条件比文档更严格（增加了语义一致率、CI上界、幻觉差异、文本一致率、延迟差异等维度），这是合理的增强，但应在文档中补充说明。

### 5.3 需要更新文档的条目

1. **01-系统架构设计.md §5.7**: 条件性门控规则应补充语义一致率≥98%、CI上界≤2%、幻觉差异≤0.5%、文本一致率≥90%、延迟差异≤20%等PASSED条件的完整描述
2. **06-开发环境与进度规划.md §7**: 应补充延迟差异>50%直接FAILED的规则说明
3. **07-数据标注与模型评估.md §4**: 应明确"幻觉率差异≤0.5%"指的是跨引擎绝对差异，而非量化幻觉增量

---

## 六、代码修复执行

以下问题已在代码中修复：

### 修复1: `organ_breakdown.py` 文本一致率阈值不一致（C2）

将硬编码的 `text_similarity(...) >= 0.8` 改为调用 `text_consistency_with_grading()` 函数。

### 修复2: `serialization.py` 缺少 `text_consistency_rate_strict` 字段（L5）

在 `report_to_dict()` 中添加缺失字段。

### 修复3: `benchmark.py` 中 `logger` 变量名错误（L3）

将 `logger.warning` 改为 `self._log.warning`。

---

## 七、测试增强建议

1. **增加cardiac器官LVEF-描述矛盾测试**: 验证当LVEF与中文描述矛盾时，提取结果的标记和报告
2. **增加 `allow_if_context` 误报测试**: 验证"甲状腺癌伴肺癌"等组合场景
3. **增加 `concurrency=2` 的Gate标准测试**: 验证并发场景下的一致性
4. **增加 `weighted_boundary_diff_rate` 分母合理性测试**: 验证全一致场景下该指标为0
5. **增加器官维度文本一致率与全局一致率的一致性测试**: 验证两者使用相同阈值逻辑

---

## 八、结论

LLM轨道Phase0A验证体系**设计合理、实施规范、测试充分**，279个单元测试全部通过。核心门控阈值与文档完全一致。主要风险集中在：

1. **幻觉检测的上下文允许机制**（C1）可能降低召回率
2. **器官维度统计与全局统计的阈值不一致**（C2）可能导致误导性报告
3. **加权边界差异率的分母高估**（C3）可能低估临床风险

建议在Gate 0A正式验证前修复C1-C3三个Critical级问题，并在核心参考文档中补充完整的门控规则描述。
