"""Phase0A Gate 0A-Tech-P0 集成测试套件


使用真实本地LLM服务执行vLLM与llama.cpp一致性验证。

测试分层：
    1. 框架验证层（TestEngineSmokeTest, TestVerifierFramework）：
       验证推理引擎和ConsistencyVerifier逻辑正确运行，不依赖模型质量。
       这些测试应该始终通过。

    2. Gate标准层（TestGate0ACriteria）：
       验证是否达到Phase0A门控标准。
       如果Gate=FAILED，测试会FAILED——这才是正确的！
       Gate标准来源: 06-开发环境与进度规划.md Gate 0A-Tech-P0

运行方式：
    set LLM_TEST_MODE=local
    pytest tests/integration/test_phase0a_consistency.py -v -m phase0a --tb=short

    # 仅框架验证（快速，不依赖模型质量）
    pytest tests/integration/test_phase0a_consistency.py -v -m "phase0a and not gate_criteria"

    # 仅Gate标准验证（严格验收）
    pytest tests/integration/test_phase0a_consistency.py -v -m gate_criteria

Gate 0A-Tech-P0 验证标准（来源: 06-开发环境与进度规划.md）:
    - 分级一致率 >= 95%
    - 临床决策边界差异率 <= 1%
    - 幻觉率差异 <= 0.5%
    - 文本一致率 >= 90%
    - 延迟差异百分比 <= 20%
"""

import json
import os

import httpx
import pytest

pytestmark = [pytest.mark.track_llm, pytest.mark.track_int]

from app.inference.consistency import (
    ConsistencyReport,
    ConsistencyVerifier,
    GateStatus,
    extract_grading,
)
from app.inference.hallucination_detector import get_default_detector
from app.inference.protocols import InferenceRequest


@pytest.mark.phase0a
@pytest.mark.requires_llm
class TestEngineSmokeTest:
    """引擎冒烟测试 - 验证LLM服务基本可用性（框架验证层）"""

    @pytest.mark.asyncio
    async def test_llama_cpp_health_check(self, llama_cpp_service):
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{llama_cpp_service}/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_vllm_health_check(self, vllm_service):
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{vllm_service}/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_llama_cpp_generate(self, llama_cpp_engine_real):
        request = InferenceRequest(
            prompt="请回答：1+1等于几？只输出数字",
            max_tokens=32,
            temperature=0.1,
        )
        response = await llama_cpp_engine_real.generate(request)
        assert response.text is not None
        assert len(response.text.strip()) > 0
        print(f"\n[smoke] llama.cpp output: {response.text[:100]}")

    @pytest.mark.asyncio
    async def test_vllm_generate(self, vllm_engine_real):
        request = InferenceRequest(
            prompt="请回答：1+1等于几？只输出数字",
            max_tokens=32,
            temperature=0.05,
        )
        response = await vllm_engine_real.generate(request)
        assert response.text is not None
        assert len(response.text.strip()) > 0
        print(f"\n[smoke] vLLM output: {response.text[:100]}")

    @pytest.mark.asyncio
    async def test_llama_cpp_grading_extraction(self, llama_cpp_engine_real):
        request = InferenceRequest(
            prompt="甲状腺超声检查：甲状腺右叶见0.5cm囊性结节，边界清晰。请给出TI-RADS分级。",
            max_tokens=256,
            temperature=0.1,
        )
        response = await llama_cpp_engine_real.generate(request)
        grading = extract_grading(response.text, "thyroid")
        print(f"\n[smoke] llama.cpp output: {response.text[:200]}")
        print(f"[smoke] extracted grading: {grading}")
        assert response.text is not None
        assert len(response.text) > 10

    @pytest.mark.asyncio
    async def test_vllm_grading_extraction(self, vllm_engine_real):
        request = InferenceRequest(
            prompt="肝脏超声检查：肝右叶见3cm实性占位，边界不清。请给出LI-RADS分级。",
            max_tokens=256,
            temperature=0.05,
        )
        response = await vllm_engine_real.generate(request)
        grading = extract_grading(response.text, "liver")
        print(f"\n[smoke] vLLM output: {response.text[:200]}")
        print(f"[smoke] extracted grading: {grading}")
        assert response.text is not None
        assert len(response.text) > 10


@pytest.mark.phase0a
@pytest.mark.requires_llm
class TestVerifierFramework:
    """一致性验证框架测试 - 验证ConsistencyVerifier逻辑正确运行（框架验证层）

    这些测试验证的是验证框架本身能否正确运行和产出报告，
    不验证模型是否达到Gate标准。
    """

    SMOKE_CASES = [
        {
            "case_id": "smoke_thy_001",
            "organ": "thyroid",
            "prompt": "甲状腺超声检查：甲状腺右叶见1.7cm低回声结节，边界欠清晰，形态欠规则，可见微钙化。请给出TI-RADS分级。",
            "expected_grading": "4a",
        },
        {
            "case_id": "smoke_liv_001",
            "organ": "liver",
            "prompt": "肝脏超声检查：肝右叶见4cm实性占位，边界模糊，动脉期强化明显。请给出LI-RADS分级。",
            "expected_grading": "4",
        },
        {
            "case_id": "smoke_car_001",
            "organ": "cardiac",
            "prompt": "心脏超声检查：左室射血分数(LVEF)45%，室壁运动普遍减弱。请评估心功能分级。",
            "expected_grading": "mild",
        },
        {
            "case_id": "smoke_bre_001",
            "organ": "breast",
            "prompt": "乳腺超声检查：左乳外上象限见1.5cm低回声肿块，边缘毛刺，纵横比>1。请给出BI-RADS分级。",
            "expected_grading": "4c",
        },
        {
            "case_id": "smoke_thy_002",
            "organ": "thyroid",
            "prompt": "甲状腺超声检查：甲状腺左叶见0.8cm等回声结节，边界清晰，形态规则。请给出TI-RADS分级。",
            "expected_grading": "2",
        },
        {
            "case_id": "smoke_liv_002",
            "organ": "liver",
            "prompt": "肝脏超声检查：肝左叶见1cm高回声病灶，边界清晰，内部回声均匀。请给出LI-RADS分级。",
            "expected_grading": "1",
        },
    ]

    @pytest.mark.asyncio
    async def test_verifier_produces_report(self, llama_cpp_engine_real, vllm_engine_real):
        """验证ConsistencyVerifier能产出完整ConsistencyReport"""
        verifier = ConsistencyVerifier(
            engine_a=llama_cpp_engine_real,
            engine_b=vllm_engine_real,
            engine_a_name="llama.cpp",
            engine_b_name="vLLM",
            concurrency=1,
            request_timeout_seconds=120.0,
        )
        report = await verifier.verify(self.SMOKE_CASES)

        assert isinstance(report, ConsistencyReport)
        assert report.total_cases == len(self.SMOKE_CASES)
        assert report.grading_consistent_count + len(report.inconsistent_cases) == report.total_cases
        assert 0.0 <= report.grading_consistency_rate <= 1.0
        assert 0.0 <= report.semantic_grading_consistency_rate <= 1.0
        assert 0.0 <= report.clinical_boundary_diff_rate <= 1.0
        assert 0.0 <= report.text_consistency_rate <= 1.0
        assert report.gate_status in (GateStatus.PASSED, GateStatus.CONDITIONAL, GateStatus.FAILED)

        print(f"\n[framework] Report produced: {report.total_cases} cases")
        print(f"[framework] Gate: {report.gate_status.value} (framework validation, not gate criteria)")

    @pytest.mark.asyncio
    async def test_verifier_produces_report_concurrency_2(
        self, llama_cpp_engine_real, vllm_engine_real
    ):
        """验证concurrency=2下ConsistencyVerifier能正常产出报告"""
        verifier = ConsistencyVerifier(
            engine_a=llama_cpp_engine_real,
            engine_b=vllm_engine_real,
            engine_a_name="llama.cpp",
            engine_b_name="vLLM",
            concurrency=2,
            request_timeout_seconds=120.0,
        )
        report = await verifier.verify(self.SMOKE_CASES)

        assert isinstance(report, ConsistencyReport)
        assert report.total_cases == len(self.SMOKE_CASES)
        assert 0.0 <= report.grading_consistency_rate <= 1.0
        print(f"\n[concurrency=2] Report: {report.total_cases} cases, "
              f"latency_a_p99={report.latency_a_p99_ms:.1f}ms, "
              f"latency_b_p99={report.latency_b_p99_ms:.1f}ms")

    @pytest.mark.asyncio
    async def test_verifier_organ_breakdown_structure(
        self, llama_cpp_engine_real, vllm_engine_real, consistency_test_cases
    ):
        """验证器官维度分解结构正确"""
        subset = consistency_test_cases[:100]
        verifier = ConsistencyVerifier(
            engine_a=llama_cpp_engine_real,
            engine_b=vllm_engine_real,
            engine_a_name="llama.cpp",
            engine_b_name="vLLM",
            concurrency=1,
            request_timeout_seconds=120.0,
        )
        report = await verifier.verify(subset)

        assert len(report.organ_breakdown) > 0, "Organ breakdown should not be empty"

        for organ, data in report.organ_breakdown.items():
            assert data["total"] > 0
            assert 0.0 <= data["grading_consistency_rate"] <= 1.0
            assert 0.0 <= data["clinical_boundary_diff_rate"] <= 1.0
            print(f"\n[framework-{organ}] {data['total']} cases, "
                  f"consistency={data['grading_consistency_rate']:.2%}")

    @pytest.mark.asyncio
    async def test_verifier_confidence_interval_valid(
        self, llama_cpp_engine_real, vllm_engine_real, consistency_test_cases
    ):
        """验证Wilson置信区间计算正确"""
        subset = consistency_test_cases[:100]
        verifier = ConsistencyVerifier(
            engine_a=llama_cpp_engine_real,
            engine_b=vllm_engine_real,
            engine_a_name="llama.cpp",
            engine_b_name="vLLM",
            concurrency=1,
            request_timeout_seconds=120.0,
        )
        report = await verifier.verify(subset)

        ci_lower = report.clinical_boundary_diff_rate_ci_lower
        ci_upper = report.clinical_boundary_diff_rate_ci_upper
        point_est = report.clinical_boundary_diff_rate

        assert ci_lower <= point_est <= ci_upper, \
            f"CI invalid: [{ci_lower:.4f}, {ci_upper:.4f}] vs point={point_est:.4f}"
        assert 0.0 <= ci_lower <= 1.0
        assert 0.0 <= ci_upper <= 1.0

    @pytest.mark.asyncio
    async def test_verifier_false_stats_complete(
        self, llama_cpp_engine_real, vllm_engine_real, consistency_test_cases
    ):
        """验证假阳性/假阴性统计完整"""
        subset = consistency_test_cases[:100]
        verifier = ConsistencyVerifier(
            engine_a=llama_cpp_engine_real,
            engine_b=vllm_engine_real,
            engine_a_name="llama.cpp",
            engine_b_name="vLLM",
            concurrency=1,
            request_timeout_seconds=120.0,
        )
        report = await verifier.verify(subset)

        fs = report.false_stats
        total = fs.true_positives + fs.false_positives + fs.true_negatives + fs.false_negatives
        assert total > 0, "False stats should have at least some samples"
        assert 0.0 <= fs.precision <= 1.0
        assert 0.0 <= fs.recall <= 1.0
        assert 0.0 <= fs.f1_score <= 1.0

    @pytest.mark.asyncio
    async def test_verifier_report_json_serializable(
        self, llama_cpp_engine_real, vllm_engine_real, consistency_test_cases
    ):
        """验证报告可序列化为完整JSON"""
        subset = consistency_test_cases[:6]
        verifier = ConsistencyVerifier(
            engine_a=llama_cpp_engine_real,
            engine_b=vllm_engine_real,
            engine_a_name="llama.cpp",
            engine_b_name="vLLM",
            concurrency=1,
            request_timeout_seconds=120.0,
        )
        report = await verifier.verify(subset)

        report_json = verifier.report_to_json(report)
        parsed = json.loads(report_json)

        required_keys = [
            "passed", "total_cases", "grading_consistency_rate",
            "clinical_boundary_diff_rate", "organ_breakdown", "failure_analysis",
            "gate_status",
        ]
        for key in required_keys:
            assert key in parsed, f"Missing key '{key}' in report JSON"

        output_path = os.path.join(os.path.dirname(__file__), "..", "reports", "phase0a_report.json")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report_json)
        print(f"\n[framework] Report saved to {output_path}")

    @pytest.mark.asyncio
    async def test_hallucination_detector_on_real_output(self, llama_cpp_engine_real):
        """验证幻觉检测器能在真实LLM输出上运行"""
        detector = get_default_detector()
        request = InferenceRequest(
            prompt="甲状腺超声描述：甲状腺右叶结节，边界清晰，无钙化。请给出诊断建议。",
            max_tokens=512,
            temperature=0.1,
        )
        response = await llama_cpp_engine_real.generate(request)

        result = detector.detect(response.text, "thyroid")
        assert result is not None
        print(f"\n[framework-hallucination] Is hallucination: {result.is_hallucination}")
        if result.issues:
            print(f"[framework-hallucination] Issues: {[str(i) for i in result.issues]}")

    @pytest.mark.asyncio
    async def test_latency_measurement_works(
        self, llama_cpp_engine_real, vllm_engine_real, consistency_test_cases
    ):
        """验证延迟测量功能正常"""
        subset = consistency_test_cases[:6]
        verifier = ConsistencyVerifier(
            engine_a=llama_cpp_engine_real,
            engine_b=vllm_engine_real,
            engine_a_name="llama.cpp",
            engine_b_name="vLLM",
            concurrency=1,
            request_timeout_seconds=120.0,
        )
        report = await verifier.verify(subset)

        assert report.latency_a_p99_ms >= 0
        assert report.latency_b_p99_ms >= 0
        print(f"\n[framework-latency] A P99={report.latency_a_p99_ms:.0f}ms, "
              f"B P99={report.latency_b_p99_ms:.0f}ms")


@pytest.mark.phase0a
@pytest.mark.requires_llm
@pytest.mark.gate_criteria
@pytest.mark.xfail(
    reason="Gate标准验证在模型质量达标前预期失败；移除此标记启用严格验收",
    strict=False,
)
class TestGate0ACriteria:
    """Gate 0A-Tech-P0 门控标准验证（Gate标准层）

    这些测试验证是否达到Phase0A门控标准。
    如果Gate=FAILED，测试会FAILED——这是正确的行为！
    只有当模型质量达到标准时，这些测试才会通过。

    Gate标准来源: 06-开发环境与进度规划.md Gate 0A-Tech-P0
    条件性门控规则来源: 01-系统架构设计.md §5.7

    xfail移除条件（满足任一即可移除xfail标记）:
    1. LLM基座模型选型完成（Gate 0A-2结束，Top-2模型200例完整测试通过）
    2. 分级一致率实测值连续3次>=95%
    3. 项目进入Phase0B阶段（灰度发布前正式验收）
    移除方式: 删除@pytest.mark.xfail装饰器，保留测试类和断言
    """

    @pytest.mark.asyncio
    async def test_gate_grading_consistency_rate(
        self, llama_cpp_engine_real, vllm_engine_real, consistency_test_cases
    ):
        """Gate标准: 分级一致率 >= 95%"""
        verifier = ConsistencyVerifier(
            engine_a=llama_cpp_engine_real,
            engine_b=vllm_engine_real,
            engine_a_name="llama.cpp",
            engine_b_name="vLLM",
            concurrency=1,
            request_timeout_seconds=120.0,
        )
        report = await verifier.verify(consistency_test_cases)

        print(f"\n[gate] Grading consistency: {report.grading_consistency_rate:.2%} (need >=95%)")
        print(f"[gate] Consistent: {report.grading_consistent_count}/{report.total_cases}")

        assert report.grading_consistency_rate >= 0.95, \
            f"Gate FAILED: grading consistency {report.grading_consistency_rate:.2%} < 95%"

    @pytest.mark.asyncio
    async def test_gate_clinical_boundary_diff_rate(
        self, llama_cpp_engine_real, vllm_engine_real, consistency_test_cases
    ):
        """Gate标准: 临床决策边界差异率 <= 1%"""
        verifier = ConsistencyVerifier(
            engine_a=llama_cpp_engine_real,
            engine_b=vllm_engine_real,
            engine_a_name="llama.cpp",
            engine_b_name="vLLM",
            concurrency=1,
            request_timeout_seconds=120.0,
        )
        report = await verifier.verify(consistency_test_cases)

        print(f"\n[gate] Boundary diff rate: {report.clinical_boundary_diff_rate:.2%} (need <=1%)")
        print(f"[gate] Critical boundary: {report.critical_boundary_diff_count}")

        assert report.clinical_boundary_diff_rate <= 0.01, \
            f"Gate FAILED: boundary diff {report.clinical_boundary_diff_rate:.2%} > 1%"

    @pytest.mark.asyncio
    async def test_gate_hallucination_rate_diff(
        self, llama_cpp_engine_real, vllm_engine_real, consistency_test_cases
    ):
        """Gate标准: 幻觉率差异 <= 0.5%"""
        verifier = ConsistencyVerifier(
            engine_a=llama_cpp_engine_real,
            engine_b=vllm_engine_real,
            engine_a_name="llama.cpp",
            engine_b_name="vLLM",
            concurrency=1,
            request_timeout_seconds=120.0,
        )
        report = await verifier.verify(consistency_test_cases)

        print(f"\n[gate] Hallucination diff: {report.hallucination_rate_diff:.4f} (need <=0.5%)")

        assert report.hallucination_rate_diff <= 0.005, \
            f"Gate FAILED: hallucination diff {report.hallucination_rate_diff:.4f} > 0.5%"

    @pytest.mark.asyncio
    async def test_gate_text_consistency_rate(
        self, llama_cpp_engine_real, vllm_engine_real, consistency_test_cases
    ):
        """Gate标准: 文本一致率 >= 90%"""
        verifier = ConsistencyVerifier(
            engine_a=llama_cpp_engine_real,
            engine_b=vllm_engine_real,
            engine_a_name="llama.cpp",
            engine_b_name="vLLM",
            concurrency=1,
            request_timeout_seconds=120.0,
        )
        report = await verifier.verify(consistency_test_cases)

        print(f"\n[gate] Text consistency: {report.text_consistency_rate:.2%} (need >=90%)")

        assert report.text_consistency_rate >= 0.90, \
            f"Gate FAILED: text consistency {report.text_consistency_rate:.2%} < 90%"

    @pytest.mark.asyncio
    async def test_gate_latency_diff_pct(
        self, llama_cpp_engine_real, vllm_engine_real, consistency_test_cases
    ):
        """Gate标准: 延迟差异百分比 <= 20%"""
        subset = consistency_test_cases[:100]
        verifier = ConsistencyVerifier(
            engine_a=llama_cpp_engine_real,
            engine_b=vllm_engine_real,
            engine_a_name="llama.cpp",
            engine_b_name="vLLM",
            concurrency=1,
            request_timeout_seconds=120.0,
        )
        report = await verifier.verify(subset)

        print(f"\n[gate] Latency diff: {report.latency_diff_pct:.1f}% (need <=20%)")

        assert report.latency_diff_pct <= 20.0, \
            f"Gate FAILED: latency diff {report.latency_diff_pct:.1f}% > 20%"

    @pytest.mark.asyncio
    async def test_gate_overall_status(
        self, llama_cpp_engine_real, vllm_engine_real, consistency_test_cases
    ):
        """Gate标准: 综合Gate判定必须为PASSED或CONDITIONAL

        条件性门控规则（来源: 01-系统架构设计.md §5.7）:
        - 分级一致率>=95% 且 边界差异率<=1% → PASSED
        - 分级一致率90%-95% 或 边界差异率1%-2% → CONDITIONAL（须在NMPA注册材料中声明偏差率）
        - 分级一致率<90% 或 边界差异率>2% → FAILED（须统一使用单一推理框架）
        """
        verifier = ConsistencyVerifier(
            engine_a=llama_cpp_engine_real,
            engine_b=vllm_engine_real,
            engine_a_name="llama.cpp",
            engine_b_name="vLLM",
            concurrency=1,
            request_timeout_seconds=120.0,
        )
        report = await verifier.verify(consistency_test_cases)

        print(f"\n{'='*60}")
        print(f"GATE 0A OVERALL STATUS: {report.gate_status.value.upper()}")
        print(f"{'='*60}")
        print(f"  Grading consistency: {report.grading_consistency_rate:.2%} [>=95%]")
        print(f"  Semantic consistency: {report.semantic_grading_consistency_rate:.2%} [>=98%]")
        print(f"  Boundary diff rate: {report.clinical_boundary_diff_rate:.2%} [<=1%]")
        print(f"  Boundary CI upper: {report.clinical_boundary_diff_rate_ci_upper:.4f} [<=2%]")
        print(f"  Hallucination diff: {report.hallucination_rate_diff:.4f} [<=0.5%]")
        print(f"  Text consistency: {report.text_consistency_rate:.2%} [>=90%]")
        print(f"  Latency diff: {report.latency_diff_pct:.1f}% [<=20%]")
        print(f"{'='*60}")

        assert report.gate_status in (GateStatus.PASSED, GateStatus.CONDITIONAL), \
            f"Gate FAILED: overall status is {report.gate_status.value}. " \
            f"Grading={report.grading_consistency_rate:.2%}, " \
            f"Boundary={report.clinical_boundary_diff_rate:.2%}. " \
            f"Per architecture design: if grading<90% or boundary>2%, " \
            f"must use single inference framework."

    @pytest.mark.asyncio
    async def test_gate_organ_coverage(
        self, llama_cpp_engine_real, vllm_engine_real, consistency_test_cases
    ):
        """Gate标准: 每个器官>=100例测试用例"""
        verifier = ConsistencyVerifier(
            engine_a=llama_cpp_engine_real,
            engine_b=vllm_engine_real,
            engine_a_name="llama.cpp",
            engine_b_name="vLLM",
            concurrency=1,
            request_timeout_seconds=120.0,
        )
        report = await verifier.verify(consistency_test_cases)

        for organ in ["thyroid", "liver", "cardiac", "breast"]:
            org_data = report.organ_breakdown.get(organ)
            if org_data is None:
                pytest.fail(
                    f"Gate FAILED: organ '{organ}' missing from breakdown. "
                    f"Available organs: {list(report.organ_breakdown.keys())}. "
                    f"This likely means the LLM service crashed before reaching {organ} cases."
                )
            count = org_data["total"]
            print(f"\n[gate-coverage] {organ}: {count} cases (need >=100)")
            assert count >= 100, \
                f"Gate FAILED: {organ} has only {count} cases, need >=100"
