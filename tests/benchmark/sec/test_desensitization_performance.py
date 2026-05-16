"""Performance benchmarks for DesensitizationEngine.

性能基准说明：
- 基准值依据：根据业务SLA要求，单次诊断请求处理时间需<500ms，脱敏作为其中一个环节，
  处理1000条记录的时间应控制在1秒以内，以确保整体响应时间达标。
- P99延迟要求：根据01-系统架构设计.md §3.4，SafetyGateway延迟基准P99 < 5ms，
  脱敏操作作为核心校验步骤，需满足高性能要求。
"""

import time
import pytest
from app.security.desensitization import get_desensitization_engine
pytestmark = [pytest.mark.track_sec, pytest.mark.track_data, pytest.mark.benchmark]


class TestDesensitizationPerformance:
    """脱敏引擎性能基准测试类。

    注意：本测试在模拟I/O环境下运行（无真实网络/数据库调用），
    测量结果仅反映逻辑正确性和相对性能，不代表生产环境的绝对延迟。
    不应将此处的基准数值直接用于生产环境SLA评估。
    """
    
    def test_performance_large_text(self):
        """测试大规模文本脱敏性能"""
        engine = get_desensitization_engine()
        
        large_text = "患者张三，身份证号110101199001011234，手机号13812345678。" * 1000

        for _ in range(10):
            engine.desensitize(large_text)
        
        start_time = time.time()
        result = engine.desensitize(large_text)
        end_time = time.time()
        
        duration = end_time - start_time
        
        assert len(result) > 0
        assert duration < 1.0, f"脱敏1000条记录耗时 {duration:.2f}s，超过1秒基准值"

    def test_performance_many_requests(self):
        """测试高频请求场景下的脱敏性能"""
        engine = get_desensitization_engine()
        
        test_cases = [
            "患者张三，身份证号110101199001011234",
            "手机号13812345678",
            "邮箱test@hospital.com",
            "IP地址192.168.1.100",
            "1995-05-15出生"
        ]

        for _ in range(10):
            for case in test_cases:
                engine.desensitize(case)
        
        start_time = time.time()
        for _ in range(1000):
            for case in test_cases:
                engine.desensitize(case)
        end_time = time.time()
        
        duration = end_time - start_time
        
        assert duration < 1.0, f"5000次脱敏请求耗时 {duration:.2f}s，超过1秒基准值"

    def test_ci_throughput(self):
        """CI吞吐量验证：>= 1000调用/秒"""
        engine = get_desensitization_engine()
        test_text = "患者张三，身份证号110101199001011234，手机号13812345678"

        for _ in range(10):
            engine.desensitize(test_text)

        iterations = 5000
        start_time = time.perf_counter()
        for _ in range(iterations):
            engine.desensitize(test_text)
        duration = time.perf_counter() - start_time

        throughput = iterations / duration
        assert throughput >= 1000, f"CI吞吐量 {throughput:.0f}/s < 1000/s"

    def test_desensitization_p99_latency(self):
        """脱敏P99延迟 < 5ms"""
        engine = get_desensitization_engine()
        test_text = "患者张三，身份证号110101199001011234，手机号13812345678"

        for _ in range(10):
            engine.desensitize(test_text)

        import gc
        gc.disable()
        try:
            latencies = []
            for _ in range(1000):
                start = time.perf_counter()
                engine.desensitize(test_text)
                elapsed = (time.perf_counter() - start) * 1000
                latencies.append(elapsed)
        finally:
            gc.enable()

        latencies.sort()
        n = len(latencies)
        p99_index = int(0.99 * (n - 1))
        p99 = latencies[p99_index]

        assert p99 < 5.0, f"脱敏P99延迟 {p99:.3f}ms 超过5ms阈值"