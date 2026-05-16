# LLM轨道Phase0A验证机制全面有效性审查计划

## 审查范围

对 `e:\Ultrasound Diagnosis Decision Support AI System` 项目中 LLM 轨道 Phase0A 阶段的全部验证环节进行系统性审查，涵盖设计规范、代码质量、代码风格、功能正确性四个维度，并对照核心参考文档进行一致性审查。

---

## 审查维度一：设计规范 — 验证机制设计合理性

### 1.1 分级提取验证（需求项1）

**审查点：**
- [ ] `extract_grading()` 正则模式是否覆盖 TI-RADS/LI-RADS/BI-RADS/LVEF 全部分级格式
- [ ] 心脏器官"中文描述优先于LVEF数值"的设计是否合理，是否存在LVEF与中文描述矛盾时的优先级冲突
- [ ] `_GRADE_HIERARCHY` 映射是否完整覆盖所有可能的分级值
- [ ] 分级提取失败的降级处理是否充分（返回None后的下游逻辑）

**关键文件：**
- `app/inference/grading_definitions.py`
- `tests/unit/llm/test_consistency.py` (TestExtractGrading)

### 1.2 双引擎分级一致性（需求项2）

**审查点：**
- [ ] `grading_consistent` 属性使用精确匹配（`==`），而 `grading_semantic_consistent` 使用语义等价，两套判定标准的适用场景是否明确区分
- [ ] 语义等价判定 `grading_semantic_equivalent()` 中 `abs(level_a[1] - level_b[1]) <= 1` 的设计：4a与4b等价、4a与4c不等价，是否与临床实践一致
- [ ] 门控判定中 `grading_consistency_rate` 使用精确匹配而非语义等价，是否过于严格
- [ ] `_GRADE_HIERARCHY` 中未定义的分级值（如新出现的分级格式）的fallback处理

**关键文件：**
- `app/inference/consistency/report.py` (ConsistencyPair.grading_consistent / grading_semantic_consistent)
- `app/inference/consistency/statistics.py` (grading_semantic_equivalent)

### 1.3 文本一致率（需求项3）

**审查点：**
- [ ] `text_similarity()` 使用 bigram Jaccard + Levenshtein 编辑距离的最大值，是否合理
- [ ] `text_consistency_with_grading()` 中分级等价时阈值0.5、不等价时阈值0.8，阈值设定依据是否充分
- [ ] 中文文本的 bigram 切分是否有效（中文无空格分词，bigram可能产生大量噪声）
- [ ] Levenshtein 距离对长文本的计算复杂度（O(n*m)），是否需要优化

**关键文件：**
- `app/inference/consistency/statistics.py` (text_similarity, text_consistency_with_grading)

---

## 审查维度二：临床边界跨越检测

### 2.1 边界跨越识别（需求项4）

**审查点：**
- [ ] `_grades_cross_clinical_boundary()` 的匹配逻辑是否正确覆盖所有边界对
- [ ] `_check_boundary_match()` 中 `from_level[0] == to_level[0]` 的分支判断是否正确（同主级别 vs 跨主级别）
- [ ] `_numeric_fallback_match()` 仅提取数字前缀，对于非数字分级（如 cardiac 的 normal/mild）是否可靠
- [ ] 边界对配置中双向定义（3→4 和 4→3）是否冗余，是否应改为无向图

**关键文件：**
- `app/inference/consistency/statistics.py` (_grades_cross_clinical_boundary, _check_boundary_match)
- `app/inference/verification_config.py` (get_default_boundary_config)

### 2.2 边界权重计算（需求项5）

**审查点：**
- [ ] 权重体系（1.0/1.5/2.0/2.5/3.0）是否与临床重要性成正比
- [ ] `weighted_boundary_diff_rate` 的计算：`total_boundary_weight / max_possible_weight`，其中 `max_possible_weight` 取每个用例对应器官的最大权重，是否合理（假设所有用例都可能跨越最严重边界）
- [ ] `_compute_boundary_weight()` 和 `_compute_boundary_weight_from_engines()` 两个方法的逻辑差异和适用场景是否清晰

**关键文件：**
- `app/inference/consistency/verifier.py` (_compute_boundary_weight, _compute_boundary_weight_from_engines, _compute_boundary_stats)

### 2.3 严重程度分级（需求项6）

**审查点：**
- [ ] `boundary_severity` 的判定逻辑：`is_critical_boundary`（配置标记）或 `boundary_weight >= 2.0` 为 critical，`>= 1.0` 为 warning，其余为 low
- [ ] 配置中 `is_critical` 标记与 `weight` 值的一致性：是否存在 `is_critical=True` 但 `weight < 2.0` 的矛盾配置
- [ ] cardiac 器官的 `normal→mild` 边界 `is_critical=False`，是否与临床实践一致

**关键文件：**
- `app/inference/consistency/report.py` (ConsistencyPair.boundary_severity)
- `app/inference/verification_config.py` (BoundaryPairConfig)

---

## 审查维度三：幻觉检测

### 3.1 禁忌词检测（需求项7）

**审查点：**
- [ ] `_is_negated()` 的否定词窗口（±50字符）是否合理，中文医疗文本中否定词与术语的距离可能更大
- [ ] `_is_context_allowed()` 的窗口（±50字符）是否足够覆盖"甲状腺癌伴骨转移"等长距离上下文
- [ ] 禁忌词列表的完整性：是否覆盖了所有器官的常见不相关术语
- [ ] `allow_if_context` 机制是否可能被滥用（如"甲状腺癌伴肺癌"中"甲状腺"上下文允许了"肺癌"）
- [ ] 变体列表是否充分（如"肺癌"变体缺少"肺部恶性肿瘤"）

**关键文件：**
- `app/inference/hallucination_detector.py` (ForbiddenTermsDetector)
- `app/inference/verification_config.py` (get_default_forbidden_terms_config)

### 3.2 双引擎幻觉率差异（需求项8）

**审查点：**
- [ ] `hallucination_rate_diff = abs(rate_a - rate_b)` 使用绝对差异而非相对差异，在低幻觉率场景下是否敏感
- [ ] 幻觉率差异阈值 0.5% 是否与文档一致（07-数据标注与模型评估.md §4 要求"幻觉率差异 ≤ 0.5%"）
- [ ] 门控判定中条件通过时幻觉差异放宽倍数 2.0（即 1.0%），是否过于宽松

**关键文件：**
- `app/inference/consistency/verifier.py` (_compute_hallucination_stats)
- `app/inference/consistency/report.py` (ConsistencyReport.gate_status)

### 3.3 假阳性/假阴性统计（需求项9）

**审查点：**
- [ ] FalseStats 的 ground truth 依赖 `expected_hallucination` 字段，但测试用例中该字段是否普遍设置
- [ ] precision/recall/F1 的计算是否正确（验证公式）
- [ ] 器官维度的 FalseStats 是否有足够的样本量支撑统计意义
- [ ] 顶层 FalseStats 的 TP/FP/FN/TN 全为0，仅通过 `engine_a_stats`/`engine_b_stats` 子对象存储，是否造成混淆

**关键文件：**
- `app/inference/consistency/report.py` (FalseStats)
- `app/inference/consistency/verifier.py` (_compute_false_stats)
- `app/inference/consistency/organ_breakdown.py` (organ_false_stats)

---

## 审查维度四：性能验证

### 4.1 P50/P99延迟（需求项10）

**审查点：**
- [ ] `_percentile()` 函数实现是否正确（需验证最近邻插值 vs 线性插值）
- [ ] 延迟测量使用引擎返回的 `latency_ms`，是否包含网络开销
- [ ] P99 可靠性判定：`latency_p99_reliable = len(latencies) >= 30`，30个样本是否足够
- [ ] 错误用例的延迟是否被排除（`error is None` 过滤）

**关键文件：**
- `app/inference/consistency/verifier.py` (_compute_latency_stats)
- `app/inference/utils.py` (percentile)

### 4.2 延迟差异百分比（需求项11）

**审查点：**
- [ ] `latency_diff_pct` 使用 P99 延迟的相对差异 `|b_p99 - a_p99| / avg_p99 * 100`，是否合理
- [ ] 阈值 20% 是否与文档一致（06-开发环境与进度规划.md §7 要求"延迟差异 ≤ 20%"）
- [ ] 当 `avg_p99 == 0` 时的除零保护

**关键文件：**
- `app/inference/consistency/verifier.py` (_compute_latency_stats)

### 4.3 器官维度分解（需求项12）

**审查点：**
- [ ] `compute_organ_breakdown()` 是否覆盖所有4个器官
- [ ] 器官维度的 Wilson CI 计算是否在样本量不足时仍有效
- [ ] 器官维度的文本一致率阈值硬编码为 0.8，与全局 `text_consistency_with_grading()` 的动态阈值不一致

**关键文件：**
- `app/inference/consistency/organ_breakdown.py`

---

## 审查维度五：失败案例分析

### 5.1 追溯链生成（需求项13）

**审查点：**
- [ ] `generate_failure_analysis()` 的 trace_chain 是否包含完整的推理路径（输入→引擎A输出→引擎B输出→各检查步骤→建议）
- [ ] trace_chain 中 `engine_a_output`/`engine_b_output` 存储完整文本，可能导致报告过大
- [ ] 失败用例的筛选条件 `not p.grading_semantic_consistent or p.is_boundary_crossing or p.expected_vs_actual_boundary_crossing` 是否遗漏了其他失败类型

**关键文件：**
- `app/inference/consistency/failure_analysis.py`

### 5.2 失败原因归类（需求项14）

**审查点：**
- [ ] 归类维度（grading_failures / boundary_failures / extraction_failures / hallucination_failures）是否完整
- [ ] `extraction_failures` 和 `hallucination_failures` 仅在 trace_chain 中统计，不在 `by_organ` 中体现
- [ ] `overlap_count`（同时为 grading 和 boundary 失败）的统计是否有实际用途

**关键文件：**
- `app/inference/consistency/failure_analysis.py`

### 5.3 优化建议（需求项15）

**审查点：**
- [ ] `suggest_for_trace()` 的建议是否足够具体和可操作
- [ ] 建议的触发条件是否合理（如 `fp_fn_diff >= 5` 的硬编码阈值）
- [ ] 是否缺少针对特定器官的定制化建议

**关键文件：**
- `app/inference/consistency/failure_analysis.py` (suggest_for_trace, recommendations)

---

## 审查维度六：门控机制与文档一致性

### 6.1 门控阈值与文档对照

**审查点：**
- [ ] 代码中 `grading_consistency_rate=0.95` ↔ 文档"分级一致率≥95%" ✓
- [ ] 代码中 `clinical_boundary_diff_rate=0.01` ↔ 文档"临床决策边界差异率≤1%" ✓
- [ ] 代码中 `hallucination_rate_diff=0.005` ↔ 文档"幻觉率差异≤0.5%" ✓
- [ ] 代码中 `text_consistency_rate=0.90` ↔ 文档"完整文本一致率≥90%" ✓
- [ ] 代码中 `semantic_grading_consistency_rate=0.98` ↔ 文档"语义分级一致率≥98%" ✓
- [ ] 代码中 `phase2_boundary_ci_upper_threshold=0.02` ↔ 文档"95%置信区间上界≤2%" ✓
- [ ] 代码中 `phase2_min_cases_per_organ=300` ↔ 文档"每器官≥300例" ✓
- [ ] 代码中 `benchmark_thresholds.hallucination_increment=0.005` ↔ 文档"量化后幻觉率增量≤0.5%" ✓
- [ ] 代码中 `benchmark_thresholds.accuracy=0.89` ↔ 文档"准确率≥89%（基层）" ✓
- [ ] 代码中 `benchmark_thresholds.latency_p99_ms=3000.0` ↔ 文档"RTX 3060单次推理<3s" ✓

### 6.2 条件性门控规则与文档对照

**审查点：**
- [ ] 代码中 CONDITIONAL 判定条件是否与 01-系统架构设计.md §5.7 一致
- [ ] NMPA 声明文本格式是否符合注册材料要求
- [ ] 延迟差异>50%直接FAILED的规则是否在文档中有明确说明

### 6.3 验证阶段与文档对照

**审查点：**
- [ ] Phase1（Gate 0A）与 Phase2（Gate 0B）的阈值差异是否与文档一致
- [ ] Phase1 每器官≥100例 vs Phase2 每器官≥300例 的切换逻辑是否正确
- [ ] `verification_phase` 字段是否在运行时正确设置

---

## 审查维度七：测试代码有效性

### 7.1 单元测试覆盖度

**审查点：**
- [ ] test_consistency.py（1589行，20个测试类）的覆盖是否充分
- [ ] 边界条件测试：空输入、None值、未知器官、超长文本
- [ ] 幻觉检测的假阳性/假阴性测试是否有 ground truth 支撑
- [ ] 自适应阈值测试是否覆盖了边界情况

### 7.2 集成测试有效性

**审查点：**
- [ ] test_phase0a_consistency.py 的 Gate 标准层测试是否真正验证了门控标准
- [ ] `consistency_test_cases` fixture 的数据来源和代表性
- [ ] 集成测试中 `concurrency=1` 是否反映了真实并发场景
- [ ] 延迟测试仅用30个用例子集，是否具有统计意义

### 7.3 测试数据充分性

**审查点：**
- [ ] boundary_test_cases.py 生成的测试用例是否覆盖了所有边界对
- [ ] 常规用例（≥200）、边界用例（≥400）、高风险用例（≥100）的样本量是否满足
- [ ] 幻觉 ground truth 用例（4器官×8例=32例）是否足够支撑 F1 统计
- [ ] 测试用例的 prompt 是否具有临床代表性

---

## 审查维度八：代码质量与风格

### 8.1 代码健壮性

**审查点：**
- [ ] 除零保护：所有除法运算是否有分母为零的保护
- [ ] 空值处理：None/空字符串/空列表的防御性处理
- [ ] 异常处理：`_verify_single_case()` 的 try/except 是否充分
- [ ] 并发安全：配置单例的线程安全、检测器单例的双重检查锁定

### 8.2 可维护性

**审查点：**
- [ ] 配置硬编码 vs 外部化：边界对、禁忌词等配置是否可通过JSON外部化
- [ ] 模块解耦：consistency/ 子模块的职责划分是否清晰
- [ ] 日志规范：structlog 的使用是否一致

### 8.3 可扩展性

**审查点：**
- [ ] 新增器官的支持成本（需修改哪些文件）
- [ ] 新增检测策略的扩展方式
- [ ] 新增分级体系的适配难度

---

## 实施步骤

### Step 1: 逐文件深度审查实现代码
- 审查 `grading_definitions.py` — 分级提取正则完整性和边界情况
- 审查 `consistency/statistics.py` — 统计函数正确性
- 审查 `consistency/report.py` — 数据模型和门控逻辑
- 审查 `consistency/verifier.py` — 验证器核心逻辑
- 审查 `consistency/failure_analysis.py` — 失败分析逻辑
- 审查 `consistency/organ_breakdown.py` — 器官维度统计
- 审查 `hallucination_detector.py` — 幻觉检测5策略
- 审查 `verification_config.py` — 配置管理与默认值
- 审查 `benchmark.py` — 基准测试逻辑
- 审查 `drift_detector.py` — 漂移检测逻辑

### Step 2: 逐文件深度审查测试代码
- 审查 `test_consistency.py` — 测试覆盖度和断言有效性
- 审查 `test_hallucination_detector.py` — 幻觉检测测试
- 审查 `test_verification_config.py` — 配置测试
- 审查 `test_phase0a_consistency.py` — 集成测试门控标准
- 审查 `test_dual_engine_consistency.py` — 双引擎测试
- 审查 `boundary_test_cases.py` — 测试数据生成器

### Step 3: 文档一致性交叉验证
- 对照 01-系统架构设计.md §5.7 验证门控规则
- 对照 06-开发环境与进度规划.md §5/§7 验证配置参数
- 对照 07-数据标注与模型评估.md §4 验证一致性标准
- 识别代码与文档的不一致之处

### Step 4: 运行现有测试验证功能正确性
- 运行单元测试套件
- 分析测试结果，识别失败或跳过的测试
- 验证测试覆盖率

### Step 5: 编写审查报告
- 汇总所有发现的问题，按严重程度分级（Critical/Warning/Low）
- 对每个问题给出具体的改进建议
- 标注需要更新核心参考文档的条目
- 输出结构化审查报告

---

## 预期产出

1. **审查报告**：包含所有维度的发现，按 Critical/Warning/Low 分级
2. **代码修复**：对发现的问题进行修复
3. **文档更新建议**：对需要更新的核心参考文档提出修改建议
4. **测试增强**：对测试覆盖不足的区域补充测试用例
