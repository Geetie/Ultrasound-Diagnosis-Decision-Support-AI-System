"""Safety Gateway性能基准测试

SG-FIX-002: 验证P99延迟基准 < 5ms

本测试套件用于验证SafetyGateway的性能指标，确保满足设计要求的P99 < 5ms延迟基准。

测试内容：
1. 完整校验流程性能测试
2. 各组件单独性能测试
3. 并发场景性能测试

性能基准要求：
- P99延迟 < 5ms
- P95延迟 < 3ms
- P50延迟 < 1ms

修复内容：
- VT-002: 添加预热机制
- VT-003: 禁用GC干扰
- SG-FIX-R7-09: time.time() → time.perf_counter()
"""

import gc
import time
from unittest.mock import patch

import pytest

from safety_gateway.checker import get_safety_gateway_checker
from safety_gateway.consent_checker import ConsentCheckResult
from safety_gateway.contraindication import get_contraindication_engine
from safety_gateway.desensitization import sg_desensitization_engine
from safety_gateway.threshold import get_threshold_engine
from safety_gateway.validation_result import ConsentStatus

pytestmark = [pytest.mark.track_sg, pytest.mark.track_sec]


def compute_percentile(sorted_data: list[float], p: int) -> float:
    n = len(sorted_data)
    if n == 0:
        return 0.0
    index = (p / 100) * (n - 1)
    if index.is_integer():
        return sorted_data[int(index)]
    lower = sorted_data[int(index)]
    upper = sorted_data[min(int(index) + 1, n - 1)]
    return lower + (upper - lower) * (index - int(index))


class TestSafetyGatewayPerformance:
    """SafetyGateway性能基准测试"""

    def setup_method(self):
        self.checker = get_safety_gateway_checker()

    @pytest.mark.asyncio
    async def test_full_check_p99_latency(self):
        """测试完整校验流程的P99延迟

        验证标准：P99延迟 < 5ms

        VT-002修复: 添加预热机制
        VT-003修复: 禁用GC干扰
        """
        test_data = {
            "organ": "thyroid",
            "diagnosis_data": {},
            "conclusions": [{"organ": "thyroid", "confidence": 0.95}],
            "fusion_output": {"diagnostic_conclusion": {"grade": "T1", "finding": "良性"}},
            "specialist_conclusions": [{"organ": "thyroid", "grade": "T1", "finding": "良性"}],
        }

        with patch.object(self.checker._consent_checker, 'check_consent_status',
                         return_value=ConsentCheckResult(status=ConsentStatus.GRANTED, timeout=False, error=False)):
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

        print("\n完整校验流程延迟统计:")
        print(f"  P50: {p50:.2f}ms")
        print(f"  P95: {p95:.2f}ms")
        print(f"  P99: {p99:.2f}ms")
        print(f"  平均: {sum(latencies)/n:.2f}ms")
        print(f"  最大: {max(latencies):.2f}ms")

        assert p50 < 1.0, f"P50延迟{ p50:.2f}ms超过1ms阈值"
        assert p95 < 3.0, f"P95延迟{ p95:.2f}ms超过3ms阈值"
        assert p99 < 5.0, f"P99延迟{ p99:.2f}ms超过5ms阈值"

    @pytest.mark.asyncio
    async def test_desensitization_performance(self):
        """测试脱敏引擎性能

        验证标准：单次脱敏 < 1ms
        """
        test_text = "患者张三，身份证号110101199001011234，手机号13812345678"

        latencies = []
        for _ in range(100):
            start = time.perf_counter()
            sg_desensitization_engine.desensitize(test_text)
            elapsed = (time.perf_counter() - start) * 1000
            latencies.append(elapsed)

        p99 = compute_percentile(sorted(latencies), 99)

        print("\n脱敏引擎延迟统计:")
        print(f"  P99: {p99:.2f}ms")

        assert p99 < 1.0, f"脱敏P99延迟{ p99:.2f}ms超过1ms阈值"

    @pytest.mark.asyncio
    async def test_threshold_check_performance(self):
        """测试阈值校验性能

        验证标准：单次阈值检查 < 0.5ms
        """
        threshold_engine = get_threshold_engine()

        latencies = []
        for _ in range(100):
            start = time.perf_counter()
            await threshold_engine.check("thyroid", 0.95)
            elapsed = (time.perf_counter() - start) * 1000
            latencies.append(elapsed)

        p99 = compute_percentile(sorted(latencies), 99)

        print("\n阈值校验延迟统计:")
        print(f"  P99: {p99:.2f}ms")

        assert p99 < 0.5, f"阈值校验P99延迟{ p99:.2f}ms超过0.5ms阈值"

    def test_contraindication_check_performance(self):
        """测试禁忌校验性能

        验证标准：单次禁忌检查 < 0.5ms
        """
        contraindication_engine = get_contraindication_engine()

        latencies = []
        for _ in range(100):
            start = time.perf_counter()
            contraindication_engine.check("thyroid", {})
            elapsed = (time.perf_counter() - start) * 1000
            latencies.append(elapsed)

        p99 = compute_percentile(sorted(latencies), 99)

        print("\n禁忌校验延迟统计:")
        print(f"  P99: {p99:.2f}ms")

        assert p99 < 0.5, f"禁忌校验P99延迟{ p99:.2f}ms超过0.5ms阈值"


class TestSafetyGatewayThroughput:
    """SafetyGateway吞吐量测试"""

    @pytest.mark.asyncio
    async def test_concurrent_requests(self):
        """测试并发请求处理能力

        验证标准：100并发请求下P99延迟 < 10ms
        """
        checker = get_safety_gateway_checker()
        test_data = {
            "organ": "thyroid",
            "diagnosis_data": {},
            "conclusions": [{"organ": "thyroid", "confidence": 0.95}],
        }

        with patch.object(checker._consent_checker, 'check_consent_status',
                         return_value=ConsentCheckResult(status=ConsentStatus.GRANTED, timeout=False, error=False)):
            import asyncio

            async def make_request(i):
                start = time.perf_counter()
                await checker.perform_full_check(f"P{i:03d}", test_data)
                return (time.perf_counter() - start) * 1000

            tasks = [make_request(i) for i in range(100)]
            latencies = await asyncio.gather(*tasks)

        latencies.sort()
        p99 = compute_percentile(latencies, 99)

        print("\n并发请求延迟统计(100并发):")
        print(f"  P99: {p99:.2f}ms")
        print(f"  吞吐量: {100 / sum(latencies) * 1000:.1f} req/s")

        assert p99 < 10.0, f"并发P99延迟{ p99:.2f}ms超过10ms阈值"

    @pytest.mark.asyncio
    async def test_ci_throughput(self):
        """CI吞吐量验证：>= 1000调用/秒"""
        checker = get_safety_gateway_checker()
        test_data = {
            "organ": "thyroid",
            "diagnosis_data": {},
            "conclusions": [{"organ": "thyroid", "confidence": 0.95}],
        }

        with patch.object(checker._consent_checker, 'check_consent_status',
                         return_value=ConsentCheckResult(status=ConsentStatus.GRANTED, timeout=False, error=False)):
            import asyncio
            iterations = 1000

            async def make_request(i):
                await checker.perform_full_check(f"P{i:03d}", test_data)
                return True

            start = time.perf_counter()
            tasks = [make_request(i) for i in range(iterations)]
            await asyncio.gather(*tasks)
            duration = time.perf_counter() - start

        throughput = iterations / duration
        assert throughput >= 1000, f"CI吞吐量 {throughput:.0f}/s < 1000/s"


@pytest.mark.integration
class TestHTTPEndToEndLatency:
    """HTTP端到端延迟测试

    验证包含HTTP请求/响应传输+连接池开销的完整链路P99 < 5ms。
    仅在集成测试环境运行（需要SG服务实际运行）。
    """

    @pytest.mark.asyncio
    async def test_http_e2e_p99_latency(self):
        """测试HTTP端到端延迟 P99 < 5ms"""
        try:
            import httpx
        except ImportError:
            pytest.skip("httpx not installed")

        sg_url = "http://localhost:9001"
        test_payload = {
            "patient_id": "P001",
            "output_data": {
                "organ": "thyroid",
                "diagnosis_data": {},
                "conclusions": [{"organ": "thyroid", "confidence": 0.95}],
            },
        }

        async with httpx.AsyncClient(base_url=sg_url, timeout=5.0) as client:
            for _ in range(20):
                try:
                    await client.post("/api/v1/safety/check", json=test_payload)
                except Exception:
                    pass

            latencies = []
            for _ in range(200):
                start = time.perf_counter()
                try:
                    resp = await client.post("/api/v1/safety/check", json=test_payload)
                    elapsed = (time.perf_counter() - start) * 1000
                    if resp.status_code == 200:
                        latencies.append(elapsed)
                except Exception:
                    pass

        if not latencies:
            pytest.skip("SG服务未运行或不可达")

        latencies.sort()
        p99 = compute_percentile(latencies, 99)
        print("\nHTTP端到端延迟统计:")
        print(f"  样本数: {len(latencies)}")
        print(f"  P99: {p99:.2f}ms")
        assert p99 < 5.0, f"HTTP端到端P99延迟{p99:.2f}ms超过5ms阈值"


@pytest.mark.integration
class TestGate0ConsentQueryLatency:
    """Gate 0验证：PostgreSQL直查同意状态P99 < 10ms

    设计文档01-系统架构设计 + 06-开发环境与进度规划Gate 0验证项：
    SafetyGateway拦截点始终直查PostgreSQL获取最新同意状态，
    P99延迟须 < 10ms。

    仅在集成测试环境运行（需要PG实际运行）。
    """

    @pytest.mark.asyncio
    async def test_consent_pg_query_p99_latency(self):
        try:
            from safety_gateway.consent_checker import get_consent_checker
            from safety_gateway.sg_db import _get_async_engine
        except ImportError:
            pytest.skip("safety_gateway not installed")

        try:
            await _get_async_engine()
        except Exception:
            pytest.skip("PostgreSQL not available")

        checker = get_consent_checker()
        test_patient = "GATE0_LATENCY_TEST"

        for _ in range(10):
            try:
                await checker.check_consent_status(test_patient)
            except Exception:
                pass

        latencies = []
        for _ in range(100):
            start = time.perf_counter()
            try:
                await checker.check_consent_status(test_patient)
                elapsed = (time.perf_counter() - start) * 1000
                latencies.append(elapsed)
            except Exception:
                pass

        if not latencies:
            pytest.skip("PG查询全部失败，无法测量延迟")

        latencies.sort()
        p99 = compute_percentile(latencies, 99)
        p95 = compute_percentile(latencies, 95)
        print("\nPG直查同意状态延迟统计:")
        print(f"  样本数: {len(latencies)}")
        print(f"  P95: {p95:.2f}ms")
        print(f"  P99: {p99:.2f}ms")
        assert p99 < 10.0, f"PG直查P99延迟{p99:.2f}ms超过10ms Gate 0阈值"
