# LLM轨道验证机制修复 & Phase0A实施完成方案

> 基于《LLM轨道验证机制全面有效性审查报告》的系统性修复计划

---

## 第一部分：审查报告问题修复实施方案

### 修复批次划分

| 批次 | 优先级 | 问题数 | 预计涉及文件 | 依赖关系 |
|------|--------|--------|-------------|----------|
| Batch 1 | P0 | 5 | 6 | 无 |
| Batch 2 | P1 | 6 | 8 | Batch 1 |
| Batch 3 | P2 | 7 | 7 | Batch 2 |

---

### Batch 1: P0级修复（必须修复，影响验证结果正确性）

#### P0-1: 增加器官级Gate检查

**问题**: `ConsistencyReport.gate_status`仅检查全局指标，全局通过但某器官可能严重不达标

**修改文件**:
- `app/inference/consistency/report.py` — `gate_status` property
- `app/inference/consistency/report.py` — `passed` property
- `tests/unit/llm/test_consistency.py` — 新增测试

**实施方案**:

1. 在 `gate_status` property 中增加器官级检查逻辑：
   - 遍历 `organ_breakdown`，检查每个器官的 `grading_consistency_rate` 是否 < `consistency_thresholds.grading_consistency_rate`
   - 检查每个器官的 `clinical_boundary_diff_rate` 是否 > `consistency_thresholds.clinical_boundary_diff_rate`
   - 任一器官不达标 → Gate 降级（PASSED→CONDITIONAL 或 CONDITIONAL→FAILED）
   - 器官分级一致率 < `conditional_grading_consistency_rate`(90%) → FAILED
   - 器官分级一致率在90%-95%之间 → 至少 CONDITIONAL

2. 新增 `organ_gate_failures` 字段到 `ConsistencyReport`，记录不达标的器官及具体指标

3. 新增测试用例：
   - 全局通过但某器官不达标的场景 → Gate应为CONDITIONAL
   - 某器官分级一致率<90% → Gate应为FAILED
   - 所有器官均达标 → Gate应为PASSED

**验证标准**: `pytest tests/unit/llm/test_consistency.py -v -k "organ_gate"` 全部通过

---

#### P0-2: 修复延迟差异阈值逻辑

**问题**: 绝对值阈值500ms与百分比阈值20%是OR关系，百分比阈值可被绕过

**修改文件**:
- `app/inference/consistency/report.py` — `passed` property 和 `gate_status` property
- `app/inference/verification_config.py` — `ConsistencyThresholdConfig`
- `tests/unit/llm/test_consistency.py` — 新增/修改测试

**实施方案**:

1. 修改 `ConsistencyThresholdConfig`：
   - 新增 `latency_diff_gate_logic: str = "and"` 字段，可选值 "and" / "or"
   - 默认 "and"：百分比阈值和绝对值阈值须同时满足才通过
   - 收紧 `latency_diff_abs_ms` 从 500.0 → 200.0（与P99<3000ms的20%=600ms相比，200ms更合理）

2. 修改 `passed` property 中延迟检查逻辑：
   - 当 `latency_diff_gate_logic == "and"` 时：`latency_diff_pct <= threshold AND latency_diff_abs_ms <= abs_threshold`
   - 当 `latency_diff_gate_logic == "or"` 时：保持原逻辑

3. 新增测试用例：
   - 百分比超阈值但绝对值未超 → AND模式下FAILED
   - 绝对值超阈值但百分比未超 → AND模式下FAILED
   - 两者都未超 → PASSED

**验证标准**: `pytest tests/unit/llm/test_consistency.py -v -k "latency"` 全部通过

---

#### P0-3: 补充BI-RADS 0类到层级定义

**问题**: `_GRADE_HIERARCHY["breast"]`缺少"0"映射，BI-RADS 0→3等边界无法检测

**修改文件**:
- `app/inference/grading_definitions.py` — `_GRADE_HIERARCHY` 和 `_GRADING_PATTERNS`
- `app/inference/verification_config.py` — `get_default_boundary_config()` 中breast边界对
- `tests/unit/llm/test_consistency.py` — 新增测试

**实施方案**:

1. 在 `_GRADE_HIERARCHY["breast"]` 中添加：
   ```python
   "0": (0, 0),  # 评估不完整
   ```

2. 在 `get_default_boundary_config()` 的 `breast_boundaries` 中添加边界对：
   - `0→3`: weight=1.5, is_critical=True（评估不完整→可能良性，临床管理路径差异大）
   - `0→4`: weight=2.0, is_critical=True
   - `0→5`: weight=3.0, is_critical=True
   - 及其反向对

3. 新增测试：
   - `extract_grading("乳腺结节 BI-RADS 0类", "breast") == "0"`
   - ConsistencyPair中BI-RADS 0→3的边界跨越检测
   - ConsistencyPair中BI-RADS 0→5的boundary_severity为critical

**验证标准**: `pytest tests/unit/llm/test_consistency.py -v -k "birads_0"` 全部通过

---

#### P0-4: 修复failure_analysis身份比较bug

**问题**: `case in report.inconsistent_cases`使用身份比较（`is`），应改为case_id比较

**修改文件**:
- `app/inference/consistency/failure_analysis.py` — `generate_failure_analysis()`

**实施方案**:

1. 将 `case in report.inconsistent_cases` 改为基于case_id的比较：
   ```python
   inconsistent_ids = {c.get("case_id") for c in report.inconsistent_cases}
   boundary_ids = {c.get("case_id") for c in report.boundary_cases}
   # ...
   is_grading = case.get("case_id") in inconsistent_ids
   is_boundary = case.get("case_id") in boundary_ids
   ```

2. 新增测试：构造同一case_id在inconsistent_cases和boundary_cases中为不同对象的场景，验证overlap_count正确

**验证标准**: `pytest tests/unit/llm/test_consistency.py -v -k "failure_analysis"` 全部通过

---

#### P0-5: 实现显存碎片化验证框架

**问题**: 07文档要求Gate 0A必须验证FunASR+vLLM+TensorRT混合运行显存碎片化<30%，代码中无任何实现

**新增文件**:
- `app/inference/gpu_memory_monitor.py` — GPU显存监控模块
- `tests/integration/llm/test_gpu_memory_fragmentation.py` — 集成测试

**实施方案**:

1. 新增 `GPUMemoryMonitor` 类：
   ```python
   @dataclass
   class GPUMemorySnapshot:
       timestamp: str
       total_memory_mb: float
       used_memory_mb: float
       free_memory_mb: float
       fragmented_memory_mb: float
       fragmentation_rate: float  # fragmented / total

   class GPUMemoryMonitor:
       def __init__(self, gpu_id: int = 0, snapshot_interval_seconds: float = 60.0): ...
       async def take_snapshot(self) -> GPUMemorySnapshot: ...
       async def monitor_during_load_test(self, duration_seconds: float, ...) -> List[GPUMemorySnapshot]: ...
       def compute_fragmentation_rate(self, snapshots: List[GPUMemorySnapshot]) -> float: ...
   ```

2. 使用 `nvidia-smi` 或 `pynvml` 获取显存信息：
   - total_memory: GPU总显存
   - used_memory: 已使用显存
   - fragmented_memory: 通过 `nvidia-smi --query-gpu=memory.free` 与实际可分配最大连续块的差值估算

3. 新增集成测试：
   - 快速验证（5分钟）：启动所有模型后测量碎片化率
   - 完整验证（4小时）：按07文档要求，10例/小时频率模拟诊断请求
   - 验收标准：碎片化率 < 30%（warning），< 40%（热重载触发）

4. 在 `ConsistencyThresholdConfig` 中新增：
   ```python
   gpu_fragmentation_warning_threshold: float = 0.30
   gpu_fragmentation_critical_threshold: float = 0.40
   gpu_memory_monitor_duration_seconds: float = 14400.0  # 4小时
   ```

**验证标准**: `pytest tests/integration/llm/test_gpu_memory_fragmentation.py -v -m "phase0a"` 通过

---

### Batch 2: P1级修复（应当修复，影响验证机制有效性）

#### P1-1: 扩展否定词列表

**修改文件**: `app/inference/hallucination_detector.py` — `ForbiddenTermsDetector`

**实施方案**:

1. 在 `NEGATION_WORDS` 列表中增加：
   - "排除" — "排除肺癌可能"
   - "否认" — "患者否认肺癌史"
   - "既往" — "既往肺癌史"（特殊处理：标记为info而非hallucination）
   - "未见" — 已存在
   - "无" — 已存在
   - "不考虑" — 已存在
   - "未发现" — 新增
   - "未提示" — 新增
   - "不伴" — 新增（"不伴骨转移"）

2. 新增 `NEGATION_PREFIXES` 和 `NEGATION_SUFFIXES` 概念，支持更灵活的否定模式匹配

3. 新增测试：每个新增否定词至少1个正向测试（否定后不标记）和1个反向测试（无否定时标记）

**验证标准**: `pytest tests/unit/llm/test_hallucination_detector.py -v -k "negation"` 全部通过

---

#### P1-2: 实现窗口式上下文检测

**修改文件**: `app/inference/hallucination_detector.py` — `ForbiddenTermsDetector._check_context_allowed()`

**实施方案**:

1. 修改 `allow_if_context` 检测逻辑：
   - 从全文搜索改为窗口搜索：在禁忌词前后N个字符（默认100字符）内搜索context关键词
   - 新增 `context_window_chars: int = 100` 配置项到 `ForbiddenTermConfig`
   - 窗口范围：`text[max(0, match_start - 100):match_end + 100]`

2. 新增测试：
   - 禁忌词与context关键词在同一窗口 → 豁免
   - 禁忌词与context关键词不在同一窗口 → 标记
   - 边界情况：禁忌词在文本开头/结尾

**验证标准**: `pytest tests/unit/llm/test_hallucination_detector.py -v -k "context_window"` 全部通过

---

#### P1-3: 增加per-strategy幻觉率差异统计

**修改文件**:
- `app/inference/consistency/verifier.py` — `_compute_hallucination_stats()`
- `app/inference/consistency/report.py` — `ConsistencyReport` 新增字段

**实施方案**:

1. 在 `_compute_hallucination_stats()` 中，按5种策略分别统计两引擎的幻觉检出数：
   ```python
   strategy_hallucination_diff: Dict[str, float] = {}
   for strategy in DetectionStrategy:
       a_count = sum(1 for p in pairs if p.engine_a_result and strategy.value in [i.strategy.value for i in p.engine_a_result.hallucination_issues])
       b_count = sum(1 for p in pairs if p.engine_b_result and strategy.value in [i.strategy.value for i in p.engine_b_result.hallucination_issues])
       strategy_hallucination_diff[strategy.value] = abs(a_count - b_count) / total
   ```

2. 在 `ConsistencyReport` 中新增 `strategy_hallucination_diff: Dict[str, float]` 字段

3. 新增测试：构造不同策略差异的mock数据，验证per-strategy diff计算正确

**验证标准**: `pytest tests/unit/llm/test_consistency.py -v -k "strategy_hallucination"` 全部通过

---

#### P1-4: 修复`_LVEF_TO_GRADING`线程安全问题

**修改文件**: `app/inference/grading_definitions.py`

**实施方案**:

1. 将 `_LVEF_TO_GRADING` 从全局可变列表改为函数级缓存：
   ```python
   _lvef_cache: Optional[List[Tuple[float, str]]] = None
   _lvef_cache_lock = threading.Lock()

   def _get_lvef_grading_table() -> List[Tuple[float, str]]:
       global _lvef_cache
       if _lvef_cache is not None:
           return _lvef_cache
       with _lvef_cache_lock:
           if _lvef_cache is not None:
               return _lvef_cache
           _lvef_cache = _compute_lvef_grading_table()
           return _lvef_cache
   ```

2. `_sync_lvef_thresholds_from_config()` 改为重置缓存而非修改全局列表

3. 新增测试：多线程并发调用 `_get_lvef_grading_table()`，验证无竞态

**验证标准**: `pytest tests/unit/llm/test_consistency.py -v -k "lvef_thread"` 全部通过

---

#### P1-5: 增加ground truth标注指南

**新增文件**: `docs/testing/hallucination_ground_truth_guide.md`

**实施方案**:

1. 编写标注指南文档，包含：
   - `expected_hallucination` 字段定义：True/False/None
   - 标注原则：基于5种检测策略的预期结果
   - 标注示例：每种策略≥2个正例和2个反例
   - 质量控制：双标注一致率要求

2. 在 `ConsistencyVerifier` 的docstring中引用此指南

3. 在 `test_consistency.py` 中增加ground truth标注完整性检查测试

**验证标准**: 文档完成 + 相关测试通过

---

#### P1-6: 文本截断策略改进

**修改文件**: `app/inference/consistency/statistics.py` — `text_similarity()`

**实施方案**:

1. 修改截断策略：从"前500字"改为"前300字+后200字"
   ```python
   def _smart_truncate(text: str, head: int = 300, tail: int = 200) -> str:
       if len(text) <= head + tail:
           return text
       return text[:head] + text[-tail:]
   ```

2. 在 `text_similarity()` 和 `text_consistency_with_grading()` 中使用新截断策略

3. 新增测试：
   - 短文本（<500字）：不截断
   - 中等文本（500-800字）：首尾拼接
   - 长文本（>800字）：首300+尾200
   - 关键信息在文末的场景：新策略应比旧策略相似度更高

**验证标准**: `pytest tests/unit/llm/test_consistency.py -v -k "text_similarity"` 全部通过

---

### Batch 3: P2级修复（建议改进，提升验证体系严谨性）

#### P2-1: 为precision/recall/F1增加置信区间

**修改文件**:
- `app/inference/consistency/report.py` — `FalseStats`
- `app/inference/consistency/verifier.py` — `_compute_false_stats()`

**实施方案**:

1. 在 `FalseStats` 中新增字段：
   ```python
   precision_ci_lower: float = 0.0
   precision_ci_upper: float = 0.0
   recall_ci_lower: float = 0.0
   recall_ci_upper: float = 0.0
   f1_ci_lower: float = 0.0
   f1_ci_upper: float = 0.0
   ```

2. 在 `_compute_false_stats()` 中使用Wilson CI计算各指标的置信区间

3. 新增测试：验证CI计算正确性（与已知值对比）

---

#### P2-2: 集成AdaptiveThresholdConfig到验证流程

**修改文件**:
- `app/inference/consistency/report.py` — `gate_status` property
- `app/inference/verification_config.py` — `compute_adaptive_threshold()`

**实施方案**:

1. 在 `gate_status` 判定中，当 `adaptive_thresholds` 非空时，使用自适应阈值替代固定阈值
2. 从历史验证结果中提取 `historical_pass_rates`
3. 调用 `compute_adaptive_threshold()` 计算当前阈值
4. 新增测试：高通过率→阈值收紧，低通过率→阈值放宽

---

#### P2-3: 增加McNemar检验

**修改文件**:
- `app/inference/consistency/report.py` — `ConsistencyReport` 新增字段
- `app/inference/consistency/verifier.py` — `_compute_false_stats()`

**实施方案**:

1. 实现 McNemar 检验函数：
   ```python
   def mcnemar_test(engine_a_results: List[bool], engine_b_results: List[bool]) -> Tuple[float, float]:
       # 返回 (statistic, p_value)
   ```

2. 在 `ConsistencyReport` 中新增 `hallucination_diff_p_value: float` 字段

3. 当 p_value < 0.05 时，标记两引擎幻觉检测性能差异显著

---

#### P2-4: 量化优化建议

**修改文件**: `app/inference/consistency/failure_analysis.py` — `generate_failure_analysis()` 和 `suggest_for_trace()`

**实施方案**:

1. 将泛化建议改为量化建议：
   - "建议增加thyroid专项训练数据" → "建议增加≥50例thyroid TI-RADS 3→4边界训练数据（当前边界差异率{rate:.2%}，需降至≤1%）"
   - "建议优化禁忌词检测阈值" → "建议将{organ}禁忌词检测severity≥2的阈值从{current}调整至{suggested}（当前假阳性率{fp_rate:.2%}）"

2. 在建议中增加具体数值和计算依据

---

#### P2-5: 追溯链增加中间推理细节

**修改文件**: `app/inference/consistency/failure_analysis.py` — trace_chain生成逻辑

**实施方案**:

1. 在 boundary_check 步骤中增加 `matched_boundary_pair` 字段：
   ```python
   {
       "stage": "boundary_check",
       "rule": f"boundary_weight={p.boundary_weight}",
       "detail": f"engine_a={grading_a}, engine_b={grading_b}, severity={severity}",
       "matched_boundary": f"{from_grade}→{to_grade} (weight={weight}, is_critical={is_critical})",
       "severity": "critical"
   }
   ```

2. 在 grading_extraction 步骤中增加 `pattern_matched` 字段

---

#### P2-6: 增加器官级延迟差异百分比

**修改文件**: `app/inference/consistency/organ_breakdown.py` — `compute_organ_breakdown()`

**实施方案**:

1. 在器官级统计中增加：
   ```python
   "latency_diff_pct": abs(organ_latency_a_p99 - organ_latency_b_p99) / min(organ_latency_a_p99, organ_latency_b_p99) * 100 if min(...) > 0 else 0.0,
   "latency_diff_abs_ms": abs(organ_latency_a_p99 - organ_latency_b_p99),
   ```

2. 新增测试验证计算正确性

---

#### P2-7: 文本相似度权重经验校准

**修改文件**:
- `app/inference/consistency/statistics.py` — `text_similarity()`
- `app/inference/verification_config.py` — 新增配置项

**实施方案**:

1. 将硬编码权重提取为配置：
   ```python
   class TextSimilarityConfig(BaseModel):
       trigram_weight: float = 0.4
       edit_ratio_weight: float = 0.4
       bigram_weight: float = 0.2
       head_chars: int = 300
       tail_chars: int = 200
       semantic_equivalent_threshold: float = 0.5
       non_equivalent_threshold: float = 0.8
   ```

2. 在 `VerificationConfig` 中新增 `text_similarity_config` 字段

3. `text_similarity()` 和 `text_consistency_with_grading()` 从配置读取参数

---

## 第二部分：Phase0A低完成度任务执行实施方案

### 任务总览

| 任务 | 当前完成度 | 目标完成度 | 预计工作量 |
|------|-----------|-----------|-----------|
| T1: 显存碎片化验证 | 0% | 100% | 大 |
| T2: compact模式内存实测 | 0% | 100% | 中 |
| T3: compact模式共享GPU调度策略 | 30% | 100% | 大 |
| T4: FunASR语音部署 | 0% | 100% | 大 |
| T5: LLM基座模型选型对比 | 60% | 100% | 中 |

---

### T1: 显存碎片化验证执行方案

**目标**: 验证FunASR+vLLM+TensorRT混合运行4小时后显存碎片化率<30%

**前置条件**:
- GPU服务器可用（RTX 3060 12GB 或更好）
- FunASR ONNX Runtime模型已部署
- vLLM服务已启动
- TensorRT视觉模型已编译

**执行步骤**:

1. **实现GPU显存监控模块** (P0-5中已规划)
   - 文件: `app/inference/gpu_memory_monitor.py`
   - 功能: 定时采集GPU显存快照，计算碎片化率
   - 依赖: `pynvml` 或 `nvidia-smi` CLI

2. **实现碎片化率计算算法**
   - 方法: 通过 `nvmlMemoryInfo` 获取 total/free/used
   - 碎片化率 = (free_memory - max_contiguous_free_block) / total_memory
   - max_contiguous_free_block 通过尝试分配递增大小的内存块来估算

3. **编写4小时持续运行测试**
   - 文件: `tests/integration/llm/test_gpu_memory_fragmentation.py`
   - 流程:
     a. 启动FunASR + vLLM + TensorRT全部模型
     b. 记录初始显存快照
     c. 以10例/小时频率模拟诊断请求
     d. 每60秒采集一次显存快照
     e. 4小时后计算最终碎片化率
   - 快速模式: 5分钟验证（仅验证监控功能）

4. **集成到Gate 0A判定**
   - 在 `test_phase0a_consistency.py` 中增加碎片化率Gate检查
   - 阈值: <30% PASSED, 30%-40% CONDITIONAL, >40% FAILED

5. **编写预防性热重载策略验证**
   - 验证每2小时无诊断请求时自动执行预防性热重载
   - 验证热重载后碎片化率下降

**验收标准**:
- [ ] `GPUMemoryMonitor` 类实现完成并通过单元测试
- [ ] 4小时持续运行测试通过（碎片化率<30%）
- [ ] Gate 0A集成测试包含碎片化率检查
- [ ] 预防性热重载策略验证通过

---

### T2: compact模式内存实测执行方案

**目标**: 验证64GB内存配置下compact模式的内存使用符合预算（12-22GB占用，剩余~40GB）

**前置条件**:
- 64GB内存的测试服务器
- compact模式配置已就绪（llama.cpp默认引擎）

**执行步骤**:

1. **实现系统内存监控模块**
   - 文件: `app/inference/system_memory_monitor.py`
   - 功能: 监控进程级和系统级内存使用
   - 依赖: `psutil`

2. **定义内存预算验证点**
   ```python
   @dataclass
   class MemoryBudget:
       total_system_memory_gb: float = 64.0
       llama_cpp_expected_gb: float = 3.0      # GPU显存
       llama_cpp_cpu_expected_gb: float = 8.0   # CPU内存（模型权重+上下文）
       funasr_expected_gb: float = 2.0          # FunASR CPU+GPU
       tensorrt_expected_gb: float = 1.5        # TensorRT GPU
       redis_expected_gb: float = 0.5
       postgres_expected_gb: float = 1.0
       os_overhead_gb: float = 2.0
       total_expected_max_gb: float = 22.0
       remaining_min_gb: float = 40.0
   ```

3. **编写内存实测测试**
   - 文件: `tests/integration/llm/test_compact_memory.py`
   - 流程:
     a. 启动compact模式全部服务
     b. 等待所有模型加载完成
     c. 采集各进程内存使用量
     d. 执行100例诊断请求
     e. 采集峰值内存使用量
     f. 验证总使用量 ≤ 22GB，剩余 ≥ 40GB

4. **内存泄漏检测**
   - 4小时持续运行后对比初始和最终内存使用
   - 增长量 > 10% 触发warning

**验收标准**:
- [ ] `SystemMemoryMonitor` 类实现完成
- [ ] compact模式内存实测测试通过（≤22GB占用）
- [ ] 内存泄漏检测测试通过（4小时增长<10%）

---

### T3: compact模式共享GPU调度策略执行方案

**目标**: 实现llama.cpp与FunASR/TensorRT在同一GPU上的调度策略

**当前状态**: 仅有profile-based引擎选择，无GPU共享调度逻辑

**执行步骤**:

1. **设计GPU共享调度架构**
   - 文件: `app/inference/gpu_scheduler.py`
   - 核心类: `GPUScheduler`
   - 职责:
     - 管理GPU显存分配给不同推理任务
     - 实现优先级调度（LLM推理 > 语音推理 > 视觉推理）
     - 实现显存不足时的排队/降级机制

2. **实现调度策略**
   ```python
   class GPUScheduler:
       def __init__(self, total_gpu_memory_mb: int, ...): ...

       async def acquire_memory(self, task_type: str, required_mb: int, priority: int = 0) -> bool: ...
       async def release_memory(self, task_type: str, required_mb: int) -> None: ...
       async def wait_for_memory(self, task_type: str, required_mb: int, timeout: float = 30.0) -> bool: ...
       def get_available_memory(self) -> int: ...
       def get_allocation_status(self) -> Dict[str, int]: ...
   ```

3. **集成到InferenceEngine**
   - `LlamaCppEngine` 在推理前调用 `acquire_memory()`
   - 推理完成后调用 `release_memory()`
   - 显存不足时排队等待或降级到CPU推理

4. **实现预防性热重载触发**
   - 当碎片化率 > 40% 时触发热重载
   - 热重载流程: 释放所有GPU显存 → 重新加载模型 → 恢复服务
   - 热重载期间请求排队

5. **编写测试**
   - 单元测试: 调度逻辑正确性
   - 集成测试: 多任务并发GPU使用
   - 压力测试: 显存不足场景的降级行为

**验收标准**:
- [ ] `GPUScheduler` 实现完成并通过单元测试
- [ ] 集成到 `LlamaCppEngine` 和 `VLLMEngine`
- [ ] 预防性热重载策略实现并测试通过
- [ ] 显存不足降级机制测试通过

---

### T4: FunASR语音部署执行方案

**目标**: 部署FunASR Paraformer-zh语音识别服务，验证术语识别准确率≥90%

**执行步骤**:

1. **FunASR ONNX Runtime部署**
   - 文件: `app/speech/funasr_engine.py`
   - 使用ONNX Runtime推理（消除PyTorch与vLLM混合运行显存碎片化风险）
   - 实现语音识别接口

2. **超声术语识别增强**
   - 集成pkuseg-medicine分词器
   - 构建超声术语热词表（从UDAF词汇表提取）
   - 实现热词增强解码

3. **性能验证**
   - 术语识别准确率 ≥ 90%（ONNX导出后WER增量 < 0.5%）
   - 推理延迟 < 500ms（RTX 3060）
   - 常驻显存 ≤ 0.5GB

4. **集成测试**
   - 文件: `tests/integration/speech/test_funasr.py`
   - 测试超声报告语音输入的识别准确率

**验收标准**:
- [ ] FunASR ONNX Runtime部署完成
- [ ] 术语识别准确率≥90%
- [ ] 推理延迟<500ms
- [ ] 常驻显存≤0.5GB

---

### T5: LLM基座模型选型对比执行方案

**目标**: 完成Qwen2.5-7B/ChatGLM4-9B/InternLM2.5-7B的对比验证

**当前状态**: benchmark框架完成，待执行实际对比

**执行步骤**:

1. **准备对比测试集**
   - 常规病例 ≥ 200例（4器官各≥50例）
   - 边界病例 ≥ 100例（TI-RADS/LI-RADS/BI-RADS 3/4类各≥30例）
   - 高风险病种 ≥ 50例

2. **执行对比验证**
   - 使用 `ModelBenchmark.benchmark_candidate()` 对每个候选模型运行测试
   - 记录: accuracy, p99_latency, hallucination_rate, quantized_hallucination_increment
   - 运行 `analyze_threshold_sensitivity()` 进行敏感性分析

3. **生成对比报告**
   - 使用 `BenchmarkReport.to_dict()` 导出结果
   - 使用 `determine_winner()` 确定最优模型
   - 分析各模型在不同器官上的表现差异

4. **Gate判定**
   - accuracy ≥ 89%（基层）
   - p99_latency ≤ 3000ms
   - quantized_hallucination_increment ≤ 0.5%
   - 不达标模型淘汰

**验收标准**:
- [ ] 3个候选模型全部完成对比
- [ ] 对比报告生成
- [ ] 确定最优模型
- [ ] 最优模型满足所有Gate标准

---

## 第三部分：核心参考文档更新方案

### 需更新的文档及内容

#### 1. 07-数据标注与模型评估.md

**更新位置**: §4 模型导出流水线规范 — 一致性验证要求

**更新内容**:
- 增加器官级Gate检查要求：每个器官须独立满足分级一致率≥95%和边界差异率≤1%
- 增加per-strategy幻觉率差异统计要求
- 增加McNemar统计显著性检验要求

#### 2. 06-开发环境与进度规划.md

**更新位置**: §6.3 Gate 0A验证项

**更新内容**:
- 显存碎片化验证：明确"4小时持续运行后测量，碎片化率<30%（warning），<40%（热重载触发）"
- compact模式内存实测：明确"64GB配置下总占用≤22GB，剩余≥40GB"
- 延迟差异双阈值：明确"百分比阈值和绝对值阈值须同时满足（AND逻辑）"

#### 3. 01-系统架构设计.md

**更新位置**: §5.7 推理框架选型

**更新内容**:
- 延迟差异判定规则：增加双阈值AND逻辑说明
- 器官级一致性验证要求
- GPU共享调度策略架构说明

---

## 第四部分：实施时间线

```
Week 1: Batch 1 (P0) 修复
  Day 1-2: P0-1 器官级Gate检查 + P0-4 failure_analysis bug修复
  Day 2-3: P0-2 延迟差异阈值逻辑 + P0-3 BI-RADS 0类
  Day 3-5: P0-5 显存碎片化验证框架实现

Week 2: Batch 2 (P1) 修复 + Phase0A任务启动
  Day 1-2: P1-1 否定词扩展 + P1-2 窗口式上下文检测
  Day 2-3: P1-3 per-strategy幻觉率差异 + P1-4 LVEF线程安全
  Day 3-4: P1-5 ground truth标注指南 + P1-6 文本截断策略
  Day 4-5: T1 显存碎片化验证执行 + T2 compact内存实测

Week 3: Phase0A任务推进 + Batch 3 (P2) 启动
  Day 1-3: T3 GPU共享调度策略实现
  Day 3-5: T4 FunASR语音部署
  Day 4-5: P2-1~P2-3 置信区间/自适应阈值/McNemar检验

Week 4: Phase0A任务收尾 + 文档更新
  Day 1-2: T5 LLM基座模型选型对比执行
  Day 2-3: P2-4~P2-7 量化建议/追溯链/器官延迟/文本权重
  Day 3-4: 核心参考文档更新
  Day 4-5: 全量回归测试 + Gate 0A综合验证
```

---

## 第五部分：风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| GPU服务器不可用 | 中 | 高 | P0-5框架实现不依赖GPU，测试用skipif标记 |
| 候选模型GGUF转换失败 | 低 | 中 | 优先验证Qwen2.5-7B（已知支持GGUF） |
| 4小时碎片化测试超时 | 中 | 低 | 提供5分钟快速验证模式 |
| 器官级Gate检查导致现有测试失败 | 中 | 中 | 先修改测试预期，再修改Gate逻辑 |
| pynvml依赖安装问题 | 低 | 低 | 提供nvidia-smi CLI回退方案 |
