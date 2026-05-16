# LLM轨道验证机制有效性审查与Phase0A实施审查计划

## 审查目标

对LLM轨道的全部验证环节进行系统性有效性审查，并对Phase0A阶段实施情况进行独立、客观、深入且批判性的审查。

---

## 第一部分：15项验证指标逐一审查

### A. 基础一致性验证

#### 1. 分级提取验证

**审查对象**: `grading_definitions.py#L75-94` `extract_grading()` + `test_consistency.py#L51-135` `TestExtractGrading`

**审查要点**:
- [ ] 正则模式覆盖度：TI-RADS/LI-RADS/BI-RADS/cardiac四种器官的分级提取正则是否覆盖所有临床常见格式
- [ ] 边界条件：大小写混合、子分类(4a/4b/4c)、无分级文本、未知器官的处理
- [ ] cardiac特殊逻辑：中文描述优先于LVEF数值的优先级策略是否合理
- [ ] 缺失场景：TI-RADS 1/2类的提取、LI-RADS子分类(如LR-TR)、BI-RADS 0类的处理
- [ ] 正则冲突：多个模式匹配同一文本时的优先级是否确定

#### 2. 双引擎分级一致性

**审查对象**: `consistency.py#L526-584` `ConsistencyVerifier.verify()` + `test_consistency.py#L214-265` `TestConsistencyPair`

**审查要点**:
- [ ] `grading_consistent`使用精确匹配 vs `grading_semantic_consistent`使用语义等价，两层判定是否合理
- [ ] 语义等价规则：`grading_semantic_equivalent()`基于`_GRADE_HIERARCHY`的level映射，4/4a/4b/4c同level=4是否临床合理
- [ ] None分级处理：引擎无法提取分级时的处理策略
- [ ] 一致率计算分母：是否包含提取失败的用例

#### 3. 文本一致率

**审查对象**: `consistency.py#L446-476` `text_similarity()` + `text_consistency_with_grading()`

**审查要点**:
- [ ] bigram Jaccard系数作为文本相似度度量是否适合中文医疗文本
- [ ] 阈值0.8的合理性：是否有经验数据支撑
- [ ] 分级感知逻辑：分级语义等价直接视为文本一致，是否过于宽松
- [ ] 与Gate标准(>=90%)的关联：文本一致率定义是否与门控标准一致

### B. 临床边界跨越检测

#### 4. 边界跨越识别

**审查对象**: `consistency.py#L110-150` `_grades_cross_clinical_boundary()` + `verification_config.py#L208-294`

**审查要点**:
- [ ] 边界对配置完整性：thyroid(22对)/liver(6对)/cardiac(12对)/breast(22对)是否覆盖所有临床重要边界
- [ ] `_check_boundary_match`的层级匹配逻辑：同level内精确匹配 vs 跨level层级匹配是否正确
- [ ] `_numeric_fallback_match`的必要性：何时触发，是否有测试覆盖
- [ ] ConsistencyPair中`is_boundary_crossing`的计算是否正确使用expected_grading

#### 5. 边界权重计算

**审查对象**: `verification_config.py#L19-38` `BoundaryPairConfig` + `consistency.py#L1008-1041`

**审查要点**:
- [ ] 权重设定合理性：3→5权重3.0、4→5权重2.0、4a→4b权重1.0是否符合临床风险
- [ ] 加权边界差异率`weighted_boundary_diff_rate`的计算公式是否正确
- [ ] 权重对称性：from→to和to→from权重是否一致（当前实现一致）

#### 6. 严重程度分级

**审查对象**: `consistency.py` ConsistencyPair.boundary_severity属性

**审查要点**:
- [ ] critical/warning/low/none四级划分逻辑：基于`is_critical_boundary`和`boundary_weight>=2.0`
- [ ] critical判定双重条件：is_critical_boundary=True OR boundary_weight>=2.0，是否合理
- [ ] 与文档一致性：01-系统架构设计.md §5.7的严重程度定义是否与此一致

### C. 幻觉检测

#### 7. 禁忌词检测

**审查对象**: `hallucination_detector.py` ForbiddenTermsDetector + `verification_config.py#L297-654`

**审查要点**:
- [ ] 禁忌词表覆盖度：各器官的禁忌词是否充分
- [ ] 变体检测：variants列表是否覆盖常见同义词
- [ ] 上下文豁免：`allow_if_context`规则的精确度和召回率
- [ ] 否定检测：长距离否定（"未见明显肺癌转移征象"）的处理
- [ ] 误报风险：正常多器官联合诊断场景的假阳性

#### 8. 双引擎幻觉率差异

**审查对象**: `consistency.py#L900-911` organ_breakdown中的hallucination_rate_diff计算

**审查要点**:
- [ ] 幻觉率差异计算：`|halluc_rate_a - halluc_rate_b|`是否为正确的度量
- [ ] 阈值0.5%的合理性：与文档要求一致
- [ ] 幻觉检测器对两引擎的输出是否使用相同检测标准

#### 9. 假阳性/假阴性统计

**审查对象**: `consistency.py#L254-283` FalseStats + `test_consistency.py#L755-868`

**审查要点**:
- [ ] TP/FP/TN/FN定义：基于`expected_hallucination`真值标签
- [ ] precision/recall/F1计算：零分母保护是否完善
- [ ] 统计粒度：仅全局统计，缺少按器官/按策略的分解统计
- [ ] 真值标签覆盖率：测试用例中`expected_hallucination`字段的标注比例

### D. 性能验证

#### 10. P50/P99延迟

**审查对象**: `consistency.py` latency统计 + `test_consistency.py#L922-947`

**审查要点**:
- [ ] 延迟测量点：是否从请求发出到响应接收的端到端延迟
- [ ] P50/P99计算：使用`percentile`工具函数，验证其正确性
- [ ] 样本量：少量用例(3-6个)的P99统计是否有意义
- [ ] 与Gate标准关联：P99<3000ms(单并发)/<8000ms(2并发)是否在测试中验证

#### 11. 延迟差异百分比

**审查对象**: `consistency.py` latency_diff_pct + `test_consistency.py#L632-656`

**审查要点**:
- [ ] 计算公式：`|p50_a - p50_b| / min(p50_a, p50_b) * 100`是否合理
- [ ] 阈值20%的合理性
- [ ] 使用P50而非P99计算差异是否合理

#### 12. 器官维度分解

**审查对象**: `consistency.py#L870-938` organ_breakdown计算

**审查要点**:
- [ ] 分解维度完整性：thyroid/liver/cardiac/breast四种器官
- [ ] 每个器官的统计指标：total/grading_consistency_rate/boundary_diff_rate/hallucination_rate/latency
- [ ] 样本量要求：每器官>=100例的Gate标准是否在测试中强制执行

### E. 失败案例分析

#### 13. 追溯链生成

**审查对象**: `consistency.py#L964-1108` `_generate_failure_analysis()`

**审查要点**:
- [ ] 追溯链完整性：input→engine_a_output→engine_b_output→steps→recommendation
- [ ] steps内容：grading_extraction/grading_check/boundary_check/expected_vs_actual_check/hallucination_check
- [ ] 是否覆盖所有失败类型

#### 14. 失败原因归类

**审查对象**: `consistency.py#L970-987` summary统计

**审查要点**:
- [ ] 归类维度：grading_failures/boundary_failures/critical_boundary_failures/extraction_failures/hallucination_failures
- [ ] 去重逻辑：使用seen_case_ids集合去重是否正确
- [ ] overlap_count：grading和boundary重叠的计数

#### 15. 优化建议

**审查对象**: `consistency.py#L1084-1108` recommendations生成

**审查要点**:
- [ ] 建议触发条件：FP/FN差异、边界差异率、提取失败等
- [ ] 建议具体性：是否可操作
- [ ] 缺失场景：延迟超标、特定器官一致性低等是否有对应建议

---

## 第二部分：验证机制整体有效性审查

### 2.1 设计合理性

- [ ] 验证指标体系是否完整覆盖临床安全需求
- [ ] 门控标准(PASSED/CONDITIONAL/FAILED)三档设计是否合理
- [ ] Wilson置信区间的使用是否恰当
- [ ] 分级一致率vs语义一致率的双层判定是否冗余或互补

### 2.2 实施流程规范性

- [ ] 测试分层（单元/集成/基准）是否合理
- [ ] 集成测试的服务可用性检测和条件跳过机制
- [ ] 测试数据版本管理(test_data_version)的追踪
- [ ] CI/CD集成：continuous_verify.py的退出码设计

### 2.3 指标设定科学性

- [ ] 分级一致率>=95%的依据
- [ ] 边界差异率<=1%与CI上界<=2%的关系
- [ ] 幻觉率差异<=0.5%的合理性
- [ ] 文本一致率>=90%与bigram Jaccard 0.8阈值的关系

### 2.4 结果可靠性

- [ ] Mock引擎测试vs真实引擎测试的覆盖差异
- [ ] 测试用例代表性：415例consistency_test_cases.json的分布
- [ ] 边界病例生成器(boundary_test_cases.py)的覆盖度
- [ ] 交叉验证(cross_validate)的K折选择

---

## 第三部分：Phase0A实施审查

### 3.1 设计规范一致性

- [ ] InferenceEngine Protocol接口定义是否与01-系统架构设计.md §5.7一致
- [ ] Gate 0A门控标准是否与06-开发环境与进度规划.md §7一致
- [ ] 一致性验证分阶段方案是否与07-数据标注与模型评估.md §4一致
- [ ] 条件性门控规则(PASSED/CONDITIONAL/FAILED)是否与架构设计一致

### 3.2 代码质量

- [ ] consistency.py的复杂度：_compute_report方法是否过长
- [ ] 错误处理：引擎调用异常、超时、空响应的处理
- [ ] 并发安全：asyncio.Semaphore的使用
- [ ] 日志记录：structlog的使用是否规范

### 3.3 代码风格

- [ ] dataclass vs Pydantic的选择是否一致（consistency用dataclass, verification_config用Pydantic）
- [ ] 命名约定：是否遵循项目统一规范
- [ ] 类型注解：是否完整

### 3.4 功能正确性

- [ ] 所有Phase0A任务(T-GA-LLM-01至06)的验收标准是否有对应测试
- [ ] 边界条件：空用例、单用例、超大量用例的处理
- [ ] 异常处理：引擎不可用、网络超时、模型加载失败

---

## 第四部分：核心参考文档一致性审查

### 4.1 与01-系统架构设计.md的一致性

- [ ] §5.7 InferenceEngine Protocol接口：generate/health_check/get_model_info/close
- [ ] compact模式llama.cpp常驻~3GB显存
- [ ] full模式vLLM预分配gpu_memory_utilization=0.90
- [ ] 条件性门控规则三档判定

### 4.2 与06-开发环境与进度规划.md的一致性

- [ ] INFERENCE_ENGINE=llama_cpp/vllm配置
- [ ] LLAMA_CPP_PORT=8001, VLLM_PORT=8002
- [ ] Gate 0A-Tech P0子门控验证标准
- [ ] §7.1技术选型验证计划

### 4.3 与07-数据标注与模型评估.md的一致性

- [ ] 一致性验证分阶段方案（阶段一>=100例/器官，阶段二>=300例）
- [ ] 边界病例测试集要求（>=300例，TI-RADS/LI-RADS/BI-RADS 3/4类各>=100例）
- [ ] 量化后幻觉率增量<=0.5%
- [ ] 显存碎片化4小时验证要求

---

## 第五部分：已识别问题与改进建议

### 5.1 高优先级问题（P0）

1. **分级提取正则覆盖不足**：TI-RADS 1/2类、LI-RADS子分类(LR-TR)、BI-RADS 0类缺少专门测试
2. **文本相似度度量不适合医疗文本**：bigram Jaccard对中文医疗文本区分度低，建议考虑编辑距离或语义嵌入
3. **FalseStats缺少器官维度分解**：仅全局统计，无法定位特定器官的幻觉检测问题
4. **延迟差异使用P50而非P99**：与Gate标准(P99)不一致
5. **Phase0A集成测试缺少显存碎片化验证**：T-GA-LLM-03(4小时连续运行)无对应集成测试

### 5.2 中优先级问题（P1）

6. **语义等价规则过于宽松**：4/4a/4b/4c同level=4，但4a→4c在临床上是显著差异
7. **禁忌词表缺少breast器官**：verification_config.py中breast的禁忌词配置
8. **cross_validate非传统ML泛化评估**：K折验证的统计意义需明确
9. **自适应阈值未集成**：AdaptiveThresholdConfig仅预留接口
10. **缺少量化幻觉增量验证的端到端测试**：TestQuantizationHallucinationIncrement使用相同配置

### 5.3 低优先级问题（P2）

11. **_compute_report方法过长**：建议拆分为子方法
12. **测试用例同质性风险**：generate_test_cases.py基于模板生成，可能缺乏多样性
13. **缺少NMPA注册材料声明模板**：CONDITIONAL状态需声明偏差率
14. **boundary_config.json与代码默认配置的同步机制**：需确保一致性

---

## 实施步骤

### Step 1: 运行现有测试套件，确认基线状态
- 运行全部单元测试
- 记录测试通过率和覆盖率
- 验证：所有单元测试通过

### Step 2: 逐项审查15项验证指标
- 对每项指标进行代码级审查
- 对照文档验证一致性
- 记录发现的问题和改进建议
- 验证：每项指标审查完成，问题清单更新

### Step 3: 验证机制整体有效性评估
- 评估指标体系完整性
- 评估测试数据代表性
- 评估门控标准科学性
- 验证：评估报告完成

### Step 4: Phase0A实施审查
- 对照任务清单检查实施状态
- 对照核心文档验证一致性
- 代码质量与风格审查
- 验证：审查报告完成

### Step 5: 生成最终审查报告
- 汇总所有发现
- 按P0/P1/P2分级
- 提出改进建议
- 验证：审查报告交付

### Step 6: 实施改进（如用户确认）
- 修复P0问题
- 改进验证机制
- 更新核心参考文档（如需要）
- 验证：改进后测试全部通过
