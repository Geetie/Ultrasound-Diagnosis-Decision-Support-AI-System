# LLM轨道Phase0A审查报告修复计划

基于审查报告中的17个问题（3 Critical + 8 Warning + 6 Low），逐一制定详实的修复方案。

---

## C1. 幻觉检测 `allow_if_context` 机制误报风险 [Critical]

### 问题分析

`_is_context_allowed()` 仅检查上下文窗口中是否包含 `allow_if_context` 关键词，未验证该上下文词是否与当前禁忌词构成合法的医学关联。例如：
- "甲状腺癌伴骨转移" → "骨转移"被"甲状腺"上下文允许 ✓（合法：甲状腺癌可骨转移）
- "甲状腺癌伴肺癌骨转移" → "骨转移"被"甲状腺"上下文允许 ✓（但"肺癌"应独立检出）
- "甲状腺癌伴肺癌" → "肺癌"无allow_if_context，被检出 ✓

核心问题：`allow_if_context` 机制本身逻辑正确（"骨转移"在"甲状腺"上下文中合法），但缺少对同一文本中其他禁忌词的交叉验证。

### 修复方案

在 `ForbiddenTermsDetector.detect()` 中，增加**二次验证**：当某个禁忌词因 `allow_if_context` 被跳过时，检查同一文本中是否存在该器官的其他禁忌词（无 `allow_if_context` 的），如果存在，则将被跳过的禁忌词降级为 `severity=1` 的警告而非完全跳过。

**修改文件**: `app/inference/hallucination_detector.py`

**具体修改**:
1. 在 `ForbiddenTermsDetector.detect()` 方法中，分两遍扫描：
   - 第一遍：收集所有被检出的禁忌词（不含allow_if_context跳过的）
   - 第二遍：对因allow_if_context跳过的禁忌词，检查第一遍结果中是否有同器官的其他禁忌词被检出，如果有，则将跳过的词降级添加（severity=1，标记为"context_co_occurrence"）
2. 在 `DetectionIssue` 中增加 `metadata: Optional[Dict[str, Any]]` 字段，存储额外信息

**验证**: 新增测试用例验证"甲状腺癌伴肺癌骨转移"场景

---

## C2. `organ_breakdown.py` 文本一致率阈值不一致 [Critical] — 已修复

上一轮已修复，`organ_breakdown.py` 已改为调用 `text_consistency_with_grading()`。

**验证**: 确认现有测试通过

---

## C3. `weighted_boundary_diff_rate` 计算分母不合理 [Critical]

### 问题分析

当前分母 `max_possible_weight` 假设每个用例都可能跨越最严重边界，导致分母被高估。例如100个用例中仅2个跨越边界，但分母按100×3.0=300计算，实际权重仅2×3.0=6.0，结果为6/300=2%，而更合理的分母应为"实际发生跨越的用例数×最大权重"或直接用"跨越用例数/总用例数"。

### 修复方案

保留 `weighted_boundary_diff_rate` 但修改分母计算方式，并新增 `boundary_crossing_rate` 作为更直观的主指标。

**修改文件**: `app/inference/consistency/verifier.py`, `app/inference/consistency/report.py`

**具体修改**:
1. 在 `ConsistencyReport` 中新增字段 `boundary_crossing_rate: float = 0.0`（边界跨越用例数/总用例数）
2. 在 `_compute_boundary_stats()` 中：
   - 新增 `boundary_crossing_rate = boundary_diff / len(pairs)` 计算
   - 修改 `max_possible_weight` 为仅对实际发生边界跨越的用例计算最大可能权重
   - 保留原 `weighted_boundary_diff_rate` 但更新分母逻辑
3. 在 `serialization.py` 中添加新字段序列化

**验证**: 新增测试验证全一致场景下 `weighted_boundary_diff_rate = 0.0` 和 `boundary_crossing_rate = 0.0`

---

## W1. 心脏器官LVEF-描述矛盾标记 [Warning]

### 修复方案

在 `extract_grading()` 中增加矛盾检测返回，在 `ConsistencyPair` 中增加标记。

**修改文件**: `app/inference/grading_definitions.py`, `app/inference/consistency/report.py`

**具体修改**:
1. `extract_grading()` 返回值改为 `Optional[Tuple[str, bool]]`，第二个元素表示是否存在LVEF-描述矛盾
2. 为保持向后兼容，新增 `extract_grading_with_conflict()` 函数
3. `GradingResult` 新增 `has_lvef_description_conflict: bool = False` 字段
4. `ConsistencyPair` 新增 `has_extraction_conflict: bool` 属性
5. `_verify_single_case()` 中调用 `extract_grading_with_conflict()`

**验证**: 新增测试验证"LVEF: 60%，收缩功能中度减低"提取结果为 `("moderate", True)`

---

## W2. 语义等价判定4a↔4c放宽 [Warning]

### 修复方案

引入"临床处理等价"概念：同一主级别内所有子级别视为临床处理等价。

**修改文件**: `app/inference/consistency/statistics.py`

**具体修改**:
1. 新增 `grading_clinically_equivalent()` 函数：同一主级别（`level_a[0] == level_b[0]`）即视为临床等价
2. `grading_semantic_equivalent()` 保持不变（子级别差≤1），作为"精确语义等价"
3. 在 `ConsistencyPair` 中新增 `grading_clinically_consistent` 属性，使用 `grading_clinically_equivalent()`
4. 在 `ConsistencyReport` 中新增 `clinical_grading_consistency_rate` 字段
5. 门控判定中 `semantic_grading_consistency_rate` 继续使用 `grading_semantic_equivalent()`，但报告同时展示 `clinical_grading_consistency_rate`

**验证**: 新增测试验证4a与4c临床等价

---

## W3. `percentile()` 函数文档与调用不一致 [Warning]

### 修复方案

**修改文件**: `app/inference/utils.py`, `app/inference/consistency/verifier.py`, `app/inference/consistency/organ_breakdown.py`

**具体修改**:
1. `utils.py`: 更新文档说明使用线性插值法，移除"无需预排序"说明（改为"函数内部会排序"）
2. `verifier.py`: 移除 `_compute_latency_stats()` 中的 `sorted_a = sorted(latencies_a)` 预排序
3. `organ_breakdown.py`: 移除 `sorted_latencies_a = sorted(organ_latencies_a)` 预排序

**验证**: 现有测试应继续通过

---

## W4. `FalseStats` 顶层字段全为0 [Warning]

### 修复方案

将顶层 `FalseStats` 改为两引擎的汇总统计。

**修改文件**: `app/inference/consistency/verifier.py`

**具体修改**:
1. 在 `_compute_false_stats()` 中，顶层 FalseStats 的 TP/FP/FN/TN 改为两引擎之和
2. `precision`/`recall`/`f1_score` 基于汇总数据计算

**验证**: 新增测试验证顶层 FalseStats 不全为0

---

## W5. 边界对配置双向定义冗余 [Warning]

### 修复方案

此修改涉及配置结构变更，影响面较大。采用**渐进式方案**：在 `BoundaryPairConfig` 中新增 `is_directional: bool = True` 字段，默认保持向后兼容。在 `_grades_cross_clinical_boundary()` 和 `_compute_boundary_weight()` 中，当 `is_directional=False` 时自动匹配双向。

**修改文件**: `app/inference/verification_config.py`, `app/inference/consistency/statistics.py`, `app/inference/consistency/verifier.py`

**具体修改**:
1. `BoundaryPairConfig` 新增 `is_directional: bool = True`
2. `_grades_cross_clinical_boundary()` 中，当 `is_directional=False` 时，from_grade 和 to_grade 可互换匹配
3. `_compute_boundary_weight()` 和 `_compute_boundary_weight_from_engines()` 同理

**验证**: 现有测试继续通过，新增测试验证无向边界匹配

---

## W6. 否定词窗口扩大 [Warning]

### 修复方案

**修改文件**: `app/inference/hallucination_detector.py`

**具体修改**:
1. `_is_negated()` 中窗口从±50字符扩大到±100字符
2. `_is_context_allowed()` 中窗口从50扩大到100

**验证**: 现有测试继续通过

---

## W7. 集成测试增加concurrency=2 [Warning]

### 修复方案

**修改文件**: `tests/integration/llm/test_phase0a_consistency.py`

**具体修改**:
1. 在 `TestVerifierFramework` 中新增 `test_concurrency_2_verification` 测试方法
2. 在 `TestGate0ACriteria` 中新增 `test_gate_latency_concurrency_2` 测试方法

**验证**: 集成测试通过

---

## W8. 幻觉ground truth用例扩充 [Warning]

### 修复方案

**修改文件**: `tests/fixtures/boundary_test_cases.py`

**具体修改**:
1. `generate_hallucination_ground_truth_cases()` 每器官从8例增加到30例（15阳性+15阴性）

**验证**: 新增测试验证用例数量

---

## L1. 中文bigram噪声 [Low]

### 修复方案

在 `text_similarity()` 中增加字符trigram作为补充，取bigram Jaccard、trigram Jaccard、Levenshtein编辑比三者的最大值。

**修改文件**: `app/inference/consistency/statistics.py`

---

## L2. Levenshtein距离长文本优化 [Low]

### 修复方案

对超长文本（>500字符）截断后再计算。

**修改文件**: `app/inference/consistency/statistics.py`

---

## L3. benchmark.py logger变量名 [Low] — 已修复

上一轮已修复 `logger.warning` → `self._log.warning`。

---

## L4. drift_detector.py最小样本量 [Low]

### 修复方案

对比例指标（accuracy, grading_consistency, hallucination_rate）降低最小样本量至10。

**修改文件**: `app/inference/drift_detector.py`

---

## L5. serialization.py缺少字段 [Low] — 已修复

上一轮已修复。

---

## L6. verification_phase自动设置 [Low]

### 修复方案

在 `ConsistencyVerifier.verify()` 中，根据用例数量自动推断阶段：如果所有器官用例数≥300，自动切换到phase2。

**修改文件**: `app/inference/consistency/verifier.py`

---

## 实施顺序

按优先级和依赖关系排序：

1. **C3** — weighted_boundary_diff_rate分母修复（Critical，独立修改）
2. **C1** — allow_if_context误报修复（Critical，需新增测试）
3. **W4** — FalseStats顶层汇总（Warning，简单修改）
4. **W3** — percentile文档和调用修复（Warning，简单修改）
5. **W6** — 否定词窗口扩大（Warning，简单修改）
6. **W2** — 临床处理等价（Warning，需新增函数和字段）
7. **W1** — LVEF-描述矛盾标记（Warning，需修改多个文件）
8. **W5** — 边界对无向设计（Warning，需修改配置结构）
9. **L1+L2** — text_similarity优化（Low，合并修改）
10. **L4** — drift_detector样本量（Low，简单修改）
11. **L6** — verification_phase自动设置（Low，简单修改）
12. **W7** — 集成测试并发（Warning，测试文件修改）
13. **W8** — ground truth扩充（Warning，测试数据修改）

每步修改后运行 `pytest tests/unit/llm/ -v --tb=short` 验证。
