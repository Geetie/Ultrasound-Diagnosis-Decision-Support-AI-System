import pytest
import time
from app.security.hsm import HSMManager, KeySlot, DegradedPhase
pytestmark = [pytest.mark.track_sec, pytest.mark.track_inf, pytest.mark.benchmark]

_WARMUP_ITERATIONS = 10


class TestHSMSigningPerformanceBenchmark:
    """HSM签名性能基准测试。

    注意：本测试在模拟I/O环境下运行（HSM操作为内存模拟，非真实硬件），
    测量结果仅反映逻辑正确性和相对性能，不代表真实硬件的绝对延迟。
    不应将此处的基准数值直接用于生产环境SLA评估。
    """
    @pytest.fixture
    def hsm(self):
        mgr = HSMManager()
        mgr.generate_key_pair("bench_sign_key", slot=KeySlot.JWT_SIGNING)
        return mgr

    def test_single_signature_latency_p99_under_5ms(self, hsm):
        data = b"benchmark diagnostic report payload for signing performance test"

        for _ in range(_WARMUP_ITERATIONS):
            hsm.sign("bench_sign_key", data)

        latencies = []
        for _ in range(1000):
            start = time.monotonic()
            signature = hsm.sign("bench_sign_key", data)
            elapsed_ms = (time.monotonic() - start) * 1000
            latencies.append(elapsed_ms)

        latencies.sort()
        p50 = latencies[500]
        p95 = latencies[950]
        p99 = latencies[990]

        assert p99 < 5.0, f"Signature P99 latency {p99:.2f}ms exceeds 5ms SLA"
        assert p95 < 3.0, f"Signature P95 latency {p95:.2f}ms unexpectedly high"

    def test_signature_verify_latency_p99_under_5ms(self, hsm):
        data = b"benchmark verification payload"
        signature = hsm.sign("bench_sign_key", data)

        for _ in range(_WARMUP_ITERATIONS):
            hsm.verify_signature("bench_sign_key", data, signature)

        latencies = []
        for _ in range(1000):
            start = time.monotonic()
            hsm.verify_signature("bench_sign_key", data, signature)
            elapsed_ms = (time.monotonic() - start) * 1000
            latencies.append(elapsed_ms)

        latencies.sort()
        p99 = latencies[990]
        assert p99 < 5.0, f"Verify P99 latency {p99:.2f}ms exceeds 5ms SLA"

    def test_jwt_signing_latency(self, hsm):
        hsm.generate_key_pair("bench_jwt_key", slot=KeySlot.JWT_SIGNING)
        header = {"alg": "RS256", "typ": "JWT"}
        payload = {
            "sub": "user123",
            "role": "ULTRASOUND_DOCTOR",
            "exp": 1234567890,
            "iat": 1234560000,
        }

        for _ in range(_WARMUP_ITERATIONS):
            hsm.sign_jwt("bench_jwt_key", header, payload)

        latencies = []
        for _ in range(500):
            start = time.monotonic()
            hsm.sign_jwt("bench_jwt_key", header, payload)
            elapsed_ms = (time.monotonic() - start) * 1000
            latencies.append(elapsed_ms)

        latencies.sort()
        p99 = latencies[495]
        assert p99 < 5.0, f"JWT signing P99 latency {p99:.2f}ms exceeds 5ms SLA"

    def test_sign_throughput(self, hsm):
        data = b"throughput test payload"

        for _ in range(_WARMUP_ITERATIONS):
            hsm.sign("bench_sign_key", data)

        iterations = 500

        start = time.monotonic()
        for _ in range(iterations):
            hsm.sign("bench_sign_key", data)
        elapsed = time.monotonic() - start

        ops_per_sec = iterations / elapsed
        assert ops_per_sec > 100, f"Signing throughput {ops_per_sec:.0f} ops/s below 100 ops/s minimum"

    def test_varying_payload_sizes(self, hsm):
        sizes = [64, 256, 1024, 4096, 16384]

        for size in sizes:
            data = b"x" * size

            for _ in range(_WARMUP_ITERATIONS):
                hsm.sign("bench_sign_key", data)

            latencies = []
            for _ in range(100):
                start = time.monotonic()
                hsm.sign("bench_sign_key", data)
                elapsed_ms = (time.monotonic() - start) * 1000
                latencies.append(elapsed_ms)

            latencies.sort()
            p99 = latencies[99]
            assert p99 < 5.0, f"Payload {size}B: P99 {p99:.2f}ms exceeds 5ms"

    def test_key_slot_routing_performance(self, hsm):
        hsm.generate_key_pair("bench_jwt_slot", slot=KeySlot.JWT_SIGNING)
        hsm.generate_key_pair("bench_data_slot", slot=KeySlot.DATA_SIGNING)
        hsm.generate_key_pair("bench_desens_slot", slot=KeySlot.DESENSITIZATION)
        hsm.generate_key_pair("bench_emerg_slot", slot=KeySlot.EMERGENCY_SIGNING)

        data = b"key slot routing benchmark"
        slots = {
            "jwt": "bench_jwt_slot",
            "data": "bench_data_slot",
            "desens": "bench_desens_slot",
            "emergency": "bench_emerg_slot",
        }

        for slot_name, key_name in slots.items():
            for _ in range(_WARMUP_ITERATIONS):
                hsm.sign(key_name, data)

            latencies = []
            for _ in range(100):
                start = time.monotonic()
                hsm.sign(key_name, data)
                elapsed_ms = (time.monotonic() - start) * 1000
                latencies.append(elapsed_ms)

            latencies.sort()
            p99 = latencies[99]
            assert p99 < 5.0, f"Slot {slot_name}: P99 {p99:.2f}ms exceeds 5ms"
