# LLM轨道验证机制有效性审查 & Phase 0A实施审查报告

**审查日期**: 2026-05-10
**审查范围**: LLM推理轨道全部验证环节 + Phase 0A实施
**测试基线**: 370/370 单元测试通过 (66.11s)
**审查原则**: 独立、客观、深入、批判性

---

## 一、15项验证机制逐项审查发现

### A. 基础一致性验证

#### A1. 分级提取验证 — 风险等级: 中

**发现**:
1. **正则覆盖不足**: `_GRADING_PATTERNS` 每器官仅1-2个正则，无法覆盖临床常见格式变体：
   - 缺少 `TI-RADS：4a`（冒号分隔）、`TI-RADS4a`（无空格）、`甲状腺TI-RADS 4a级`（"级"替代"类"）
   - 心脏LVEF仅匹配 `LVEF: XX%`，缺少 `左室射血分数55%`、`EF 55%`、`LVEF约55%`
2. **子分级提取限制**: `(\d[a-z]?)` 仅匹配1位字母后缀，无法处理 `4a-4b` 等复合分级
3. **多分级冲突**: `extract_grading` 仅取第一个匹配，当文本含多个分级时可能取错
4. **中文正则过于宽松**: `甲状腺.*?(\d[a-z]?)\s*类` 的 `.*?` 可能匹配到非分级上下文中的数字

**测试覆盖**: 9个测试用例覆盖基本格式，但缺少上述变体和边界场景

#### A2. 双引擎分级一致性 — 风险等级: 高

**发现**:
1. **严格相等 vs 语义等价矛盾**: `grading_consistent` 使用 `==` 严格字符串比较，但 `grading_semantic_equivalent` 已实现语义等价（如"4"与"4a"等价）。`passed` 判定使用 `grading_consistency_rate`（严格），而 `semantic_grading_consistency_rate`（语义）被计算但未纳入通过判定
2. **一致性率分母问题**: 使用 `len(pairs)` 而非有效分级对数。当大量提取失败时，失败对被计入分母但分子为0，稀释了一致性率
3. **影响**: 甲状腺"4"vs"4a"被判为不一致，可能导致一致性率虚低，无法通过Gate 0A

#### A3. 文本一致率 — 风险等级: 高

**发现**:
1. **阈值不可达**: `text_consistency_rate` 使用 `strip()` 后严格字符串相等，阈值90%。LLM输出在措辞、格式、标点上几乎不可能完全一致，该指标实际可达率可能<5%
2. **Jaccard系数不适配中文**: `text_similarity` 使用字符级Jaccard系数，中文单字符信息量远大于英文，"甲状腺结节TI-RADS 4a类"与"甲状腺占位TI-RADS 4a类"仅差2字但临床含义差异大
3. **`text_similarity_avg` 未纳入判定**: 被计算但未用于 `passed`，与 `text_consistency_rate` 的关系未明确

---

### B. 临床边界跨越检测

#### B4. 边界跨越识别 — 风险等级: 高

**发现**:
1. **不处理语义等价**: `_grades_cross_clinical_boundary` 使用 `from_grade == grade_a` 精确匹配。当引擎输出"4a"而边界配置为"4"→"5"时，"4a"→"5"的跨越不会被检测到
2. **未知器官安全缺失**: 未知器官返回空边界列表，`is_boundary_crossing` 返回False——应视为安全机制缺失，至少应记录警告
3. **双向权重不对称**: 边界对配置双向匹配（from↔to），但权重是单向的。3→5权重3.0，5→3是否也应为3.0未明确

#### B5. 边界权重计算 — 风险等级: Critical

**发现**:
1. **🔴 `weighted_boundary_diff_rate` 恒为1.0的逻辑BUG**:
   ```python
   # consistency.py L533-534
   max_possible_weight = sum(p.boundary_weight for p in pairs if p.is_boundary_crossing)
   report.weighted_boundary_diff_rate = total_boundary_weight / max_possible_weight
   ```
   `max_possible_weight` 与 `total_boundary_weight` 完全相同（都是边界跨越对的权重之和），导致结果恒为1.0。正确做法应使用所有可能边界对的权重之和作为分母
2. **无expected_grading时权重缺失**: `_compute_boundary_weight` 仅在 `expected_grading` 存在时计算，否则 `boundary_weight=0.0`、`is_critical_boundary=False`
3. **权重仅基于expected→actual**: 未考虑双引擎间分级的相对跨越权重

#### B6. 严重程度分级 — 风险等级: 高

**发现**:
1. **无expected_grading时严重程度失准**: `boundary_severity` 依赖 `is_critical_boundary` 和 `boundary_weight`，无expected时两者均为默认值，所有边界跨越都被判为"low"
2. **权重边界条件**: `boundary_weight >= 2.0` 判为critical，但4→5权重恰好为2.0——踩在边界上
3. **严重程度未纳入passed判定**: critical边界跨越仅有计数，无独立否决权。即使出现critical边界跨越，只要比率≤1%仍可通过

---

### C. 幻觉检测

#### C7. 禁忌词检测 — 风险等级: 中

**发现**:
1. **精确匹配局限**: `text.find()` 精确匹配+预定义变体，无法检测拼写变体、缩写、英文混写
2. **否定词窗口**: `_is_negated` 检查前后25字符窗口，中文语境中否定词与术语可能相隔更远
3. **乳腺"骨转移"误报**: 配置标注"乳腺骨转移需特殊处理"，但检测器未实现特殊处理逻辑
4. **结节大小正则误匹配**: `r'(?:结节|占位)?.*?(\d+(?:\.\d+)?)\s*(cm|mm)'` 中 `(?:结节|占位)?` 可选，导致"患者身高170cm"被误匹配为结节大小170cm

#### C8. 双引擎幻觉率差异 — 风险等级: 中

**发现**:
1. **仅绝对差异**: `hallucination_rate_diff = abs(rate_a - rate_b)`，未考虑基数效应。0.1% vs 0.6%的0.5%差异在低基数下可能显著
2. **阈值体系不一致**: 差异阈值0.5%与绝对阈值（warning 1%, critical 2%）的关系未明确
3. **同一检测器偏差**: 两引擎使用同一检测器，检测器自身的FP/FN会同时影响两侧

#### C9. 假阳性/假阴性统计 — 风险等级: 中

**发现**:
1. **TP/FP定义问题**: `any_halluc = a_halluc or b_halluc`——任一引擎检测到即视为阳性，与"双引擎差异"语义不一致
2. **ground truth不足**: 仅5例幻觉标注用例（`generate_hallucination_ground_truth_cases`），F1统计可靠性存疑
3. **默认值问题**: `expected_hallucination` 默认为False，大量TN可能稀释统计

---

### D. 性能验证

#### D10. P50/P99延迟 — 风险等级: 低

**发现**:
1. **未排除超时结果**: 延迟统计仅排除error，超时但未抛异常的结果可能引入异常值
2. **P99小样本不稳定**: 样本量<100时P99不稳定
3. **冷/热启动未区分**: vLLM的KV cache影响延迟分布

#### D11. 延迟差异百分比 — 风险等级: 低

**发现**:
1. **仅基于P50**: `latency_diff_pct` 使用P50，但P99才是临床关键指标
2. **未纳入passed判定**: 延迟差异20%阈值未纳入通过判定

#### D12. 器官维度分解 — 风险等级: 低

**发现**:
1. **器官明细不完整**: 缺少幻觉率差异、文本一致率分解
2. **器官级无独立判定**: 某器官一致性率极低不影响整体通过

---

### E. 失败案例分析

#### E13. 追溯链生成 — 风险等级: 低

**发现**:
1. **仅覆盖验证流程**: 未记录推理中间步骤（prompt构造、知识库检索结果）
2. **内存风险**: 完整文本存储，大量失败案例时内存占用高

#### E14. 失败原因归类 — 风险等级: 中

**发现**:
1. **分类不完整**: 仅 `grading_failures` 和 `boundary_failures`，`extraction_failures` 和 `hallucination_failures` 仅计数未独立归类
2. **重复计数**: 同一case可同时出现在 `inconsistent_cases` 和 `boundary_cases`，导致 `by_organ.total` 重复
3. **未区分引擎**: 不区分"引擎A错"vs"引擎B错"vs"两者都错"

#### E15. 优化建议 — 风险等级: 低

**发现**:
1. **规则硬编码**: 仅5条建议规则，无法覆盖所有失败模式
2. **优先级不合理**: `extract_grading_failed` 优先级最高，但临床最紧急的应是 `expected_vs_actual_boundary_crossing`

---

## 二、验证体系整体有效性评估

### 2.1 设计合理性

| 维度 | 评估 | 详情 |
|------|------|------|
| 通过判定逻辑 | ⚠️ 不完整 | `passed` 仅检查4指标，计算了10+指标未纳入（语义等价率、延迟差异、器官级、critical计数） |
| 阈值来源 | ⚠️ 待验证 | ≥95%/≤1%/≤0.5%阈值缺乏统计学依据说明，疑似经验设定 |
| 条件性门控 | ❌ 不符合 | 架构设计要求三档（通过/NMPA声明/不通过），代码仅两档 |
| 语义等价 | ❌ 矛盾 | 实现了语义等价函数但未在关键判定路径中使用 |

### 2.2 数据代表性与覆盖范围

| 维度 | 评估 | 详情 |
|------|------|------|
| 测试用例数量 | ⚠️ 部分达标 | 310例（甲状腺100/肝脏100/心脏100），**缺少乳腺** |
| 边界病例 | ⚠️ 需调整 | 440例但分布未严格按07文档要求（TI-RADS/LI-RADS/BI-RADS 3/4类各≥100例） |
| 对抗性用例 | ⚠️ 不足 | 35例对抗性用例偏少 |
| 幻觉ground truth | ❌ 严重不足 | 仅5例标注，F1统计不可靠 |

### 2.3 与实际应用场景关联性

| 维度 | 评估 | 详情 |
|------|------|------|
| Mock vs 真实引擎 | ⚠️ 有效性受限 | 单元测试全用Mock，集成测试需真实服务且默认跳过 |
| 温度参数 | ⚠️ 不一致 | 验证用temperature=0.05，实际部署可能不同 |
| Prompt格式 | ⚠️ 差异大 | 验证用简化prompt，实际用RAG增强prompt |

### 2.4 效率、可扩展性、可重复性

| 维度 | 评估 | 详情 |
|------|------|------|
| K折交叉验证 | ⚠️ 耗时大 | 5折=10次完整验证 |
| 并发控制 | ⚠️ 硬编码 | `concurrency=4` 未根据引擎负载调整 |
| 可重复性 | ⚠️ 受限 | seed=42保证数据分割可重复，但LLM输出非确定性 |
| 漂移检测基线 | ⚠️ 单机 | 文件系统存储，未考虑分布式 |

---

## 三、Phase 0A实施审查

### 3.1 设计规范一致性

| 设计要求 | 状态 | 差异 |
|---------|------|------|
| InferenceEngine Protocol | ✅ | protocols.py 实现4个接口方法 |
| compact默认llama.cpp (port 8001) | ✅ | llama_cpp.py 实现 |
| full默认vLLM (port 8002) | ✅ | vllm.py 实现 |
| vLLM gpu_memory_utilization=0.90 | ❌ 缺失 | 代码中未设置此参数 |
| Gate 0A条件性门控三档规则 | ❌ 不符合 | 仅两档（通过/不通过） |
| DEPLOYMENT_PROFILE联动校验 | ❌ 缺失 | factory.py 无Profile校验 |
| compact模式64GB内存预算 | ⚠️ 部分 | gpu_monitor.py有SystemMemoryProfiler但未集成到验证流程 |

### 3.2 代码质量

**健壮性**:
- 🔴 `weighted_boundary_diff_rate` 恒为1.0（Critical BUG）
- 🔴 无expected_grading时边界权重/严重程度判定失准
- 🔴 `text_consistency_rate` 90%阈值对LLM输出不可达

**可维护性**:
- `consistency.py` 987行，`_compute_report` ~180行过长
- `verification_config.py` 766行，默认配置占大量篇幅应抽取到JSON
- 幻觉检测器5种策略耦合在单文件

**可扩展性**:
- 新增器官需修改4处（`_GRADING_PATTERNS`、`_GRADE_HIERARCHY`、边界配置、禁忌词配置），违反开闭原则
- 新增检测策略需修改枚举和主检测器

**性能**:
- `_grades_cross_clinical_boundary` O(n)线性扫描，应使用集合查找
- `text_similarity` 对每个字符创建集合，长文本内存开销大

### 3.3 代码风格

- dataclass vs Pydantic: 有注释说明设计决策，合理
- `_grades_cross_clinical_boundary` 以下划线开头但被test直接导入，违反封装
- 部分属性（如`grading_consistent`）缺少语义等价含义的文档
- 类型标注混用Python 3.10+语法

### 3.4 幻觉检测器额外发现

| 问题 | 严重程度 | 位置 |
|------|---------|------|
| breast器官在ContextAwareDetector中未覆盖 | 高 | hallucination_detector.py L285 |
| breast器官在NumericalValidityDetector中数值检测被跳过 | 高 | hallucination_detector.py L206-212 |
| 结节大小正则误匹配非结节数值（如身高170cm） | 高 | hallucination_detector.py L197-199 |
| "不除外"在排除性表述中语义方向错误 | 中 | hallucination_detector.py L162 |
| 置信度计算仅反映平均严重程度 | 中 | hallucination_detector.py L436-444 |
| LVEF 50%被心脏过度反应正则视为"轻度异常" | 中 | hallucination_detector.py L276 |
| ContradictoryGradingDetector把"4"与"4a"误报为矛盾 | 中 | hallucination_detector.py L122-135 |

### 3.5 基准测试与漂移检测额外发现

| 问题 | 严重程度 | 位置 |
|------|---------|------|
| determine_winner空列表未处理 | 高 | benchmark.py L301 |
| 降级策略过于激进（无安全底线） | 中 | benchmark.py L301 |
| 幻觉指标不一致（增量vs绝对率） | 中 | benchmark.py vs analyze_threshold_sensitivity |
| Wilson区间critical阈值使用非标准2*margin | 中 | drift_detector.py L282 |
| max(Wilson, Sigma)降低检测灵敏度 | 中 | drift_detector.py L286 |
| test_organ_isolation样本量不足(20<30)导致测试无效 | 高 | test_drift_detector.py L326 |

---

## 四、完整BUG清单（按严重程度排序）

### Critical (阻塞Gate 0A)

| # | BUG | 位置 |
|---|-----|------|
| 1 | `weighted_boundary_diff_rate` 恒为1.0 | consistency.py L533-534 |
| 2 | 条件性门控缺少中间档（90%-95%须NMPA声明） | consistency.py L229-238 |

### High (影响验证有效性)

| # | BUG | 位置 |
|---|-----|------|
| 3 | 无expected_grading时boundary_weight=0导致severity判定失准 | consistency.py L486-513 |
| 4 | `text_consistency_rate` 90%阈值对LLM输出不可达 | consistency.py L556-561 |
| 5 | `grading_consistent` 使用严格相等忽略语义等价 | consistency.py L92-98 |
| 6 | breast器官ContextAwareDetector未覆盖 | hallucination_detector.py L285 |
| 7 | breast器官NumericalValidityDetector数值检测被跳过 | hallucination_detector.py L206-212 |
| 8 | 结节大小正则误匹配非结节数值 | hallucination_detector.py L197-199 |
| 9 | determine_winner空列表未处理 | benchmark.py L301 |
| 10 | test_organ_isolation样本量不足导致测试无效 | test_drift_detector.py L326 |

### Medium (影响验证严谨性)

| # | BUG | 位置 |
|---|-----|------|
| 11 | `_grades_cross_clinical_boundary` 不处理语义等价 | consistency.py L66-76 |
| 12 | 乳腺"骨转移"禁忌词未实现特殊处理 | verification_config.py L594-597 |
| 13 | FalseStats的TP/FP定义使用any_halluc | consistency.py L584-601 |
| 14 | "不除外"排除性表述语义方向错误 | hallucination_detector.py L162 |
| 15 | 置信度计算仅反映平均严重程度 | hallucination_detector.py L436-444 |
| 16 | LVEF 50%被过度反应正则视为"轻度异常" | hallucination_detector.py L276 |
| 17 | ContradictoryGradingDetector"4"vs"4a"误报 | hallucination_detector.py L122-135 |
| 18 | Wilson区间critical阈值非标准 | drift_detector.py L282 |
| 19 | 幻觉指标不一致（增量vs绝对率） | benchmark.py |
| 20 | 降级策略无安全底线 | benchmark.py L301 |

### Low (改善验证成熟度)

| # | BUG | 位置 |
|---|-----|------|
| 21 | 延迟差异仅基于P50 | consistency.py L632-635 |
| 22 | 器官明细缺少幻觉率差异分解 | consistency.py L637-674 |
| 23 | 失败原因by_organ重复计数 | consistency.py L726-741 |
| 24 | max(Wilson,Sigma)降低检测灵敏度 | drift_detector.py L286 |

---

## 五、核心参考文档一致性审查

### 5.1 与01-系统架构设计.md

| 要求 | 状态 | 行动 |
|-----|------|------|
| §5.7 InferenceEngine Protocol | ✅ | 保留 |
| §5.7 条件性门控三档规则 | ❌ | **需更新代码实现三档** |
| §5.7 vLLM与llama.cpp一致性验证 | ✅ | 保留，修复BUG后重新验证 |
| §5.7 compact模式llama.cpp常驻~3GB | ⚠️ | 需补充GPU显存验证测试 |

### 5.2 与06-开发环境与进度规划.md

| 要求 | 状态 | 行动 |
|-----|------|------|
| §5 INFERENCE_ENGINE配置 | ✅ | 保留 |
| §5 DEPLOYMENT_PROFILE联动校验 | ❌ | **需补充** |
| §5 VLLM_GPU_MEMORY_UTILIZATION=0.90 | ❌ | **需补充** |
| §7 Gate 0A-Tech P0子门控 | ⚠️ | 修复BUG后重新验证 |

### 5.3 与07-数据标注与模型评估.md

| 要求 | 状态 | 行动 |
|-----|------|------|
| §4 量化后幻觉率增量≤0.5% | ✅ | 保留 |
| §4 边界病例≥300例 | ⚠️ | 需调整用例分布 |
| §4 FunASR+vLLM+TensorRT显存碎片化<30% | ❌ | **需补充** |
| §4 持续运行≥4小时验证 | ❌ | **需补充** |

### 5.4 文档更新建议

以下场景代码实现优于文档设计，建议更新文档：

1. **语义等价机制**: 代码已实现 `grading_semantic_equivalent` 和 `_GRADE_HIERARCHY`，但文档未描述此机制——建议在01-系统架构设计.md中补充语义等价判定规则
2. **K折交叉验证**: 代码已实现 `cross_validate` 方法，文档未提及——建议在07-数据标注与模型评估.md中补充
3. **漂移检测**: 代码已实现完整的漂移检测体系（Wilson区间+Sigma规则），文档仅简单提及——建议在06-开发环境与进度规划.md中补充漂移检测策略
4. **自适应阈值**: 代码已实现 `compute_adaptive_threshold`，文档未提及——建议在07文档中补充

---

## 六、改进建议优先级排序

### P0 — 必须修复（阻塞Gate 0A通过）

1. **修复 `weighted_boundary_diff_rate` 恒为1.0**: 分母应使用所有可能边界对的权重之和
2. **实现条件性门控三档规则**: 替代当前两档，增加"条件性通过须NMPA声明"中间档
3. **修复无expected_grading时边界权重/严重程度判定**: 基于双引擎间分级差异计算权重
4. **重新评估 `text_consistency_rate`**: 改用语义相似度指标或降低阈值至合理水平

### P1 — 应该修复（影响验证有效性）

5. `grading_consistent` 改用语义等价判定
6. 乳腺"骨转移"禁忌词特殊处理
7. FalseStats的TP/FP定义改进（独立统计每引擎）
8. 幻觉ground truth标注扩充至≥50例
9. 延迟差异改用P99基准
10. 补充VLLM_GPU_MEMORY_UTILIZATION配置
11. breast器官ContextAwareDetector和NumericalValidityDetector支持
12. 结节大小正则增加前缀约束防止误匹配
13. determine_winner空列表保护+降级安全底线

### P2 — 建议改进（提升验证体系成熟度）

14. 分级提取正则模式扩展
15. 器官明细增加幻觉率差异、文本一致率分解
16. 失败原因细粒度分类
17. 边界跨越检测支持语义等价
18. DEPLOYMENT_PROFILE联动校验
19. 显存碎片化持续运行验证
20. 配置抽取为JSON/YAML外部文件
21. Wilson区间critical阈值改用z=2.576
22. test_organ_isolation修复样本量

---

## 七、审查结论

### 总体评估

LLM轨道验证体系的**架构设计合理**（策略模式、配置驱动、模板方法），**核心功能完整**（双引擎一致性、幻觉检测5策略、漂移检测、基准测试），但在**实现细节**上存在1个Critical级逻辑BUG和多个High级问题，导致验证结果的可信度受损。

### 关键风险

1. **`weighted_boundary_diff_rate` 恒为1.0** 使得加权边界差异率指标完全失效
2. **条件性门控仅两档** 与架构设计文档不一致，可能导致不合规的模型通过验证
3. **`text_consistency_rate` 90%阈值不可达** 可能导致验证永远无法通过
4. **语义等价已实现但未使用** 导致一致性率虚低

### 测试基线

- 370/370 单元测试通过
- 但测试覆盖存在盲区：breast器官幻觉检测、结节大小误匹配、Wilson区间数学正确性、drift_detector样本量不足

### 建议下一步

优先修复P0的4项问题，然后重新运行验证流程确认Gate 0A可通过性。P1问题应在Phase 0B前修复。
