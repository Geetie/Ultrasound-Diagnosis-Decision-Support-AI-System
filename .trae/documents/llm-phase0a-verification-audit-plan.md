# LLM轨道Phase0A验证机制有效性审查计划

## 审查目标

对LLM轨道Phase0A阶段的全部验证环节进行系统性、独立、深入且批判性的有效性审查，覆盖5大验证维度（基础一致性、临床边界、幻觉检测、性能验证、失败分析），对照3份核心参考文档进行一致性审查，并评估设计规范、代码质量、代码风格、功能正确性4个关键维度。

---

## 第一阶段：验证机制系统性阅读与理解

### 步骤1：阅读全部LLM测试代码
- **目标**：系统性阅读所有LLM相关测试文件，理解测试覆盖范围
- **文件清单**：
  - `tests/unit/llm/test_consistency.py` — 一致性验证单元测试
  - `tests/unit/llm/test_verification_config.py` — 验证配置单元测试
  - `tests/unit/llm/test_hallucination_detector.py` — 幻觉检测单元测试
  - `tests/unit/llm/test_drift_detector.py` — 漂移检测单元测试
  - `tests/unit/llm/test_benchmark.py` — 基准测试单元测试
  - `tests/unit/llm/test_inference.py` — 推理引擎单元测试
  - `tests/unit/llm/test_factory.py` — 引擎工厂单元测试
  - `tests/integration/llm/test_dual_engine_consistency.py` — 双引擎一致性集成测试
  - `tests/integration/llm/test_phase0a_consistency.py` — Phase0A门控集成测试
  - `test_verification_modules.py` — 根级验证模块测试
  - `tests/fixtures/boundary_test_cases.py` — 边界测试用例fixture
- **验证**：输出每个测试文件的测试函数列表、断言数量、覆盖的验证维度

### 步骤2：阅读全部LLM实现代码
- **目标**：理解验证机制的完整实现
- **文件清单**：
  - `app/inference/consistency/verifier.py` — 核心一致性验证器
  - `app/inference/consistency/statistics.py` — 统计分析
  - `app/inference/consistency/report.py` — 报告数据模型
  - `app/inference/consistency/failure_analysis.py` — 失败分析
  - `app/inference/consistency/organ_breakdown.py` — 器官维度分解
  - `app/inference/consistency/serialization.py` — 序列化
  - `app/inference/verification_config.py` — 验证配置
  - `app/inference/hallucination_detector.py` — 幻觉检测器
  - `app/inference/grading_definitions.py` — 分级定义
  - `app/inference/benchmark.py` — 基准测试
  - `app/inference/drift_detector.py` — 漂移检测
  - `app/inference/vram_fragmentation.py` — VRAM碎片化
  - `app/inference/gpu_monitor.py` — GPU监控
  - `app/inference/base.py` — 推理引擎基类
  - `app/inference/protocols.py` — Protocol接口
  - `safety_gateway/checker/consistency_phase.py` — 安全网关一致性检查
- **验证**：输出每个模块的核心函数/类、输入输出、依赖关系

### 步骤3：阅读验证数据与配置
- **目标**：理解测试数据的代表性与覆盖范围
- **文件清单**：
  - `app/inference/data/default_verification_config.json` — 默认验证配置
  - `app/inference/data/consistency_test_cases.json` — 一致性测试用例(415例)
  - `app/inference/data/boundary_config.json` — 边界配置
  - `app/inference/data/forbidden_terms.json` — 禁忌词配置
  - `scripts/consistency_verify.py` — 一致性验证CLI
  - `scripts/continuous_verify.py` — 持续验证流水线
  - `scripts/start_dual_engine_test.py` — 双引擎测试启动
  - `scripts/model_benchmark.py` — 模型基准测试
- **验证**：输出数据统计（用例数/器官分布/边界覆盖/幻觉标注比例）

---

## 第二阶段：五大验证维度有效性审查

### A. 基础一致性验证

#### A1. 分级提取验证
- **审查项**：`grading_definitions.py`中`extract_grading`和`extract_grading_with_conflict`的提取逻辑
- **审查要点**：
  1. 正则模式是否覆盖所有分级格式（TI-RADS/LI-RADS/BI-RADS/LVEF）
  2. 冲突分级检测（如同时出现TI-RADS 3类和5类）的处理是否完备
  3. 心脏LVEF数值→分级映射的边界值处理（如LVEF=50%属于normal还是mild）
  4. 提取失败的降级策略是否合理
- **对照文档**：01-系统架构设计.md §5.7（分级一致率≥95%）
- **验证**：运行单元测试，检查提取覆盖率

#### A2. 双引擎分级一致性
- **审查项**：`ConsistencyVerifier._compute_basic_stats`和`ConsistencyPair.grading_consistent`
- **审查要点**：
  1. `grading_consistent`使用严格相等（`==`），是否应考虑语义等价
  2. `grading_semantic_consistent`的语义等价定义（同大类±1级）是否合理
  3. 一致率计算是否排除了提取失败的案例（当前未排除，可能虚低）
  4. Wilson置信区间的应用是否正确（用于边界差异率，但未用于分级一致率）
- **对照文档**：07-数据标注与模型评估.md §4（分级一致率≥95%、全文一致率≥90%）
- **验证**：构造边界用例验证计算逻辑

#### A3. 文本一致率
- **审查项**：`statistics.py`中`text_similarity`和`text_consistency_with_grading`
- **审查要点**：
  1. bigram/trigram Jaccard + Levenshtein的混合策略是否适合中文医学文本
  2. 文本截断到500字符是否丢失关键信息
  3. `text_consistency_with_grading`中分级语义等价时阈值0.5 vs 不等价时0.8的设计合理性
  4. `text_consistency_rate_strict`使用0.8阈值，与`text_consistency_with_grading`的0.8阈值语义是否一致
- **对照文档**：01-系统架构设计.md §5.7（全文一致率≥90%）
- **验证**：用已知文本对验证相似度计算

### B. 临床边界跨越检测

#### B4. 边界跨越识别
- **审查项**：`statistics.py`中`_grades_cross_clinical_boundary`和`_check_boundary_match`
- **审查要点**：
  1. 边界匹配逻辑是否覆盖所有器官的分级体系
  2. 非方向性边界的双向匹配是否正确
  3. `_numeric_fallback_match`的数字提取是否健壮（如"4a"提取出4而非4a）
  4. 心脏分级（normal/mild/moderate/severe）的边界匹配是否依赖`_GRADE_HIERARCHY`
- **对照文档**：01-系统架构设计.md §5.7（临床决策边界差异率≤1%）
- **验证**：构造所有器官的边界跨越组合验证

#### B5. 边界权重计算
- **审查项**：`verifier.py`中`_compute_boundary_weight`和`_compute_boundary_weight_from_engines`
- **审查要点**：
  1. 权重设计是否反映临床重要性（关键边界如4↔5权重2.0+，3↔5权重3.0）
  2. `is_critical`标记与权重的关系是否一致
  3. **数据不一致问题**：`default_verification_config.json`中thyroid 3→4a标记`is_critical: true`，但`boundary_config.json`中相同边界标记`is_critical: false`
  4. `weighted_boundary_diff_rate`的计算：`total_boundary_weight / max_possible_weight`，分母计算是否正确
- **对照文档**：07-数据标注与模型评估.md §4（边界差异率95% CI上限≤2%）
- **验证**：检查两个配置文件的一致性

#### B6. 严重程度分级
- **审查项**：`ConsistencyPair.boundary_severity`属性
- **审查要点**：
  1. critical/warning/low三级划分标准（weight≥2.0→critical, ≥1.0→warning, <1.0→low）
  2. `is_critical_boundary`与`boundary_weight`的优先级关系
  3. 严重程度是否影响Gate判定逻辑（当前`gate_status`未直接使用severity）
- **验证**：构造不同权重组合验证severity计算

### C. 幻觉检测

#### C7. 禁忌词检测
- **审查项**：`hallucination_detector.py`中`ForbiddenTermsDetector`和`forbidden_terms.json`
- **审查要点**：
  1. 禁忌词列表的覆盖范围是否充分（当前thyroid 18词、liver 13词、cardiac 12词、breast 12词）
  2. `allow_if_context`机制是否正确实现（如甲状腺报告中的"骨转移"在有"甲状腺"上下文时应允许）
  3. 变体词（variants）的覆盖是否完整
  4. severity分级（1/2/3）是否与临床风险对应
- **对照文档**：00-项目总览与核心指标.md（幻觉率≤0.5%严重幻觉）
- **验证**：构造含禁忌词的文本验证检测率

#### C8. 双引擎幻觉率差异
- **审查项**：`verifier.py`中`_compute_hallucination_stats`
- **审查要点**：
  1. 幻觉率差异计算为`abs(rate_a - rate_b)`，是否需要统计显著性检验
  2. 当前仅报告差异绝对值，未考虑差异的方向性
  3. 幻觉率差异阈值0.5%是否过严（在少量测试用例下，1例差异就可能导致超标）
- **对照文档**：07-数据标注与模型评估.md §4（量化后幻觉率增量≤0.5%）
- **验证**：计算不同样本量下的最小可检测差异

#### C9. 假阳性/假阴性统计
- **审查项**：`report.py`中`FalseStats`和`organ_breakdown.py`中器官级FP/FN
- **审查要点**：
  1. **关键缺陷**：`organ_breakdown.py`第105-125行，器官级`FalseStats`顶层`false_positives/negatives`等全部为0，实际数据在`engine_a_stats`和`engine_b_stats`中，导致器官级`precision/recall/F1`始终为0
  2. 幻觉ground truth仅15例（consistency_test_cases.json）+ 32例（boundary_test_cases.py），样本量是否足够计算可靠的precision/recall
  3. FP/FN统计未区分幻觉类型（禁忌词 vs 矛盾分级 vs 医学常识），无法定位改进方向
- **对照文档**：07-数据标注与模型评估.md §4
- **验证**：运行测试验证organ_false_stats的precision是否为0

### D. 性能验证

#### D10. P50/P99延迟
- **审查项**：`verifier.py`中`_compute_latency_stats`
- **审查要点**：
  1. 延迟数据来源于推理引擎响应的`latency_ms`字段，是否包含网络开销
  2. P99计算使用`_percentile`函数，验证其实现是否正确（最近邻插值 vs 线性插值）
  3. `latency_p99_reliable`要求≥30样本，但未在Gate判定中使用
- **对照文档**：06-开发环境与进度规划.md（P99<5s无重试/P99<8s有重试）
- **验证**：构造已知延迟分布验证百分位计算

#### D11. 延迟差异百分比
- **审查项**：`_compute_latency_stats`中`latency_diff_pct`
- **审查要点**：
  1. 计算方式为`abs(b_p99 - a_p99) / avg_p99 * 100`，使用平均值作分母是否合理
  2. 默认阈值30%是否过宽（llama.cpp和vLLM的延迟差异可能很大）
  3. P50差异`latency_diff_p50_pct`计算但未在Gate判定中使用
- **对照文档**：06-开发环境与进度规划.md §5
- **验证**：计算不同延迟组合的差异百分比

#### D12. 器官维度分解
- **审查项**：`organ_breakdown.py`中`compute_organ_breakdown`
- **审查要点**：
  1. 每个器官独立计算所有指标（一致率/边界率/幻觉率/延迟/文本相似度/FP/FN）
  2. 器官级Wilson CI计算是否正确（样本量较小时CI很宽）
  3. 器官级延迟P99在样本量<30时不可靠，但未标记
- **对照文档**：07-数据标注与模型评估.md §4（≥100例/器官边界类别）
- **验证**：检查器官级统计的完整性

### E. 失败案例分析

#### E13. 追溯链生成
- **审查项**：`failure_analysis.py`中`generate_failure_analysis`
- **审查要点**：
  1. 追溯链包含：输入→引擎A输出→引擎B输出→各阶段检查步骤→建议
  2. 检查步骤覆盖：分级提取→分级一致性→边界检查→预期vs实际→幻觉检查
  3. 追溯链是否包含足够信息用于问题定位（当前包含grading和severity，但缺少具体冲突的文本位置）
- **验证**：构造失败用例验证追溯链完整性

#### E14. 失败原因归类
- **审查项**：`failure_analysis.py`中summary部分
- **审查要点**：
  1. 归类维度：grading_failures / boundary_failures / extraction_failures / hallucination_failures
  2. 重叠案例（既是grading_failure又是boundary_failure）的计数是否正确（当前使用`overlap_count`）
  3. 器官级归类是否完整
- **验证**：构造多类型失败用例验证归类

#### E15. 优化建议
- **审查项**：`failure_analysis.py`中recommendations和`suggest_for_trace`
- **审查要点**：
  1. 建议是否基于统计阈值自动生成（FP/FN比例、边界差异率、提取失败数、器官级一致率、延迟差异）
  2. 建议的触发条件是否合理（如FP-FN差≥5或比例>1.5触发FP过高建议）
  3. 建议是否可操作（当前建议较笼统，如"增加训练数据"）
- **验证**：验证各建议触发条件的边界值

---

## 第三阶段：核心参考文档一致性审查

### 步骤4：对照01-系统架构设计.md §5.7
- **审查项**：
  1. InferenceEngine Protocol接口定义 vs `protocols.py`实现一致性
  2. compact模式llama.cpp常驻~3GB vs 实际实现
  3. full模式vLLM预分配90% vs `VLLM_GPU_MEMORY_UTILIZATION=0.90`配置
  4. 分级一致率≥95% vs `consistency_thresholds.grading_consistency_rate=0.95` ✓
  5. 全文一致率≥90% vs `consistency_thresholds.text_consistency_rate=0.90` ✓
  6. 临床决策边界差异率≤1% vs `consistency_thresholds.clinical_boundary_diff_rate=0.01` ✓
  7. 语义分级一致率≥98% vs `consistency_thresholds.semantic_grading_consistency_rate=0.98` ✓
  8. 条件性通过标准（90%-95% / 边界1%-2%）vs `conditional_*`阈值 ✓
  9. 三级门控规则（pass/conditional/fail）vs `GateStatus`枚举 ✓
- **验证**：逐项对比，标记一致/不一致/缺失

### 步骤5：对照06-开发环境与进度规划.md §5+§7
- **审查项**：
  1. `INFERENCE_ENGINE=llama_cpp/vllm` vs factory.py实现
  2. `LLAMA_CPP_PORT=8001` / `VLLM_PORT=8002` vs 默认配置
  3. `VLLM_GPU_MEMORY_UTILIZATION`值 vs 配置
  4. compact模式64GB内存预算（12-22GB）vs 实际测量
  5. Gate 0A-Tech P0子门控验证项 vs 测试覆盖
  6. §7.1技术选型验证计划 vs benchmark.py实现
- **验证**：逐项对比

### 步骤6：对照07-数据标注与模型评估.md §4
- **审查项**：
  1. 模型导出流水线5步验证 vs 实现覆盖
  2. 分级一致率≥95%（Phase1≥100例/器官）vs 测试数据（100例/器官）✓
  3. 边界病例≥300例（各≥100）vs 当前测试数据（仅15例幻觉标注，边界病例未单独统计）
  4. 量化后幻觉率增量≤0.5% vs 阈值配置
  5. FunASR+vLLM+TensorRT混合运行显存碎片化<30% vs vram_fragmentation.py
  6. 4小时持续运行验证 vs 当前测试（未见持续运行测试）
- **验证**：逐项对比

---

## 第四阶段：四维度审查

### 步骤7：设计规范审查
- **审查项**：
  1. InferenceEngine Protocol是否满足§5.7要求（generate/health_check/get_model_info/close）
  2. 双引擎一致性验证流程是否符合文档规定的分阶段验证
  3. Gate判定逻辑是否完整实现三级门控
  4. NMPA声明生成是否符合法规要求
  5. 跨验证（cross_validate）的设计是否合理

### 步骤8：代码质量审查
- **审查项**：
  1. **关键缺陷**：`organ_breakdown.py`器官级FalseStats顶层字段全为0
  2. **数据不一致**：`default_verification_config.json` vs `boundary_config.json`的thyroid 3→4a边界is_critical标记不一致
  3. **潜在问题**：`report.py`第153行`FalseStats.f1_score`缩进异常
  4. **设计问题**：`cross_validate`方法对full和subset分别做verify，但两者都是对同一引擎对验证，不是传统的train/test分割
  5. **健壮性**：`_compute_boundary_stats`中`max_possible_weight`计算可能为0导致除零
  6. **可维护性**：`ConsistencyReport`有30+字段，dataclass过大
  7. **可扩展性**：新增器官需修改多处（grading_definitions、boundary_config、forbidden_terms）

### 步骤9：代码风格审查
- **审查项**：
  1. 是否遵循项目编码规范（类型注解、docstring、命名约定）
  2. 日志使用structlog是否一致
  3. 异常处理模式是否统一
  4. 配置管理模式是否一致（Pydantic模型 vs JSON文件）

### 步骤10：功能正确性审查
- **审查项**：
  1. 运行全部LLM单元测试，记录通过/失败
  2. 验证关键计算逻辑（Wilson CI、百分位、F1）的正确性
  3. 验证边界跨越检测的完整性（所有器官所有边界组合）
  4. 验证幻觉检测的召回率（禁忌词+矛盾分级+医学常识+数值验证+上下文感知）
  5. 验证Gate判定逻辑的边界条件

---

## 第五阶段：综合评估与改进建议

### 步骤11：验证机制有效性综合评估
- **评估维度**：
  1. 设计合理性：验证机制是否能准确识别LLM潜在风险
  2. 实施规范性：验证流程是否规范、可重复
  3. 指标科学性：阈值设定是否有据可依
  4. 结果可靠性：验证结果是否可信
  5. 数据代表性：测试数据是否充分覆盖
  6. 场景关联性：验证与实际应用场景的关联度
  7. 效率与可扩展性：验证机制的运行效率
  8. 可重复性：验证结果的可重复性

### 步骤12：输出审查报告
- **报告内容**：
  1. 执行摘要（关键发现、风险等级）
  2. 五大验证维度审查结果（每项的评估结论）
  3. 核心文档一致性审查结果
  4. 四维度审查结果
  5. 关键缺陷清单（按严重程度排序）
  6. 改进建议（按优先级排序）
  7. 核心参考文档更新建议（如发现更优实践）

---

## 已识别的关键问题（预审查发现）

### P0 - 必须修复
1. **器官级FalseStats顶层字段全为0**：`organ_breakdown.py`第105-110行，器官级`FalseStats`的`false_positives/negatives/true_positives/true_negatives`全部硬编码为0，导致器官级precision/recall/F1始终为0
2. **边界配置数据不一致**：`default_verification_config.json`中thyroid 3→4a标记`is_critical: true`，但`boundary_config.json`中相同边界标记`is_critical: false`

### P1 - 应当修复
3. **幻觉ground truth样本量不足**：仅15例（consistency_test_cases.json）+ 32例（boundary_test_cases.py），远低于07文档要求的边界病例≥300例
4. **Phase2边界病例密度不足**：当前100例/器官，但文档要求每个边界类别≥100例（如TI-RADS 3/4a/4b各≥100）
5. **cross_validate设计不合理**：对full和subset分别做双引擎验证，不是传统的train/test分割
6. **FalseStats.f1_score缩进异常**：`report.py`第153行缩进不一致

### P2 - 建议改进
7. **文本相似度算法不适合中文医学文本**：bigram/trigram Jaccard对中文效果有限，建议考虑字符级或语义级相似度
8. **Gate判定未使用latency_p99_reliable**：P99可靠性标记存在但未影响Gate判定
9. **Phase自动升级缺乏确认**：phase1→phase2自动升级发生在验证过程中，无用户确认
10. **幻觉检测未区分类型**：FP/FN统计未区分幻觉类型，无法定向优化
11. **缺少持续运行验证**：07文档要求4小时持续运行验证，当前未见实现
12. **器官级延迟P99未标记可靠性**：样本量<30时P99不可靠但未标记

---

## 执行顺序

1. 步骤1-3：系统性阅读（只读，约需3轮并行搜索）
2. 步骤4-6：文档一致性审查（逐项对比）
3. 步骤7-10：四维度审查（代码审查+测试运行）
4. 步骤11-12：综合评估与报告输出

## 产出物

1. 审查报告文档（含所有发现、评估、建议）
2. 关键缺陷修复代码（P0/P1级别）
3. 核心参考文档更新建议（如适用）
