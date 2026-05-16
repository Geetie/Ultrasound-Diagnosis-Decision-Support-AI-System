# LLM轨道Phase 0A验证机制修复计划

> 基于《LLM轨道Phase 0A验证机制有效性审查报告》的逐项修复计划
> 优先级：P0（必须修复）→ P1（应当修复）→ P2（建议改进）

---

## P0-1: 修正分级提取正则 — 支持大写子分类

**问题**：`grading_definitions.py` 中 `[a-z]?` 不匹配大写子分类（如 `TI-RADS 4A` 被提取为 `4` 而非 `4a`）

**修改文件**：
- `app/inference/grading_definitions.py`

**修改内容**：
1. 将所有 `_GRADING_PATTERNS` 中 thyroid/liver/breast 的 `(\d[a-z]?)` 改为 `(\d[a-zA-Z]?)`
2. 在 `extract_grading()` 函数中，对提取的 `raw` 值调用 `.lower()` 统一转小写后再返回（确保与 `_GRADE_HIERARCHY` 中的小写键一致）
3. 心脏的中文描述模式 `(正常|轻度|中度|重度)` 不受影响，无需修改

**验证**：
- 在 `tests/unit/test_consistency.py` 的 `TestExtractGrading` 中增加测试：
  - `test_thyroid_uppercase_subcategory`: `"TI-RADS 4A类"` → `"4a"`
  - `test_breast_uppercase_subcategory`: `"BI-RADS 4B类"` → `"4b"`
  - `test_liver_uppercase_no_subcategory`: `"LI-RADS 4类"` → `"4"`
  - `test_mixed_case_grading`: `"ti-rads 4C类"` → `"4c"`

---

## P0-2: 修正text_consistency_rate定义 — 与架构文档对齐

**问题**：代码中 `text_consistency_rate` 定义为"分级一致且无幻觉"的比例，架构文档定义为"分级相同且UDT病种编码相同即为一致，仅措辞差异但分级与编码相同仍视为一致"

**修改文件**：
- `app/inference/consistency.py`

**修改内容**：
1. 将当前 `text_consistency_rate` 的计算逻辑（L663-670）改为：两引擎输出分级语义等价（`grading_semantic_equivalent`）即视为文本一致，不再要求"无幻觉"
2. 原来的"分级一致+无幻觉"指标重命名为 `clinical_consistency_rate`（临床一致性率），作为独立指标保留在 `ConsistencyReport` 中
3. 更新 `ConsistencyThresholdConfig` 中的 `text_consistency_rate` 阈值说明，明确其语义为"分级语义一致率"
4. 更新 `gate_status` 属性中的 `tc` 判定使用新的 `text_consistency_rate`

**具体代码变更**：
```python
# _compute_report 方法中
text_consistent = sum(
    1 for p in pairs
    if p.engine_a_result and p.engine_b_result
    and grading_semantic_equivalent(
        p.engine_a_result.grading, p.engine_b_result.grading, p.organ
    )
)
report.text_consistency_rate = text_consistent / len(pairs) if pairs else 0.0

clinical_consistent = sum(
    1 for p in pairs
    if p.engine_a_result and p.engine_b_result
    and p.grading_consistent
    and not p.engine_a_result.is_hallucination
    and not p.engine_b_result.is_hallucination
)
report.clinical_consistency_rate = clinical_consistent / len(pairs) if pairs else 0.0
```

5. 在 `ConsistencyReport` dataclass 中增加 `clinical_consistency_rate: float = 0.0` 字段
6. 在 `report_to_dict` 中增加 `clinical_consistency_rate` 输出

**验证**：
- 更新 `tests/unit/test_consistency.py` 中 `TestConsistencyReport` 的门控判定测试
- 增加 `test_text_consistency_rate_semantic_definition` 测试：两引擎分级语义等价但文本不同时，text_consistency_rate 应为 1.0
- 增加 `test_clinical_consistency_rate_includes_hallucination` 测试

---

## P0-3: 增加幻觉阳性测试用例 — 使FalseStats有效

**问题**：`consistency_test_cases.json` 中310例全部 `expected_hallucination=false`，导致 FalseStats 的 true_positives 永远为0

**修改文件**：
- `app/inference/data/consistency_test_cases.json`
- `tests/fixtures/boundary_test_cases.py`（已有 `generate_hallucination_ground_truth_cases()`，需整合）

**修改内容**：
1. 在 `consistency_test_cases.json` 中增加至少30例 `expected_hallucination=true` 的用例：
   - 甲状腺10例：包含肺癌/胃癌/脑梗等禁忌词的文本
   - 肝脏10例：包含甲状腺癌/肺癌等禁忌词的文本
   - 心脏10例：包含肝癌/胰腺炎等禁忌词的文本
2. 每例需包含 `hallucination_text` 字段，标注触发幻觉的具体文本片段
3. 同时增加5例 `expected_hallucination=false` 但包含否定词+禁忌词的用例（如"未见肺癌转移"），验证否定词排除功能

**验证**：
- 在 `tests/unit/test_consistency.py` 中增加 `TestFalseStatsWithPositiveCases` 测试类
- 验证 true_positives > 0、precision > 0、recall > 0、f1_score > 0

---

## P0-4: 扩充边界病例测试集 — 达到文档要求

**问题**：当前310例（常规+边界混合），文档要求≥400例边界+≥200例常规+≥100例高风险

**修改文件**：
- `app/inference/data/consistency_test_cases.json`
- `scripts/generate_test_cases.py`

**修改内容**：
1. 重构 `consistency_test_cases.json`，明确分为三个子集：
   - `boundary_cases`：≥400例（TI-RADS 3/4a/4b 每类≥100例、LI-RADS 3/4 每类≥100例、BI-RADS 3/4a 每类≥100例、cardiac normal/mild/moderate 每类≥100例）
   - `routine_cases`：≥200例（覆盖4个器官常见病种）
   - `high_risk_cases`：≥100例（覆盖高风险病种）
2. 更新 `scripts/generate_test_cases.py` 生成完整数据集
3. 在JSON中增加 `case_type` 字段标记 `boundary`/`routine`/`high_risk`
4. 总用例数目标：≥700例

**验证**：
- 更新 `tests/unit/test_consistency.py` 中 `TestBoundaryCaseValidation` 的计数断言
- 验证 `test_boundary_case_count >= 400`
- 验证 `test_routine_case_count >= 200`
- 验证 `test_high_risk_case_count >= 100`

---

## P1-1: 统一语义等价与边界跨越判定逻辑

**问题**：`grading_consistent` 使用 `grading_semantic_equivalent`（4与4a视为一致），但 `is_boundary_crossing` 使用严格字符串匹配（4与4a可能触发边界跨越）

**修改文件**：
- `app/inference/consistency.py`

**修改内容**：
1. 修改 `_grades_cross_clinical_boundary()` 函数，在匹配边界配置时使用分级层次结构而非严格字符串匹配：
   - 当 `level_a` 和 `level_b` 都在 hierarchy 中有定义时，使用层次级别匹配边界
   - 当 boundary 的 `from_grade`/`to_grade` 也在 hierarchy 中有定义时，比较层次级别而非字符串
2. 具体修改：当前代码（L116-130）已有层次匹配逻辑（`level_a == from_level and level_b == to_level`），但 fallback 到严格字符串匹配。修改为：**当两个分级都在 hierarchy 中有定义时，仅使用层次匹配，不再 fallback 到严格匹配**
3. 清理 `verification_config.py` 中的边界配置：移除语义等价分级间的边界对（如 `4↔4a`、`4↔4b`、`4a↔4b` 等），因为这些对在同一层次级别内，不应触发边界跨越

**验证**：
- 增加 `test_no_boundary_crossing_for_semantic_equivalent` 测试：`4` vs `4a` 不触发边界跨越
- 增加 `test_boundary_crossing_for_different_levels` 测试：`3` vs `4a` 触发边界跨越
- 更新 `TestConsistencyPair` 中的相关测试

---

## P1-2: 将延迟差异纳入gate_status判定

**问题**：`gate_status` 属性未检查延迟差异百分比，但 Gate 标准要求延迟差异≤20%

**修改文件**：
- `app/inference/consistency.py`

**修改内容**：
1. 在 `gate_status` 属性中增加延迟差异判定：
   ```python
   ld = self.latency_diff_pct
   # PASSED 条件增加:
   and ld <= thresholds.latency_diff_pct
   # CONDITIONAL 条件增加:
   and ld <= thresholds.latency_diff_pct * 1.5
   ```
2. 在 `ConsistencyThresholdConfig` 中增加 `conditional_latency_diff_pct: float = Field(0.30, description="条件通过延迟差异阈值")`

**验证**：
- 增加 `test_failed_high_latency_diff` 测试
- 增加 `test_conditional_with_moderate_latency_diff` 测试
- 更新现有 `TestConsistencyReport` 测试用例

---

## P1-3: 修正心脏分级提取优先级

**问题**：心脏模式中 LVEF 排第一，当文本同时包含 LVEF 值和中文描述时，LVEF 映射结果可能覆盖更精确的中文描述

**修改文件**：
- `app/inference/grading_definitions.py`

**修改内容**：
1. 调整 `_GRADING_PATTERNS["cardiac"]` 的模式顺序：将中文描述模式 `(正常|轻度|中度|重度)` 移到 LVEF 模式之前
2. 修改 `extract_grading()` 函数中心脏分支的逻辑：当中文描述和 LVEF 同时存在时，优先使用中文描述的结果（因为中文描述通常更精确地反映临床判断）
3. 具体实现：遍历所有模式收集所有匹配结果，如果中文描述匹配成功则优先返回中文描述的映射值

**验证**：
- 增加 `test_cardiac_chinese_description_priority_over_lvef` 测试
- 增加 `test_cardiac_lvef_only_fallback` 测试
- 增加 `test_cardiac_conflicting_lvef_and_description` 测试

---

## P1-4: 为甲状腺转移相关禁忌词添加allow_if_context

**问题**：甲状腺癌确实可能发生骨转移/肝转移/脑转移，但在甲状腺报告中出现这些词被一律判定为幻觉

**修改文件**：
- `app/inference/verification_config.py`

**修改内容**：
1. 为甲状腺禁忌词中的转移相关条目添加 `allow_if_context`：
   - `"骨转移"` → `allow_if_context="甲状腺"`（甲状腺癌骨转移是已知转移途径）
   - `"肝转移"` → `allow_if_context="甲状腺"`（甲状腺癌肝转移虽少见但存在）
   - `"脑转移"` → `allow_if_context="甲状腺"`（甲状腺癌脑转移虽少见但存在）
2. 同理，为肝脏的 `"骨转移"` 添加 `allow_if_context="肝脏"`（肝癌骨转移常见）
3. 为心脏的 `"肝转移"` 添加 `allow_if_context="心脏"`（虽然心脏转移罕见，但保持一致性）

**验证**：
- 增加 `test_thyroid_bone_metastasis_with_context_allowed` 测试
- 增加 `test_thyroid_bone_metastasis_without_context_detected` 测试
- 增加 `test_liver_bone_metastasis_with_context_allowed` 测试

---

## P2-1: 将Jaccard系数替换为更适合中文的相似度度量

**问题**：字符级Jaccard系数对中文文本区分度低

**修改文件**：
- `app/inference/consistency.py`

**修改内容**：
1. 将 `text_similarity()` 改为基于 bigram 的 Jaccard 系数（2-gram Jaccard），对中文文本区分度显著优于单字符
2. 实现方式：
   ```python
   def text_similarity(text_a: str, text_b: str) -> float:
       if not text_a and not text_b:
           return 1.0
       if not text_a or not text_b:
           return 0.0
       def bigrams(text):
           return {text[i:i+2] for i in range(len(text) - 1)}
       set_a = bigrams(text_a)
       set_b = bigrams(text_b)
       intersection = set_a & set_b
       union = set_a | set_b
       return len(intersection) / len(union) if union else 0.0
   ```

**验证**：
- 更新 `TestTextSimilarity` 中的 `test_partial_overlap` 断言
- 增加 `test_chinese_text_better_discrimination` 测试：验证不同含义的中文文本相似度低于相同含义的文本

---

## P2-2: 补充器官分解中的缺失指标

**问题**：器官分解缺少 `text_similarity_avg`、`semantic_grading_consistency_rate`、`false_stats`

**修改文件**：
- `app/inference/consistency.py`

**修改内容**：
1. 在 `_compute_report` 的器官分解部分增加：
   - `semantic_grading_consistency_rate`
   - `text_similarity_avg`
   - `false_stats`（按器官维度）

**验证**：
- 更新 `TestOrganBreakdown` 测试验证新增字段

---

## P2-3: 将CONDITIONAL放宽系数移至配置

**问题**：`hallucination_rate_diff * 2` 硬编码在 `gate_status` 中

**修改文件**：
- `app/inference/verification_config.py`
- `app/inference/consistency.py`

**修改内容**：
1. 在 `ConsistencyThresholdConfig` 中增加：
   - `conditional_hallucination_rate_diff_multiplier: float = Field(2.0, description="条件通过幻觉差异放宽倍数")`
   - `conditional_latency_diff_pct: float = Field(0.30, description="条件通过延迟差异阈值")`
2. 在 `gate_status` 中使用配置值替代硬编码

**验证**：
- 验证默认配置下门控判定结果不变
- 验证自定义配置可生效

---

## P2-4: 清理边界配置中的语义等价对

**问题**：边界配置包含 `4↔4a`、`4↔4b` 等语义等价分级间的边界对

**修改文件**：
- `app/inference/verification_config.py`

**修改内容**：
1. 移除以下语义等价对（同属4类的子分级间不应有边界跨越）：
   - `4↔4a`、`4a↔4`（同属4类）
   - `4↔4b`、`4b↔4`（同属4类）
   - `4↔4c`、`4c↔4`（同属4类）
   - `4a↔4b`、`4b↔4a`（同属4类，子分类间差异不是临床决策边界）
2. 保留跨类边界对：`3↔4`、`3↔4a`、`4a↔4c`、`4↔5`、`3↔5` 等
3. 同理清理 breast 的语义等价对

**验证**：
- 更新 `TestGetDefaultBoundaryConfig` 中的 `test_thyroid_boundaries` 计数断言
- 验证语义等价分级间不触发边界跨越

---

## P2-5: 区分"量化幻觉增量"和"跨框架幻觉差异"

**问题**：代码中 `hallucination_rate_diff` 实际是跨框架幻觉差异，但阈值0.5%来源于文档中"量化后幻觉率增量≤0.5%"的要求

**修改文件**：
- `app/inference/verification_config.py`
- `app/inference/consistency.py`

**修改内容**：
1. 在 `ConsistencyThresholdConfig` 中将 `hallucination_rate_diff` 重命名为 `cross_engine_hallucination_rate_diff`，保留原字段作为别名（向后兼容）
2. 在 `ConsistencyReport` 中增加注释说明 `hallucination_rate_diff` 是跨框架差异而非量化增量
3. 在 `BenchmarkThresholdConfig` 中保留 `hallucination_increment` 作为量化幻觉增量的阈值

**验证**：
- 验证现有测试不受影响

---

## 实施顺序与依赖关系

```
P0-1 (正则修复) ──────────────────────────────────────────┐
P0-2 (text_consistency_rate) ──────────────────────────────┤
P0-3 (幻觉阳性用例) ──────────────────────────────────────┤──→ 运行全量单元测试验证
P0-4 (扩充测试集) ────────────────────────────────────────┤
P1-1 (语义等价统一) ← 依赖 P0-1 ──────────────────────────┤
P1-2 (延迟纳入门控) ──────────────────────────────────────┤
P1-3 (心脏优先级) ────────────────────────────────────────┤
P1-4 (allow_if_context) ──────────────────────────────────┤
P2-1 (bigram Jaccard) ────────────────────────────────────┤
P2-2 (器官分解补全) ← 依赖 P0-2 ─────────────────────────┤
P2-3 (配置化放宽系数) ← 依赖 P1-2 ───────────────────────┤
P2-4 (清理边界配置) ← 依赖 P1-1 ─────────────────────────┤
P2-5 (区分幻觉指标) ──────────────────────────────────────┘
```

**建议实施批次**：
- **批次1**：P0-1 + P0-2 + P0-3 + P0-4（并行，无依赖）
- **批次2**：P1-1 + P1-2 + P1-3 + P1-4（P1-1依赖P0-1，其余并行）
- **批次3**：P2-1 ~ P2-5（依赖前置P0/P1完成）

---

## 每步验证检查点

| 步骤 | 验证命令 | 预期结果 |
|------|----------|----------|
| P0-1 | `pytest tests/unit/test_consistency.py::TestExtractGrading -v` | 全部通过，含新增大写测试 |
| P0-2 | `pytest tests/unit/test_consistency.py::TestConsistencyReport -v` | 门控判定使用新定义 |
| P0-3 | `pytest tests/unit/test_consistency.py::TestFalseStatsWithPositiveCases -v` | TP/FP/FN/TN均有非零值 |
| P0-4 | `pytest tests/unit/test_consistency.py::TestBoundaryCaseValidation -v` | 计数≥400/200/100 |
| P1-1 | `pytest tests/unit/test_consistency.py::TestConsistencyPair -v` | 4↔4a不触发边界 |
| P1-2 | `pytest tests/unit/test_consistency.py::TestConsistencyReport -v` | 延迟超标→FAILED |
| P1-3 | `pytest tests/unit/test_consistency.py::TestExtractGrading -v` | 中文描述优先 |
| P1-4 | `pytest tests/unit/test_hallucination_detector.py -v` | 上下文允许生效 |
| 全量 | `pytest tests/unit/ -v --tb=short` | 全部通过 |
