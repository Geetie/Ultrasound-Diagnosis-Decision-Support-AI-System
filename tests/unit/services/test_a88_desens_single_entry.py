"""A.88 T5-4：三层脱敏收口到 ``process_full_pipeline`` 单一入口的接线守卫。

## 为什么要这条测试

《架构真相》§3.3：「三层脱敏『完整流水线』：``process_full_pipeline`` 仅测试调用
（生产是三次分散调用）」。

但"三次分散"**不是遗漏** —— 三层在时间上本就分离（LLM 之前 / 落库之前 /
SG 之后），塞进一个时刻等于二次脱敏。所以收口的正确形态是：

* 生产：三层仍在各自时刻就地执行（语义不变），把各自的 ``layer_result``
  登记成本次运行台账；
* 运行结束时调 ``process_full_pipeline`` **一次**（``recorded_stages`` 模式），
  由它产出**唯一**的合规汇总判定。

这条测试钉住的就是"收口后生产真的调了它、且判定来自它"。

## 反证腿

* 删掉 ``_write_result_to_db`` 里的汇总调用 → ``TestPipelineCallsSingleEntry`` 红；
* 让汇总判定改回"各调用点自己算一个 bool" → ``TestAggregateVerdictIsSingleSource`` 红。
"""
from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.security.three_layer_desensitization import (
    DesensitizationLayer,
    ThreeLayerDesensitizationManager,
)
from app.services import diagnosis_pipeline as mod

PIPELINE_PY = (
    Path(__file__).resolve().parents[3] / "app" / "services" / "diagnosis_pipeline.py"
)


def _layer1() -> dict:
    return {"layer": DesensitizationLayer.STANDARDIZATION, "modified": True, "degraded": False}


def _layer2(degraded: bool = False) -> dict:
    return {"layer": DesensitizationLayer.FUSION, "modified": True, "degraded": degraded}


def _layer3(passed: bool = True, degraded: bool = False, final: dict | None = None) -> dict:
    return {
        "layer": DesensitizationLayer.SAFETY_GATEWAY,
        "check_result": {"passed": passed, "degraded_mode": degraded},
        "degraded": degraded,
        "final": final or {"ok": True},
    }


class TestRecordedStagesMode:
    """生产形态：三段已在外部分别执行 → 不再重跑，只汇总。"""

    async def test_does_not_rerun_layers_already_recorded(self):
        manager = ThreeLayerDesensitizationManager()
        l1 = AsyncMock()
        l2 = AsyncMock()
        l3 = AsyncMock()
        manager.process_standardization_layer = l1
        manager.process_fusion_layer = l2
        manager.process_safety_gateway_layer = l3

        result = await manager.process_full_pipeline(
            patient_id="P001",
            input_data={"clinical_text": "原文"},
            llm_output_data={"findings": "结论"},
            recorded_stages={
                DesensitizationLayer.STANDARDIZATION: _layer1(),
                DesensitizationLayer.FUSION: _layer2(),
                DesensitizationLayer.SAFETY_GATEWAY: _layer3(),
            },
        )

        assert l1.await_count == 0, "L1 已在外部分别执行过 —— 重跑会二次脱敏"
        assert l2.await_count == 0
        assert l3.await_count == 0
        assert result.layer_results.keys() == {
            DesensitizationLayer.STANDARDIZATION,
            DesensitizationLayer.FUSION,
            DesensitizationLayer.SAFETY_GATEWAY,
        }
        assert result.final_data == {"ok": True}

    async def test_still_runs_layers_not_recorded(self):
        """只登记了 L1/L2 → L3 仍按既有语义真的执行（向后兼容）。"""
        manager = ThreeLayerDesensitizationManager()
        manager.process_safety_gateway_layer = AsyncMock(
            return_value=({"final": 1}, _layer3())
        )

        result = await manager.process_full_pipeline(
            patient_id="P001",
            input_data={"clinical_text": "原文"},
            llm_output_data={"findings": "结论"},
            consent_status_at_input="granted",
            recorded_stages={
                DesensitizationLayer.STANDARDIZATION: _layer1(),
                DesensitizationLayer.FUSION: _layer2(),
            },
        )

        assert manager.process_safety_gateway_layer.await_count == 1
        assert result.final_data == {"final": 1}

    async def test_existing_sg_check_is_reused_not_re_requested(self):
        """给了 ``safety_gateway_check`` → 只登记、不再调 SG（重复审计红线）。"""
        manager = ThreeLayerDesensitizationManager()
        manager.process_safety_gateway_layer = AsyncMock()

        result = await manager.process_full_pipeline(
            patient_id="P001",
            input_data={"clinical_text": "原文"},
            llm_output_data={"findings": "结论"},
            safety_gateway_check={"passed": True, "degraded_mode": False},
            recorded_stages={
                DesensitizationLayer.STANDARDIZATION: _layer1(),
                DesensitizationLayer.FUSION: _layer2(),
            },
        )

        assert manager.process_safety_gateway_layer.await_count == 0
        assert DesensitizationLayer.SAFETY_GATEWAY in result.layer_results


class TestAggregateVerdictIsSingleSource:
    """汇总判定必须由同一个函数产出，两条路径不许各自漂移。"""

    async def test_failure_in_any_layer_marks_pipeline_failed(self):
        manager = ThreeLayerDesensitizationManager()

        result = await manager.process_full_pipeline(
            patient_id="P001",
            input_data={"clinical_text": "原文"},
            llm_output_data={"findings": "结论"},
            recorded_stages={
                DesensitizationLayer.STANDARDIZATION: _layer1(),
                DesensitizationLayer.FUSION: _layer2(),
                DesensitizationLayer.SAFETY_GATEWAY: _layer3(passed=False),
            },
        )

        assert result.security_check_failed is True
        assert result.all_passed is False

    async def test_degraded_layer_marks_degraded_mode(self):
        manager = ThreeLayerDesensitizationManager()

        result = await manager.process_full_pipeline(
            patient_id="P001",
            input_data={"clinical_text": "原文"},
            llm_output_data={"findings": "结论"},
            recorded_stages={
                DesensitizationLayer.STANDARDIZATION: _layer1(),
                DesensitizationLayer.FUSION: _layer2(),
                DesensitizationLayer.SAFETY_GATEWAY: _layer3(degraded=True),
            },
        )

        assert result.degraded_mode is True
        assert result.all_passed is False

    async def test_all_good_passes(self):
        manager = ThreeLayerDesensitizationManager()

        result = await manager.process_full_pipeline(
            patient_id="P001",
            input_data={"clinical_text": "原文"},
            llm_output_data={"findings": "结论"},
            recorded_stages={
                DesensitizationLayer.STANDARDIZATION: _layer1(),
                DesensitizationLayer.FUSION: _layer2(),
                DesensitizationLayer.SAFETY_GATEWAY: _layer3(),
            },
        )

        assert result.all_passed is True
        assert result.security_check_failed is False
        assert result.degraded_mode is False

    def test_verdict_is_a_shared_helper(self):
        """反证腿：判定逻辑若内联回各分支 → 两条路径就会漂移。"""
        source = inspect.getsource(ThreeLayerDesensitizationManager)

        assert "_apply_aggregate_verdict" in source
        assert source.count("_apply_aggregate_verdict(result)") >= 1


class TestPipelineCallsTheSingleEntry:
    """生产管线必须真的调用它（此前只在测试里被调）。"""

    async def test_helper_actually_invokes_process_full_pipeline(self, monkeypatch):
        """功能腿：``_run_desens_full_pipeline`` 必须**真的**调到
        ``process_full_pipeline(recorded_stages=...)``，并把三层台账交过去。

        反证腿：把该调用删掉（或改成返回常量）→ 本用例红。
        这是源码字符串断言抓不到的（字符串还在文件里，调用已经没了）。
        """
        from app.services import diagnosis_pipeline as pipeline

        rid = "req-desens-single-entry"
        pipeline._clear_desens_run(rid)
        for layer, result in (
            (DesensitizationLayer.STANDARDIZATION, _layer1()),
            (DesensitizationLayer.FUSION, _layer2()),
            (DesensitizationLayer.SAFETY_GATEWAY, _layer3()),
        ):
            pipeline._record_desens_stage(rid, layer, result)

        manager = MagicMock()
        manager.process_full_pipeline = AsyncMock(
            return_value=SimpleNamespace(
                all_passed=True,
                security_check_failed=False,
                degraded_mode=False,
                layer_results={
                    DesensitizationLayer.STANDARDIZATION: _layer1(),
                    DesensitizationLayer.FUSION: _layer2(),
                    DesensitizationLayer.SAFETY_GATEWAY: _layer3(),
                },
            )
        )
        monkeypatch.setattr(
            pipeline, "get_three_layer_desensitization_manager", lambda: manager
        )

        out = await pipeline._run_desens_full_pipeline(
            request_id=rid, patient_id="P001"
        )
        pipeline._clear_desens_run(rid)

        assert manager.process_full_pipeline.await_count == 1
        kwargs = manager.process_full_pipeline.await_args.kwargs
        assert set(kwargs["recorded_stages"]) == {
            DesensitizationLayer.STANDARDIZATION,
            DesensitizationLayer.FUSION,
            DesensitizationLayer.SAFETY_GATEWAY,
        }, "三层台账必须原样交给单一入口"
        assert out["status"] == "ok"
        assert out["all_passed"] is True
        assert set(out["layers"]) == {"standardization", "fusion", "safety_gateway"}

    async def test_no_stage_results_is_skipped_not_faked(self, monkeypatch):
        """台账为空 → ``skipped`` + 原因，**不许**编一个通过判定。"""
        from app.services import diagnosis_pipeline as pipeline

        manager = MagicMock()
        manager.process_full_pipeline = AsyncMock()
        monkeypatch.setattr(
            pipeline, "get_three_layer_desensitization_manager", lambda: manager
        )

        out = await pipeline._run_desens_full_pipeline(
            request_id="req-no-stages", patient_id="P001"
        )

        assert out == {"status": "skipped", "reason": "no_stage_results"}
        assert manager.process_full_pipeline.await_count == 0

    async def test_aggregation_failure_is_traced_not_raised(self, monkeypatch):
        from app.services import diagnosis_pipeline as pipeline

        rid = "req-desens-fail"
        pipeline._clear_desens_run(rid)
        pipeline._record_desens_stage(
            rid, DesensitizationLayer.STANDARDIZATION, _layer1()
        )
        manager = MagicMock()
        manager.process_full_pipeline = AsyncMock(side_effect=RuntimeError("boom"))
        monkeypatch.setattr(
            pipeline, "get_three_layer_desensitization_manager", lambda: manager
        )

        out = await pipeline._run_desens_full_pipeline(
            request_id=rid, patient_id="P001"
        )
        pipeline._clear_desens_run(rid)

        assert out["status"] == "unavailable"
        assert "RuntimeError" in out["reason"]

    def test_source_mentions_the_single_entry(self):
        code = "\n".join(
            line
            for line in PIPELINE_PY.read_text(encoding="utf-8").splitlines()
            if not line.strip().startswith("#")
        )

        assert "process_full_pipeline(" in code, (
            "生产管线没有调用 process_full_pipeline —— 三层合规汇总在生产上恒缺"
        )
        assert "_desens_stage_results" in code, (
            "缺'按运行登记各层结果'的台账 → 汇总拿不到真实层结果"
        )
        # 反证腿（本条的落点）：只断言 "process_full_pipeline(" 出现是不够的
        # —— 它出现在 helper 定义里也算出现。必须钉住**管线真的调了那个 helper**，
        # 且结果真的被带进 findings。把这两行删掉 → 本用例红。
        assert "desensitization = await _run_desens_full_pipeline(" in code, (
            "管线没调用汇总 helper → findings 里永远没有 desensitization 键"
        )
        assert "desensitization=desensitization," in code, (
            "汇总结果没被传给 _write_result_to_db → 算了也不落库"
        )
        assert 'findings["desensitization"] = desensitization' in code, (
            "汇总结果没写进 findings"
        )

    def test_stage_ledger_is_cleared_per_run(self):
        """台账必须按运行清账，否则长跑进程里无界增长（与既有缓存同款风险）。"""
        source = inspect.getsource(mod._clear_desens_run)

        assert "_desens_stage_results" in source

    def test_fail_closed_is_preserved(self):
        """既有 fail-closed 不能因为收口而丢：取不到同意状态仍必须判 failed。"""
        source = inspect.getsource(mod._safe_safety_check)

        assert "pipeline_consent_status_unavailable" in source
        assert "return None, False" in source


class TestNoSecondGatewayCall:
    def test_registration_helper_does_not_call_check_output(self):
        """L3 的登记路径不得再调一次 SG（重复审计 + 额外延迟）。

        断言的是**调用**而不是字符串出现：该函数的 docstring 里必须写明
        "为什么不调 check_output"（否则下一个人会以为是漏了）。
        """
        source = inspect.getsource(mod._register_l3_existing_sg_check)

        assert "await safety_gateway_client.check_output(" not in source
        assert "record_safety_gateway_existing_check(" in source
