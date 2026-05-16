# LLM轨道Phase0A审查报告问题修复计划

**基于文档**: `llm-phase0a-verification-audit-report.md`
**计划日期**: 2026-05-13

---

## 修复总览

| ID | 严重程度 | 描述 | 修复类型 | 涉及文件 |
|----|----------|------|----------|----------|
| P1-3 | P1 | 幻觉ground truth仅47例 | 数据扩充 | boundary_test_cases.py |
| P1-4 | P1 | Phase2边界病例密度不足 | 代码增强 | organ_breakdown.py, verification_config.py |
| P1-5 | P1 | cross_validate设计意图不明确 | 文档+重构 | verifier.py, report.py |
| P2-1 | P2 | 文本相似度对中文效果有限 | 算法增强 | statistics.py |
| P2-2 | P2 | Gate判定未使用latency_p99_reliable | 逻辑修复 | report.py |
| P2-3 | P2 | Phase自动升级缺乏确认 | 配置+日志 | verifier.py, verification_config.py, report.py |
| P2-4 | P2 | 幻觉检测未区分类型统计 | 数据模型+统计 | report.py, verifier.py, organ_breakdown.py, hallucination_detector.py |
| P2-5 | P2 | 缺少4小时持续运行验证 | 新功能 | 新建sustained_load_test.py |
| P2-6 | P2 | 器官级延迟P99未标记可靠性 | 字段补充 | organ_breakdown.py |

---

## 修复方案详述

### P1-3: 幻觉ground truth仅47例，远低于文档要求≥300例

**问题根因**: `generate_hallucination_ground_truth_cases()` 每器官仅生成8例（4阳性+4阴性），4器官共32例。加上对抗性用例10例和JSON中15例，总计约47例有`expected_hallucination`标注，远低于文档要求的边界病例≥300例标准。

**修复方案**: 扩充`generate_hallucination_ground_truth_cases()`，每器官从8例扩充至25例（12阳性+13阴性），4器官共100例。新增用例需覆盖5种检测策略的更多场景：

**每器官新增用例设计**（以thyroid为例，其他器官同构）：

| 用例编号 | 阳性/阴性 | 触发策略 | 描述 |
|----------|-----------|----------|------|
| halluc_thyroid_001-004 | 阴性×4 | 无触发 | 正常描述，无幻觉（现有） |
| halluc_thyroid_005-008 | 阴性×4 | 否定词/上下文允许 | 含否定词修饰的禁忌词、上下文允许的禁忌词（现有） |
| halluc_thyroid_009 | 阳性 | FORBIDDEN_TERMS | 禁忌词出现（现有：肺癌转移） |
| halluc_thyroid_010 | 阳性 | FORBIDDEN_TERMS | 禁忌词变体出现（新增：肺恶性肿瘤） |
| halluc_thyroid_011 | 阳性 | FORBIDDEN_TERMS | 另一器官禁忌词（新增：胃癌） |
| halluc_thyroid_012 | 阳性 | CONTRADICTORY_GRADING | 矛盾分级（现有：3类同时5类） |
| halluc_thyroid_013 | 阳性 | CONTRADICTORY_GRADING | 子分级矛盾（新增：4a类同时4c类） |
| halluc_thyroid_014 | 阳性 | MEDICAL_COMMON_SENSE | 良性+恶性矛盾（现有） |
| halluc_thyroid_015 | 阳性 | MEDICAL_COMMON_SENSE | 良性+增强性表述（新增：良性但不除外恶性） |
| halluc_thyroid_016 | 阳性 | NUMERICAL_VALIDITY | LVEF超范围（仅cardiac适用，thyroid用结节大小超范围） |
| halluc_thyroid_017 | 阳性 | NUMERICAL_VALIDITY | 结节大小超范围（新增：50cm结节） |
| halluc_thyroid_018 | 阳性 | CONTEXT_AWARE | 描述-结论矛盾（现有：良性表现+5类） |
| halluc_thyroid_019 | 阳性 | CONTEXT_AWARE | 过度反应（现有：3类+立即手术） |
| halluc_thyroid_020 | 阳性 | CONTEXT_AWARE | 恶性描述+良性结论（新增） |
| halluc_thyroid_021-025 | 阴性×5 | 边界情况 | 分级子类差异（4类vs4a类）、正常变体描述、合理建议差异 |

**实施步骤**:
1. 在`boundary_test_cases.py`的`generate_hallucination_ground_truth_cases()`中，为4个器官各新增17例
2. 确保新增用例的`hallucination_text_a`能被对应策略检测到（阳性用例），或不会被误检（阴性用例）
3. 更新测试用例总数从32→100

**验证**: 运行`test_false_stats_with_hallucination_ground_truth`确认新增用例的FP/FN/TP/TN计算正确

---

### P1-4: Phase2边界病例密度不足(100/器官 vs 要求各≥100/类别)

**问题根因**: 当前JSON数据集每器官约100例，但文档要求Phase2边界病例各≥100/类别（如TI-RADS 3类≥100例、4a类≥100例等）。当前代码未在organ_breakdown中追踪各类别的用例密度。

**修复方案**: 分两步——(1) 在organ_breakdown中增加边界病例密度追踪；(2) 在验证配置中增加类别密度阈值。

**实施步骤**:

1. **organ_breakdown.py**: 在`compute_organ_breakdown()`中增加`grading_distribution`字段，统计每个分级值的用例数：
   ```python
   grading_distribution: Dict[str, int]  # {"3": 25, "4a": 20, "4b": 20, ...}
   ```
   遍历organ_pairs，按expected_grading或实际提取的grading统计分布。

2. **verification_config.py**: 在`ConsistencyThresholdConfig`中增加：
   ```python
   phase2_min_cases_per_grading_category: int = Field(100, description="阶段二每个分级类别最小用例数")
   ```

3. **organ_breakdown.py**: 在organ_breakdown输出中增加`grading_distribution`和`boundary_case_density`字段。

4. **verifier.py**: 在organ_sample_warnings中增加类别密度不足的警告。

5. **report.py**: 在gate_status的phase2前置检查中，增加类别密度检查（可选，作为warning而非硬性Gate阻断，因为当前数据量确实不足）。

**验证**: 运行现有测试确认organ_breakdown新增字段不影响已有逻辑

---

### P1-5: cross_validate设计意图不明确

**问题根因**: `cross_validate`方法使用K-Fold分割，但变量命名（`fold_full`/`fold_subset`）和文档未清晰表达其设计意图——这是稳定性验证而非传统ML的train/test分割。

**修复方案**: 重命名变量+增强文档注释，不改变逻辑。

**实施步骤**:

1. **verifier.py**: 
   - 将`fold_full`重命名为`train_subset`（除当前折外的所有数据）
   - 将`fold_subset`重命名为`validation_fold`（当前折数据）
   - 在`cross_validate`方法docstring中明确说明设计意图：
     > 此方法实现K-Fold交叉验证用于**稳定性评估**，而非传统ML的模型选择。通过对比不同数据子集下一致率和幻觉率的波动幅度，评估验证结果的统计稳定性。若数据量变化导致一致率大幅波动，说明验证结果不可靠。

2. **report.py**: 将`CrossValidationResult`中的字段重命名：
   - `fold_full_count` → `train_count`
   - `fold_subset_count` → `validation_count`
   - `fold_full_accuracy` → `train_accuracy`
   - `fold_subset_accuracy` → `validation_accuracy`
   - `fold_full_hallucination_rate` → `train_hallucination_rate`
   - `fold_subset_hallucination_rate` → `validation_hallucination_rate`

3. **测试更新**: 更新test_consistency.py中引用旧字段名的测试代码。

**验证**: 运行`pytest tests/unit/llm/test_consistency.py`确认重命名后测试通过

---

### P2-1: 文本相似度算法对中文效果有限

**问题根因**: 当前使用bigram/trigram Jaccard + Levenshtein。对中文而言，字符级bigram（2个汉字）实际上近似词级匹配，效果尚可。但存在两个不足：(1) 500字符截断可能丢失关键信息；(2) 缺少单字符Jaccard作为补充。

**修复方案**: 增加字符级单字Jaccard相似度，取四路最大值；将截断长度可配置化。

**实施步骤**:

1. **statistics.py**: 在`text_similarity()`中增加单字符Jaccard：
   ```python
   def _unigrams(text: str) -> Set[str]:
       return set(text)
   
   set_a_uni = _unigrams(text_a)
   set_b_uni = _unigrams(text_b)
   unigram_jaccard = len(set_a_uni & set_b_uni) / len(set_a_uni | set_b_uni) if (set_a_uni | set_b_uni) else 0.0
   
   return max(bigram_jaccard, trigram_jaccard, edit_ratio, unigram_jaccard)
   ```

2. **statistics.py**: 将截断长度参数化（默认500，但可通过参数调整）：
   ```python
   def text_similarity(text_a: str, text_b: str, max_chars: int = 500) -> float:
   ```

3. **verification_config.py**: 在`ConsistencyThresholdConfig`中增加可选配置：
   ```python
   text_similarity_max_chars: int = Field(500, description="文本相似度计算截断字符数")
   ```

**验证**: 运行`test_text_similarity`相关测试确认单字符Jaccard计算正确

---

### P2-2: Gate判定未使用latency_p99_reliable

**问题根因**: `latency_p99_reliable`（两侧引擎各≥30样本）仅被计算存储，未参与Gate判定。当样本不足时，P99差异可能基于极少量数据，导致Gate误判。

**修复方案**: 当`latency_p99_reliable`为False时，在Gate判定中跳过延迟差异检查（视为通过），但在报告中标记为"延迟数据不可靠"。

**实施步骤**:

1. **report.py**: 修改`gate_status`属性，在PASSED和CONDITIONAL判定中，当`latency_p99_reliable`为False时跳过`ld`检查：
   ```python
   ld_check_passed = (ld <= thresholds.latency_diff_pct) if self.latency_p99_reliable else True
   ld_check_conditional = (ld <= thresholds.latency_diff_pct * thresholds.conditional_latency_diff_pct_multiplier) if self.latency_p99_reliable else True
   ```
   将PASSED条件中的`ld <= thresholds.latency_diff_pct`替换为`ld_check_passed`，CONDITIONAL同理。

2. **report.py**: 在`remediation`属性中，当`latency_p99_reliable`为False时增加提示：
   ```python
   if not self.latency_p99_reliable:
       deviations["latency_reliability"] = {
           "deviation_pct": 0,
           "action": "insufficient_samples",
           "detail": f"延迟样本不足(A={self.latency_sample_count_a}, B={self.latency_sample_count_b}, 需各≥30)"
       }
   ```

3. **serialization.py**: 在`report_to_dict`中增加`latency_p99_reliable`字段输出。

**验证**: 编写测试验证当`latency_p99_reliable=False`时Gate不因延迟差异而失败

---

### P2-3: Phase自动升级缺乏用户确认

**问题根因**: `verifier.py`第121-124行，当所有器官样本≥300时自动将phase1升级为phase2，就地修改配置，无确认机制。

**修复方案**: 增加配置开关控制自动升级行为，并在报告中记录升级事件。

**实施步骤**:

1. **verification_config.py**: 在`ConsistencyThresholdConfig`中增加：
   ```python
   auto_upgrade_phase: bool = Field(True, description="是否自动从phase1升级到phase2(当样本量满足时)")
   ```

2. **verifier.py**: 修改自动升级逻辑，检查`auto_upgrade_phase`配置：
   ```python
   if all_organs_meet_phase2 and self.config.consistency_thresholds.verification_phase == "phase1":
       if self.config.consistency_thresholds.auto_upgrade_phase:
           self.config.consistency_thresholds.verification_phase = "phase2"
           ...
       else:
           logger.info("phase_upgrade_skipped", reason="auto_upgrade_phase_disabled")
   ```

3. **report.py**: 在`ConsistencyReport`中增加字段记录升级事件：
   ```python
   phase_upgraded: bool = False
   phase_upgrade_from: str = ""
   phase_upgrade_to: str = ""
   ```

4. **verifier.py**: 在升级时设置report字段：
   ```python
   report.phase_upgraded = True
   report.phase_upgrade_from = "phase1"
   report.phase_upgrade_to = "phase2"
   ```

5. **serialization.py**: 在`report_to_dict`中输出升级事件字段。

**验证**: 测试`auto_upgrade_phase=False`时不发生自动升级

---

### P2-4: 幻觉检测未区分类型统计

**问题根因**: `is_hallucination`仅为布尔值，`DetectionResult.issues`含策略标识但统计层面未按策略聚合FP/FN。

**修复方案**: 在GradingResult中存储DetectionResult，在FalseStats中增加按策略分解的统计。

**实施步骤**:

1. **report.py**: 在`GradingResult`中增加字段存储完整检测结果：
   ```python
   hallucination_issues: List[Dict[str, Any]] = field(default_factory=list)
   ```
   存储简化版的DetectionIssue（strategy + severity + description）。

2. **report.py**: 新增数据类`StrategyFalseStats`：
   ```python
   @dataclass
   class StrategyFalseStats:
       strategy: str
       false_positives: int = 0
       false_negatives: int = 0
       true_positives: int = 0
       true_negatives: int = 0
   ```

3. **report.py**: 在`FalseStats`中增加字段：
   ```python
   strategy_breakdown: Dict[str, StrategyFalseStats] = field(default_factory=dict)
   ```

4. **verifier.py**: 修改`_verify_single_case`，将DetectionResult的issues存入GradingResult：
   ```python
   detection_result = self._hallucination_detector.detect(resp_a.text, organ)
   is_hallucination_a = detection_result.is_hallucination
   hallucination_issues_a = [
       {"strategy": issue.strategy.value, "severity": issue.severity, "description": issue.description}
       for issue in detection_result.issues
   ]
   ```

5. **verifier.py**: 修改`_compute_false_stats`，按策略分解统计。对每个有ground truth的用例，不仅统计全局FP/FN，还按触发的策略分别统计。

6. **organ_breakdown.py**: 在器官级FalseStats中也增加strategy_breakdown。

7. **serialization.py**: 在`report_to_dict`中输出strategy_breakdown。

**验证**: 编写测试验证按策略分解的FP/FN统计正确

---

### P2-5: 缺少4小时持续运行验证

**问题根因**: 项目中仅有VRAM碎片化的4小时持续监测测试，缺少推理引擎的持续负载测试。

**修复方案**: 创建`sustained_load_test.py`模块，实现推理引擎持续负载测试框架。

**实施步骤**:

1. **新建文件** `app/inference/sustained_load_test.py`，包含：
   - `SustainedLoadTest`类，接受两个InferenceEngine实例
   - `run(duration_seconds, cases, interval_seconds)`方法
   - 持续发送推理请求，按时间窗口统计：
     - 延迟P50/P99趋势（检测退化）
     - 幻觉率趋势（检测漂移）
     - 错误率趋势
   - 结果输出`SustainedLoadTestReport`

2. **新建测试** `tests/integration/llm/test_sustained_load.py`：
   - 快速模式：5分钟（环境变量`SUSTAINED_LOAD_ENABLED=1`启用）
   - 完整模式：4小时
   - 验证延迟退化不超过20%、幻觉率漂移不超过0.5%

3. **数据模型**:
   ```python
   @dataclass
   class TimeWindowStats:
       window_start: float
       window_end: float
       request_count: int
       error_count: int
       latency_p50_ms: float
       latency_p99_ms: float
       hallucination_rate: float
   
   @dataclass
   class SustainedLoadTestReport:
       duration_seconds: float
       total_requests: int
       total_errors: int
       windows: List[TimeWindowStats]
       latency_degradation_pct: float
       hallucination_drift: float
       passed: bool
   ```

**验证**: 运行快速模式测试确认框架正常工作

---

### P2-6: 器官级延迟P99未标记可靠性

**问题根因**: 全局`latency_p99_reliable`存在（两侧引擎各≥30样本），但器官级breakdown中仅有P99数值，无可靠性标记。当某器官样本很少时，P99可能极不可靠。

**修复方案**: 在器官级breakdown中增加`latency_p99_reliable`字段。

**实施步骤**:

1. **organ_breakdown.py**: 在`compute_organ_breakdown()`中增加可靠性计算：
   ```python
   organ_latency_p99_reliable = (
       len(organ_latencies_a) >= 30 and len(organ_latencies_b) >= 30
   )
   ```

2. **organ_breakdown.py**: 在organ_breakdown字典中增加字段：
   ```python
   "latency_p99_reliable": organ_latency_p99_reliable,
   "latency_sample_count_a": len(organ_latencies_a),
   "latency_sample_count_b": len(organ_latencies_b),
   ```

**验证**: 确认organ_breakdown输出包含新增字段

---

## 实施顺序

按依赖关系和优先级排序：

```
Step 1: P2-6 (器官级延迟P99可靠性) — 最小改动，无依赖
Step 2: P2-2 (Gate判定使用latency_p99_reliable) — 依赖Step 1的概念
Step 3: P2-3 (Phase自动升级确认) — 独立改动
Step 4: P2-1 (文本相似度增强) — 独立改动
Step 5: P1-5 (cross_validate重命名+文档) — 独立改动
Step 6: P1-3 (幻觉ground truth扩充) — 数据扩充，需仔细构造
Step 7: P1-4 (边界病例密度追踪) — 依赖Step 6的数据
Step 8: P2-4 (幻觉按策略统计) — 涉及多文件改动
Step 9: P2-5 (持续运行验证) — 新功能，独立开发
Step 10: 运行全量测试验证无回归
```

## 验证标准

- 每步修复后运行`pytest tests/unit/llm/ -v --tb=short`确认无回归
- 全部修复完成后运行`pytest tests/ -v --tb=short`确认全量通过
- 新增代码需有对应单元测试覆盖
