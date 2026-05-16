# LLM轨道验证机制修复实施方案

> 基于《LLM轨道验证机制全面有效性审查报告》，针对全部识别问题制定编码修复计划。
> 审查日期：2026-05-15 | 实施优先级：P0 > P1 > P2

---

## 修改文件清单

| 文件 | 修改类型 | 涉及问题 |
|------|----------|----------|
| `app/inference/consistency/statistics.py` | 修改 | R1, R7 |
| `app/inference/grading_definitions.py` | 修改 | R2 |
| `app/inference/verification_config.py` | 修改 | R3, R7 |
| `app/inference/consistency/failure_analysis.py` | 修改 | R5, E15-1 |
| `app/inference/consistency/report.py` | 修改 | B6-1 |
| `app/inference/hallucination_detector.py` | 修改 | C8-2 |
| `tests/unit/llm/test_consistency.py` | 修改+新增 | R1, R7, B6-1, D11-1, E14-1 |
| `tests/unit/llm/test_hallucination_detector.py` | 修改+新增 | R8, C9-1 |
| `tests/integration/llm/test_phase0a_consistency.py` | 修改 | R6 |
| `tests/unit/llm/test_verification_config.py` | 修改+新增 | R3 |

---

## P0 修复项（高严重度）

### P0-1: 校准语义等价规则（R1）

**问题**：`grading_semantic_equivalent("4a", "4b", "thyroid")` 返回 `True`，但TI-RADS 4a（2-10%恶性风险）与4b（10-50%）临床处理差异显著。

**修复方案**：

1. **修改 `statistics.py` L52-64 `grading_semantic_equivalent`**：
   - 当前逻辑：同主级别内子级别差≤1即等价
   - 修改为：同主级别内子级别差≤1 **且** 不跨越 `_CLINICALLY_SIGNIFICANT_SUBCLASS_PAIRS` 中的配对
   - 具体实现：导入 `_CLINICALLY_SIGNIFICANT_SUBCLASS_PAIRS`，在返回True前检查 `{grade_a, grade_b}` 的frozenset是否在该器官的临床显著子分级对集合中

2. **修改 `grading_definitions.py` L116-121 `_CLINICALLY_SIGNIFICANT_SUBCLASS_PAIRS`**：
   - thyroid: 增加 `{frozenset({"4a", "4b"})}` — 4a↔4b恶性风险跨度大
   - breast: 增加 `{frozenset({"4a", "4b"})}` — 同理
   - 保留原有 `{frozenset({"4b", "4c"})}`

3. **修改 `test_consistency.py` `TestGradingSemanticEquivalent`**：
   - `test_thyroid_4a_4b_semantic_equivalent`：断言从 `True` 改为 `False`
   - `test_breast_4a_4b_semantic_equivalent`：新增，断言 `False`
   - `test_thyroid_4_4a_semantic_equivalent`：保持 `True`（主级别包含子级别仍等价）
   - `test_thyroid_4_4b_semantic_equivalent`：保持 `True`
   - 新增 `test_thyroid_4a_4b_now_clinically_significant`：验证4a↔4b不再是语义等价

**验证标准**：
- `grading_semantic_equivalent("4a", "4b", "thyroid")` → `False`
- `grading_semantic_equivalent("4", "4a", "thyroid")` → `True`（不变）
- `grading_semantic_equivalent("4b", "4c", "thyroid")` → `False`（已有）
- `grading_clinically_equivalent("4a", "4b", "thyroid")` → `True`（不变，临床等价仍保留）

---

### P0-2: 修正LVEF矛盾处理策略（R2）

**问题**：`_pick_more_severe_grading` 在LVEF与中文描述矛盾时取更严重值，可能导致过度诊断。

**修复方案**：

1. **修改 `grading_definitions.py` L145-148 `_pick_more_severe_grading`**：
   - 重命名为 `_pick_lvef_preferred_grading`
   - 修改逻辑：当存在LVEF数值时，优先采用LVEF映射的分级（客观指标优先）
   - 新增参数 `prefer_lvef: bool = True`
   - 当 `prefer_lvef=True` 时返回 `lvef_grading`，否则返回更严重值（兼容旧逻辑）

2. **修改 `grading_definitions.py` L194-195 调用点**：
   - `extract_grading_with_conflict` 中 L194-195 的调用：
   - 从 `chosen = _pick_more_severe_grading(cn_grading, lvef_grading)` 
   - 改为 `chosen = _pick_lvef_preferred_grading(cn_grading, lvef_grading, prefer_lvef=True)`

3. **修改 `test_consistency.py` `test_cardiac_conflicting_lvef_and_description`**：
   - "LVEF: 60%，收缩功能中度减低" → 应返回 `normal`（LVEF 60%≥55%映射为normal），而非 `moderate`
   - 新增 `test_cardiac_lvef_preferred_over_description`：验证LVEF正常但描述异常时输出normal
   - 新增 `test_cardiac_description_preferred_when_no_lvef`：验证无LVEF时使用中文描述

**验证标准**：
- LVEF 60% + "中度减低" → `normal`（LVEF优先）
- LVEF 40% + "正常" → `mild`（LVEF优先）
- 无LVEF + "中度减低" → `moderate`（中文描述）
- `has_conflict=True`（矛盾仍标记）

---

### P0-3: 统一Gate 0A边界差异率标准（R3）

**问题**：06文档要求"≤1%"，01/07文档Phase0A为"≤3%（点估计）"，代码当前使用1%。

**修复方案**：

1. **修改 `verification_config.py` `ConsistencyThresholdConfig`**：
   - 新增字段：`phase1_clinical_boundary_diff_rate: float = Field(0.03, description="阶段一(Gate0A)临床边界差异率阈值(点估计≤3%)")`
   - 保留 `clinical_boundary_diff_rate: float = 0.01` 作为Phase2标准

2. **修改 `report.py` `gate_status` 属性 L248-344**：
   - 在PASSED条件判定中，根据 `verification_phase` 选择阈值：
     - `phase1`：使用 `phase1_clinical_boundary_diff_rate`（0.03）
     - `phase2`：使用 `clinical_boundary_diff_rate`（0.01）+ CI上界检查
   - CONDITIONAL条件同步调整

3. **修改 `test_verification_config.py`**：
   - 新增 `test_phase1_boundary_diff_rate_default`：验证默认值0.03
   - 新增 `test_phase1_vs_phase2_threshold_difference`：验证Phase1阈值宽松于Phase2

4. **修改 `test_consistency.py` 相关门控测试**：
   - Phase1下边界差异率2.5%应PASSED（<3%），Phase2下应FAILED（>1%）
   - 新增 `test_gate_phase1_boundary_diff_passed` 和 `test_gate_phase2_boundary_diff_failed`

**验证标准**：
- Phase1 + 边界差异率2.5% → PASSED（其他指标达标时）
- Phase1 + 边界差异率3.5% → FAILED
- Phase2 + 边界差异率1.5% → CONDITIONAL或FAILED
- Phase2 + 边界差异率0.8% + CI上界≤2% → PASSED

---

### P0-4: 修正量化幻觉增量验证方法（R4）

**问题**：`TestQuantizationHallucinationIncrement` 对比的是不同文本的检测结果，而非同一输入在不同精度模型下的输出差异。

**修复方案**：

1. **在 `test_consistency.py` 中重写 `TestQuantizationHallucinationIncrement`**：
   - 修改测试逻辑：准备同一组输入文本 → 分别模拟FP16和Q4_K_M模型输出（使用MockInferenceEngine返回略有差异的文本）→ 对比两输出的幻觉率差异
   - 新增 `test_quantization_same_input_different_precision`：验证同一输入在两个精度下幻觉率差异≤0.5%
   - 新增 `test_quantization_increment_exceeds_threshold`：验证差异>0.5%时标记为不达标

2. **在 `test_consistency.py` 中新增辅助函数**：
   - `_make_quantization_pair(fp16_text, q4_text, organ)`：构造同一输入在两个精度下的ConsistencyPair

**验证标准**：
- FP16输出无幻觉 + Q4输出无幻觉 → 增量0% → 通过
- FP16输出无幻觉 + Q4输出有幻觉（1/200）→ 增量0.5% → 通过
- FP16输出无幻觉 + Q4输出有幻觉（2/200）→ 增量1% → 不通过

---

### P0-5: 增强追溯链完整性（R5）

**问题**：追溯链缺少完整推理路径，仅有case_id和分级差异。

**修复方案**：

1. **修改 `failure_analysis.py` `generate_failure_analysis` L64-142**：
   - 在trace字典中增加以下字段：
     - `extracted_grading_a`: 引擎A提取的分级
     - `extracted_grading_b`: 引擎B提取的分级
     - `hallucination_issues_a`: 引擎A的幻觉检测详情（策略、严重度、描述）
     - `hallucination_issues_b`: 引擎B的幻觉检测详情
     - `boundary_details`: 边界匹配详情（匹配的边界对、权重、是否关键）
     - `text_similarity_score`: 两引擎输出的文本相似度
   - 在steps中增加：
     - `grading_extraction_a` 步骤：记录提取过程和结果
     - `grading_extraction_b` 步骤：同上
     - `hallucination_check_detail` 步骤：记录每个策略的检测结果

2. **修改 `verifier.py` `_compute_anomaly_cases` L661-680**：
   - 在 `boundary_cases` 和 `inconsistent_cases` 中增加 `hallucination_issues_a/b` 字段

3. **修改 `test_consistency.py` `TestFailureAnalysis`**：
   - `test_failure_analysis_on_inconsistent`：验证trace_chain中包含 `extracted_grading_a/b`、`hallucination_issues_a/b`、`text_similarity_score` 字段
   - 新增 `test_trace_chain_includes_extraction_steps`：验证steps中包含grading_extraction步骤
   - 新增 `test_trace_chain_includes_hallucination_detail`：验证steps中包含幻觉检测详情

**验证标准**：
- 每个trace_chain条目包含 `extracted_grading_a/b`
- 每个trace_chain条目包含 `hallucination_issues_a/b`（可为空列表）
- 每个trace_chain条目包含 `text_similarity_score`（float）
- steps中包含 `grading_extraction` 阶段

---

## P1 修复项（中严重度）

### P1-1: 收紧医学否定对的相似度阈值（R7）

**问题**：`text_consistency_with_grading` 语义等价时阈值0.5过于宽松，非等价时0.8对医学否定对区分力不足。

**修复方案**：

1. **修改 `verification_config.py` `TextSimilarityConfig`**：
   - `semantic_equivalent_threshold`: 0.5 → 0.7
   - `non_equivalent_threshold`: 0.8 → 0.85

2. **修改 `statistics.py` `text_consistency_with_grading` L137-146**：
   - 从配置中读取阈值而非硬编码：
   ```python
   config = get_verification_config()
   sim_config = config.text_similarity_config
   if grading_a and grading_b and grading_semantic_equivalent(grading_a, grading_b, organ):
       return text_similarity(text_a, text_b) >= sim_config.semantic_equivalent_threshold
   return text_similarity(text_a, text_b) >= sim_config.non_equivalent_threshold
   ```
   - 移除函数签名中的硬编码默认参数

3. **修改 `test_consistency.py` `TestTextSimilarity`**：
   - `test_medical_negation_pair_benign_malignant`：断言从 `sim < 0.9` 改为 `sim < 0.7`（验证实际区分力）
   - 新增 `test_text_consistency_semantic_equivalent_threshold_070`：验证语义等价时0.7阈值生效
   - 新增 `test_text_consistency_non_equivalent_threshold_085`：验证非等价时0.85阈值生效

**验证标准**：
- "良性可能" vs "恶性可能" → similarity < 0.7
- 语义等价 + similarity=0.65 → 不一致（<0.7）
- 语义等价 + similarity=0.75 → 一致（≥0.7）
- 非等价 + similarity=0.82 → 不一致（<0.85）

---

### P1-2: 增加禁忌词全量遍历测试（R8）

**问题**：禁忌词列表覆盖度未系统验证，仅测试了少数代表性术语。

**修复方案**：

1. **在 `test_hallucination_detector.py` 中新增 `TestForbiddenTermsFullCoverage`**：
   - 使用 `pytest.mark.parametrize` 遍历 `get_default_forbidden_terms_config()` 中全部禁忌词
   - 每个禁忌词构造包含该术语的阳性文本，验证检测到
   - 每个禁忌词构造不含该术语的阴性文本，验证未检测到
   - 每个变体同样测试

2. **新增测试方法**：
   - `test_all_primary_terms_detected`：遍历4个器官的全部主禁忌词
   - `test_all_variants_detected`：遍历全部变体术语
   - `test_no_false_positive_on_clean_text`：正常报告文本不应触发任何禁忌词

**验证标准**：
- 全部18+13+12+13=56个主禁忌词均被正确检测
- 全部变体均被正确检测
- 正常超声报告不触发假阳性

---

### P1-3: Phase0A集成测试延迟子集扩大（R6）

**问题**：Phase0A集成测试仅用30例子集测延迟，不满足P99可靠性要求（≥100样本）。

**修复方案**：

1. **修改 `test_phase0a_consistency.py` 中延迟相关测试**：
   - 将 `subset = consistency_test_cases[:30]` 改为 `subset = consistency_test_cases[:100]`
   - 若总用例不足100，则使用全部用例并标记 `latency_p99_reliable=False`
   - 新增 `test_gate_latency_with_sufficient_samples`：验证≥100样本时P99可靠

**验证标准**：
- 100例/器官时 `latency_p99_reliable=True`
- 30例/器官时 `latency_p99_reliable=False`

---

### P1-4: 增加FalseStats的Wilson CI测试（C9-1）

**问题**：`FalseStats` 的Wilson CI字段未在测试中验证。

**修复方案**：

1. **在 `test_consistency.py` 中新增 `TestFalseStatsWilsonCI`**：
   - `test_precision_ci_with_known_values`：构造已知TP/FP值，验证Wilson CI计算
   - `test_recall_ci_with_known_values`：同上
   - `test_ci_bounds_monotonic_with_sample_size`：验证样本量增大时CI收窄
   - `test_ci_zero_denominator`：验证零分母时CI为(0,0)

**验证标准**：
- Wilson CI与手动计算值误差<0.01
- CI下界≥0，上界≤1
- 样本量增大时CI宽度减小

---

### P1-5: 增加组合门控场景测试（C8-1）

**问题**：绝对幻觉率超标+幻觉率差异超标同时触发的组合场景未测试。

**修复方案**：

1. **在 `test_consistency.py` 中新增 `TestCombinedGateScenarios`**：
   - `test_absolute_hallucination_exceeded_and_diff_exceeded`：引擎A幻觉率6%（超5%绝对阈值）+ 差异5%（超0.5%差异阈值）→ FAILED
   - `test_absolute_hallucination_exceeded_but_diff_within`：引擎A幻觉率6% + 差异0.3% → FAILED（绝对阈值优先）
   - `test_organ_failure_and_absolute_hallucination`：某器官一致率<90% + 绝对幻觉率超标 → FAILED

**验证标准**：
- 绝对幻觉率超标直接导致FAILED，不论差异率是否达标
- 组合场景下FAILED优先级最高

---

## P2 修复项（低严重度）

### P2-1: 增加cardiac中文心功能分级映射测试（A1-4）

**修复方案**：

1. **在 `test_consistency.py` `TestExtractGrading` 中新增**：
   - `test_cardiac_class_1_normal`："心功能Ⅰ级" → `normal`
   - `test_cardiac_class_2_mild`："心功能Ⅱ级" → `mild`
   - `test_cardiac_class_3_moderate`："心功能Ⅲ级" → `moderate`
   - `test_cardiac_class_4_severe`："心功能Ⅳ级" → `severe`
   - `test_cardiac_arabic_numeral_class`："心功能2级" → `mild`

---

### P2-2: 增加"low"严重度级别的测试覆盖（B6-1）

**修复方案**：

1. **修改 `report.py` L104-112 `boundary_severity`**：
   - 当前逻辑：`is_critical_boundary` → critical，`boundary_weight >= 1.0` → warning，else → low
   - 逻辑正确，但 `boundary_weight < 1.0` 的场景在实际配置中不存在（所有边界权重≥1.0）
   - 保持代码不变，在测试中显式构造 `boundary_weight=0.5` 的场景

2. **在 `test_consistency.py` 中新增**：
   - `test_boundary_severity_low`：构造 `is_critical_boundary=False, boundary_weight=0.5` → `low`

---

### P2-3: 优化建议量化（E15-1）

**修复方案**：

1. **修改 `failure_analysis.py` `suggest_for_trace` L185-197**：
   - 在建议文本中加入量化信息：
   - 分级不一致时：`"双引擎分级不一致({organ}): engine_a={grading_a}, engine_b={grading_b}, 差异级别={severity}"`
   - 边界跨越时：`"边界跨越({organ}): {grading_a}→{grading_b}, 权重={weight}, 严重度={severity}"`
   - 提取失败时：`"分级提取失败({organ}): 输出文本前100字符={text_preview}"`

2. **修改 `test_consistency.py` `test_failure_analysis_recommendations`**：
   - 验证建议中包含具体的分级值和器官名称

---

### P2-4: 增加延迟差异方向性测试（D11-1）

**修复方案**：

1. **在 `test_consistency.py` 中新增 `TestLatencyDiffDirectionality`**：
   - `test_latency_diff_pct_engine_a_slower`：引擎A比B慢时差异仍为正值
   - `test_latency_diff_pct_engine_b_slower`：引擎B比A慢时差异仍为正值
   - `test_latency_faster_engine_identified_correctly`：验证 `latency_faster_engine` 字段

---

## 实施顺序与依赖关系

```
Phase 1 (P0, 可并行):
  P0-1 (语义等价) ──┐
  P0-2 (LVEF优先) ──┤── 无依赖，可并行实施
  P0-3 (Phase1阈值) ──┤
  P0-4 (量化验证) ──┤
  P0-5 (追溯链)   ──┘

Phase 2 (P1, 部分依赖P0):
  P1-1 (相似度阈值) ── 依赖P0-1（语义等价规则变更影响阈值判定）
  P1-2 (禁忌词遍历) ── 无依赖
  P1-3 (延迟子集)   ── 无依赖
  P1-4 (Wilson CI)  ── 无依赖
  P1-5 (组合门控)   ── 依赖P0-3（Phase1阈值变更影响门控逻辑）

Phase 3 (P2, 无依赖):
  P2-1 ~ P2-4 可并行
```

## 每步验证检查点

| 步骤 | 验证命令 | 预期结果 |
|------|----------|----------|
| P0-1 完成后 | `pytest tests/unit/llm/test_consistency.py::TestGradingSemanticEquivalent -v` | 全部通过，4a↔4b=False |
| P0-2 完成后 | `pytest tests/unit/llm/test_consistency.py -k "lvef" -v` | 全部通过，LVEF优先 |
| P0-3 完成后 | `pytest tests/unit/llm/test_verification_config.py -k "phase1" -v` | 全部通过，0.03默认值 |
| P0-4 完成后 | `pytest tests/unit/llm/test_consistency.py::TestQuantizationHallucinationIncrement -v` | 全部通过 |
| P0-5 完成后 | `pytest tests/unit/llm/test_consistency.py::TestFailureAnalysis -v` | 全部通过，含新字段 |
| Phase 1 全部 | `pytest tests/unit/llm/ -v` | 全部通过 |
| P1 全部 | `pytest tests/unit/llm/ tests/integration/llm/ -v` | 全部通过 |
| P2 全部 | `pytest tests/unit/llm/ -v` | 全部通过 |
| 最终验证 | `pytest tests/ -k "llm" -v` | 全部通过 |

## 风险与回退

| 风险 | 影响 | 回退方案 |
|------|------|----------|
| P0-1语义等价变更导致门控通过率下降 | 现有测试数据可能从PASSED降为CONDITIONAL | 语义等价率阈值从98%降至95% |
| P0-2 LVEF优先导致心脏评估分级变化 | 部分cardiac测试用例预期结果需更新 | 通过配置项 `prefer_lvef=False` 回退到旧行为 |
| P0-3 Phase1阈值放宽导致验收标准争议 | 与06文档表述不一致 | 更新06文档为分阶段表述 |
| P1-1 相似度阈值收紧导致文本一致率下降 | 现有测试数据文本一致率可能低于0.85 | 调整阈值为0.75/0.82（折中方案） |
