"""P99 latency benchmark for SafetyGateway internal check pipeline.

Measures the latency of each check phase independently and the full pipeline,
without external dependencies (Redis, PostgreSQL, fusion layer).
Uses mock data to isolate internal processing latency.
"""
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from safety_gateway.contraindication import ContraindicationEngine
from safety_gateway.degraded_output import apply_degraded_output
from safety_gateway.desensitization import SafetyGatewayDesensitizationEngine
from safety_gateway.threshold import ThresholdEngine
from safety_gateway.validation_result import DegradedReason

N = 1000

def bench_desensitize(engine: SafetyGatewayDesensitizationEngine):
    samples = []
    test_texts = [
        "患者身份证号440111199001011234，手机号13800138000",
        "超声检查报告：肝脏大小正常，未见明显占位性病变",
        "患者张三，出生日期1990年1月1日，住址北京市朝阳区xxx",
        "No sensitive data in this text, just normal medical report",
        "邮箱test@example.com，病历号MR20240001",
    ]
    for _ in range(N):
        for text in test_texts:
            t0 = time.perf_counter()
            engine.desensitize(text)
            samples.append((time.perf_counter() - t0) * 1000)
    return samples

def bench_check_sensitive(engine: SafetyGatewayDesensitizationEngine):
    samples = []
    test_texts = [
        "患者身份证号440111199001011234，手机号13800138000",
        "超声检查报告：肝脏大小正常，未见明显占位性病变",
        "患者张三，出生日期1990年1月1日",
        "No sensitive data in this text",
        "邮箱test@example.com",
    ]
    for _ in range(N):
        for text in test_texts:
            t0 = time.perf_counter()
            engine.check_sensitive_data(text)
            samples.append((time.perf_counter() - t0) * 1000)
    return samples

def bench_contraindication(engine: ContraindicationEngine):
    samples = []
    test_cases = [
        ("thyroid", {"condition": "pregnancy"}),
        ("liver", {"condition": "hepatic_failure"}),
        ("cardiac", {"condition": "pacemaker"}),
        ("thyroid", {}),
        ("liver", {"condition": "unknown_condition"}),
    ]
    for _ in range(N):
        for organ, data in test_cases:
            t0 = time.perf_counter()
            engine.check(organ, data)
            samples.append((time.perf_counter() - t0) * 1000)
    return samples

def bench_threshold(engine: ThresholdEngine):
    samples = []
    test_cases = [
        ("thyroid", 0.90),
        ("liver", 0.85),
        ("cardiac", 0.95),
        ("thyroid", 0.70),
        ("unknown_organ", 0.88),
    ]
    engine._load_default_thresholds()
    engine._loaded = True
    for _ in range(N):
        for organ, confidence in test_cases:
            t0 = time.perf_counter()
            engine._check_sync(organ, confidence)
            samples.append((time.perf_counter() - t0) * 1000)
    return samples

def bench_degraded_output():
    samples = []
    test_data = {
        "observations": "肝脏大小正常，未见明显占位",
        "diagnostic_conclusion": "肝脏未见异常",
        "confidence": 0.92,
        "organ": "liver",
        "patient_id": "P001",
        "session_id": "S001",
    }
    for _ in range(N):
        t0 = time.perf_counter()
        apply_degraded_output(test_data.copy(), DegradedReason.FUSION_UNAVAILABLE)
        samples.append((time.perf_counter() - t0) * 1000)
    return samples

def bench_full_pipeline(desens_engine, contra_engine, thresh_engine):
    samples = []
    test_data = {
        "observations": "甲状腺右叶可见低回声结节，大小约5x4mm",
        "diagnostic_conclusion": "甲状腺结节TI-RADS 3类",
        "confidence": 0.88,
        "organ": "thyroid",
        "patient_id": "P001",
        "session_id": "S001",
    }
    for _ in range(N):
        t0 = time.perf_counter()
        desens_result = desens_engine.check_sensitive_data(
            str(test_data.get("observations", "")) + str(test_data.get("diagnostic_conclusion", ""))
        )
        contra_result = contra_engine.check("thyroid", {"condition": "pregnancy"})
        thresh_result = thresh_engine._check_sync("thyroid", 0.88)  # 同步基准测试需用_check_sync; 生产路径走async check()
        _ = (desens_result, contra_result, thresh_result)
        samples.append((time.perf_counter() - t0) * 1000)
    return samples

def compute_stats(samples, name):
    samples_sorted = sorted(samples)
    p50 = samples_sorted[int(len(samples_sorted) * 0.50)]
    p95 = samples_sorted[int(len(samples_sorted) * 0.95)]
    p99 = samples_sorted[int(len(samples_sorted) * 0.99)]
    avg = statistics.mean(samples_sorted)
    mx = max(samples_sorted)
    mn = min(samples_sorted)
    passed = p99 < 5.0
    status = "PASS" if passed else "FAIL"
    print(f"  {name}: P50={p50:.3f}ms  P95={p95:.3f}ms  P99={p99:.3f}ms  avg={avg:.3f}ms  max={mx:.3f}ms  min={mn:.3f}ms  [{status}]")
    return p99, passed

def main():
    print("=" * 80)
    print("SafetyGateway P99 Latency Benchmark")
    print(f"Samples per test: {N} iterations x 5 cases = {N*5} samples")
    print("=" * 80)

    desens_engine = SafetyGatewayDesensitizationEngine()
    contra_engine = ContraindicationEngine()
    thresh_engine = ThresholdEngine()
    thresh_engine._load_default_thresholds()
    thresh_engine._loaded = True

    print("\n--- Desensitization Engine ---")
    p99_desens, _ = compute_stats(bench_desensitize(desens_engine), "desensitize()")
    p99_check, _ = compute_stats(bench_check_sensitive(desens_engine), "check_sensitive_data()")

    print("\n--- Contraindication Engine ---")
    p99_contra, _ = compute_stats(bench_contraindication(contra_engine), "check()")

    print("\n--- Threshold Engine ---")
    p99_thresh, _ = compute_stats(bench_threshold(thresh_engine), "check()")

    print("\n--- Degraded Output ---")
    p99_degrad, _ = compute_stats(bench_degraded_output(), "apply_degraded_output()")

    print("\n--- Full Pipeline (desensitize+contraindication+threshold) ---")
    p99_full, full_passed = compute_stats(
        bench_full_pipeline(desens_engine, contra_engine, thresh_engine),
        "full_pipeline()",
    )

    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    all_pass = p99_full < 5.0
    print(f"  Desensitization P99: {p99_desens:.3f}ms  {'PASS' if p99_desens < 1.0 else 'WARN'}")
    print(f"  Contraindication P99: {p99_contra:.3f}ms  {'PASS' if p99_contra < 0.5 else 'WARN'}")
    print(f"  Threshold P99:        {p99_thresh:.3f}ms  {'PASS' if p99_thresh < 0.5 else 'WARN'}")
    print(f"  Degraded Output P99:  {p99_degrad:.3f}ms  {'PASS' if p99_degrad < 1.0 else 'WARN'}")
    print(f"  Full Pipeline P99:    {p99_full:.3f}ms  {'PASS <5ms' if p99_full < 5.0 else 'FAIL >=5ms'}")
    print(f"\n  Overall: {'PASS - P99 < 5ms requirement met' if all_pass else 'FAIL - P99 >= 5ms requirement NOT met'}")

if __name__ == "__main__":
    main()
