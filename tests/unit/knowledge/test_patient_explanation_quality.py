"""A.138 / G39：患者话术的确定性质量检查（此前零评估）。

不引入 LLM 判官：这几条可以确定计算，且"给患者听的话"恰恰不能靠模型自评。
边界必须清楚：`passed=True` 只代表上述确定性检查通过，**不代表医学正确**。
"""

from __future__ import annotations

import pytest

from app.knowledge.patient_explanation_quality import evaluate
from app.models.schemas import PatientExplanation

pytestmark = pytest.mark.track_rag


def _pe(**kw) -> PatientExplanation:
    base = {"explanation": "结节多数为良性，本次超声未见明显恶性征象，请规律复查。"}
    base.update(kw)
    return PatientExplanation(**base)


def test_good_explanation_passes():
    q = evaluate(_pe())
    assert q.evaluated is True and q.passed is True and q.issues == []


def test_none_is_not_evaluated_not_passed():
    q = evaluate(None)
    assert q.evaluated is False
    assert q.passed is None, "没有话术 ≠ 通过"


def test_absolute_claim_flagged():
    q = evaluate(_pe(explanation="确诊为恶性，无需再复查。"))
    codes = [i.code for i in q.issues]
    assert "absolute_claim:confirmed_diagnosis" in codes
    assert "absolute_claim:no_followup_needed" in codes


def test_phi_leak_flagged():
    q = evaluate(_pe(explanation="请联系 13800138000 复诊，见 chunk_a1b2。"))
    codes = [i.code for i in q.issues]
    assert "phi_leak:mobile" in codes
    assert "phi_leak:sample_chunk_id" in codes


def test_placeholder_flagged():
    q = evaluate(_pe(explanation="您的结节情况为{TYPE}，请复查。"))
    assert "placeholder" in [i.code for i in q.issues]


def test_not_chinese_flagged():
    q = evaluate(_pe(explanation="Your nodule looks benign, please follow up."))
    assert "not_chinese" in [i.code for i in q.issues]


def test_length_bounds():
    assert "length:too_short" in [i.code for i in evaluate(_pe(explanation="没事")).issues]
    long_text = "结节" * 200
    assert "length:too_long" in [i.code for i in evaluate(_pe(explanation=long_text)).issues]


def test_as_dict_shape_is_contract_stable():
    payload = evaluate(_pe()).as_dict()
    assert set(payload) == {"evaluated", "passed", "issues"}
    assert payload["issues"] == []
