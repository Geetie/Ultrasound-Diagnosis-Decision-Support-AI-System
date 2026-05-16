# LLM轨道Phase0A审查问题逐项修复计划

## 修复总览

基于审查报告发现的15个问题，按优先级（P0→P1→P2）逐项修复。每个修复步骤独立可验证。

---

## P0 关键问题（3项）

### Fix-1: 从ForbiddenTermsDetector否定词集合中移除"不除外/不能除外/无法除外"

**问题**：`_NEGATION_TERMS` 包含"不除外"，但 `MedicalCommonSenseDetector._AUGMENT_PATTERNS` 也包含"不除外"。同一术语在两个策略中语义矛盾——禁忌词检测视为"否定"跳过检测，医学常识检测视为"增强怀疑"加重检测。

**修改文件**：
- `app/inference/hallucination_detector.py` L70-80：从 `ForbiddenTermsDetector.NEGATION_TERMS` 中移除 `"不除外"`, `"不能除外"`, `"无法除外"`
- `tests/unit/llm/test_hallucination_detector.py`：增加测试验证"不除外"不再作为否定词跳过禁忌词检测

**验证**：运行 `pytest tests/unit/llm/test_hallucination_detector.py -v`

### Fix-2: 统一4b↔4c语义等价与临床显著子分级对的矛盾

**问题**：`grading_semantic_equivalent` 判定4b↔4c为等价（子级差=1），但 `_CLINICALLY_SIGNIFICANT_SUBCLASS_PAIRS` 将4b↔4c标记为临床显著对。两个机制语义矛盾。

**修改文件**：
- `app/inference/consistency/statistics.py` L52-64：在 `grading_semantic_equivalent` 中增加临床显著子分级对排除逻辑——若两个分级构成临床显著子分级对，则不视为语义等价
- `app/inference/grading_definitions.py`：导出 `_CLINICALLY_SIGNIFICANT_SUBCLASS_PAIRS`
- `tests/unit/llm/test_consistency.py`：更新 `TestGradingSemanticEquivalent` 中4b↔4c的测试用例，断言改为 `False`

**验证**：运行 `pytest tests/unit/llm/test_consistency.py::TestGradingSemanticEquivalent -v`

### Fix-3: 修复延迟绝对阈值与百分比阈值不匹配问题

**问题**：`latency_diff_abs_ms=200.0` 与 `latency_diff_pct=20.0` 在AND逻辑下，低延迟基线时绝对阈值先触发，实际有效百分比阈值远低于20%。

**修改文件**：
- `app/inference/verification_config.py` L156：将 `latency_diff_gate_logic` 默认值从 `"and"` 改为 `"or"`
- `app/inference/consistency/report.py` L282-288：更新注释说明OR逻辑的合理性（百分比或绝对值任一满足即通过，避免低基线时过度严格）
- `tests/unit/llm/test_consistency.py`：增加测试验证OR逻辑下低延迟基线场景

**验证**：运行 `pytest tests/unit/llm/test_consistency.py -v -k latency`

---

## P1 重要问题（5项）

### Fix-4: 将thyroid 4a↔4b边界标记为critical

**问题**：TI-RADS 4a（恶性风险2-10%）和4b（恶性风险10-50%）的穿刺建议差异显著，但当前标记为non-critical。

**修改文件**：
- `app/inference/verification_config.py` L242-243：thyroid 4a↔4b 的 `is_critical` 从 `False` 改为 `True`，`weight` 从 `1.0` 改为 `1.5`
- `app/inference/verification_config.py` L303-304：breast 4a↔4b 同步修改

**验证**：运行 `pytest tests/unit/llm/test_verification_config.py -v`

### Fix-5: 将cardiac normal↔mild边界标记为critical

**问题**：LVEF从正常（≥55%）降至轻度减低（45-54%）是心功能评估的关键转折点。

**修改文件**：
- `app/inference/verification_config.py` L274-275：cardiac normal↔mild 的 `is_critical` 从 `False` 改为 `True`，`weight` 从 `1.0` 改为 `1.5`

**验证**：运行 `pytest tests/unit/llm/test_verification_config.py -v`

### Fix-6: 提升P99延迟可靠性最小样本量

**问题**：100个样本的P99估计仅基于第99个最大值，方差极大。

**修改文件**：
- `app/inference/verification_config.py` L173：`latency_p99_min_samples` 从 `100` 改为 `500`

**验证**：运行 `pytest tests/unit/llm/ -v -k latency`

### Fix-7: 幻觉率差异计算分母一致性

**问题**：`hallucination_rate_diff` 的分母是全部用例（含无ground truth的），但FP/FN仅基于有ground truth的子集。

**修改文件**：
- `app/inference/consistency/verifier.py` `_compute_hallucination_stats`：增加 `hallucination_rate_a_gt` / `hallucination_rate_b_gt` 字段，基于有ground truth的子集计算幻觉率
- `app/inference/consistency/report.py` ConsistencyReport：增加 `hallucination_rate_a_gt`, `hallucination_rate_b_gt`, `hallucination_rate_diff_gt` 字段

**验证**：运行 `pytest tests/unit/llm/test_consistency.py -v`

### Fix-8: 增加contradiction_similarity_threshold配置项

**问题**：06文档要求 `CONTRADICTION_SIMILARITY_THRESHOLD=0.85`，但代码中无此配置项。

**修改文件**：
- `app/inference/verification_config.py` `TextSimilarityConfig`：增加 `contradiction_similarity_threshold: float = Field(0.85, description="矛盾检测相似度阈值")`
- `app/inference/hallucination_detector.py` `ContextAwareDetector`：在描述-结论矛盾检测中使用此阈值

**验证**：运行 `pytest tests/unit/llm/test_hallucination_detector.py -v`

---

## P2 改进建议（7项）

### Fix-9: 增加分级提取负面测试用例

**修改文件**：
- `tests/unit/llm/test_consistency.py` `TestExtractGrading`：增加测试用例验证"随访3月后复查"、"4a类可能，建议4-6周复查"等场景不误匹配

**验证**：运行 `pytest tests/unit/llm/test_consistency.py::TestExtractGrading -v`

### Fix-10: FalseStats增加统计可靠性标记

**修改文件**：
- `app/inference/consistency/report.py` `FalseStats`：增加 `has_valid_precision: bool` 和 `has_valid_recall: bool` 属性
  - `has_valid_precision = (true_positives + false_positives) > 0`
  - `has_valid_recall = (true_positives + false_negatives) > 0`

**验证**：运行 `pytest tests/unit/llm/test_consistency.py -v`

### Fix-11: 缺少expected_grading时记录警告

**修改文件**：
- `app/inference/consistency/verifier.py` `_verify_single_case`：当 `expected_grading` 为 None 且发生边界跨越时，记录warning日志

**验证**：运行 `pytest tests/unit/llm/test_consistency.py -v`

### Fix-12: 优化建议量化基准与文档对齐

**修改文件**：
- `app/inference/consistency/failure_analysis.py` L158-160：将"建议增加≥50例"改为"建议增加≥100例/类别边界训练数据（参考07-数据标注与模型评估.md）"

**验证**：运行 `pytest tests/unit/llm/test_consistency.py -v`

### Fix-13: 增加accuracy_retention_rate字段

**修改文件**：
- `app/inference/consistency/report.py` ConsistencyReport：增加 `accuracy_retention_rate: float = 0.0` 字段，与语义分级一致率区分
- `app/inference/consistency/verifier.py`：在基准测试结果中计算并填充此字段

**验证**：运行 `pytest tests/unit/llm/test_benchmark.py -v`

### Fix-14: 文本相似度增加医学否定对测试

**修改文件**：
- `tests/unit/llm/test_consistency.py` `TestTextSimilarity`：增加测试验证"良性可能" vs "恶性可能"的相似度确实低于0.5（而非仅<0.9）

**验证**：运行 `pytest tests/unit/llm/test_consistency.py::TestTextSimilarity -v`

### Fix-15: 增加FunASR+vLLM+TensorRT混合运行集成测试骨架

**修改文件**：
- `tests/integration/llm/test_hybrid_runtime.py`：新建集成测试文件，包含混合运行显存碎片化验证的测试骨架（标记为skipif需要真实环境）

**验证**：运行 `pytest tests/integration/llm/ -v --collect-only`

---

## 执行顺序

1. Fix-1 → Fix-2 → Fix-3（P0，依次执行）
2. Fix-4 → Fix-5 → Fix-6 → Fix-7 → Fix-8（P1，可并行）
3. Fix-9 → Fix-10 → Fix-11 → Fix-12 → Fix-13 → Fix-14 → Fix-15（P2，可并行）
4. 全量测试验证

## 验证策略

每个Fix完成后运行对应的单元测试。全部Fix完成后运行：
```bash
pytest tests/unit/llm/ -v
pytest tests/integration/llm/ -v --collect-only
```
