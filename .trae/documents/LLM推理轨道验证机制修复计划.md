# LLM推理轨道验证机制修复计划

**基于文档**：`docs-for-guiding generating docs/开发计划/LLM推理轨道验证机制全面审查报告.md`  
**修复目标**：解决审查报告中识别的5个P0严重问题，确保Gate 0A验证机制有效可靠

---

## 一、修复问题清单

### P0 严重问题（必须修复）

| # | 问题 | 文件位置 | 修复优先级 | 预估工作量 |
|---|------|---------|----------|-----------|
| 1 | 幻觉检测在一致性验证中完全未实现 | `consistency.py:27` | 🔴 最高 | 中 |
| 2 | 临床边界检测不完整（缺4↔5等） | `consistency.py:56-65` | 🔴 高 | 低 |
| 3 | 碎片化率计算公式存严重疑问 | `gpu_monitor.py:178-180` | 🔴 高 | 高 |
| 4 | 基准测试正确性默认用子串匹配 | `benchmark.py:226` | 🔴 高 | 低 |
| 5 | 量化幻觉增量未计算 | `benchmark.py:33` | 🔴 高 | 中 |

### P1 重要问题（建议修复）

| # | 问题 | 文件位置 | 修复优先级 |
|---|------|---------|----------|
| 6 | 基准测试幻觉检测过于简陋 | `benchmark.py:244-252` | ⚠️ 中 |
| 7 | 内存单位解析不完整 | `gpu_monitor.py:286` | ⚠️ 中 |

---

## 二、详细修复方案

### 修复1：实现幻觉检测功能

**问题**：`GradingResult.is_hallucination` 永远为 `False`，无任何检测逻辑

**修复方案**：
1. 在 `consistency.py` 中实现 `_detect_hallucination()` 函数
2. 检测逻辑包括：
   - 器官无关禁忌词检测（复用 benchmark.py 的逻辑并扩展）
   - 矛盾分级检测（如同时出现4a和4b）
   - 医疗常识违规检测
3. 在 `_verify_single_case()` 中调用检测函数

**代码修改**：
```python
# consistency.py 新增函数
def _detect_hallucination(text: str, organ: str) -> bool:
    """检测医疗幻觉"""
    # 1. 器官无关禁忌词
    forbidden_terms = {
        "thyroid": ["肺癌", "胃癌", "肠癌", "骨折", "脑梗"],
        "liver": ["肺癌", "甲状腺癌", "骨折", "脑梗", "胃穿孔"],
        "cardiac": ["肺癌", "肝癌", "骨折", "胃穿孔", "肠癌"],
    }
    if any(term in text for term in forbidden_terms.get(organ, [])):
        return True
    
    # 2. 矛盾分级检测
    grading_pattern = re.compile(r'(TI-RADS|LI-RADS)\s*(\d[a-z]?)', re.IGNORECASE)
    matches = grading_pattern.findall(text)
    if len(set(matches)) > 1:
        return True
    
    return False
```

---

### 修复2：补全临床边界检测

**问题**：缺失 `4↔5`、`4a↔4c`、`4b↔4c` 等关键边界

**修复方案**：
扩展 `boundary_pairs` 列表，添加所有临床关键边界

**代码修改**：
```python
# consistency.py _crosses_boundary() 方法
boundary_pairs = [
    # 原有边界
    ("3", "4"), ("4", "3"),
    ("4a", "4b"), ("4b", "4a"),
    ("4a", "3"), ("3", "4a"),
    # 新增边界
    ("4", "5"), ("5", "4"),  # 最关键的良恶性分界
    ("4a", "4c"), ("4c", "4a"),
    ("4b", "4c"), ("4c", "4b"),
    ("3", "4b"), ("4b", "3"),
    ("4", "4a"), ("4a", "4"),
    ("4", "4b"), ("4b", "4"),
    ("4", "4c"), ("4c", "4"),
]
```

---

### 修复3：修正碎片化率计算公式

**问题**：当前公式 `1.0 - (free_mb / (total_mb - used_mb + free_mb))` 在 `T = U + F` 时恒等于 0.5

**修复方案**：
采用更合理的碎片化率估算方法：
- 使用显存使用率波动作为碎片化代理指标
- 或记录峰值使用率与当前使用率的差异

**代码修改**：
```python
# gpu_monitor.py _read_gpu_metrics() 方法
# 方案A：使用使用率波动估算碎片化
# 记录历史峰值，碎片化 = (峰值使用 - 当前使用) / 总显存

# 方案B（推荐）：简化为显存使用率监控
fragmentation = used_mb / total_mb  # 使用率作为代理指标

# 方案C：使用NVML获取真实碎片化（需要pynvml库）
# 需要添加依赖，暂不采用
```

---

### 修复4：修复基准测试正确性判断

**问题**：默认使用子串匹配 `expected.lower() in text.lower()`，易误判

**修复方案**：
默认调用 `extract_grading()` 进行精确分级提取和比较

**代码修改**：
```python
# benchmark.py _benchmark_single() 方法
# 修改正确性判断逻辑
is_correct = False
if expected and text:
    # 优先使用分级提取器
    from app.inference.consistency import extract_grading
    extracted = extract_grading(text, case.get("organ", ""))
    if extracted:
        is_correct = extracted == expected
    elif grading_extractor:
        extracted = grading_extractor(text, case.get("organ", ""))
        if extracted:
            is_correct = extracted == expected
```

---

### 修复5：实现量化幻觉增量计算

**问题**：`quantized_hallucination_increment` 永远为初始值 0

**修复方案**：
1. 在基准测试中记录量化模型的幻觉率
2. 需要外部传入基线幻觉率（非量化模型）
3. 计算增量

**代码修改**：
```python
# benchmark.py BenchmarkResult 类
@dataclass
class BenchmarkResult:
    # ... 现有字段
    baseline_hallucination_rate: float = 0.0  # 新增：基线幻觉率
    
    @property
    def quantized_hallucination_increment(self) -> float:
        """计算量化导致的幻觉率增量"""
        return max(0.0, self.hallucination_rate - self.baseline_hallucination_rate)

# benchmark.py benchmark_candidate() 方法
# 添加参数接收基线幻觉率
async def benchmark_candidate(
    self,
    engine: InferenceEngine,
    candidate: ModelCandidate,
    test_cases: list[dict[str, Any]],
    grading_extractor: Any = None,
    baseline_hallucination_rate: float = 0.0,  # 新增参数
) -> BenchmarkResult:
    result = BenchmarkResult(
        candidate=candidate,
        baseline_hallucination_rate=baseline_hallucination_rate,
    )
    # ...
```

---

### 修复6：增强幻觉检测

**问题**：仅4个禁忌词，覆盖太少

**修复方案**：
扩展禁忌词列表，添加更多医疗幻觉场景

**代码修改**：
```python
# benchmark.py _detect_hallucination() 方法
forbidden_terms = {
    "thyroid": [
        "肺癌", "胃癌", "肠癌", "骨折", "脑梗", "肾癌", "胰腺癌",
        "骨转移", "肝转移", "脑转移",  # 转移相关
        "前列腺", "卵巢", "子宫",  # 无关器官
    ],
    "liver": [
        "肺癌", "甲状腺癌", "骨折", "脑梗", "肾癌",
        "骨转移", "脑转移", "前列腺", "卵巢",
    ],
    "cardiac": [
        "肺癌", "肝癌", "骨折", "胃穿孔", "肠癌",
        "骨转移", "肝转移", "甲状腺癌",
    ],
}
```

---

### 修复7：完善内存单位解析

**问题**：缺少部分单位解析

**修复方案**：
添加更多单位支持

**代码修改**：
```python
# gpu_monitor.py _parse_docker_mem() 方法
@staticmethod
def _parse_docker_mem(mem_str: str) -> float:
    mem_str = mem_str.split("/")[0].strip()
    try:
        if mem_str.endswith("TiB"):
            return float(mem_str[:-3]) * 1024 * 1024
        elif mem_str.endswith("GiB"):
            return float(mem_str[:-3]) * 1024
        elif mem_str.endswith("MiB"):
            return float(mem_str[:-3])
        elif mem_str.endswith("KiB"):
            return float(mem_str[:-3]) / 1024
        elif mem_str.endswith("BiB"):
            return float(mem_str[:-3]) / 1024 / 1024
        elif mem_str.endswith("TB"):
            return float(mem_str[:-2]) * 1024 * 1024
        elif mem_str.endswith("GB"):
            return float(mem_str[:-2]) * 1024
        elif mem_str.endswith("MB"):
            return float(mem_str[:-2])
        elif mem_str.endswith("KB"):
            return float(mem_str[:-2]) / 1024
        elif mem_str.endswith("B"):
            return float(mem_str[:-1]) / 1024 / 1024
    except ValueError:
        pass
    return 0.0
```

---

## 三、测试验证计划

### 单元测试新增

| 测试文件 | 新增测试用例 |
|---------|------------|
| `test_consistency.py` | `test_hallucination_detection` |
| `test_consistency.py` | `test_boundary_crossing_4_to_5` |
| `test_consistency.py` | `test_boundary_crossing_4a_to_4c` |
| `test_benchmark.py` | `test_correctness_with_grading_extractor` |
| `test_benchmark.py` | `test_quantized_hallucination_increment` |
| `test_gpu_monitor.py` | `test_parse_docker_mem_all_units` |

### 集成测试

1. 运行完整的一致性验证流程
2. 运行完整的基准测试流程
3. 验证所有P0问题已修复

---

## 四、实施步骤

### 阶段1：修复 consistency.py（预估30分钟）

1. [ ] 实现 `_detect_hallucination()` 函数
2. [ ] 扩展 `boundary_pairs` 列表
3. [ ] 在 `_verify_single_case()` 中调用幻觉检测
4. [ ] 更新 `GradingResult` 初始化逻辑
5. [ ] 添加单元测试

### 阶段2：修复 benchmark.py（预估20分钟）

1. [ ] 修改正确性判断逻辑
2. [ ] 实现量化幻觉增量计算
3. [ ] 扩展幻觉检测禁忌词
4. [ ] 添加单元测试

### 阶段3：修复 gpu_monitor.py（预估15分钟）

1. [ ] 修正碎片化率计算公式
2. [ ] 完善内存单位解析
3. [ ] 添加单元测试

### 阶段4：验证与文档（预估15分钟）

1. [ ] 运行所有单元测试
2. [ ] 生成修复报告
3. [ ] 更新相关文档

---

## 五、风险评估

| 风险 | 影响 | 缓解措施 |
|-----|------|---------|
| 幻觉检测可能误报 | 中 | 设置可配置的检测阈值，允许调整 |
| 碎片化公式修改影响现有监控 | 高 | 保留旧公式作为备选，添加新公式 |
| 测试用例覆盖不足 | 中 | 参考审查报告补充边界测试 |

---

## 六、验收标准

- [ ] 所有P0问题已修复
- [ ] 单元测试覆盖率 ≥ 90%
- [ ] 所有测试通过
- [ ] 代码符合项目规范（ruff检查通过）
- [ ] 修复报告与审查报告一一对应
