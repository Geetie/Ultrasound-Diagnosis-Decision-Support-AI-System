# LLM推理轨道验证机制修复与优化计划

## 概述
根据审查报告中发现的问题，本计划系统性地修复未完成的改进项和问题点，提升验证机制的整体质量和可靠性。

## 修复优先级

### P0 - 高优先级（必须修复）
1. 增强幻觉检测能力
2. 修复碎片化率计算
3. 添加跨平台兼容层

### P1 - 中优先级（建议修复）
4. 优化内存实测 - 区分缓存与实际占用
5. 增强GPU调度 - 添加超时/死锁保护机制
6. 统一幻觉检测逻辑

### P2 - 低优先级（可选优化）
7. 添加单元测试覆盖
8. 重构代码结构，提升可维护性

---

## 详细修复计划

### 1. 增强幻觉检测能力

#### 目标
- 建立统一的幻觉检测模块
- 支持多种检测策略
- 可配置的检测规则

#### 实现方案
创建新的 `hallucination_detector.py` 模块：

```python
# 检测策略枚举
class DetectionStrategy(Enum):
    FORBIDDEN_TERMS = "forbidden_terms"
    CONTRADICTORY_GRADING = "contradictory_grading"
    MEDICAL_COMMON_SENSE = "medical_common_sense"
    NUMERICAL_VALIDITY = "numerical_validity"

# 检测器类
class HallucinationDetector:
    def __init__(self, strategies: List[DetectionStrategy] = None):
        self.strategies = strategies or list(DetectionStrategy)
        self._init_detectors()

    def detect(self, text: str, organ: str) -> DetectionResult:
        pass
```

#### 修改文件
- 新建：`app/inference/hallucination_detector.py`
- 修改：`app/inference/consistency.py` - 使用统一检测器
- 修改：`app/inference/benchmark.py` - 使用统一检测器

---

### 2. 修复碎片化率计算

#### 目标
- 使用pynvml获取真实内存块分布
- 计算准确的碎片化率
- 保持向后兼容

#### 实现方案
扩展 `gpu_monitor.py`：

```python
# 新增依赖
# requirements.txt 添加: nvidia-ml-py3==7.352.0

# 碎片化计算类
class FragmentationCalculator:
    def __init__(self):
        self.using_pynvml = False
        self._init_pynvml()

    def _init_pynvml(self):
        try:
            import pynvml
            pynvml.nvmlInit()
            self.using_pynvml = True
        except (ImportError, pynvml.NVMLError):
            self.using_pynvml = False

    def calculate_fragmentation(self, gpu_index: int = 0) -> float:
        if self.using_pynvml:
            return self._calculate_with_pynvml(gpu_index)
        return self._calculate_fallback()
```

#### 修改文件
- 修改：`app/inference/gpu_monitor.py`
- 修改：`requirements.txt`
- 修改：`tests/unit/test_gpu_monitor.py`

---

### 3. 添加跨平台兼容层

#### 目标
- 支持Windows、Linux、macOS
- 自动检测平台并选择合适实现
- 优雅降级处理

#### 实现方案
创建平台抽象层：

```python
# 平台检测
class Platform(Enum):
    LINUX = "linux"
    WINDOWS = "windows"
    DARWIN = "darwin"
    UNKNOWN = "unknown"

# 系统内存接口
class SystemMemoryReader(ABC):
    @abstractmethod
    async def read_memory(self) -> SystemMemorySnapshot:
        pass

class LinuxMemoryReader(SystemMemoryReader):
    async def read_memory(self) -> SystemMemorySnapshot:
        pass

class WindowsMemoryReader(SystemMemoryReader):
    async def read_memory(self) -> SystemMemorySnapshot:
        pass

# 工厂方法
def create_memory_reader(platform: Platform = None) -> SystemMemoryReader:
    pass
```

#### 修改文件
- 修改：`app/inference/gpu_monitor.py`
- 新增：平台检测和抽象接口
- 修改：`tests/unit/test_gpu_monitor.py`

---

### 4. 优化内存实测 - 区分缓存

#### 目标
- 区分cache/buffer与实际进程占用
- 正确计算可用内存
- 提供更准确的预算评估

#### 实现方案
扩展内存快照结构：

```python
@dataclass
class SystemMemorySnapshot:
    timestamp: float
    total_memory_mb: float
    used_memory_mb: float
    available_memory_mb: float
    cached_mb: float = 0.0
    buffers_mb: float = 0.0
    process_used_mb: float = 0.0
    process_memory_mb: dict[str, float] = field(default_factory=dict)

    @property
    def effective_used_mb(self) -> float:
        return self.used_memory_mb - self.cached_mb - self.buffers_mb
```

#### 修改文件
- 修改：`app/inference/gpu_monitor.py`
- 修改：`tests/unit/test_gpu_monitor.py`

---

### 5. 增强GPU调度 - 添加超时/死锁保护

#### 目标
- 超时自动释放机制
- 防止死锁的等待策略
- 优先级队列支持

#### 实现方案
扩展GPU调度器：

```python
@dataclass
class ScheduledRequest:
    resource_type: str
    request_id: str
    timestamp: float
    timeout_seconds: float
    callback: Callable[[bool], None]

class GPUScheduleStrategy:
    def __init__(self, ..., default_timeout_seconds: float = 300.0):
        self.default_timeout = default_timeout_seconds
        self.pending_requests: Deque[ScheduledRequest] = deque()
        self._timeout_check_task = None

    async def acquire_with_timeout(self, resource_type: str, timeout_seconds: float = None) -> bool:
        pass

    def _check_timeouts(self):
        pass
```

#### 修改文件
- 修改：`app/inference/gpu_monitor.py`
- 修改：`tests/unit/test_gpu_monitor.py`

---

### 6. 统一幻觉检测逻辑

#### 目标
- consistency.py和benchmark.py使用相同的检测规则
- 配置文件驱动的规则管理
- 易于扩展新规则

#### 实现方案
使用统一的HallucinationDetector（任务1）

---

## 实施步骤

### 阶段1：基础设施准备
1. 创建统一幻觉检测模块
2. 添加pynvml依赖
3. 创建平台抽象层

### 阶段2：核心修复
4. 实现增强的幻觉检测
5. 实现准确的碎片化计算
6. 实现跨平台兼容
7. 优化内存测量
8. 增强GPU调度

### 阶段3：测试与验证
9. 添加单元测试
10. 运行现有测试
11. 集成测试
12. 性能测试

---

## 依赖变更

### 新增依赖
- `nvidia-ml-py3==7.352.0` (可选，用于精确GPU内存管理)
- `psutil==5.9.8` (用于跨平台系统信息读取)

### 修改文件
- `requirements.txt`

---

## 测试策略

### 单元测试
- 为每个新组件编写完整的单元测试
- 使用mock模拟外部依赖
- 测试覆盖率目标 > 80%

### 集成测试
- 测试跨平台兼容性
- 测试与现有模块的集成
- 测试端到端流程

---

## 验收标准

### 功能验收
- [ ] 幻觉检测能识别多种类型的医疗幻觉
- [ ] 碎片化率计算准确
- [ ] Windows/Linux平台均能正常运行
- [ ] 内存测量区分缓存与实际占用
- [ ] GPU调度有超时保护

### 质量验收
- [ ] 代码通过flake8/mypy检查
- [ ] 所有单元测试通过
- [ ] 完整的文档和注释
- [ ] 符合项目编码规范

---

## 风险评估

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|----------|
| pynvml在某些环境不可用 | 中 | 中 | 提供fallback方案 |
| 跨平台实现复杂 | 中 | 中 | 先实现主要平台，其他平台优雅降级 |
| 与现有代码冲突 | 高 | 低 | 充分测试，保持向后兼容 |

---

## 文件变更清单

### 新增文件
- `app/inference/hallucination_detector.py` - 统一幻觉检测模块

### 修改文件
- `app/inference/consistency.py` - 集成新的幻觉检测
- `app/inference/benchmark.py` - 集成新的幻觉检测
- `app/inference/gpu_monitor.py` - 多项改进
- `requirements.txt` - 添加新依赖
- `tests/unit/test_consistency.py` - 更新测试
- `tests/unit/test_benchmark.py` - 更新测试
- `tests/unit/test_gpu_monitor.py` - 更新测试
- `tests/unit/test_hallucination_detector.py` - 新增测试
