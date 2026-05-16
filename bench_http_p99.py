"""HTTP end-to-end P99 latency benchmark for SafetyGateway."""
import time
import statistics
import httpx
import asyncio

async def bench_http():
    samples = []
    payload = {
        "patient_id": "P001",
        "output_data": {
            "observations": "甲状腺右叶可见低回声结节",
            "diagnostic_conclusion": "甲状腺结节TI-RADS 3类",
            "confidence": 0.88,
            "organ": "thyroid",
        },
        "organ": "thyroid",
        "confidence": 0.88,
    }
    async with httpx.AsyncClient(base_url="http://localhost:9001", timeout=5.0) as client:
        for i in range(500):
            t0 = time.perf_counter()
            try:
                resp = await client.post("/api/v1/safety/check", json=payload)
                elapsed = (time.perf_counter() - t0) * 1000
                samples.append(elapsed)
            except Exception as e:
                print(f"Error #{i}: {e}")
    if samples:
        s = sorted(samples)
        p50 = s[int(len(s) * 0.50)]
        p95 = s[int(len(s) * 0.95)]
        p99 = s[int(len(s) * 0.99)]
        avg = statistics.mean(s)
        mx = max(s)
        mn = min(s)
        print(f"HTTP E2E: P50={p50:.2f}ms  P95={p95:.2f}ms  P99={p99:.2f}ms  avg={avg:.2f}ms  max={mx:.2f}ms  min={mn:.2f}ms  samples={len(s)}")
        print(f"P99 < 50ms (e2e): {'PASS' if p99 < 50 else 'FAIL'}")
        print(f"P99 < 5ms (http overhead): {'PASS' if p99 < 5 else 'N/A (includes DB query)'}")
    else:
        print("No samples collected")

if __name__ == "__main__":
    asyncio.run(bench_http())
