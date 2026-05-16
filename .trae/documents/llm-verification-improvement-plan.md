# LLM轨道验证机制改进实施计划

## 改进总览

基于审查报告，需改进项共15项，按优先级分组实施。每项改进均包含：修改文件、具体代码变更、对应测试、验证标准。

---

## P0 改进（5项）— 核心验证有效性问题

### P0-1: 延迟差异应使用P99而非P50

**问题**: `latency_diff_pct` 基于 P99 均值计算（已正确），但 `latency_diff_p50_pct` 基于 P50 且 `gate_status` 使用的 `latency_diff_pct` 实际已基于 P99。需确认 gate_status 使用的延迟差异指标正确，并移除冗余的 P50 差异字段。

**当前代码** (`consistency.py#L865-874`):
```python
avg_p99 = (report.latency_a_p99_ms + report.latency_b_p99_ms) / 2
if avg_p99 > 0:
    report.latency_diff_pct = abs(...) / avg_p99 * 100  # 基于P99 ✅
if report.latency_a_p50_ms > 0:
    report.latency_diff_p50_pct = abs(...) / report.latency_a_p50_ms * 100  # 基于P50
```

**修改方案**:
1. `consistency.py` — `latency_diff_pct` 已基于P99，无需修改计算逻辑
2. `consistency.py` — `gate_status` 属性使用 `self.latency_diff_pct`（基于P99），已正确
3. `consistency.py#L871-874` — 保留 `latency_diff_p50_pct` 作为辅助诊断指标（不删除，但文档标注为辅助）
4. `verification_config.py` — `ConsistencyThresholdConfig` 添加 `latency_diff_pct_description` 字段说明基于P99

**验证**: 运行 `test_consistency.py::TestConsistencyReport` 和 `test_consistency.py::TestLatencyStatistics`

---

### P0-2: FalseStats需增加器官维度分解

**问题**: 当前 `FalseStats` 仅全局统计 FP/FN/TP/TN，无法定位特定器官的幻觉检测问题。

**当前代码** (`consistency.py#L794-842`):
- 全局遍历所有 pairs 计算 FP/FN/TP/TN
- `organ_breakdown` 中有 `hallucination_rate_a/b/diff` 但无 FP/FN/TP/TN

**修改方案**:

1. **`consistency.py` — ConsistencyReport 新增字段**:
   ```python
   organ_false_stats: Dict[str, FalseStats] = field(default_factory=dict)
   ```

2. **`consistency.py` — `_compute_report` 方法** (在 L842 之后、L844 之前):
   - 在器官明细循环中，为每个器官计算独立的 FP/FN/TP/TN
   - 存入 `report.organ_false_stats[organ_name]`

3. **`consistency.py` — `_generate_failure_analysis` 方法**:
   - 在 `by_organ` 部分增加 `false_stats` 字段

4. **`test_consistency.py` — 新增测试**:
   ```python
   class TestOrganFalseStats:
       def test_organ_false_stats_populated(self): ...
       def test_organ_false_stats_with_hallucination(self): ...
       def test_organ_false_stats_precision_recall(self): ...
   ```

**验证**: 运行 `test_consistency.py::TestOrganFalseStats` + 全量测试

---

### P0-3: 分级提取验证覆盖不足

**问题**: TI-RADS 1/2类、LI-RADS子分类(LR-TR)、BI-RADS 0类缺少测试覆盖。

**当前代码** (`grading_definitions.py#L6-28`):
- TI-RADS 正则 `(\d[a-zA-Z]?)` 可匹配 1/2，但无专门测试
- LI-RADS 无 LR-TR 子分类模式
- BI-RADS 无 0 类专门测试

**修改方案**:

1. **`grading_definitions.py` — 新增 LI-RADS LR-TR 模式**:
   - 在 `_GRADING_PATTERNS["liver"]` 列表末尾添加:
   ```python
   re.compile(r'LR[-_]?TR\s*(\w+)', re.IGNORECASE),
   ```
   - 在 `_GRADE_HIERARCHY["liver"]` 中添加: `"lr-tr": 4`（LR-TR 等同 LR-4，需活检）

2. **`test_consistency.py` — TestExtractGrading 新增测试**:
   ```python
   def test_thyroid_ti_rads_1(self):  # TI-RADS 1类
   def test_thyroid_ti_rads_2(self):  # TI-RADS 2类
   def test_liver_li_rads_lr_tr(self):  # LI-RADS LR-TR
   def test_breast_bi_rads_0(self):  # BI-RADS 0类
   def test_breast_bi_rads_4b(self):  # BI-RADS 4b子分类
   def test_thyroid_ti_rads_5(self):  # TI-RADS 5类
   ```

3. **`grading_definitions.py` — `_GRADE_HIERARCHY` 更新**:
   - thyroid: 确认 `"1": 1, "2": 2` 已存在（已存在，无需修改）
   - liver: 添加 `"lr-tr": 4`

**验证**: 运行 `test_consistency.py::TestExtractGrading`

---

### P0-4: 文本一致率度量改进

**问题**: bigram Jaccard 对中文医疗文本区分度低；分级感知逻辑（语义等价直接视为一致）过于宽松。

**当前代码** (`consistency.py#L446-476`):
- `text_similarity`: bigram Jaccard 系数
- `text_consistency_with_grading`: 分级语义等价直接返回 True

**修改方案**:

1. **`consistency.py` — `text_similarity` 增加字符级 Jaccard 作为补充**:
   ```python
   def text_similarity(text_a: str, text_b: str) -> float:
       # 保留 bigram Jaccard 作为主度量
       # 新增：当 bigram Jaccard < 0.5 时，用字符级 Jaccard 作为下界
       # 避免短文本 bigram 集合过小导致的区分度不足
   ```
   - 实际方案：bigram Jaccard 保留，但增加**编辑距离比率**作为辅助度量
   - `text_similarity` 返回 `max(bigram_jaccard, 1 - edit_distance / max(len_a, len_b))`

2. **`consistency.py` — `text_consistency_with_grading` 增加约束**:
   ```python
   def text_consistency_with_grading(...):
       if grading_a and grading_b and grading_semantic_equivalent(grading_a, grading_b, organ):
           # 语义等价不再直接返回True，需额外检查文本相似度下界
           sim = text_similarity(text_a, text_b)
           return sim >= 0.5  # 语义等价时降低阈值但仍需基本相似
       return text_similarity(text_a, text_b) >= 0.8
   ```

3. **`consistency.py` — ConsistencyReport 新增字段**:
   ```python
   text_consistency_rate_strict: float = 0.0  # 严格文本一致率(阈值0.8，无分级感知)
   ```

4. **`test_consistency.py` — 新增测试**:
   ```python
   class TestTextConsistencyImproved:
       def test_semantic_equivalent_still_requires_similarity(self): ...
       def test_text_similarity_edit_distance_fallback(self): ...
       def test_text_consistency_strict_rate(self): ...
   ```

**验证**: 运行 `test_consistency.py::TestTextConsistencyImproved` + 全量测试

---

### P0-5: 优化建议缺少延迟超标和器官一致性低触发

**问题**: `_generate_failure_analysis` 的 recommendations 缺少延迟超标和特定器官一致性低的建议。

**当前代码** (`consistency.py#L1084-1108`):
- 已有: FP/FN差异、边界差异率、提取失败、器官边界跨越
- 缺少: 延迟超标、器官分级一致率低

**修改方案**:

1. **`consistency.py` — `_generate_failure_analysis` 新增建议触发** (在 L1106 之后):
   ```python
   # 延迟超标建议
   if report.latency_diff_pct > thresholds.latency_diff_pct:
       analysis["recommendations"].append(
           f"延迟差异{report.latency_diff_pct:.1f}%超过阈值{thresholds.latency_diff_pct}%，"
           f"建议检查慢引擎的推理配置或模型量化参数"
       )
   
   # 器官分级一致率低建议
   for organ_name, organ_data in report.organ_breakdown.items():
       organ_rate = organ_data.get("grading_consistency_rate", 1.0)
       if organ_rate < thresholds.grading_consistency_rate:
           analysis["recommendations"].append(
               f"{organ_name}器官分级一致率{organ_rate:.2%}低于阈值{thresholds.grading_consistency_rate:.0%}，"
               f"建议增加{organ_name}专项训练数据"
           )
   ```

2. **`test_consistency.py` — TestFailureAnalysis 新增测试**:
   ```python
   async def test_failure_analysis_latency_recommendation(self): ...
   async def test_failure_analysis_organ_consistency_recommendation(self): ...
   ```

**验证**: 运行 `test_consistency.py::TestFailureAnalysis`

---

## P1 改进（5项）— 验证精度与完整性提升

### P1-1: 语义等价规则细化

**问题**: 4a→4c 虽同 level=4 但临床差异显著，当前语义等价规则不区分。

**当前代码** (`grading_definitions.py#L31-36`):
```python
_GRADE_HIERARCHY = {
    "thyroid": {"1": 1, "2": 2, "3": 3, "4": 4, "4a": 4, "4b": 4, "4c": 4, "5": 5},
    ...
}
```

**修改方案**:

1. **`grading_definitions.py` — 引入二级层级**:
   ```python
   _GRADE_HIERARCHY = {
       "thyroid": {
           "1": (1, 0), "2": (2, 0), "3": (3, 0),
           "4": (4, 0), "4a": (4, 1), "4b": (4, 2), "4c": (4, 3), "5": (5, 0)
       },
       "liver": {"1": (1, 0), "2": (2, 0), "3": (3, 0), "4": (4, 0), "5": (5, 0), "lr-tr": (4, 0)},
       "breast": {
           "1": (1, 0), "2": (2, 0), "3": (3, 0),
           "4": (4, 0), "4a": (4, 1), "4b": (4, 2), "4c": (4, 3), "5": (5, 0)
       },
       "cardiac": {"normal": (0, 0), "mild": (1, 0), "moderate": (2, 0), "severe": (3, 0)},
   }
   ```

2. **`consistency.py` — `grading_semantic_equivalent` 更新**:
   ```python
   def grading_semantic_equivalent(grade_a, grade_b, organ):
       if grade_a == grade_b:
           return True
       hierarchy = _GRADE_HIERARCHY.get(organ, {})
       level_a = hierarchy.get(grade_a)
       level_b = hierarchy.get(grade_b)
       if level_a is not None and level_b is not None:
           # 主层级相同 且 子层级差<=1 视为语义等价
           # 4a↔4b 等价, 4a↔4c 不等价(子层级差2)
           if level_a[0] == level_b[0]:
               return abs(level_a[1] - level_b[1]) <= 1
           return False
       return False
   ```

3. **`consistency.py` — `_grades_cross_clinical_boundary` 更新**:
   - 需适配新的 tuple 层级格式
   - `_check_boundary_match` 中 `level_a == level_b` 改为 `level_a[0] == level_b[0]`

4. **`test_consistency.py` — 更新受影响测试**:
   - `test_grading_consistent_exact_match_required`: 4 vs 4a 仍语义等价（子层级差1）
   - 新增: `test_semantic_equivalent_4a_4c_not_equivalent`
   - 新增: `test_semantic_equivalent_4a_4b_equivalent`

**验证**: 运行 `test_consistency.py` 全量 + `test_hallucination_detector.py`

---

### P1-2: 自适应阈值集成到验证流程

**问题**: `AdaptiveThresholdConfig` 仅预留接口，未集成到实际验证流程。

**修改方案**:

1. **`verification_config.py` — `get_default_verification_config` 初始化自适应阈值**:
   ```python
   adaptive_thresholds=[
       AdaptiveThresholdConfig(
           metric_name="grading_consistency_rate",
           base_threshold=0.95, min_threshold=0.90, max_threshold=0.99,
       ),
       AdaptiveThresholdConfig(
           metric_name="clinical_boundary_diff_rate",
           base_threshold=0.01, min_threshold=0.005, max_threshold=0.02,
       ),
       AdaptiveThresholdConfig(
           metric_name="hallucination_rate_diff",
           base_threshold=0.005, min_threshold=0.003, max_threshold=0.01,
       ),
   ]
   ```

2. **`consistency.py` — `gate_status` 属性集成自适应阈值**:
   - 在 `gate_status` 计算前，调用 `compute_adaptive_threshold` 动态调整阈值
   - 需传入历史通过率数据（从 `DriftDetector` 获取或从配置注入）

3. **`consistency.py` — ConsistencyVerifier 添加 `historical_pass_rates` 参数**:
   ```python
   def __init__(self, ..., historical_pass_rates: Optional[Dict[str, List[float]]] = None):
       self._historical_pass_rates = historical_pass_rates or {}
   ```

4. **`test_consistency.py` — 新增测试**:
   ```python
   class TestAdaptiveThreshold:
       def test_adaptive_threshold_tightens_after_high_pass_rate(self): ...
       def test_adaptive_threshold_relaxes_after_low_pass_rate(self): ...
       def test_adaptive_threshold_no_history_uses_base(self): ...
   ```

**验证**: 运行 `test_consistency.py::TestAdaptiveThreshold` + `test_verification_config.py`

---

### P1-3: 测试用例多样性增强

**问题**: `generate_test_cases.py` 基于模板生成，同质性风险。

**修改方案**:

1. **`scripts/generate_test_cases.py` — 增加变异机制**:
   - 添加 `_add_noise_to_template` 函数：随机替换尺寸/回声/边界等描述词
   - 添加同义词映射表：`{"清晰": ["清楚", "明晰"], "模糊": ["不清", "欠清"], ...}`
   - 添加随机组合逻辑：从不同模板中随机抽取特征组合

2. **`scripts/generate_test_cases.py` — 增加边界病例生成**:
   - 添加 `generate_boundary_ambiguous_cases` 函数
   - 生成介于两个分级之间的模糊描述（如 TI-RADS 3-4a 边界）

3. **`scripts/analyze_cases.py` — 增加同质性检测**:
   - 计算 bigram 多样性指标
   - 输出同质性警告

**验证**: 运行 `generate_test_cases.py` 生成新用例，检查多样性指标

---

### P1-4: P50/P99延迟统计样本量标注

**问题**: 少量用例(3-6个)的P99无统计意义。

**修改方案**:

1. **`consistency.py` — ConsistencyReport 新增字段**:
   ```python
   latency_sample_count_a: int = 0
   latency_sample_count_b: int = 0
   latency_p99_reliable: bool = False  # 样本量>=30时为True
   ```

2. **`consistency.py` — `_compute_report` 延迟统计部分**:
   ```python
   report.latency_sample_count_a = len(latencies_a)
   report.latency_sample_count_b = len(latencies_b)
   report.latency_p99_reliable = len(latencies_a) >= 30 and len(latencies_b) >= 30
   ```

3. **`test_consistency.py` — 新增测试**:
   ```python
   class TestLatencyReliability:
       async def test_latency_sample_count_recorded(self): ...
       async def test_latency_p99_reliable_with_sufficient_samples(self): ...
       async def test_latency_p99_not_reliable_with_few_samples(self): ...
   ```

**验证**: 运行 `test_consistency.py::TestLatencyReliability`

---

### P1-5: Phase0A显存碎片化4小时集成测试

**问题**: T-GA-LLM-03 的4小时连续运行验证测试被SKIP。

**修改方案**:

1. **`tests/integration/inf/test_vram_fragmentation_sustained.py` — 新建**:
   - 标记 `@pytest.mark.sustained_run` 和 `@pytest.mark.skipif(not config.SUSTAINED_RUN_ENABLED)`
   - 测试逻辑：启动本地LLM服务 → 4小时持续推理 → 每5分钟采集VRAM快照 → 验证碎片化率<30%

2. **`pyproject.toml` — 添加自定义标记**:
   ```toml
   [tool.pytest.ini_options]
   markers = ["sustained_run: 长时间持续运行测试(需手动启用)"]
   ```

3. **`scripts/gpu_fragmentation_test.py` — 添加CI集成模式**:
   - `--quick` 模式：5分钟快速验证（CI用）
   - `--full` 模式：4小时完整验证（手动触发）

**验证**: 运行 `pytest -m sustained_run` (需手动启用)

---

## P2 改进（4项）— 代码质量与合规性

### P2-1: _compute_report方法拆分

**问题**: `_compute_report` 方法约250行，过长。

**修改方案**:

1. **`consistency.py` — 拆分为子方法**:
   ```python
   def _compute_basic_stats(self, report, pairs): ...        # L717-720
   def _compute_boundary_stats(self, report, pairs): ...     # L722-743
   def _compute_hallucination_stats(self, report, pairs): ... # L745-758
   def _compute_text_stats(self, report, pairs): ...         # L760-792
   def _compute_false_stats(self, report, pairs): ...        # L794-842
   def _compute_latency_stats(self, report, pairs): ...      # L844-874
   def _compute_organ_breakdown(self, report, pairs): ...    # L876-938
   def _compute_anomaly_cases(self, report, pairs): ...      # L940-959
   ```

2. **`_compute_report` 重构为调用子方法**:
   ```python
   def _compute_report(self, report, pairs, error_count):
       self._compute_basic_stats(report, pairs)
       self._compute_boundary_stats(report, pairs)
       self._compute_hallucination_stats(report, pairs)
       self._compute_text_stats(report, pairs)
       self._compute_false_stats(report, pairs)
       self._compute_latency_stats(report, pairs)
       self._compute_organ_breakdown(report, pairs)
       self._compute_anomaly_cases(report, pairs)
       report.failure_analysis = self._generate_failure_analysis(report, pairs)
   ```

**验证**: 运行全量测试确保行为不变

---

### P2-2: NMPA注册声明模板

**问题**: CONDITIONAL 状态需偏差率声明模板。

**修改方案**:

1. **`consistency.py` — ConsistencyReport 新增属性**:
   ```python
   @property
   def nmpa_declaration(self) -> Optional[Dict[str, Any]]:
       """生成NMPA注册材料偏差声明（仅CONDITIONAL状态）"""
       if self.gate_status != GateStatus.CONDITIONAL:
           return None
       thresholds = get_verification_config().consistency_thresholds
       return {
           "framework_a": self.engine_a_name,
           "framework_b": self.engine_b_name,
           "grading_consistency_rate": f"{self.grading_consistency_rate:.2%}",
           "threshold": f"{thresholds.grading_consistency_rate:.0%}",
           "boundary_diff_rate": f"{self.clinical_boundary_diff_rate:.2%}",
           "boundary_threshold": f"{thresholds.clinical_boundary_diff_rate:.0%}",
           "declaration": (
               f"本产品使用{self.engine_a_name}与{self.engine_b_name}双推理框架，"
               f"分级一致率{self.grading_consistency_rate:.2%}，"
               f"临床边界差异率{self.clinical_boundary_diff_rate:.2%}，"
               f"符合条件性通过标准。前端已标注当前使用的推理框架类型。"
           ),
       }
   ```

2. **`test_consistency.py` — 新增测试**:
   ```python
   class TestNMPADeclaration:
       def test_nmpa_declaration_conditional(self): ...
       def test_nmpa_declaration_passed_returns_none(self): ...
       def test_nmpa_declaration_failed_returns_none(self): ...
   ```

**验证**: 运行 `test_consistency.py::TestNMPADeclaration`

---

### P2-3: 器官维度样本量强制检查

**问题**: 每器官>=100例的Gate标准未在验证流程中强制执行。

**修改方案**:

1. **`consistency.py` — ConsistencyVerifier.verify 方法添加样本量检查**:
   ```python
   # 在 _compute_report 之后
   min_cases = self.config.consistency_thresholds.phase2_min_cases_per_organ \
       if self.config.consistency_thresholds.verification_phase == "phase2" else 100
   for organ_name, organ_data in report.organ_breakdown.items():
       if organ_data["total"] < min_cases:
           logger.warning(
               "insufficient_organ_samples",
               organ=organ_name,
               actual=organ_data["total"],
               required=min_cases,
           )
   ```

2. **`consistency.py` — ConsistencyReport 新增字段**:
   ```python
   organ_sample_warnings: List[Dict[str, Any]] = field(default_factory=list)
   ```

3. **`test_consistency.py` — 新增测试**:
   ```python
   class TestOrganSampleSize:
       async def test_organ_sample_warning_when_insufficient(self): ...
       async def test_no_warning_when_sufficient(self): ...
   ```

**验证**: 运行 `test_consistency.py::TestOrganSampleSize`

---

### P2-4: Phase2 CI上界阈值检查启用

**问题**: 07-数据标注 §4 要求 Phase2(>=300例) CI上界<=2%，当前 `phase2_boundary_ci_upper_threshold` 已定义但需在 Gate 0B 阶段启用更严格的检查。

**修改方案**:

1. **`verification_config.py` — `ConsistencyThresholdConfig` 已有 `verification_phase` 字段**:
   - `phase1` (Gate 0A): CI上界检查使用 `phase2_boundary_ci_upper_threshold` (0.02) — 已实现
   - `phase2` (Gate 0B): 需要更严格检查 + 样本量验证

2. **`consistency.py` — `gate_status` 添加 Phase2 额外检查**:
   ```python
   if thresholds.verification_phase == "phase2":
       # Phase2 额外检查：每器官样本量
       for organ_name, organ_data in self.organ_breakdown.items():
           if organ_data.get("total", 0) < thresholds.phase2_min_cases_per_organ:
               return GateStatus.FAILED
   ```

3. **`test_consistency.py` — 新增测试**:
   ```python
   class TestPhase2GateCriteria:
       def test_phase2_fails_with_insufficient_organ_samples(self): ...
       def test_phase2_passes_with_sufficient_samples(self): ...
   ```

**验证**: 运行 `test_consistency.py::TestPhase2GateCriteria`

---

## 实施顺序

### 批次1: P0核心修复（预计改动文件最少，影响最大）

| 步骤 | 任务 | 修改文件 | 新增测试 |
|------|------|---------|---------|
| 1.1 | P0-3: 分级提取覆盖 | `grading_definitions.py`, `test_consistency.py` | 6个 |
| 1.2 | P0-5: 优化建议补充 | `consistency.py`, `test_consistency.py` | 2个 |
| 1.3 | P0-2: FalseStats器官分解 | `consistency.py`, `test_consistency.py` | 3个 |
| 1.4 | P0-4: 文本一致率改进 | `consistency.py`, `test_consistency.py` | 3个 |
| 1.5 | P0-1: 延迟差异确认 | `consistency.py` (文档标注) | 0个 |
| **验证** | 运行全量测试 | | |

### 批次2: P1精度提升

| 步骤 | 任务 | 修改文件 | 新增测试 |
|------|------|---------|---------|
| 2.1 | P1-1: 语义等价细化 | `grading_definitions.py`, `consistency.py`, `test_consistency.py` | 3个 |
| 2.2 | P1-4: 延迟样本量标注 | `consistency.py`, `test_consistency.py` | 3个 |
| 2.3 | P1-2: 自适应阈值集成 | `verification_config.py`, `consistency.py`, `test_consistency.py` | 3个 |
| 2.4 | P1-3: 测试用例多样性 | `scripts/generate_test_cases.py`, `scripts/analyze_cases.py` | 0个 |
| 2.5 | P1-5: 显存碎片化集成测试 | 新建 `test_vram_fragmentation_sustained.py`, `pyproject.toml` | 2个 |
| **验证** | 运行全量测试 | | |

### 批次3: P2质量提升

| 步骤 | 任务 | 修改文件 | 新增测试 |
|------|------|---------|---------|
| 3.1 | P2-1: _compute_report拆分 | `consistency.py` | 0个(行为不变) |
| 3.2 | P2-2: NMPA声明模板 | `consistency.py`, `test_consistency.py` | 3个 |
| 3.3 | P2-3: 器官样本量检查 | `consistency.py`, `test_consistency.py` | 2个 |
| 3.4 | P2-4: Phase2 CI检查 | `consistency.py`, `test_consistency.py` | 2个 |
| **验证** | 运行全量测试 | | |

---

## 验证标准

每个批次完成后必须满足：

1. **全量单元测试通过**: `pytest tests/unit/llm/ tests/unit/inf/ -v` → 0 failed
2. **新增测试覆盖**: 每项改进至少1个针对性测试
3. **无回归**: 修改前后的测试结果对比，无新增失败
4. **文档一致性**: 修改后的代码逻辑与核心参考文档一致
