"""02-3 / 02-5 链A 的档位闸门与默认开关回归（B1 批次）。

钉住两个行为差异：
1. ``_self_rag_should_run``：判据从「== FULL」放宽为「!= REALTIME」——
   放宽前 standard 档恒 False（Self-RAG 在生产 UI 路径上从未执行），放宽后
   standard/full 都跑；REALTIME 与 retry 请求仍跳过（反证腿 R2/R3）。
2. ``hallucination_detection_in_pipeline`` 默认值 False → True（02-5 链A A2）：
   默认档开始执行幻觉检测 → CUSUM 有观测样本。
"""

import pytest

from app.config.settings import Settings
from app.models.schemas import ResponseTier
from app.services.diagnosis_pipeline import _self_rag_should_run


class TestSelfRagTierGate:
    """02-3 方案A：档位判据放宽（!= REALTIME）。"""

    def test_standard_tier_runs_self_rag(self):
        # 行为差异主断言：改动前 == FULL 判据下 standard 恒 False
        assert _self_rag_should_run(skip_self_rag=False, effective_tier=ResponseTier.STANDARD) is True

    def test_full_tier_runs_self_rag(self):
        assert _self_rag_should_run(skip_self_rag=False, effective_tier=ResponseTier.FULL) is True

    def test_realtime_tier_skipped(self):
        # 反证腿 R2：REALTIME 档仍跳过（省一次评估 LLM）
        assert _self_rag_should_run(skip_self_rag=False, effective_tier=ResponseTier.REALTIME) is False

    def test_retry_request_skipped_regardless_of_tier(self):
        # 反证腿 R3：retry 请求无论什么档位都跳过
        assert _self_rag_should_run(skip_self_rag=True, effective_tier=ResponseTier.FULL) is False
        assert _self_rag_should_run(skip_self_rag=True, effective_tier=ResponseTier.STANDARD) is False


class TestHallucinationDefaultEnabled:
    """02-5 链A A2：检测开关默认开 → 全档执行。"""

    def test_settings_default_is_true(self):
        assert Settings.model_fields["hallucination_detection_in_pipeline"].default is True

    def test_standard_tier_runs_detection_when_forced(self):
        # 默认开关打开后，standard 档（is_full_tier=False）真跑检测
        # （fusion_result 需带 final_response+organ：无器官无词表 → 不猜）。
        from types import SimpleNamespace

        from app.services.diagnosis_pipeline import _detect_hallucination_safely

        fusion = SimpleNamespace(
            final_response=SimpleNamespace(
                content="超声提示：甲状腺右叶低回声结节，TI-RADS 3 类，建议随访。",
                organ="thyroid",
            )
        )
        result = _detect_hallucination_safely(
            fusion, organ="thyroid", request_id="test-02-5",
            force_enabled=True, is_full_tier=False,
        )
        assert result["checked"] is True, result.get("reason")

    def test_switch_off_still_gates_non_full_tier(self):
        # 显式置 false 时退回「仅 FULL 档」语义（关闭路径不被默认值改变吞掉）
        from app.services.diagnosis_pipeline import _detect_hallucination_safely

        result = _detect_hallucination_safely(
            None, organ=None, request_id="test-02-5", force_enabled=False, is_full_tier=False
        )
        assert result == {
            "checked": False,
            "severity": None,
            "reason": "not_full_tier_and_switch_off",
        }
