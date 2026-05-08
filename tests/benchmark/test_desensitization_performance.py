"""Performance benchmarks for DesensitizationEngine."""

import time
import pytest
from app.security.desensitization import get_desensitization_engine


class TestDesensitizationPerformance:
    
    def test_performance_large_text(self):
        engine = get_desensitization_engine()
        
        large_text = "患者张三，身份证号110101199001011234，手机号13812345678。" * 1000
        
        start_time = time.time()
        result = engine.desensitize(large_text)
        end_time = time.time()
        
        duration = end_time - start_time
        
        assert len(result) > 0
        assert duration < 1.0, f"脱敏1000条记录耗时 {duration:.2f}s，超过1秒"

    def test_performance_many_requests(self):
        engine = get_desensitization_engine()
        
        test_cases = [
            "患者张三，身份证号110101199001011234",
            "手机号13812345678",
            "邮箱test@hospital.com",
            "IP地址192.168.1.100",
            "1995-05-15出生"
        ]
        
        start_time = time.time()
        for _ in range(1000):
            for case in test_cases:
                engine.desensitize(case)
        end_time = time.time()
        
        duration = end_time - start_time
        
        assert duration < 1.0, f"5000次脱敏请求耗时 {duration:.2f}s，超过1秒"