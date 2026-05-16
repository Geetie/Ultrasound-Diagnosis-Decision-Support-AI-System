# LLM验证机制修复 — 详细实施计划（续）

> 基于已完成P0修复的后续实施计划，覆盖P1/P2修复、P0单元测试补充、Phase0A任务执行方案

---

## 实施状态总览

| 批次 | 状态 | 完成项 | 待完成项 |
|------|------|--------|----------|
| P0 | ✅ 代码修改完成 | P0-1~P0-5 | 单元测试补充 |
| P1 | ❌ 未开始 | — | P1-1~P1-6 |
| P2 | ❌ 未开始 | — | P2-1~P2-7 |
| Phase0A | ❌ 未开始 | — | T1~T5 |
| 文档更新 | ❌ 未开始 | — | 3份核心文档 |

---

## 第一部分：P0单元测试补充

### P0-TEST-1: 器官级Gate检查单元测试

**文件**: `tests/unit/llm/test_consistency.py`

**新增测试类**: `TestOrganGateCheck`

```python
class TestOrganGateCheck:
    def test_global_passed_but_organ_conditional(self):
        """全局通过但某器官不达标 → Gate应为CONDITIONAL"""
        from unittest.mock import patch
        from app.inference.verification_config import VerificationConfig, ConsistencyThresholdConfig
        config = VerificationConfig(
            consistency_thresholds=ConsistencyThresholdConfig(),
        )
        with patch("app.inference.consistency.report.get_verification_config", return_value=config):
            report = ConsistencyReport(
                engine_a_name="a", engine_b_name="b",
                total_cases=200,
                grading_consistency_rate=0.96,
                semantic_grading_consistency_rate=0.98,
                clinical_boundary_diff_rate=0.005,
                clinical_boundary_diff_rate_ci_upper=0.015,
                hallucination_rate_diff=0.005,
                text_consistency_rate=0.91,
                organ_breakdown={
                    "thyroid": {"total": 100, "grading_consistency_rate": 0.93, "clinical_boundary_diff_rate": 0.005},
                    "liver": {"total": 100, "grading_consistency_rate": 0.96, "clinical_boundary_diff_rate": 0.005},
                },
            )
            assert report.gate_status == GateStatus.CONDITIONAL
            assert len(report.organ_gate_failures) == 1
            assert report.organ_gate_failures[0]["organ"] == "thyroid"

    def test_organ_severe_failure_gate_failed(self):
        """某器官分级一致率<90% → Gate应为FAILED"""
        from unittest.mock import patch
        from app.inference.verification_config import VerificationConfig, ConsistencyThresholdConfig
        config = VerificationConfig(
            consistency_thresholds=ConsistencyThresholdConfig(),
        )
        with patch("app.inference.consistency.report.get_verification_config", return_value=config):
            report = ConsistencyReport(
                engine_a_name="a", engine_b_name="b",
                total_cases=200,
                grading_consistency_rate=0.96,
                semantic_grading_consistency_rate=0.98,
                clinical_boundary_diff_rate=0.005,
                clinical_boundary_diff_rate_ci_upper=0.015,
                hallucination_rate_diff=0.005,
                text_consistency_rate=0.91,
                organ_breakdown={
                    "thyroid": {"total": 100, "grading_consistency_rate": 0.88, "clinical_boundary_diff_rate": 0.005},
                    "liver": {"total": 100, "grading_consistency_rate": 0.96, "clinical_boundary_diff_rate": 0.005},
                },
            )
            assert report.gate_status == GateStatus.FAILED

    def test_all_organs_pass_gate_passed(self):
        """所有器官均达标 → Gate应为PASSED"""
        from unittest.mock import patch
        from app.inference.verification_config import VerificationConfig, ConsistencyThresholdConfig
        config = VerificationConfig(
            consistency_thresholds=ConsistencyThresholdConfig(),
        )
        with patch("app.inference.consistency.report.get_verification_config", return_value=config):
            report = ConsistencyReport(
                engine_a_name="a", engine_b_name="b",
                total_cases=200,
                grading_consistency_rate=0.96,
                semantic_grading_consistency_rate=0.98,
                clinical_boundary_diff_rate=0.005,
                clinical_boundary_diff_rate_ci_upper=0.015,
                hallucination_rate_diff=0.005,
                text_consistency_rate=0.91,
                organ_breakdown={
                    "thyroid": {"total": 100, "grading_consistency_rate": 0.96, "clinical_boundary_diff_rate": 0.005},
                    "liver": {"total": 100, "grading_consistency_rate": 0.96, "clinical_boundary_diff_rate": 0.005},
                },
            )
            assert report.gate_status == GateStatus.PASSED
```

**验证**: `pytest tests/unit/llm/test_consistency.py -v -k "organ_gate"` 全部通过

---

### P0-TEST-2: 延迟差异AND逻辑单元测试

**文件**: `tests/unit/llm/test_consistency.py`

**新增测试类**: `TestLatencyDiffAndLogic`

```python
class TestLatencyDiffAndLogic:
    def test_and_logic_pct_exceeds_abs_ok(self):
        """AND模式：百分比超阈值但绝对值未超 → FAILED"""
        from unittest.mock import patch
        from app.inference.verification_config import VerificationConfig, ConsistencyThresholdConfig
        config = VerificationConfig(
            consistency_thresholds=ConsistencyThresholdConfig(
                latency_diff_pct=20.0,
                latency_diff_abs_ms=200.0,
                latency_diff_gate_logic="and",
            ),
        )
        with patch("app.inference.consistency.report.get_verification_config", return_value=config):
            report = ConsistencyReport(
                engine_a_name="a", engine_b_name="b",
                grading_consistency_rate=0.96,
                semantic_grading_consistency_rate=0.98,
                clinical_boundary_diff_rate=0.005,
                clinical_boundary_diff_rate_ci_upper=0.015,
                hallucination_rate_diff=0.005,
                text_consistency_rate=0.91,
                latency_a_p99_ms=1000.0,
                latency_b_p99_ms=1300.0,
                latency_p99_reliable=True,
            )
            assert report.passed is False

    def test_and_logic_abs_exceeds_pct_ok(self):
        """AND模式：绝对值超阈值但百分比未超 → FAILED"""
        from unittest.mock import patch
        from app.inference.verification_config import VerificationConfig, ConsistencyThresholdConfig
        config = VerificationConfig(
            consistency_thresholds=ConsistencyThresholdConfig(
                latency_diff_pct=20.0,
                latency_diff_abs_ms=200.0,
                latency_diff_gate_logic="and",
            ),
        )
        with patch("app.inference.consistency.report.get_verification_config", return_value=config):
            report = ConsistencyReport(
                engine_a_name="a", engine_b_name="b",
                grading_consistency_rate=0.96,
                semantic_grading_consistency_rate=0.98,
                clinical_boundary_diff_rate=0.005,
                clinical_boundary_diff_rate_ci_upper=0.015,
                hallucination_rate_diff=0.005,
                text_consistency_rate=0.91,
                latency_a_p99_ms=2000.0,
                latency_b_p99_ms=2250.0,
                latency_p99_reliable=True,
            )
            assert report.passed is False

    def test_and_logic_both_pass(self):
        """AND模式：百分比和绝对值都通过 → PASSED"""
        from unittest.mock import patch
        from app.inference.verification_config import VerificationConfig, ConsistencyThresholdConfig
        config = VerificationConfig(
            consistency_thresholds=ConsistencyThresholdConfig(
                latency_diff_pct=20.0,
                latency_diff_abs_ms=200.0,
                latency_diff_gate_logic="and",
            ),
        )
        with patch("app.inference.consistency.report.get_verification_config", return_value=config):
            report = ConsistencyReport(
                engine_a_name="a", engine_b_name="b",
                grading_consistency_rate=0.96,
                semantic_grading_consistency_rate=0.98,
                clinical_boundary_diff_rate=0.005,
                clinical_boundary_diff_rate_ci_upper=0.015,
                hallucination_rate_diff=0.005,
                text_consistency_rate=0.91,
                latency_a_p99_ms=2000.0,
                latency_b_p99_ms=2100.0,
                latency_p99_reliable=True,
            )
            assert report.passed is True
```

**验证**: `pytest tests/unit/llm/test_consistency.py -v -k "latency_diff_and"` 全部通过

---

### P0-TEST-3: BI-RADS 0类单元测试

**文件**: `tests/unit/llm/test_consistency.py`

**新增测试**: 在 `TestExtractGrading` 和 `TestConsistencyPair` 中追加

```python
# 在 TestConsistencyPair 中新增
def test_breast_birads_0_to_3_boundary_crossing(self):
    pair = ConsistencyPair(
        case_id="001", organ="breast", prompt="test",
        expected_grading="0",
        engine_a_result=GradingResult(text="a", grading="0"),
        engine_b_result=GradingResult(text="b", grading="3"),
    )
    assert pair.is_boundary_crossing is True

def test_breast_birads_0_to_5_critical_boundary(self):
    pair = ConsistencyPair(
        case_id="001", organ="breast", prompt="test",
        expected_grading="0",
        engine_a_result=GradingResult(text="a", grading="0"),
        engine_b_result=GradingResult(text="b", grading="5"),
        boundary_weight=3.0,
        is_critical_boundary=True,
    )
    assert pair.boundary_severity == "critical"
```

**验证**: `pytest tests/unit/llm/test_consistency.py -v -k "birads_0"` 全部通过

---

## 第二部分：P1级修复详细实施方案

### P1-1: 扩展否定词列表

**问题**: `NEGATION_TERMS` 缺少"未发现"、"未提示"、"不伴"等临床常见否定表达

**修改文件**: `app/inference/hallucination_detector.py` — `ForbiddenTermsDetector.NEGATION_TERMS`

**当前代码** (L77):
```python
NEGATION_TERMS = {"排除", "未见", "不考虑", "无", "未发现", "否认", "不支持", "不可能"}
```

**修改为**:
```python
NEGATION_TERMS = {
    "排除", "未见", "不考虑", "无", "未发现", "否认", "不支持", "不可能",
    "未提示", "不伴", "未查及", "未探及", "未见明显", "不除外",
}
```

**新增否定词说明**:
| 否定词 | 临床场景 | 预期效果 |
|--------|---------|---------|
| 未提示 | "未提示骨转移" | 骨转移不标记为幻觉 |
| 不伴 | "不伴骨转移" | 骨转移不标记为幻觉 |
| 未查及 | "未查及明显占位" | 占位相关不标记 |
| 未探及 | "未探及明显血流信号" | 血流相关不标记 |
| 未见明显 | "未见明显转移征象" | 转移不标记 |
| 不除外 | "不除外恶性可能" | 特殊处理：标记为info而非hallucination |

**新增测试** (在 `tests/unit/llm/test_hallucination_detector.py` 中):
```python
class TestNegationExpansion:
    def test_negation_weitishi(self):
        detector = ForbiddenTermsDetector()
        text = "甲状腺结节，未提示骨转移"
        issues = detector.detect(text, "thyroid")
        bone_issues = [i for i in issues if "骨转移" in i.description]
        assert len(bone_issues) == 0

    def test_negation_buban(self):
        detector = ForbiddenTermsDetector()
        text = "甲状腺结节，不伴骨转移"
        issues = detector.detect(text, "thyroid")
        bone_issues = [i for i in issues if "骨转移" in i.description]
        assert len(bone_issues) == 0

    def test_negation_weichaji(self):
        detector = ForbiddenTermsDetector()
        text = "肝脏未查及明显占位，伴肺癌"
        issues = detector.detect(text, "liver")
        lung_issues = [i for i in issues if "肺癌" in i.description]
        assert len(lung_issues) >= 1

    def test_negation_weijianmingxian(self):
        detector = ForbiddenTermsDetector()
        text = "未见明显骨转移征象"
        issues = detector.detect(text, "thyroid")
        bone_issues = [i for i in issues if "骨转移" in i.description]
        assert len(bone_issues) == 0

    def test_no_negation_still_detected(self):
        detector = ForbiddenTermsDetector()
        text = "甲状腺结节伴骨转移"
        issues = detector.detect(text, "thyroid")
        bone_issues = [i for i in issues if "骨转移" in i.description]
        assert len(bone_issues) >= 1
```

**验证**: `pytest tests/unit/llm/test_hallucination_detector.py -v -k "negation"` 全部通过

---

### P1-2: 实现窗口式上下文检测

**问题**: `_is_context_allowed` 已使用窗口检测（L151-155），但窗口大小硬编码为100字符，且不可配置

**当前代码** (L150-155):
```python
@staticmethod
def _is_context_allowed(text: str, term_pos: int, term_len: int, context_word: str, window: int = 100) -> bool:
    start = max(0, term_pos - window)
    end = min(len(text), term_pos + term_len + window)
    local_context = text[start:end]
    return context_word in local_context
```

**修改方案**:

1. 在 `ForbiddenTermConfig` 中新增 `context_window_chars` 字段:
   ```python
   # verification_config.py — ForbiddenTermConfig
   context_window_chars: int = Field(100, description="上下文允许检测窗口大小(字符数)")
   ```

2. 修改 `_is_context_allowed` 方法签名，接受 `window` 参数:
   ```python
   @staticmethod
   def _is_context_allowed(text: str, term_pos: int, term_len: int, context_word: str, window: int = 100) -> bool:
       start = max(0, term_pos - window)
       end = min(len(text), term_pos + term_len + window)
       local_context = text[start:end]
       return context_word in local_context
   ```

3. 修改 `detect` 方法中的调用，传入 `term_config.context_window_chars`:
   ```python
   if has_allow_if_context and self._is_context_allowed(
       text, pos, len(search_text), term_config.allow_if_context,
       term_config.context_window_chars
   ):
   ```

4. 新增测试:
   ```python
   class TestContextWindowDetection:
       def test_context_in_window_allowed(self):
           detector = ForbiddenTermsDetector()
           text = "甲状腺癌伴骨转移，TI-RADS 5类"
           issues = detector.detect(text, "thyroid")
           bone_issues = [i for i in issues if "骨转移" in i.description]
           assert len(bone_issues) == 0

       def test_context_outside_window_detected(self):
           from app.inference.verification_config import (
               ForbiddenTermConfig, ForbiddenTermsConfig, StrategyWeightConfig,
               VerificationConfig, get_default_boundary_config,
               get_default_numerical_validation_config,
           )
           custom_terms = ForbiddenTermsConfig(
               organ_terms={
                   "thyroid": [
                       ForbiddenTermConfig(
                           term="骨转移", severity=3,
                           description="骨转移",
                           allow_if_context="甲状腺",
                           context_window_chars=10,
                       ),
                   ],
               },
           )
           config = VerificationConfig(
               boundary_config=get_default_boundary_config(),
               forbidden_terms_config=custom_terms,
               strategy_weights=StrategyWeightConfig(),
               numerical_validation_config=get_default_numerical_validation_config(),
           )
           detector = ForbiddenTermsDetector(config=config)
           text = "甲状腺癌" + "。"*50 + "伴骨转移"
           issues = detector.detect(text, "thyroid")
           bone_issues = [i for i in issues if "骨转移" in i.description]
           assert len(bone_issues) >= 1

       def test_boundary_at_text_start(self):
           detector = ForbiddenTermsDetector()
           text = "骨转移，甲状腺癌伴"
           issues = detector.detect(text, "thyroid")
           bone_issues = [i for i in issues if "骨转移" in i.description]
           assert len(bone_issues) == 0
   ```

**验证**: `pytest tests/unit/llm/test_hallucination_detector.py -v -k "context_window"` 全部通过

---

### P1-3: 增加per-strategy幻觉率差异统计

**修改文件**:
- `app/inference/consistency/report.py` — `ConsistencyReport` 新增字段
- `app/inference/consistency/verifier.py` — `_compute_hallucination_stats()`

**步骤1**: 在 `ConsistencyReport` 中新增字段:
```python
strategy_hallucination_diff: Dict[str, float] = field(default_factory=dict)
```

**步骤2**: 在 `_compute_hallucination_stats()` 末尾添加:
```python
strategy_diff: Dict[str, float] = {}
for strategy in DetectionStrategy:
    a_count = sum(
        1 for p in pairs
        if p.engine_a_result and any(
            i.get("strategy") == strategy.value
            for i in p.engine_a_result.hallucination_issues
        )
    )
    b_count = sum(
        1 for p in pairs
        if p.engine_b_result and any(
            i.get("strategy") == strategy.value
            for i in p.engine_b_result.hallucination_issues
        )
    )
    if len(pairs) > 0:
        strategy_diff[strategy.value] = abs(a_count - b_count) / len(pairs)
report.strategy_hallucination_diff = strategy_diff
```

**步骤3**: 新增测试:
```python
class TestStrategyHallucinationDiff:
    @pytest.mark.asyncio
    async def test_strategy_diff_populated(self):
        engine_a = MockInferenceEngine(
            responses=["甲状腺结节伴肺癌转移，TI-RADS 4a类"] * 3,
            name="a",
        )
        engine_b = MockInferenceEngine(
            responses=["甲状腺结节，TI-RADS 4a类"] * 3,
            name="b",
        )
        cases = [
            {"case_id": f"00{i}", "organ": "thyroid", "prompt": "test"}
            for i in range(3)
        ]
        report = await ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b).verify(cases)
        assert isinstance(report.strategy_hallucination_diff, dict)
        if report.strategy_hallucination_diff:
            assert "forbidden_terms" in report.strategy_hallucination_diff
```

**验证**: `pytest tests/unit/llm/test_consistency.py -v -k "strategy_hallucination"` 全部通过

---

### P1-4: 修复`_LVEF_TO_GRADING`线程安全问题

**问题**: `_LVEF_TO_GRADING` 是全局可变列表，`_sync_lvef_thresholds_from_config()` 直接替换全局引用，多线程下不安全

**修改文件**: `app/inference/grading_definitions.py`

**步骤1**: 将 `_LVEF_TO_GRADING` 改为缓存+锁模式:
```python
import threading

_LVEF_TO_GRADING_DEFAULTS: List[Tuple[float, str]] = [
    (55.0, "normal"),
    (45.0, "mild"),
    (30.0, "moderate"),
    (0.0, "severe"),
]

_lvef_cache: Optional[List[Tuple[float, str]]] = None
_lvef_cache_lock = threading.Lock()


def _get_lvef_grading_table() -> List[Tuple[float, str]]:
    global _lvef_cache
    if _lvef_cache is not None:
        return _lvef_cache
    with _lvef_cache_lock:
        if _lvef_cache is not None:
            return _lvef_cache
        _lvef_cache = list(_LVEF_TO_GRADING_DEFAULTS)
        return _lvef_cache


def _sync_lvef_thresholds_from_config() -> None:
    global _lvef_cache
    try:
        from app.inference.verification_config import get_verification_config
        config = get_verification_config()
        cardiac_ranges = config.numerical_validation_config.organ_ranges.get("cardiac", [])
        lvef_range = next((r for r in cardiac_ranges if r.parameter == "LVEF"), None)
        if lvef_range is not None:
            new_table = [
                (lvef_range.max_value, "normal"),
                (55.0, "mild"),
                (lvef_range.min_value + 10.0, "moderate"),
                (lvef_range.min_value, "severe"),
            ]
            with _lvef_cache_lock:
                _lvef_cache = new_table
    except Exception:
        with _lvef_cache_lock:
            _lvef_cache = list(_LVEF_TO_GRADING_DEFAULTS)
```

**步骤2**: 修改 `_map_lvef_to_grading` 使用新接口:
```python
def _map_lvef_to_grading(lvef_str: str) -> str:
    try:
        lvef = float(lvef_str)
    except (ValueError, TypeError):
        return lvef_str
    for threshold, label in _get_lvef_grading_table():
        if lvef >= threshold:
            return label
    return "severe"
```

**步骤3**: 新增测试:
```python
class TestLVEFThreadSafety:
    def test_concurrent_lvef_access(self):
        import threading
        results = []
        errors = []
        def access_lvef():
            try:
                for _ in range(100):
                    result = _map_lvef_to_grading("50")
                    results.append(result)
            except Exception as e:
                errors.append(str(e))
        threads = [threading.Thread(target=access_lvef) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
        assert all(r == "mild" for r in results)
```

**验证**: `pytest tests/unit/llm/test_consistency.py -v -k "lvef_thread"` 全部通过

---

### P1-5: 增加ground truth标注指南

**新增文件**: `docs/testing/hallucination_ground_truth_guide.md`

**内容结构**:
1. `expected_hallucination` 字段定义：True/False/None
2. 标注原则：基于5种检测策略的预期结果
3. 标注示例：每种策略≥2个正例和2个反例
4. 质量控制：双标注一致率要求

**同时修改**: `app/inference/consistency/verifier.py` — `ConsistencyVerifier` docstring中引用此指南

**验证**: 文档完成 + 相关测试通过

---

### P1-6: 文本截断策略改进

**问题**: `text_similarity()` 仅截取前500字符，丢失文末关键信息（如结论部分）

**修改文件**: `app/inference/consistency/statistics.py` — `text_similarity()`

**当前代码** (L58-64):
```python
def text_similarity(text_a: str, text_b: str, max_chars: int = 500) -> float:
    if not text_a and not text_b:
        return 1.0
    if not text_a or not text_b:
        return 0.0
    trunc_a = text_a[:max_chars] if len(text_a) > max_chars else text_a
    trunc_b = text_b[:max_chars] if len(text_b) > max_chars else text_b
```

**修改为**:
```python
def _smart_truncate(text: str, head: int = 300, tail: int = 200) -> str:
    if len(text) <= head + tail:
        return text
    return text[:head] + text[-tail:]


def text_similarity(text_a: str, text_b: str, max_chars: int = 500) -> float:
    if not text_a and not text_b:
        return 1.0
    if not text_a or not text_b:
        return 0.0
    trunc_a = _smart_truncate(text_a, head=300, tail=200)
    trunc_b = _smart_truncate(text_b, head=300, tail=200)
```

**新增测试**:
```python
class TestSmartTruncate:
    def test_short_text_no_truncation(self):
        assert _smart_truncate("短文本", head=300, tail=200) == "短文本"

    def test_medium_text_head_tail(self):
        text = "A" * 600
        result = _smart_truncate(text, head=300, tail=200)
        assert len(result) == 500
        assert result[:300] == "A" * 300
        assert result[-200:] == "A" * 200

    def test_long_text_preserves_conclusion(self):
        text = "描述部分" * 100 + "结论：TI-RADS 4a类"
        result = _smart_truncate(text, head=300, tail=200)
        assert "TI-RADS 4a类" in result

    def test_text_similarity_with_conclusion_at_end(self):
        text_a = "甲状腺结节描述" * 50 + "TI-RADS 4a类"
        text_b = "甲状腺结节描述" * 50 + "TI-RADS 4a类"
        sim = text_similarity(text_a, text_b)
        assert sim > 0.8
```

**验证**: `pytest tests/unit/llm/test_consistency.py -v -k "smart_truncate or text_similarity"` 全部通过

---

## 第三部分：P2级修复详细实施方案

### P2-1: 为precision/recall/F1增加置信区间

**修改文件**:
- `app/inference/consistency/report.py` — `FalseStats`
- `app/inference/consistency/verifier.py` — `_compute_false_stats()`

**步骤1**: 在 `FalseStats` 中新增字段:
```python
precision_ci_lower: float = 0.0
precision_ci_upper: float = 0.0
recall_ci_lower: float = 0.0
recall_ci_upper: float = 0.0
f1_ci_lower: float = 0.0
f1_ci_upper: float = 0.0
```

**步骤2**: 在 `_compute_false_stats()` 末尾计算CI:
```python
from .statistics import wilson_confidence_interval

total_positive = a_tp + b_tp + a_fp + b_fp
total_actual_positive = a_tp + b_tp + a_fn + b_fn

if total_positive > 0:
    p_ci = wilson_confidence_interval(a_tp + b_tp, total_positive)
    stats.precision_ci_lower, stats.precision_ci_upper = p_ci
if total_actual_positive > 0:
    r_ci = wilson_confidence_interval(a_tp + b_tp, total_actual_positive)
    stats.recall_ci_lower, stats.recall_ci_upper = r_ci
```

**验证**: `pytest tests/unit/llm/test_consistency.py -v -k "false_stats_ci"` 全部通过

---

### P2-2: 集成AdaptiveThresholdConfig到验证流程

**修改文件**:
- `app/inference/consistency/report.py` — `gate_status` property

**步骤**: 在 `gate_status` 判定中，当 `self._config.adaptive_thresholds` 非空时，使用自适应阈值:
```python
if self._config and self._config.adaptive_thresholds:
    for at in self._config.adaptive_thresholds:
        if at.metric_name == "grading_consistency_rate":
            historical = self._config._historical_pass_rates.get("grading_consistency_rate", [])
            gc_threshold = compute_adaptive_threshold(at, historical)
        elif at.metric_name == "clinical_boundary_diff_rate":
            historical = self._config._historical_pass_rates.get("clinical_boundary_diff_rate", [])
            bd_threshold = compute_adaptive_threshold(at, historical)
```

**验证**: `pytest tests/unit/llm/test_consistency.py -v -k "adaptive"` 全部通过

---

### P2-3: 增加McNemar检验

**修改文件**:
- `app/inference/consistency/report.py` — `ConsistencyReport` 新增字段
- `app/inference/consistency/verifier.py` — `_compute_hallucination_stats()`

**步骤1**: 实现McNemar检验:
```python
def mcnemar_test(engine_a_results: List[bool], engine_b_results: List[bool]) -> Tuple[float, float]:
    b = sum(1 for a, b in zip(engine_a_results, engine_b_results) if a and not b)
    c = sum(1 for a, b in zip(engine_a_results, engine_b_results) if not a and b)
    if b + c == 0:
        return (0.0, 1.0)
    statistic = (abs(b - c) - 1) ** 2 / (b + c) if b + c > 0 else 0.0
    from scipy.stats import chi2
    p_value = 1 - chi2.cdf(statistic, df=1)
    return (statistic, p_value)
```

**步骤2**: 在 `ConsistencyReport` 新增字段:
```python
hallucination_diff_p_value: float = 0.0
```

**注意**: scipy为可选依赖，需提供fallback:
```python
try:
    from scipy.stats import chi2
except ImportError:
    chi2 = None
```

**验证**: `pytest tests/unit/llm/test_consistency.py -v -k "mcnemar"` 全部通过

---

### P2-4: 量化优化建议

**修改文件**: `app/inference/consistency/failure_analysis.py`

**修改建议生成逻辑**: 将泛化建议改为包含量化数据的建议

**示例修改**:
```python
# 原: "建议增加thyroid专项训练数据"
# 改为:
f"建议增加≥50例thyroid TI-RADS 3→4边界训练数据（当前边界差异率{organ_data.get('clinical_boundary_diff_rate', 0):.2%}，需降至≤1%）"
```

---

### P2-5: 追溯链增加中间推理细节

**修改文件**: `app/inference/consistency/failure_analysis.py`

**在 boundary_check 步骤中增加**:
```python
trace["steps"].append({
    "stage": "boundary_check",
    "rule": f"boundary_weight={p.boundary_weight}",
    "detail": f"engine_a={grading_a}, engine_b={grading_b}, severity={severity}",
    "matched_boundary": f"{from_grade}→{to_grade} (weight={weight}, is_critical={is_critical})",
    "severity": "critical" if p.is_critical_boundary else "warning",
})
```

---

### P2-6: 增加器官级延迟差异百分比

**修改文件**: `app/inference/consistency/organ_breakdown.py`

**在 `compute_organ_breakdown()` 的器官统计中新增**:
```python
"latency_diff_pct": (
    abs(organ_latency_a_p99 - organ_latency_b_p99) / min(organ_latency_a_p99, organ_latency_b_p99) * 100
    if min(organ_latency_a_p99, organ_latency_b_p99) > 0 else 0.0
),
"latency_diff_abs_ms": abs(organ_latency_a_p99 - organ_latency_b_p99),
```

---

### P2-7: 文本相似度权重经验校准

**修改文件**:
- `app/inference/verification_config.py` — 新增 `TextSimilarityConfig`
- `app/inference/consistency/statistics.py` — 从配置读取参数

**步骤1**: 新增配置类:
```python
class TextSimilarityConfig(BaseModel):
    trigram_weight: float = Field(0.4, description="trigram Jaccard权重")
    edit_ratio_weight: float = Field(0.4, description="编辑距离比率权重")
    bigram_weight: float = Field(0.2, description="bigram Jaccard权重")
    head_chars: int = Field(300, description="截断头部字符数")
    tail_chars: int = Field(200, description="截断尾部字符数")
    semantic_equivalent_threshold: float = Field(0.5, description="语义等价文本相似度阈值")
    non_equivalent_threshold: float = Field(0.8, description="非等价文本相似度阈值")
```

**步骤2**: 在 `VerificationConfig` 中新增:
```python
text_similarity_config: TextSimilarityConfig = Field(
    default_factory=TextSimilarityConfig,
    description="文本相似度计算配置"
)
```

**步骤3**: 修改 `text_similarity()` 和 `text_consistency_with_grading()` 从配置读取参数

---

## 第四部分：Phase0A任务详细执行实施方案

### T1: 显存碎片化验证执行方案

**目标**: 验证FunASR+vLLM+TensorRT混合运行4小时后显存碎片化率<30%

**当前状态**: `gpu_memory_monitor.py` 已创建，集成测试已创建，待实际GPU环境执行

**执行步骤**:

1. **环境准备**
   - 确认GPU服务器可用（RTX 3060 12GB+）
   - 安装nvidia-smi CLI工具
   - 部署FunASR ONNX Runtime模型
   - 启动vLLM服务（端口8002）
   - 编译TensorRT视觉模型

2. **快速验证模式** (5分钟)
   ```bash
   pytest tests/integration/llm/test_gpu_memory_fragmentation.py -v -m "phase0a and quick"
   ```
   - 验证 `GPUMemoryMonitor.take_snapshot()` 能正确获取GPU显存信息
   - 验证碎片化率计算逻辑正确
   - 验证 `FragmentationReport` 生成正确

3. **完整验证模式** (4小时)
   ```bash
   pytest tests/integration/llm/test_gpu_memory_fragmentation.py -v -m "phase0a and full"
   ```
   - 启动全部模型
   - 以10例/小时频率模拟诊断请求
   - 每60秒采集一次显存快照
   - 4小时后验证碎片化率<30%

4. **Gate 0A集成**
   - 在 `test_phase0a_consistency.py` 中增加碎片化率Gate检查
   - 阈值: <30% PASSED, 30%-40% CONDITIONAL, >40% FAILED

5. **预防性热重载验证**
   - 验证每2小时无诊断请求时自动执行预防性热重载
   - 验证热重载后碎片化率下降

**验收标准**:
- [ ] `GPUMemoryMonitor` 单元测试通过
- [ ] 快速验证模式通过
- [ ] 完整验证模式通过（碎片化率<30%）
- [ ] Gate 0A集成测试包含碎片化率检查
- [ ] 预防性热重载策略验证通过

**风险缓解**:
- 无GPU环境：使用 `skipif` 标记跳过
- 4小时测试超时：提供5分钟快速模式
- nvidia-smi不可用：返回None并记录warning

---

### T2: compact模式内存实测执行方案

**目标**: 验证64GB内存配置下compact模式的内存使用符合预算（12-22GB占用，剩余~40GB）

**执行步骤**:

1. **实现系统内存监控模块**
   - 文件: `app/inference/system_memory_monitor.py`
   - 依赖: `psutil`
   - 功能:
     ```python
     @dataclass
     class SystemMemorySnapshot:
         timestamp: str
         total_memory_gb: float
         used_memory_gb: float
         available_memory_gb: float
         process_memory_gb: Dict[str, float]

     class SystemMemoryMonitor:
         def __init__(self, target_processes: List[str] = None): ...
         def take_snapshot(self) -> SystemMemorySnapshot: ...
         async def monitor_during_load_test(self, duration_seconds: float, ...) -> List[SystemMemorySnapshot]: ...
         def compute_peak_usage(self, snapshots: List[SystemMemorySnapshot]) -> Dict[str, float]: ...
     ```

2. **定义内存预算验证点**
   ```python
   @dataclass
   class MemoryBudget:
       total_system_memory_gb: float = 64.0
       llama_cpp_expected_gb: float = 3.0
       llama_cpp_cpu_expected_gb: float = 8.0
       funasr_expected_gb: float = 2.0
       tensorrt_expected_gb: float = 1.5
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

**执行步骤**:

1. **设计GPU共享调度架构**
   - 文件: `app/inference/gpu_scheduler.py`
   - 核心类: `GPUScheduler`
   ```python
   class GPUScheduler:
       def __init__(self, total_gpu_memory_mb: int, ...): ...
       async def acquire_memory(self, task_type: str, required_mb: int, priority: int = 0) -> bool: ...
       async def release_memory(self, task_type: str, required_mb: int) -> None: ...
       async def wait_for_memory(self, task_type: str, required_mb: int, timeout: float = 30.0) -> bool: ...
       def get_available_memory(self) -> int: ...
       def get_allocation_status(self) -> Dict[str, int]: ...
   ```

2. **实现调度策略**
   - 优先级调度: LLM推理 > 语音推理 > 视觉推理
   - 显存不足时排队/降级机制
   - 预防性热重载触发（碎片化率>40%）

3. **集成到InferenceEngine**
   - `LlamaCppEngine` 推理前调用 `acquire_memory()`
   - 推理完成后调用 `release_memory()`
   - 显存不足时排队等待或降级到CPU推理

4. **编写测试**
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
   - 术语识别准确率 ≥ 90%
   - 推理延迟 < 500ms（RTX 3060）
   - 常驻显存 ≤ 0.5GB

4. **集成测试**
   - 文件: `tests/integration/speech/test_funasr.py`

**验收标准**:
- [ ] FunASR ONNX Runtime部署完成
- [ ] 术语识别准确率≥90%
- [ ] 推理延迟<500ms
- [ ] 常驻显存≤0.5GB

---

### T5: LLM基座模型选型对比执行方案

**目标**: 完成Qwen2.5-7B/ChatGLM4-9B/InternLM2.5-7B的对比验证

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

4. **Gate判定**
   - accuracy ≥ 89%（基层）
   - p99_latency ≤ 3000ms
   - quantized_hallucination_increment ≤ 0.5%

**验收标准**:
- [ ] 3个候选模型全部完成对比
- [ ] 对比报告生成
- [ ] 确定最优模型
- [ ] 最优模型满足所有Gate标准

---

## 第五部分：核心参考文档更新方案

### 文档1: 07-数据标注与模型评估.md

**更新位置**: §4 模型导出流水线规范 — 一致性验证要求

**更新内容**:
- 增加器官级Gate检查要求：每个器官须独立满足分级一致率≥95%和边界差异率≤1%
- 增加per-strategy幻觉率差异统计要求
- 增加McNemar统计显著性检验要求

### 文档2: 06-开发环境与进度规划.md

**更新位置**: §6.3 Gate 0A验证项

**更新内容**:
- 显存碎片化验证：明确"4小时持续运行后测量，碎片化率<30%（warning），<40%（热重载触发）"
- compact模式内存实测：明确"64GB配置下总占用≤22GB，剩余≥40GB"
- 延迟差异双阈值：明确"百分比阈值和绝对值阈值须同时满足（AND逻辑）"

### 文档3: 01-系统架构设计.md

**更新位置**: §5.7 推理框架选型

**更新内容**:
- 延迟差异判定规则：增加双阈值AND逻辑说明
- 器官级一致性验证要求
- GPU共享调度策略架构说明

---

## 第六部分：实施时间线

```
Week 2 (当前): P0测试补充 + P1修复
  Day 1: P0-TEST-1~3 单元测试补充 + P1-1 否定词扩展
  Day 2: P1-2 窗口式上下文检测 + P1-3 per-strategy幻觉率差异
  Day 3: P1-4 LVEF线程安全 + P1-5 ground truth标注指南
  Day 4: P1-6 文本截断策略 + T1 显存碎片化验证执行
  Day 5: T2 compact内存实测

Week 3: Phase0A任务推进 + P2启动
  Day 1-2: T3 GPU共享调度策略实现
  Day 3-4: T4 FunASR语音部署
  Day 4-5: P2-1~P2-3 置信区间/自适应阈值/McNemar检验

Week 4: Phase0A收尾 + P2完成 + 文档更新
  Day 1: T5 LLM基座模型选型对比执行
  Day 2-3: P2-4~P2-7 量化建议/追溯链/器官延迟/文本权重
  Day 3-4: 核心参考文档更新
  Day 4-5: 全量回归测试 + Gate 0A综合验证
```

---

## 第七部分：风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| P1-4 LVEF缓存修改引入回归 | 低 | 中 | 保留旧接口`_LVEF_TO_GRADING`作为兼容别名 |
| P1-6 截断策略修改影响现有相似度计算 | 中 | 中 | 先运行现有测试确认无回归 |
| P2-3 scipy依赖缺失 | 中 | 低 | 提供纯Python fallback实现 |
| T1-T4 GPU服务器不可用 | 中 | 高 | 所有GPU测试用skipif标记 |
| T5 候选模型GGUF转换失败 | 低 | 中 | 优先验证Qwen2.5-7B |
| P0测试补充暴露P0代码bug | 低 | 高 | 如发现立即修复 |
