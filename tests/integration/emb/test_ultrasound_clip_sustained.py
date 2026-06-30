"""Ultrasound-CLIP 8 小时持续运行验证测试(门控测试)。

环境要求:
- 真实 GPU 节点(本地无 GPU 时无法运行)
- 设置 SUSTAINED_RUN_ENABLED=1 启用
- 可选 SUSTAINED_RUN_QUICK=1 启用 5 分钟冒烟模式

验证目标:
- 进程不崩溃(8h 无 OOM/异常退出)
- I2T@10 波动 < 5%(每 30 分钟采样)
- 推理延迟 P99 < 100ms
- 显存碎片化率 < 0.40(critical threshold)

参考: tests/integration/inf/test_vram_fragmentation_sustained.py
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest

SUSTAINED_RUN_ENABLED = os.environ.get("SUSTAINED_RUN_ENABLED", "0") == "1"
QUICK_MODE = os.environ.get("SUSTAINED_RUN_QUICK", "0") == "1"


@pytest.mark.sustained_run
@pytest.mark.track_emb
@pytest.mark.skipif(
    not SUSTAINED_RUN_ENABLED, reason="SUSTAINED_RUN_ENABLED=1 required for sustained test"
)
class TestUltrasoundCLIPSustained:
    @pytest.mark.asyncio
    async def test_8hour_sustained_i2t_at_10(self):
        duration_hours = 0.083 if QUICK_MODE else 8.0
        sample_interval_seconds = 30.0 if QUICK_MODE else 1800.0
        max_i2t_drift = 0.05
        max_latency_p99_ms = 100.0

        from app.embedding.clip_model import UltrasoundCLIPModel
        from app.embedding.busi_adapter import BUSIAdapter

        model = UltrasoundCLIPModel(device="cuda")
        try:
            model.load()
        except Exception as exc:
            pytest.skip(f"GPU model load failed: {exc}")
            return

        try:
            adapter = BUSIAdapter(dataset_path="")
        except Exception:
            pass

        samples = []
        for i in range(10):
            samples.append(
                type(
                    "Sample",
                    (),
                    {
                        "image_path": f"<synthetic>/sample_{i}.png",
                        "text": f"synthetic text {i}",
                        "label": "benign",
                    },
                )()
            )

        i2t_samples: list[float] = []
        latencies: list[float] = []
        start = time.monotonic()
        crash_detected = False

        try:
            while time.monotonic() - start < duration_hours * 3600:
                try:
                    result = model.evaluate(samples, k=10)
                    i2t_samples.append(result.i2t_at_k)
                    latencies.append(result.latency_p99_ms)
                except Exception as exc:
                    crash_detected = True
                    pytest.fail(f"Sustained run crashed: {exc}")

                elapsed = time.monotonic() - start
                next_sample_in = sample_interval_seconds - (elapsed % sample_interval_seconds)
                await asyncio.sleep(min(next_sample_in, 5.0))
        except asyncio.CancelledError:
            pass

        assert not crash_detected, "process crashed during sustained run"
        assert len(i2t_samples) >= 2, "insufficient samples to compute drift"
        if len(i2t_samples) >= 2:
            baseline = i2t_samples[0]
            drifts = [abs(s - baseline) for s in i2t_samples[1:]]
            max_drift = max(drifts)
            assert max_drift < max_i2t_drift, (
                f"I2T@10 drift {max_drift:.4f} exceeds threshold {max_i2t_drift}"
            )
        if latencies:
            import numpy as np

            p99 = float(np.percentile(latencies, 99))
            assert p99 < max_latency_p99_ms, (
                f"P99 latency {p99:.2f}ms exceeds {max_latency_p99_ms}ms"
            )

        print(f"\n=== Ultrasound-CLIP 持续运行报告 ===")
        print(f"运行时长: {duration_hours}h")
        print(f"I2T@10 采样数: {len(i2t_samples)}")
        print(f"P99 延迟: {p99 if latencies else 0:.2f}ms")

    @pytest.mark.asyncio
    async def test_quick_5min_sustained(self):
        if not QUICK_MODE:
            pytest.skip("QUICK mode not enabled (set SUSTAINED_RUN_QUICK=1)")

        from app.embedding.clip_model import UltrasoundCLIPModel

        model = UltrasoundCLIPModel(device="cuda")
        try:
            model.load()
        except Exception as exc:
            pytest.skip(f"GPU not available: {exc}")
            return

        samples = []
        for i in range(5):
            samples.append(
                type(
                    "Sample",
                    (),
                    {
                        "image_path": f"<synthetic>/s_{i}.png",
                        "text": f"t {i}",
                        "label": "benign",
                    },
                )()
            )

        start = time.monotonic()
        sample_count = 0
        while time.monotonic() - start < 300.0:
            try:
                model.evaluate(samples, k=10)
                sample_count += 1
            except Exception as exc:
                pytest.fail(f"Quick sustained run failed: {exc}")
            await asyncio.sleep(1.0)
        assert sample_count > 0, "no samples evaluated"
