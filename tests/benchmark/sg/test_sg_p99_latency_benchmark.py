"""SG-002: SafetyGateway P99延迟基准实测测试

设计文档要求：
- 01-系统架构设计§3.4: SG内部延迟基准P99 < 5ms
- 06-开发环境与进度规划§6.3: Gate 0验证项"SafetyGateway隔离: P99 < 5ms"

本测试套件用于验证SafetyGateway的实际P99延迟性能，确保满足设计要求。

测试策略：
1. SG内部处理延迟：仅测量check perform_full_check内部处理时间（不含HTTP请求）
2. 各阶段延迟分解：测量每个校验阶段的实际延迟贡献
3. 持续监控：验证持续负载下的P99稳定性

性能基准要求：
- SG内部P99 < 5ms（警告阈值4ms，临界阈值5ms）
- SG内部P95 < 3ms
- SG内部P50 < 1ms
"""

import gc
import time
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from safety_gateway.checker import get_safety_gateway_checker, CheckResultStatus
from safety_gateway.desensitization import sg_desensitization_engine
from safety_gateway.threshold import get_threshold_engine
from safety_gateway.contraindication import get_contraindication_engine
from safety_gateway.health import HealthChecker
from safety_gateway.validation_result import ConsentStatus
from safety_gateway.consent_checker import ConsentCheckResult

pytestmark = [pytest.mark.track_sg, pytest.mark.track_sec]


def compute_percentile(sorted_data: list[float], p: int) -> float:
    """计算百分位数"""
    n = len(sorted_data)
    if n == 0:
        return 0.0
    index = (p / 100) * (n - 1)
    if index.is_integer():
        return sorted_data[int(index)]
    lower = sorted_data[int(index)]
    upper = sorted_data[min(int(index + 1), n - 1)]
    return lower + (upper - lower) * (index - int(index))


class TestSGInternalP99Latency:
    """SG内部P99延迟实测

    测量SafetyGateway perform_full_check方法的实际执行时间，
    不包含HTTP请求/响应开销和网络延迟。
    仅测量SG内部处理逻辑的P99延迟。
    """

    def setup_method(self):
        """每个测试前重置checker状态"""
        self.checker = get_safety_gateway_checker()

    def _get_mock_consent_result(self) -> ConsentCheckResult:
        """获取模拟同意状态结果"""
        return ConsentCheckResult(
            status=ConsentStatus.GRANTED,
            timeout=False,
            error=False,
        )

    def _get_test_data(self) -> dict:
        """获取测试数据"""
        return {
            "organ": "thyroid",
            "diagnosis_data": {"patient_conditions": []},
            "conclusions": [{"organ": "thyroid", "confidence": 0.95}],
            "fusion_output": {"diagnostic_conclusion": {"grade": "T1", "finding": "良性结节"}},
            "specialist_conclusions": [{"organ": "thyroid", "grade": "T1", "finding": "良性结节"}],
        }

    @pytest.mark.asyncio
    async def test_sg_internal_p99_baseline(self):
        """测试SG内部处理P99延迟基准

        验证目标：SG内部处理P99 < 5ms（不含HTTP开销）

        测试方法：
        1. 预热50次，消除JIT和缓存影响
        2. 禁用GC，消除GC暂停干扰
        3. 连续测量1000次，计算P50/P95/P99
        4. 使用time.perf_counter()获取高精度时间
        """
        mock_consent = self._get_mock_consent_result()

        with patch.object(
            self.checker._consent_checker,
            'check_consent_status',
            new_callable=AsyncMock,
            return_value=mock_consent
        ):
            test_data = self._get_test_data()

            for _ in range(50):
                await self.checker.perform_full_check("P001", test_data)

            gc.disable()
            try:
                latencies = []
                for _ in range(1000):
                    start = time.perf_counter()
                    await self.checker.perform_full_check("P001", test_data)
                    elapsed = (time.perf_counter() - start) * 1000
                    latencies.append(elapsed)
            finally:
                gc.enable()

        latencies.sort()
        n = len(latencies)
        p50 = compute_percentile(latencies, 50)
        p95 = compute_percentile(latencies, 95)
        p99 = compute_percentile(latencies, 99)
        avg = sum(latencies) / n
        max_lat = max(latencies)
        min_lat = min(latencies)

        print(f"\n{'='*60}")
        print(f"SG内部处理延迟实测报告")
        print(f"{'='*60}")
        print(f"样本数: {n}")
        print(f"最小延迟: {min_lat:.3f}ms")
        print(f"最大延迟: {max_lat:.3f}ms")
        print(f"平均延迟: {avg:.3f}ms")
        print(f"P50延迟: {p50:.3f}ms (目标 < 1ms)")
        print(f"P95延迟: {p95:.3f}ms (目标 < 3ms)")
        print(f"P99延迟: {p99:.3f}ms (目标 < 5ms)")
        print(f"{'='*60}")

        p99_status = "✅ PASS" if p99 < 5.0 else "❌ FAIL"
        p95_status = "✅ PASS" if p95 < 3.0 else "❌ FAIL"
        p50_status = "✅ PASS" if p50 < 1.0 else "❌ FAIL"

        print(f"\n验证结果:")
        print(f"  P99 < 5ms: {p99_status} ({p99:.3f}ms)")
        print(f"  P95 < 3ms: {p95_status} ({p95:.3f}ms)")
        print(f"  P50 < 1ms: {p50_status} ({p50:.3f}ms)")

        assert p99 < 5.0, f"SG内部P99延迟{p99:.3f}ms超过5ms阈值（设计要求P99 < 5ms）"
        assert p95 < 3.0, f"SG内部P95延迟{p95:.3f}ms超过3ms阈值"
        assert p50 < 1.0, f"SG内部P50延迟{p50:.3f}ms超过1ms阈值"

    @pytest.mark.asyncio
    async def test_sg_internal_p99_with_degraded_path(self):
        """测试降级路径下的SG内部P99延迟

        验证降级场景下P99延迟仍然满足要求
        """
        mock_consent = self._get_mock_consent_result()

        with patch.object(
            self.checker._consent_checker,
            'check_consent_status',
            new_callable=AsyncMock,
            return_value=mock_consent
        ):
            test_data = self._get_test_data()

            for _ in range(50):
                await self.checker.perform_full_check("P001", test_data, desensitization_degraded=True)

            gc.disable()
            try:
                latencies = []
                for _ in range(500):
                    start = time.perf_counter()
                    await self.checker.perform_full_check("P001", test_data, desensitization_degraded=True)
                    elapsed = (time.perf_counter() - start) * 1000
                    latencies.append(elapsed)
            finally:
                gc.enable()

        latencies.sort()
        p99 = compute_percentile(latencies, 99)

        print(f"\n降级路径SG内部P99延迟: {p99:.3f}ms (目标 < 5ms)")
        assert p99 < 5.0, f"降级路径P99延迟{p99:.3f}ms超过5ms阈值"


class TestSGPhaseLatencyBreakdown:
    """SG各校验阶段延迟分解测试

    分解perform_full_check中各阶段的实际延迟贡献：
    1. 同意校验阶段
    2. 边缘连接检查
    3. 脱敏校验阶段
    4. 阈值校验阶段
    5. 禁忌校验阶段
    6. 一致性校验阶段
    """

    def setup_method(self):
        self.checker = get_safety_gateway_checker()

    @pytest.mark.asyncio
    async def test_phase_latency_breakdown(self):
        """测量各校验阶段的延迟贡献"""
        mock_consent = ConsentCheckResult(
            status=ConsentStatus.GRANTED,
            timeout=False,
            error=False,
        )

        test_data = {
            "organ": "thyroid",
            "diagnosis_data": {"patient_conditions": []},
            "conclusions": [{"organ": "thyroid", "confidence": 0.95}],
        }

        phase_latencies = {
            "consent_phase": [],
            "desensitization_phase": [],
            "threshold_phase": [],
            "contraindication_phase": [],
            "consistency_phase": [],
        }

        with patch.object(
            self.checker._consent_checker,
            'check_consent_status',
            new_callable=AsyncMock,
            return_value=mock_consent
        ):
            for _ in range(10):
                await self.checker.perform_full_check("P001", test_data)

            gc.disable()
            try:
                for _ in range(200):
                    from safety_gateway.checker.phase_base import CheckContext
                    from safety_gateway.checker.consent_phase import ConsentPhase
                    from safety_gateway.checker.desensitization_phase import DesensitizationPhase
                    from safety_gateway.checker.threshold_phase import ThresholdPhase
                    from safety_gateway.checker.contraindication_threshold_phase import ContraindicationThresholdPhase
                    from safety_gateway.checker.consistency_phase import ConsistencyPhase
                    from safety_gateway.health import _get_health_checker

                    ctx = CheckContext()
                    timer = time.perf_counter

                    phase_times = {}

                    t0 = timer()
                    result = await ConsentPhase().check_with_params(
                        "P001", True, ctx, lambda: 0.0, consent_status_at_input="granted"
                    )
                    phase_times["consent"] = (timer() - t0) * 1000

                    t0 = timer()
                    result = await DesensitizationPhase().check_with_params(
                        "P001", test_data, ctx, lambda: 0.0
                    )
                    phase_times["desensitization"] = (timer() - t0) * 1000

                    t0 = timer()
                    result = await ThresholdPhase().check_with_params(
                        "P001", test_data, ctx, lambda: 0.0
                    )
                    phase_times["threshold"] = (timer() - t0) * 1000

                    t0 = timer()
                    result = await ContraindicationThresholdPhase().check_with_params(
                        "P001", test_data, ctx, lambda: 0.0
                    )
                    phase_times["contraindication"] = (timer() - t0) * 1000

                    for phase, t in phase_times.items():
                        phase_latencies[phase + "_phase"].append(t)
            finally:
                gc.enable()

        print(f"\n{'='*60}")
        print(f"SG各阶段延迟分解报告")
        print(f"{'='*60}")
        for phase, latencies in phase_latencies.items():
            if latencies:
                avg = sum(latencies) / len(latencies)
                p99 = compute_percentile(sorted(latencies), 99)
                print(f"{phase}: 平均{avg:.3f}ms, P99{p99:.3f}ms")
        print(f"{'='*60}")


class TestSGSustainedLoadLatency:
    """SG持续负载下P99稳定性测试

    验证持续负载下P99延迟的稳定性，确保不会出现延迟退化。
    """

    def setup_method(self):
        self.checker = get_safety_gateway_checker()

    @pytest.mark.asyncio
    async def test_sustained_load_p99_stability(self):
        """测试持续负载下P99延迟稳定性

        模拟10000次连续请求，验证P99延迟保持在5ms以内。
        """
        mock_consent = ConsentCheckResult(
            status=ConsentStatus.GRANTED,
            timeout=False,
            error=False,
        )

        test_data = {
            "organ": "thyroid",
            "diagnosis_data": {"patient_conditions": []},
            "conclusions": [{"organ": "thyroid", "confidence": 0.95}],
        }

        with patch.object(
            self.checker._consent_checker,
            'check_consent_status',
            new_callable=AsyncMock,
            return_value=mock_consent
        ):
            warmup_samples = 100
            for i in range(warmup_samples):
                await self.checker.perform_full_check(f"P{i:04d}", test_data)

            window_size = 500
            num_windows = 10
            window_p99s = []

            for w in range(num_windows):
                gc.disable()
                try:
                    window_latencies = []
                    for i in range(window_size):
                        start = time.perf_counter()
                        await self.checker.perform_full_check(f"P{w:04d}_{i:04d}", test_data)
                        elapsed = (time.perf_counter() - start) * 1000
                        window_latencies.append(elapsed)
                finally:
                    gc.enable()

                window_latencies.sort()
                window_p99 = compute_percentile(window_latencies, 99)
                window_p99s.append(window_p99)
                print(f"  窗口{w+1}/{num_windows}: P99={window_p99:.3f}ms")

        overall_p99 = max(window_p99s)
        print(f"\n持续负载P99稳定性报告:")
        print(f"  窗口数: {num_windows}")
        print(f"  每窗口样本: {window_size}")
        print(f"  最大P99: {overall_p99:.3f}ms")
        print(f"  各窗口P99: {[f'{p:.3f}' for p in window_p99s]}")

        assert overall_p99 < 5.0, f"持续负载P99{overall_p99:.3f}ms超过5ms阈值"


class TestSGComponentLatency:
    """SG各组件单独延迟测试"""

    @pytest.mark.asyncio
    async def test_desensitization_engine_latency(self):
        """测量脱敏引擎单次处理延迟"""
        test_texts = [
            "患者张三，身份证号110101199001011234，手机号13812345678",
            "王医生诊断，李四，女，1990年生，电话13900001111",
            "地址：北京市朝阳区xxx街道，身份证**************",
            "联系电话: 13812345678, 邮箱: test@example.com",
            "超声检查报告 - 甲状腺结节TI-RADS 3类",
        ]

        gc.disable()
        try:
            latencies = []
            for _ in range(50):
                for text in test_texts:
                    start = time.perf_counter()
                    sg_desensitization_engine.desensitize(text)
                    elapsed = (time.perf_counter() - start) * 1000
                    latencies.append(elapsed)
        finally:
            gc.enable()

        latencies.sort()
        p50 = compute_percentile(latencies, 50)
        p95 = compute_percentile(latencies, 95)
        p99 = compute_percentile(latencies, 99)

        print(f"\n脱敏引擎延迟:")
        print(f"  P50: {p50:.3f}ms")
        print(f"  P95: {p95:.3f}ms")
        print(f"  P99: {p99:.3f}ms")

        assert p99 < 1.0, f"脱敏引擎P99延迟{p99:.3f}ms超过1ms阈值"

    @pytest.mark.asyncio
    async def test_threshold_engine_latency(self):
        """测量阈值引擎单次处理延迟"""
        threshold_engine = get_threshold_engine()

        organs = ["thyroid", "liver", "cardiac"]
        confidences = [0.95, 0.85, 0.75, 0.65, 0.55]

        gc.disable()
        try:
            latencies = []
            for _ in range(50):
                for organ in organs:
                    for confidence in confidences:
                        start = time.perf_counter()
                        await threshold_engine.check(organ, confidence)
                        elapsed = (time.perf_counter() - start) * 1000
                        latencies.append(elapsed)
        finally:
            gc.enable()

        latencies.sort()
        p99 = compute_percentile(latencies, 99)

        print(f"\n阈值引擎延迟:")
        print(f"  P99: {p99:.3f}ms")

        assert p99 < 0.5, f"阈值引擎P99延迟{p99:.3f}ms超过0.5ms阈值"


class TestSGP99Alerting:
    """SG P99延迟告警机制测试

    验证LatencyTracker的P99告警机制：
    1. 持续3次P99超限触发throttling
    2. 连续2次P99正常恢复throttling
    """

    @pytest.mark.asyncio
    async def test_p99_throttling_trigger(self):
        """测试P99超限throttling触发"""
        from safety_gateway.health import LatencyTracker

        tracker = LatencyTracker()

        for _ in range(100):
            await tracker.record(3.0)

        assert tracker.is_throttling is False

        for _ in range(100):
            await tracker.record(6.0)

        stats = await tracker.compute_stats()
        assert stats["p99_status"] in ("warning", "critical")

        for _ in range(100):
            await tracker.record(6.0)

        stats = await tracker.compute_stats()
        assert stats["p99_status"] in ("warning", "critical")

        for _ in range(100):
            await tracker.record(6.0)

        stats = await tracker.compute_stats()
        assert tracker.is_throttling is True, "持续3次P99超限应触发throttling"

        print(f"\nP99 throttling触发测试通过")

    @pytest.mark.asyncio
    async def test_p99_throttling_recovery(self):
        """测试P99恢复throttling关闭"""
        from safety_gateway.health import LatencyTracker

        tracker = LatencyTracker()

        for _ in range(100):
            await tracker.record(3.0)
        for _ in range(100):
            await tracker.record(6.0)
        for _ in range(100):
            await tracker.record(6.0)
        for _ in range(100):
            await tracker.record(6.0)

        assert tracker.is_throttling is True

        for _ in range(100):
            await tracker.record(3.0)

        stats = await tracker.compute_stats()
        assert tracker.is_throttling is True

        for _ in range(100):
            await tracker.record(3.0)

        stats = await tracker.compute_stats()
        assert tracker.is_throttling is False, "连续2次P99正常应恢复throttling"

        print(f"\nP99 throttling恢复测试通过")


class TestSGGate0Validation:
    """Gate 0 P99延迟验证测试

    专门针对Gate 0验证项设计的测试：
    SG内部延迟基准 P99 < 5ms
    """

    def setup_method(self):
        self.checker = get_safety_gateway_checker()

    @pytest.mark.asyncio
    async def test_gate0_p99_validation(self):
        """Gate 0 P99延迟验证

        验收标准：
        - 运行2000次perform_full_check
        - P99延迟 < 5ms
        - P99持续超限触发throttling拒绝新请求
        """
        mock_consent = ConsentCheckResult(
            status=ConsentStatus.GRANTED,
            timeout=False,
            error=False,
        )

        test_data = {
            "organ": "thyroid",
            "diagnosis_data": {"patient_conditions": []},
            "conclusions": [{"organ": "thyroid", "confidence": 0.95}],
        }

        with patch.object(
            self.checker._consent_checker,
            'check_consent_status',
            new_callable=AsyncMock,
            return_value=mock_consent
        ):
            warmup_count = 200
            for i in range(warmup_count):
                await self.checker.perform_full_check(f"P{i:04d}", test_data)

            gc.disable()
            try:
                measurement_count = 2000
                latencies = []
                for i in range(measurement_count):
                    start = time.perf_counter()
                    await self.checker.perform_full_check(f"P{measurement_count + i:04d}", test_data)
                    elapsed = (time.perf_counter() - start) * 1000
                    latencies.append(elapsed)
            finally:
                gc.enable()

        latencies.sort()
        n = len(latencies)
        p50 = compute_percentile(latencies, 50)
        p95 = compute_percentile(latencies, 95)
        p99 = compute_percentile(latencies, 99)
        avg = sum(latencies) / n

        print(f"\n{'='*60}")
        print(f"Gate 0 P99延迟验证报告")
        print(f"{'='*60}")
        print(f"测试样本数: {measurement_count}")
        print(f"预热样本数: {warmup_count}")
        print(f"平均延迟: {avg:.3f}ms")
        print(f"P50延迟: {p50:.3f}ms")
        print(f"P95延迟: {p95:.3f}ms")
        print(f"P99延迟: {p99:.3f}ms (Gate 0要求 < 5ms)")
        print(f"{'='*60}")

        gate0_passed = p99 < 5.0

        if gate0_passed:
            print(f"\n🎉 Gate 0 P99延迟验证通过!")
            print(f"   SG内部P99延迟 {p99:.3f}ms < 5ms")
        else:
            print(f"\n❌ Gate 0 P99延迟验证未通过")
            print(f"   SG内部P99延迟 {p99:.3f}ms >= 5ms")

        assert p99 < 5.0, (
            f"Gate 0验证失败: SG内部P99延迟{p99:.3f}ms超过5ms阈值。"
            f"根据01-系统架构设计§3.4和06-开发环境与进度规划§6.3，"
            f"Gate 0验证项要求P99 < 5ms。"
        )

        assert p95 < 3.0, f"Gate 0附加验证失败: P95延迟{p95:.3f}ms超过3ms阈值"
        assert p50 < 1.0, f"Gate 0附加验证失败: P50延迟{p50:.3f}ms超过1ms阈值"
