"""Unit tests for app.fusion.self_rag (T-GA-FUS-02 Self-RAG 自我评估).

覆盖：
- 启发式评估（无 LLM）：通过 / 失败场景各 5 个；
- LLM 评估：满分通过 / 低分失败 / 解析失败回退；
- 边界：空 content / 阈值校验。
- 验收：质检通过率 ≥ 80%（用通过/失败样本集验证）。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.track_fus]

from app.fusion.self_rag import (
    DEFAULT_HEURISTIC_THRESHOLD,
    DEFAULT_PASS_THRESHOLD,
    SelfRAGEvaluator,
    _parse_self_rag_output,
)
from app.inference.protocols import InferenceError, InferenceErrorCode
from app.models.schemas import Organ
from tests.unit.fus.conftest import (
    FakeInferenceEngine,
    make_response,
)


# ======================================================================
# 启发式评估 - 通过场景
# ======================================================================


@pytest.mark.asyncio
async def test_self_rag_heuristic_pass_high_confidence_with_evidence(
    self_rag_evaluator_heuristic,
):
    """高 conf + 多个证据 + 非空 content → passed=True。"""
    resp = make_response(
        organ=Organ.THYROID,
        content="TR4 边界不清晰，建议活检进一步确诊",
        confidence=0.92,
        evidence=["c1", "c2", "c3", "c4", "c5"],
    )
    result = await self_rag_evaluator_heuristic.evaluate(resp)
    assert result.passed is True
    assert result.score > DEFAULT_PASS_THRESHOLD


@pytest.mark.asyncio
async def test_self_rag_heuristic_pass_moderate_confidence(
    self_rag_evaluator_heuristic,
):
    """中 conf + 充足证据 → passed=True。"""
    resp = make_response(
        organ=Organ.LIVER,
        content="LR-4 中度怀疑恶性",
        confidence=0.85,
        evidence=["c1", "c2", "c3"],
    )
    result = await self_rag_evaluator_heuristic.evaluate(resp)
    assert result.passed is True


@pytest.mark.asyncio
async def test_self_rag_heuristic_pass_threshold(self_rag_evaluator_heuristic):
    """刚好在阈值 0.7 + 至少 1 证据 → passed=True。"""
    resp = make_response(
        organ=Organ.CARDIAC,
        content="LVEF 45%",
        confidence=DEFAULT_HEURISTIC_THRESHOLD,
        evidence=["c1"],
    )
    result = await self_rag_evaluator_heuristic.evaluate(resp)
    assert result.passed is True


@pytest.mark.asyncio
async def test_self_rag_heuristic_pass_full_score(self_rag_evaluator_heuristic):
    """满 confidence + 满 evidence + 长 content → 满分。"""
    resp = make_response(
        organ=Organ.THYROID,
        content="TR5 高度怀疑恶性 " * 20,  # 长 content
        confidence=1.0,
        evidence=["c1", "c2", "c3", "c4", "c5"],
    )
    result = await self_rag_evaluator_heuristic.evaluate(resp)
    assert result.passed is True
    assert result.score == 5.0  # 满分


@pytest.mark.asyncio
async def test_self_rag_heuristic_no_missing_features(self_rag_evaluator_heuristic):
    """启发式不报 missing_features。"""
    resp = make_response(
        organ=Organ.THYROID, content="TR3", confidence=0.9, evidence=["c1", "c2"]
    )
    result = await self_rag_evaluator_heuristic.evaluate(resp)
    assert result.missing_features == []


# ======================================================================
# 启发式评估 - 失败场景
# ======================================================================


@pytest.mark.asyncio
async def test_self_rag_heuristic_fail_low_confidence(self_rag_evaluator_heuristic):
    """低 conf → passed=False。"""
    resp = make_response(
        organ=Organ.THYROID, content="TR3", confidence=0.5, evidence=["c1", "c2"]
    )
    result = await self_rag_evaluator_heuristic.evaluate(resp)
    assert result.passed is False


@pytest.mark.asyncio
async def test_self_rag_heuristic_fail_no_evidence(self_rag_evaluator_heuristic):
    """无证据 → passed=False。"""
    resp = make_response(
        organ=Organ.THYROID, content="TR3", confidence=0.9, evidence=[]
    )
    result = await self_rag_evaluator_heuristic.evaluate(resp)
    assert result.passed is False
    assert "heuristic" in result.reason or "insufficient" in result.reason


@pytest.mark.asyncio
async def test_self_rag_heuristic_fail_empty_content(self_rag_evaluator_heuristic):
    """空 content → passed=False。"""
    resp = make_response(
        organ=Organ.THYROID, content="", confidence=0.9, evidence=["c1"]
    )
    result = await self_rag_evaluator_heuristic.evaluate(resp)
    assert result.passed is False


@pytest.mark.asyncio
async def test_self_rag_heuristic_fail_zero_confidence(self_rag_evaluator_heuristic):
    """confidence=0 → passed=False。"""
    resp = make_response(
        organ=Organ.THYROID, content="TR3", confidence=0.0, evidence=["c1"]
    )
    result = await self_rag_evaluator_heuristic.evaluate(resp)
    assert result.passed is False


@pytest.mark.asyncio
async def test_self_rag_heuristic_fail_below_custom_threshold():
    """自定义启发式阈值 0.95 → 0.85 confidence 失败。"""
    evaluator = SelfRAGEvaluator(inference_engine=None, heuristic_threshold=0.95)
    resp = make_response(
        organ=Organ.THYROID, content="TR3", confidence=0.85, evidence=["c1", "c2"]
    )
    result = await evaluator.evaluate(resp)
    assert result.passed is False


# ======================================================================
# LLM 评估
# ======================================================================


@pytest.mark.asyncio
async def test_self_rag_llm_full_pass(self_rag_evaluator_with_llm):
    """LLM 满 pass（5 5 5 无）→ passed=True, score=5.0。"""
    resp = make_response(
        organ=Organ.THYROID, content="TR4", confidence=0.9, evidence=["c1", "c2"]
    )
    result = await self_rag_evaluator_with_llm.evaluate(resp)
    assert result.passed is True
    assert result.score == 5.0
    assert result.evidence_sufficiency == 5.0
    assert result.evidence_consistency == 5.0
    assert result.feature_completeness == 5.0


@pytest.mark.asyncio
async def test_self_rag_llm_fail_low_score():
    """LLM 低分（2 2 2 钙化）→ passed=False。"""
    fake_llm = FakeInferenceEngine(
        text="evidence_sufficiency: 2\n"
        "evidence_consistency: 2\n"
        "feature_completeness: 2\n"
        "missing_features: 钙化, 边界"
    )
    evaluator = SelfRAGEvaluator(inference_engine=fake_llm)
    resp = make_response(organ=Organ.THYROID, content="TR3", confidence=0.8)
    result = await evaluator.evaluate(resp)
    assert result.passed is False
    assert result.score == 2.0
    assert "钙化" in result.missing_features


@pytest.mark.asyncio
async def test_self_rag_llm_partial_pass():
    """LLM 部分通过（4 3 5 无）→ passed=True（min=3 >= 3.0）。"""
    fake_llm = FakeInferenceEngine(
        text="evidence_sufficiency: 4\n"
        "evidence_consistency: 3\n"
        "feature_completeness: 5\n"
        "missing_features: 无"
    )
    evaluator = SelfRAGEvaluator(inference_engine=fake_llm, pass_threshold=3.0)
    resp = make_response(organ=Organ.THYROID, content="TR3", confidence=0.8)
    result = await evaluator.evaluate(resp)
    assert result.passed is True
    assert result.score == 3.0


@pytest.mark.asyncio
async def test_self_rag_llm_failure_fallback_to_heuristic():
    """LLM 抛错 → 回退到启发式。"""
    fake_llm = FakeInferenceEngine(
        raise_exc=InferenceError(
            error_code=InferenceErrorCode.TIMEOUT, message="timeout"
        )
    )
    evaluator = SelfRAGEvaluator(inference_engine=fake_llm)
    resp = make_response(
        organ=Organ.THYROID, content="TR3", confidence=0.9, evidence=["c1", "c2"]
    )
    result = await evaluator.evaluate(resp)
    # 启发式：conf=0.9 >= 0.7 + 2 evidence + 非空 content → passed=True
    assert result.passed is True
    assert "llm_failed" in result.reason or "heuristic" in result.reason


@pytest.mark.asyncio
async def test_self_rag_llm_parse_failure_fallback_to_heuristic():
    """LLM 输出无法解析 → 回退到启发式。"""
    fake_llm = FakeInferenceEngine(text="not a valid output")
    evaluator = SelfRAGEvaluator(inference_engine=fake_llm)
    resp = make_response(
        organ=Organ.THYROID, content="TR3", confidence=0.9, evidence=["c1"]
    )
    result = await evaluator.evaluate(resp)
    # 启发式兜底
    assert "llm_parse_failed" in result.reason or result.passed is True


# ======================================================================
# 验收：质检通过率 ≥ 80%
# ======================================================================


@pytest.mark.asyncio
async def test_self_rag_acceptance_pass_rate_above_80_percent(
    self_rag_evaluator_heuristic,
):
    """10 个标准样本中 ≥ 8 个通过 → 验收通过。"""
    # 8 个高质响应（应通过）
    high_quality = [
        make_response(
            organ=Organ.THYROID,
            content="TR4 边界不清晰，内见钙化，建议活检",
            confidence=0.9,
            evidence=["c1", "c2", "c3"],
        )
        for _ in range(8)
    ]
    # 2 个低质响应（应失败）
    low_quality = [
        make_response(
            organ=Organ.THYROID, content="", confidence=0.0, evidence=[]
        )
        for _ in range(2)
    ]
    samples = high_quality + low_quality
    passed = 0
    for resp in samples:
        result = await self_rag_evaluator_heuristic.evaluate(resp)
        if result.passed:
            passed += 1
    pass_rate = passed / len(samples)
    assert pass_rate >= 0.8, f"pass_rate={pass_rate:.2%} < 80%"


# ======================================================================
# 边界 / 校验
# ======================================================================


def test_self_rag_pass_threshold_validation():
    """pass_threshold 必须在 [0, 5] 范围。"""
    with pytest.raises(ValueError):
        SelfRAGEvaluator(pass_threshold=6.0)
    with pytest.raises(ValueError):
        SelfRAGEvaluator(pass_threshold=-0.1)


def test_self_rag_heuristic_threshold_validation():
    """heuristic_threshold 必须在 [0, 1] 范围。"""
    with pytest.raises(ValueError):
        SelfRAGEvaluator(heuristic_threshold=1.5)
    with pytest.raises(ValueError):
        SelfRAGEvaluator(heuristic_threshold=-0.1)


def test_self_rag_max_tokens_validation():
    """max_tokens 必须 > 0。"""
    with pytest.raises(ValueError):
        SelfRAGEvaluator(max_tokens=0)


def test_parse_self_rag_output_valid():
    """_parse_self_rag_output 正确解析标准格式。"""
    text = (
        "evidence_sufficiency: 4\n"
        "evidence_consistency: 5\n"
        "feature_completeness: 3\n"
        "missing_features: 钙化, 边界"
    )
    parsed = _parse_self_rag_output(text)
    assert parsed is not None
    sufficiency, consistency, completeness, missing = parsed
    assert sufficiency == 4.0
    assert consistency == 5.0
    assert completeness == 3.0
    assert "钙化" in missing
    assert "边界" in missing


def test_parse_self_rag_output_no_missing():
    """missing_features="无" → 空列表。"""
    text = (
        "evidence_sufficiency: 5\n"
        "evidence_consistency: 5\n"
        "feature_completeness: 5\n"
        "missing_features: 无"
    )
    parsed = _parse_self_rag_output(text)
    assert parsed is not None
    assert parsed[3] == []


def test_parse_self_rag_output_invalid():
    """无效格式 → 返回 None。"""
    assert _parse_self_rag_output("") is None
    assert _parse_self_rag_output("random text") is None
    assert _parse_self_rag_output(None) is None  # type: ignore[arg-type]


def test_parse_self_rag_output_clamp():
    """分数超出 [0, 5] 范围时截断。

    模式本身只接受单字符 0-5 加可选小数（``[0-5](?:\\.\\d+)?``），但
    ``5.5`` 仍能完整匹配（``5`` + ``.5``），需在解析时截断到 ``5.0``。
    """
    text = (
        "evidence_sufficiency: 5.5\n"
        "evidence_consistency: 5\n"
        "feature_completeness: 5\n"
        "missing_features: 无"
    )
    parsed = _parse_self_rag_output(text)
    assert parsed is not None
    sufficiency, consistency, completeness, _ = parsed
    assert sufficiency == 5.0  # 5.5 截断到 5.0
    assert consistency == 5.0
    assert completeness == 5.0


def test_parse_self_rag_output_negative_via_failing_pattern():
    """负数（'-1'）不在 [0-5] 模式范围 → 整体解析失败（保守策略）。

    注：这是有意的 — 负数表明 LLM 输出格式错误，应触发启发式兜底，
    不应静默接受。
    """
    text = (
        "evidence_sufficiency: -1\n"
        "evidence_consistency: 5\n"
        "feature_completeness: 5\n"
        "missing_features: 无"
    )
    parsed = _parse_self_rag_output(text)
    # 模式不匹配 → 返回 None
    assert parsed is None
