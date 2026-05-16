# LLM轨道验证机制 — 30项修复与优化实施计划

## 概述

基于对LLM推理轨道全部验证环节的系统性审查，识别出30项需修复和优化的问题，按优先级分为4个批次实施。

---

## 批次1：关键逻辑缺陷修复（P0，7项）

### 1. text_exact_match_rate阈值导致Gate PASSED不可达
- **文件**: `verification_config.py` L162, `report.py` L359
- **问题**: 两个不同推理引擎(llama.cpp vs vLLM)无法达到90%精确文本匹配，导致Gate PASSED条件永远无法满足
- **修复**: 将`text_exact_match_rate`默认值从0.90降为0.05，并从Gate PASSED必要条件中移除（仅作为参考指标保留在报告中）
- **验证**: 确认Gate PASSED不再检查text_exact_match_rate

### 2. 量化幻觉增量验证缺失
- **文件**: 新增`app/inference/consistency/quantization_verifier.py`
- **问题**: 文档要求"量化后幻觉率增量≤0.5%"，但代码只比较双引擎幻觉率差异，未实现量化前后幻觉率增量验证
- **修复**: 添加`QuantizationHallucinationVerifier`类，接收量化前(基线)和量化后(测试)的幻觉率，计算增量并与`BenchmarkThresholdConfig.hallucination_increment`(0.005)比较
- **验证**: 单元测试验证增量计算和阈值判定

### 3. 边界配置三源不一致 — 统一为代码单一数据源
- **文件**: `verification_config.py`, `boundary_config.json`, `default_verification_config.json`
- **问题**: Python代码、boundary_config.json、default_verification_config.json三处边界配置存在冲突：
  - thyroid 4a↔4b: JSON=weight=1.0/is_critical=false, 代码=weight=1.5/is_critical=true
  - cardiac normal↔mild: JSON=weight=1.0/is_critical=false, 代码=weight=1.5/is_critical=true
  - breast 4a↔4b: JSON=weight=1.0/is_critical=false, 代码=weight=1.5/is_critical=true
  - breast: JSON缺少BI-RADS 0类边界(0↔3/0↔4/0↔5)
- **修复**: 以代码(`get_default_boundary_config()`)为权威数据源，从代码生成JSON配置文件，删除独立的boundary_config.json
- **验证**: 添加配置一致性测试，确保JSON与代码始终同步

### 4. default_verification_config.json缺少多个ConsistencyThresholdConfig字段
- **文件**: `default_verification_config.json`
- **问题**: JSON配置缺少`text_exact_match_rate`, `phase1_clinical_boundary_diff_rate`, `weighted_boundary_diff_rate`, `critical_boundary_diff_rate`, `lvef_conflict_rate_threshold`, `hallucination_rate_warning`, `hallucination_rate_critical`, `absolute_hallucination_rate_threshold`, `latency_diff_abs_ms`, `latency_diff_gate_logic`, `conditional_*`系列, `phase2_*`系列, `gpu_*`系列等字段
- **修复**: 从代码的默认值生成完整的JSON配置
- **验证**: 加载JSON后与代码默认配置对比，确保字段完整

### 5. ConsistencyReport缺少量化幻觉增量相关字段
- **文件**: `report.py`
- **问题**: 报告缺少`quantization_hallucination_increment`, `quantization_baseline_rate`, `quantization_test_rate`等字段
- **修复**: 在ConsistencyReport中添加量化幻觉增量相关字段
- **验证**: 确认新字段在序列化输出中正确呈现

### 6. Gate PASSED条件中text_exact_match_rate检查移除
- **文件**: `report.py` L351-364
- **问题**: Gate PASSED条件包含`self.text_exact_match_rate >= thresholds.text_exact_match_rate`，即使降低阈值也不应作为必要条件
- **修复**: 从Gate PASSED条件中移除text_exact_match_rate检查，将其降级为参考指标
- **验证**: 确认PASSED条件不再包含text_exact_match_rate

### 7. load_config_from_json无法正确加载嵌套Pydantic配置
- **文件**: `verification_config.py` L774-790
- **问题**: `VerificationConfig(**data)`对嵌套的Pydantic模型（如BoundaryConfig内的organ_boundaries）可能无法正确解析
- **修复**: 使用`VerificationConfig.model_validate(data)`替代`VerificationConfig(**data)`
- **验证**: 测试从JSON加载完整配置的正确性

---

## 批次2：性能优化与冗余消除（P1，8项）

### 8. 双引擎幻觉检测重复调用合并
- **文件**: `verifier.py` L227-234, L259-266
- **问题**: `_verify_single_case`中对每个引擎分别调用`is_hallucination()`和`detect()`，`is_hallucination`内部会再次调用检测逻辑，造成冗余计算
- **修复**: 只调用`detect()`一次，从`DetectionResult.is_hallucination`获取布尔结果，从`DetectionResult.issues`获取问题列表
- **验证**: 确认每个引擎只调用一次检测

### 9. _verify_single_case中hasattr检查冗余移除
- **文件**: `verifier.py` L234, L266
- **问题**: `hasattr(self._hallucination_detector, "detect")`始终为True（HallucinationDetector类始终有detect方法）
- **修复**: 移除hasattr条件检查，直接使用detect结果
- **验证**: 代码逻辑不变，减少不必要的条件判断

### 10. ConsistencyReport.gate_status属性缓存
- **文件**: `report.py` L263-372
- **问题**: `gate_status`是property，每次访问都重新计算复杂的门控判定逻辑
- **修复**: 添加`_gate_status_cache`字段，首次计算后缓存结果；当关键字段变更时清除缓存
- **验证**: 多次访问gate_status只计算一次

### 11. cross_validate方法中幻觉率只统计engine_a
- **文件**: `verifier.py` L765-771
- **问题**: 交叉验证中幻觉率只使用`hallucination_a_count`，忽略了engine_b的幻觉率
- **修复**: 使用双引擎平均幻觉率`(hallucination_a_count + hallucination_b_count) / (2 * total_cases)`
- **验证**: 交叉验证结果包含双引擎幻觉率

### 12. _compute_false_stats中策略级假阳性统计逻辑修正
- **文件**: `verifier.py` L573-595
- **问题**: 策略级统计将两个引擎的issues合并后统计fp/tp/fn/tn，但应分别统计每个引擎的策略级表现
- **修复**: 分别统计engine_a和engine_b的策略级假阳性，合并时取并集
- **验证**: StrategyFalseStats的fp/fn/tp/tn更准确

### 13. _compute_boundary_stats中weighted_boundary_diff_rate计算修正
- **文件**: `verifier.py` L376-395
- **问题**: 当所有边界用例属于同一器官时，max_possible_weight等于所有用例的organ_max之和，但权重归一化基准不合理（应基于实际涉及的边界对，而非器官最大权重）
- **修复**: 使用实际涉及的边界对的权重之和作为归一化基准
- **验证**: weighted_boundary_diff_rate在0-1范围内合理

### 14. organ_breakdown中添加semantic_grading_consistency_rate的Wilson CI
- **文件**: `organ_breakdown.py`
- **问题**: 器官分解统计中`semantic_grading_consistency_rate`缺少Wilson置信区间
- **修复**: 添加`semantic_grading_consistency_rate_ci_lower/upper`字段
- **验证**: 器官分解报告包含语义一致率的CI

### 15. GPU显存碎片化监控实现
- **文件**: 新增`app/inference/gpu_monitor.py`
- **问题**: 配置中有`gpu_fragmentation_warning_threshold`(0.30)和`gpu_fragmentation_critical_threshold`(0.40)，但无实际监控代码
- **修复**: 实现GPU显存碎片化监控模块，使用nvidia-smi或pynvml获取显存信息，计算碎片化率
- **验证**: 单元测试验证碎片化率计算逻辑

---

## 批次3：数据完整性与报告完善（P2，9项）

### 16. 测试数据集添加grading_category字段
- **文件**: `consistency_test_cases.json`
- **问题**: 测试数据集缺少`grading_category`字段，无法验证每个分级类别的样本量是否满足Phase2要求(≥100/类别)
- **修复**: 为每个用例添加`grading_category`字段（如"ti-rads-3", "li-rads-4"等），确保3/4类各≥100例
- **验证**: 统计各grading_category的样本量

### 17. 幻觉标注样本增加至≥30/器官
- **文件**: `consistency_test_cases.json`
- **问题**: 当前仅15个幻觉标注用例，导致精确率/召回率统计不可靠
- **修复**: 将幻觉标注用例增加到每器官≥30例（≥120总计），覆盖5种检测策略
- **验证**: 每器官幻觉阳性+阴性各≥15例

### 18. boundary_test_cases.py幻觉用例与consistency_test_cases.json同步
- **文件**: `boundary_test_cases.py`, `consistency_test_cases.json`
- **问题**: boundary_test_cases.py生成200个幻觉用例(50/器官)，但consistency_test_cases.json只有15个
- **修复**: 将boundary_test_cases.py的幻觉用例导出到consistency_test_cases.json
- **验证**: 两个数据源的幻觉用例数量一致

### 19. organ_breakdown添加text_exact_match_rate
- **文件**: `organ_breakdown.py`
- **问题**: 器官分解统计缺少`text_exact_match_rate`
- **修复**: 在compute_organ_breakdown中添加按器官的精确匹配率计算
- **验证**: 器官分解报告包含text_exact_match_rate

### 20. serialization.py添加text_exact_match_rate序列化
- **文件**: `serialization.py`
- **问题**: 报告序列化缺少`text_exact_match_rate`和`text_consistency_rate_strict`字段
- **修复**: 在report_to_dict中添加这两个字段的序列化
- **验证**: JSON输出包含精确匹配率和严格一致率

### 21. failure_analysis添加量化幻觉增量相关建议
- **文件**: `failure_analysis.py`
- **问题**: 失败分析未考虑量化幻觉增量场景
- **修复**: 在suggest_for_trace和推荐排序中添加量化幻觉增量相关逻辑
- **验证**: 量化幻觉增量超标时生成针对性建议

### 22. remediation属性添加text_exact_match_rate偏差信息
- **文件**: `report.py` L379-430
- **问题**: 修复建议中未包含text_exact_match_rate的偏差信息
- **修复**: 在remediation属性中添加text_exact_match_rate偏差计算
- **验证**: 当精确匹配率低于阈值时，remediation包含该指标

### 23. nmpa_declaration添加幻觉率信息
- **文件**: `report.py` L432-458
- **问题**: NMPA合规声明缺少幻觉率相关声明
- **修复**: 在nmpa_declaration中添加幻觉率差异和绝对幻觉率信息
- **验证**: 条件通过时的NMPA声明包含幻觉率

### 24. cross_validate添加boundary_diff_rate稳定性验证
- **文件**: `verifier.py` L707-798
- **问题**: 交叉验证只验证一致率和幻觉率的稳定性，缺少边界差异率的稳定性验证
- **修复**: 在CrossValidationResult中添加`train_boundary_diff_rate`, `validation_boundary_diff_rate`, `boundary_diff_rate_diff`字段
- **验证**: 交叉验证结果包含边界差异率波动

---

## 批次4：测试补全与防御性编程（P3，6项）

### 25. 量化幻觉增量单元测试
- **文件**: `tests/unit/llm/test_consistency.py`
- **问题**: 缺少QuantizationHallucinationVerifier的单元测试
- **修复**: 添加TestQuantizationHallucinationVerifier测试类，覆盖增量计算、阈值判定、边界情况
- **验证**: pytest通过

### 26. GPU显存碎片化监控单元测试
- **文件**: `tests/unit/llm/test_consistency.py`
- **问题**: 缺少GPU显存碎片化监控的单元测试
- **修复**: 添加TestGPUFragmentationMonitor测试类，使用Mock测试碎片化率计算
- **验证**: pytest通过

### 27. 配置同步一致性测试
- **文件**: `tests/unit/llm/test_consistency.py`
- **问题**: 缺少验证JSON配置与代码默认配置一致性的测试
- **修复**: 添加TestConfigConsistency测试类，验证JSON配置加载后与代码默认值一致
- **验证**: pytest通过

### 28. weighted_boundary_diff_rate修正后测试
- **文件**: `tests/unit/llm/test_consistency.py`
- **问题**: 修正weighted_boundary_diff_rate计算后需要更新相关测试
- **修复**: 添加/更新TestWeightedBoundaryGateCheck测试用例
- **验证**: pytest通过

### 29. 幻觉检测合并调用后测试更新
- **文件**: `tests/unit/llm/test_consistency.py`
- **问题**: 合并is_hallucination和detect调用后，需更新TestConsistencyVerifier相关测试
- **修复**: 更新Mock预期，确保detect只调用一次
- **验证**: pytest通过

### 30. Phase1/Phase2门控标准完整测试
- **文件**: `tests/unit/llm/test_consistency.py`
- **问题**: 缺少Phase1使用3%边界差异率阈值的完整门控测试
- **修复**: 添加TestPhase1GateCriteria测试类，验证Phase1使用phase1_clinical_boundary_diff_rate=0.03
- **验证**: pytest通过

---

## 实施顺序与依赖关系

```
批次1 (P0): #1 → #6 → #5 → #2 → #3 → #4 → #7
批次2 (P1): #8 → #9 → #10 → #11 → #12 → #13 → #14 → #15
批次3 (P2): #16 → #17 → #18 → #19 → #20 → #21 → #22 → #23 → #24
批次4 (P3): #25 → #26 → #27 → #28 → #29 → #30
```

**关键依赖**:
- #6依赖#1（先确定阈值策略再修改Gate条件）
- #5依赖#2（先定义量化幻觉增量验证器再添加报告字段）
- #25依赖#2（先实现量化验证器再写测试）
- #26依赖#15（先实现GPU监控再写测试）
- #27依赖#3和#4（先统一配置再写一致性测试）
- #28依赖#13（先修正计算再写测试）
- #29依赖#8（先合并调用再更新测试）

## 修改文件清单

| 文件 | 涉及项 |
|------|--------|
| `app/inference/verification_config.py` | #1, #3, #4, #7 |
| `app/inference/consistency/report.py` | #5, #6, #10, #22, #23 |
| `app/inference/consistency/verifier.py` | #8, #9, #11, #12, #13, #24 |
| `app/inference/consistency/organ_breakdown.py` | #14, #19 |
| `app/inference/consistency/serialization.py` | #20 |
| `app/inference/consistency/failure_analysis.py` | #21 |
| `app/inference/consistency/statistics.py` | (无修改) |
| `app/inference/hallucination_detector.py` | (无修改) |
| `app/inference/data/boundary_config.json` | #3 (删除) |
| `app/inference/data/default_verification_config.json` | #3, #4 |
| `app/inference/data/consistency_test_cases.json` | #16, #17, #18 |
| `app/inference/consistency/quantization_verifier.py` | #2 (新增) |
| `app/inference/gpu_monitor.py` | #15 (新增) |
| `tests/unit/llm/test_consistency.py` | #25, #26, #27, #28, #29, #30 |
