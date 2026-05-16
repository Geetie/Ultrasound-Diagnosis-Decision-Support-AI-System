# LLM轨道验证机制有效性审查与Phase0A实施审查计划

## 审查范围

本次审查覆盖LLM轨道全部验证环节，包括5个核心源文件、8个测试文件、4个辅助脚本，对照5份核心设计文档进行一致性审查。

---

## 第一部分：验证机制有效性审查

### A. 基础一致性验证

#### A1. 分级提取验证

**审查对象**: `consistency.py#L280-306` (`extract_grading` + `_GRADING_PATTERNS`)

**审查要点**:
1. 正则模式覆盖度：当前4器官(thyroid/liver/cardiac/breast)各有1-2个模式，是否覆盖所有临床常见格式
2. 甲状腺模式 `TI[-_]?RADS\s*(\d[a-z]?)` — 是否遗漏"TI-RADS 4a类"→"4a"、"TI-RADS 4"→"4"等边界
3. 中文模式 `甲状腺.*?(\d[a-z]?)\s*类` — 贪婪匹配`.*?`是否可能跨句匹配导致错误提取
4. 心脏模式仅提取LVEF数值(`55`)，不返回分级标签(`normal/mild/moderate/severe`)，与`_GRADE_HIERARCHY`中心脏使用文字标签不一致
5. 乳腺BI-RADS模式与甲状腺TI-RADS模式结构相同，是否覆盖"BI-RADS 4a"等子分级
6. 无fallback机制：正则匹配失败返回None，但未尝试从上下文推断

**测试覆盖评估**: `test_consistency.py#L50-86` (TestExtractGrading) — 8个测试用例，覆盖基本格式但缺少：
- 多分级出现在同一段文本的冲突场景
- 分级出现在否定语境中（"排除TI-RADS 4类"）
- 分级与描述距离过远的场景
- 心脏LVEF提取后与分级标签的映射缺失测试

#### A2. 双引擎分级一致性

**审查对象**: `consistency.py#L117-125` (`ConsistencyPair.grading_consistent`) + `grading_semantic_equivalent`

**审查要点**:
1. 语义等价判定：`grading_semantic_equivalent`通过`_GRADE_HIERARCHY`将子分级映射到大类（4a→4, 4b→4），但设计文档要求"分级一致率≥95%"的定义是"TI-RADS/BI-RADS/ACR分级相同且UDT病种编码相同"——当前实现仅比较分级，未比较病种编码
2. 设计文档§5.7要求"语义分级一致率≥98%"，但代码中`semantic_grading_consistency_rate`仅作为报告字段计算，未纳入`gate_status`判定条件
3. `gate_status`的CONDITIONAL判定硬编码了0.90/0.02等阈值，未使用`ConsistencyThresholdConfig`中的配置值

**测试覆盖评估**: `test_consistency.py#L98-119` (TestGradingSemanticEquivalent) — 7个用例，覆盖基本等价判定但缺少：
- 未知器官的等价判定
- 同一大类内不同子分级(4a vs 4b)的等价判定与边界跨越判定的矛盾
- 分级为None时的等价判定（已覆盖）

#### A3. 文本一致率

**审查对象**: `consistency.py#L334-347` (`text_similarity`) + `_compute_report`中的text_consistency_rate计算

**审查要点**:
1. `text_similarity`使用字符级Jaccard系数，这是最简单的文本相似度度量，对医疗文本的语义相似度评估严重不足
   - "甲状腺结节TI-RADS 3类，建议6个月随访" vs "甲状腺结节TI-RADS 3类，建议定期复查" → Jaccard可能仅0.6-0.7，但语义完全一致
   - 设计文档要求"完整文本一致率≥90%"，但代码使用Jaccard≥0.60作为阈值（`text_consistency_rate`），与设计文档定义不一致
2. 设计文档定义"仅措辞差异但分级与编码相同仍视为一致"，当前Jaccard方法无法实现此语义
3. `text_similarity_avg`作为补充指标计算了平均Jaccard，但未纳入gate判定

**测试覆盖评估**: `test_consistency.py#L121-134` (TestTextSimilarity) — 4个用例，仅测试基本Jaccard行为，未测试医疗文本语义等价场景

---

### B. 临床边界跨越检测

#### B4. 边界跨越识别

**审查对象**: `consistency.py#L79-101` (`_grades_cross_clinical_boundary`) + `ConsistencyPair.is_boundary_crossing`

**审查要点**:
1. 边界配置完整性：`verification_config.py`中4器官共定义了28+6+12+22=68个边界对，覆盖了所有相邻和跳跃边界
2. 甲状腺边界对包含双向配置（3→4和4→3），设计合理
3. `_grades_cross_clinical_boundary`同时支持hierarchy级别匹配和直接字符串匹配的双路径，兼容性好
4. **潜在问题**：当grade_a="4", grade_b="4a"时，hierarchy中4→4, 4a→4，level相同，`is_boundary_crossing`会因`grade_a == grade_b`为False而跳过，但实际不应视为边界跨越——这是正确行为
5. **潜在问题**：`expected_vs_actual_boundary_crossing`属性检测每个引擎输出与expected_grading的差异，但仅检查是否跨越边界，未检查跨越方向（从低到高vs从高到低），临床意义不同

**测试覆盖评估**: `test_consistency.py#L177-284` — 11个边界测试用例，覆盖甲状腺/肝脏/心脏的基本边界场景，但缺少：
- 乳腺边界跨越测试
- 同一大类内子分级跨越(4a→4c)的权重计算测试
- expected_vs_actual与is_boundary_crossing的交叉场景

#### B5. 边界权重计算

**审查对象**: `consistency.py#L533-571` (`_compute_boundary_weight` + `_compute_boundary_weight_from_engines`)

**审查要点**:
1. 权重设计合理：3→5跳跃权重3.0 > 4→5权重2.0 > 3→4权重1.0，符合临床重要性
2. `_compute_boundary_weight`仅检查expected→actual方向，未检查反向跨越（expected=5, actual=3时，配置中5→3存在所以可以匹配）
3. `weighted_boundary_diff_rate`的分母使用`max_possible_weight`（所有案例的最大可能权重之和），这个归一化方式可能低估实际权重差异率——因为大多数案例不会跨越所有边界
4. 设计文档要求"临床决策边界差异率≤1%"，代码中`clinical_boundary_diff_rate`是简单计数比率（跨越数/总数），未使用加权比率作为gate判定条件

**测试覆盖评估**: `test_consistency.py#L733-766` (TestWeightedBoundaryDiffRate) — 2个用例，仅验证零值和非零值，未验证权重计算的正确性

#### B6. 严重程度分级

**审查对象**: `consistency.py#L142-151` (`ConsistencyPair.boundary_severity`)

**审查要点**:
1. 三级分级逻辑：critical(weight≥2.0或is_critical) → warning(weight≥1.0) → low
2. `is_critical_boundary`由配置决定，`boundary_weight≥2.0`也自动为critical，双重保障合理
3. **潜在问题**：当`is_critical_boundary=True`但`boundary_weight<1.0`时（配置不一致），仍判定为critical——这是安全的保守策略

---

### C. 幻觉检测

#### C7. 禁忌词检测

**审查对象**: `hallucination_detector.py#L71-120` (ForbiddenTermsDetector)

**审查要点**:
1. 禁忌词配置丰富：甲状腺18个、肝脏13个、心脏12个、乳腺13个，每个含变体列表
2. 否定词检测(`_is_negated`)：25字符窗口+正则模式双重检测，覆盖"未见肺癌""不考虑肺癌"等场景
3. `allow_if_context`机制：乳腺"骨转移"在包含"乳腺"上下文时跳过检测——但实现有缺陷：`term_config.allow_if_context in text`检查的是整个文本是否包含"乳腺"，而非检查禁忌词附近的上下文，可能产生假阴性
4. **严重问题**：禁忌词检测使用`text.find()`进行子串匹配，可能产生误匹配——如"前列腺"中的"前列"不会误匹配，但"胃腺癌"可能被"胃癌"变体匹配到
5. 设计文档要求"量化后幻觉率增量≤0.5%"，但禁忌词检测无法区分量化导致的幻觉vs全精度模型的幻觉——幻觉检测器检测的是输出中的幻觉，而非量化增量

**测试覆盖评估**: `test_hallucination_detector.py#L20-65` (TestForbiddenTermsDetector) — 6个用例，覆盖基本检测和否定词，但缺少：
- `allow_if_context`的测试（仅在TestCustomConfigDetection中间接测试）
- 变体检测的假阳性场景
- 多禁忌词同时出现的场景

#### C8. 双引擎幻觉率差异

**审查对象**: `consistency.py#L607-620` (幻觉统计) + `ConsistencyReport.hallucination_rate_diff`

**审查要点**:
1. 幻觉率差异计算为`abs(rate_a - rate_b)`，简单绝对差异
2. 设计文档要求"幻觉率差异≤0.5%"，代码中`ConsistencyThresholdConfig.hallucination_rate_diff=0.005`，一致
3. **潜在问题**：幻觉率差异仅计算两引擎间的差异，未计算与金标准的差异——如果两引擎都有高幻觉率但差异小，gate仍可通过
4. `hallucination_rate_warning=0.01`和`hallucination_rate_critical=0.02`是绝对阈值，用于漂移检测，但未纳入gate判定

#### C9. 假阳性/假阴性统计

**审查对象**: `consistency.py#L643-675` (FalseStats计算) + `FalseStats`类

**审查要点**:
1. FalseStats计算将两引擎独立统计（每个案例贡献2个统计点），这导致样本量翻倍但非独立
2. **设计问题**：当`expected_hallucination=False`时，引擎检测出幻觉算FP，未检测出算TN；当`expected_hallucination=True`时，检测出算TP，未检测出算FN——但`expected_hallucination`字段在测试用例中默认为False，大多数案例都没有幻觉标注，导致TN占主导，precision/recall可能失真
3. `generate_hallucination_ground_truth_cases()`仅5个用例，远不足以可靠评估FP/FN率
4. 设计文档要求"严重幻觉率≤0.5%"，但FalseStats未区分严重幻觉vs轻微幻觉

**测试覆盖评估**: `test_consistency.py#L551-593` (TestFalseStats) — 7个用例，仅测试数学计算正确性，未测试与实际幻觉检测的集成

---

### D. 性能验证

#### D10. P50/P99延迟

**审查对象**: `consistency.py#L677-701` (延迟统计) + `benchmark.py#L203-208`

**审查要点**:
1. 百分位数计算使用自定义`_percentile`函数，需验证其实现是否与标准百分位数定义一致
2. 延迟统计仅排除error案例，但未排除超时案例——超时案例的latency_ms可能为超时值，会显著拉高P99
3. 设计文档要求"推理延迟<3s"和"2并发P99<8s"，但代码中`BenchmarkThresholdConfig.latency_p99_ms=3000.0`，与设计文档的8s不一致
4. 器官维度分解中延迟统计正确按器官分组

#### D11. 延迟差异百分比

**审查对象**: `consistency.py#L698-701`

**审查要点**:
1. `latency_diff_pct = abs(p99_b - p99_a) / p99_a * 100`，基于P99延迟计算
2. 设计文档要求"推理延迟差异≤20%"，代码中`ConsistencyThresholdConfig.latency_diff_pct=0.20`，一致
3. **潜在问题**：当p99_a接近0时，差异百分比可能极大，需要分母保护

#### D12. 器官维度分解

**审查对象**: `consistency.py#L703-748`

**审查要点**:
1. 器官分解覆盖所有统计维度（一致性、边界、幻觉、延迟），设计完整
2. **缺失**：器官分解中未包含FalseStats——无法按器官分析FP/FN
3. **缺失**：器官分解中未包含加权边界差异率
4. 设计文档要求"按thyroid/liver/cardiac分别统计"，代码覆盖了4个器官（含breast），超出要求

---

### E. 失败案例分析

#### E13. 追溯链生成

**审查对象**: `consistency.py#L822-887` (trace_chain生成)

**审查要点**:
1. 追溯链包含完整的推理路径：输入→分级提取→一致性检查→边界检查→幻觉检查，每步记录stage/rule/detail/severity
2. 追溯链为每个失败案例生成，包含两引擎的完整输出文本，便于人工审查
3. **缺失**：追溯链未记录幻觉检测的具体策略和触发词——仅记录"engine_a output flagged as hallucination"，未说明是哪个策略检测到的
4. **缺失**：追溯链未记录分级提取的正则匹配过程——仅记录"extract_grading_failed"，未说明尝试了哪些模式

#### E14. 失败原因归类

**审查对象**: `consistency.py#L779-912` (_generate_failure_analysis)

**审查要点**:
1. 分类维度：grading_failures / boundary_failures / critical_boundary_failures / extraction_failures / hallucination_failures
2. **潜在问题**：`extraction_failures`和`hallucination_failures`仅在trace_chain中统计，未在summary的total_failures中体现——total_failures仅统计inconsistent_cases + boundary_cases的去重数
3. 按器官统计失败使用集合去重，避免同一案例被重复计数，设计合理

#### E15. 优化建议

**审查对象**: `consistency.py#L889-911` (recommendations生成)

**审查要点**:
1. 4条建议规则：假阳性高→优化阈值、假阴性高→增强召回、边界差异率偏高→专项优化、提取失败→扩展正则
2. **缺失**：未根据器官维度给出针对性建议——仅"XX器官存在N例边界跨越"
3. **缺失**：未给出延迟优化建议
4. **缺失**：未给出幻觉检测策略级别的建议（如"禁忌词检测假阳性率高，建议增加上下文窗口"）

---

## 第二部分：与核心设计文档的一致性审查

### 关键偏差清单

| # | 设计文档要求 | 代码实现 | 偏差等级 | 建议 |
|---|---|---|---|---|
| 1 | 分级一致率定义含"UDT病种编码相同" | 仅比较分级，未比较病种编码 | **高** | 需增加病种编码比较或明确声明当前为分级级一致率 |
| 2 | 语义分级一致率≥98%纳入gate判定 | 仅作为报告字段，未纳入gate | **高** | 需纳入gate_status判定条件 |
| 3 | 完整文本一致率≥90%（措辞差异但分级相同视为一致） | 使用Jaccard≥0.60，与语义一致率定义不符 | **高** | 需重新定义text_consistency的计算方式或修改阈值 |
| 4 | 临床决策边界差异率≤1%（95%CI上界≤2%） | clinical_boundary_diff_rate为简单计数比率，无置信区间计算 | **高** | 需增加置信区间计算 |
| 5 | 采样参数temperature=0 | ConsistencyVerifier使用temperature=0.05 | **中** | 需改为0或论证0.05的等价性 |
| 6 | 边界病例≥300例（阶段二） | boundary_test_cases.py每器官110例，总计440例，满足 | 无 | — |
| 7 | 幻觉率差异≤0.5% | ConsistencyThresholdConfig.hallucination_rate_diff=0.005 | 无 | — |
| 8 | 量化后幻觉率增量≤0.5% | BenchmarkResult.quantized_hallucination_increment计算正确 | 无 | — |
| 9 | 推理延迟<3s | BenchmarkThresholdConfig.latency_p99_ms=3000.0 | 无 | — |
| 10 | 2并发P99<8s | 代码中无此阈值配置 | **中** | 需增加2并发场景的P99阈值 |
| 11 | Gate判定CONDITIONAL阈值应使用配置 | 硬编码0.90/0.02等值 | **中** | 需使用ConsistencyThresholdConfig |
| 12 | 心脏分级应返回normal/mild/moderate/severe | extract_grading返回LVEF数值(如"55") | **高** | 需增加LVEF→分级的映射逻辑 |
| 13 | 禁忌词allow_if_context应检查局部上下文 | 检查整个文本是否包含context词 | **中** | 需改为检查禁忌词附近的上下文窗口 |

---

## 第三部分：Phase0A实施情况审查

### 审查维度1：设计规范一致性

**已实现**:
- InferenceEngine Protocol接口定义 ✓
- 双引擎一致性验证框架 ✓
- 边界跨越检测与权重配置 ✓
- 幻觉检测5策略架构 ✓
- 验证配置管理（Pydantic BaseModel） ✓
- K折交叉验证 ✓
- 漂移检测器（SPC + Wilson区间） ✓
- 基准测试框架（含敏感性分析） ✓

**未实现/不完整**:
- 分级一致率定义缺少病种编码比较 ✗
- 语义分级一致率未纳入gate判定 ✗
- 置信区间计算缺失 ✗
- 心脏LVEF→分级映射缺失 ✗
- temperature=0的采样参数要求未满足 ✗

### 审查维度2：代码质量

**优点**:
- 模块化设计清晰：consistency/hallucination_detector/verification_config/drift_detector/benchmark职责分明
- 数据模型选择合理：纯数据容器用dataclass，需验证/序列化用Pydantic
- 异步并发设计：asyncio.Semaphore控制并发，asyncio.gather收集结果
- 日志记录完善：structlog结构化日志，关键操作均有记录
- 测试数据版本管理：test_data_version贯穿所有模块

**问题**:
- `get_verification_config()`全局单例非线程安全（无锁保护），而`get_default_detector()`使用了双重检查锁
- `_compute_report`方法过长（~170行），职责过多，建议拆分
- `text_similarity`的Jaccard系数对医疗文本语义相似度评估不足
- `FalseStats`的TP/FP/FN/TN统计将两引擎独立计数导致样本非独立

### 审查维度3：代码风格

**优点**:
- 类型注解完整（typing模块使用规范）
- docstring覆盖主要类和方法
- 命名约定一致（snake_case）
- 枚举类使用规范（str, Enum）

**问题**:
- 部分docstring使用中文，部分使用英文，不一致
- `_GRADE_HIERARCHY`和`_GRADING_PATTERNS`在consistency.py和hallucination_detector.py中重复定义
- `detect_hallucination`在consistency.py中作为兼容性别名存在，增加了维护负担

### 审查维度4：功能正确性

**关键功能缺陷**:
1. 心脏LVEF提取返回数值而非分级标签，导致`grading_semantic_equivalent`无法正确工作
2. `gate_status`的CONDITIONAL判定硬编码阈值，与配置系统不一致
3. `text_consistency_rate`使用Jaccard≥0.60，与设计文档"完整文本一致率≥90%"定义不符
4. 禁忌词`allow_if_context`检查全局文本而非局部上下文
5. FalseStats统计将两引擎独立计数，统计效力评估可能失真

---

## 第四部分：实施步骤

### 步骤1：修复心脏分级提取（高优先级）
- 在`extract_grading`中增加LVEF→分级映射逻辑
- 或在`_GRADE_HIERARCHY`中增加LVEF数值范围到分级标签的映射
- 更新测试用例

### 步骤2：修复gate判定与配置一致性（高优先级）
- 将`gate_status`中CONDITIONAL的硬编码阈值替换为`ConsistencyThresholdConfig`中的配置值
- 将`semantic_grading_consistency_rate`纳入gate判定条件
- 增加置信区间计算（二项分布Wilson区间）

### 步骤3：修复文本一致率计算（高优先级）
- 重新定义`text_consistency_rate`：当两引擎分级一致且无幻觉时视为文本一致
- 或修改Jaccard阈值以匹配设计文档要求
- 更新`ConsistencyThresholdConfig.text_consistency_rate`默认值

### 步骤4：修复采样参数（中优先级）
- 将`ConsistencyVerifier`中temperature从0.05改为0
- 在`BenchmarkResult`中记录采样参数

### 步骤5：修复禁忌词上下文检测（中优先级）
- 将`allow_if_context`检查改为局部上下文窗口检查（禁忌词前后N个字符）
- 增加相关测试用例

### 步骤6：增加置信区间计算（中优先级）
- 在`ConsistencyReport`中增加边界差异率的95%置信区间
- 实现Wilson区间计算
- 更新gate判定使用置信区间上界

### 步骤7：消除重复定义（低优先级）
- 将`_GRADING_PATTERNS`和`_GRADE_HIERARCHY`提取为共享模块
- 删除`consistency.py`中的`detect_hallucination`兼容性别名

### 步骤8：增加2并发P99阈值（低优先级）
- 在`BenchmarkThresholdConfig`中增加2并发场景的P99阈值
- 更新基准测试框架支持并发场景测试

### 步骤9：更新核心参考文档（如需要）
- 如果代码实现中有更优实践，更新设计文档以保持一致
- 特别关注：分级一致率定义、文本一致率定义、采样参数规范

---

## 审查结论预判

**整体评价**：LLM轨道验证框架的架构设计合理，模块化程度高，覆盖了设计文档要求的主要验证维度。但在以下方面存在显著偏差需要修复：

1. **分级一致率定义偏差**（高）：缺少病种编码比较，与设计文档定义不一致
2. **心脏分级提取缺陷**（高）：LVEF数值未映射为分级标签
3. **文本一致率计算偏差**（高）：Jaccard方法与设计文档语义定义不符
4. **gate判定配置不一致**（中）：CONDITIONAL阈值硬编码
5. **置信区间缺失**（中）：边界差异率无统计推断能力

**建议优先级**：步骤1-3为高优先级，应在Phase0A验证前修复；步骤4-6为中优先级，应在Gate0A前修复；步骤7-8为低优先级，可在后续迭代中完成。
