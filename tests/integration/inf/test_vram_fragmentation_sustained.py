import asyncio
import os
import time

import pytest

from app.inference.vram_fragmentation import VRAMFragmentationVerifier

SUSTAINED_RUN_ENABLED = os.environ.get("SUSTAINED_RUN_ENABLED", "0") == "1"
QUICK_MODE = os.environ.get("SUSTAINED_RUN_QUICK", "0") == "1"


@pytest.mark.sustained_run
@pytest.mark.skipif(not SUSTAINED_RUN_ENABLED, reason="SUSTAINED_RUN_ENABLED=1 required")
@pytest.mark.asyncio
class TestVRAMFragmentationSustained:
    async def test_4hour_sustained_fragmentation(self):
        duration_seconds = 300 if QUICK_MODE else 4 * 3600
        snapshot_interval = 30 if QUICK_MODE else 300
        warning_threshold = 0.30
        critical_threshold = 0.40

        verifier = VRAMFragmentationVerifier(
            warning_threshold=warning_threshold,
            critical_threshold=critical_threshold,
        )

        snapshots = []
        start_time = time.monotonic()
        iteration = 0

        while time.monotonic() - start_time < duration_seconds:
            snapshot = verifier.take_snapshot()
            snapshots.append(snapshot)

            if snapshot.fragmentation_ratio > critical_threshold:
                pytest.fail(
                    f"VRAM碎片化率{snapshot.fragmentation_ratio:.2%}超过严重阈值{critical_threshold:.0%}，"
                    f"迭代{iteration}，已运行{time.monotonic()-start_time:.0f}秒"
                )

            if snapshot.fragmentation_ratio > warning_threshold:
                print(
                    f"WARNING: 碎片化率{snapshot.fragmentation_ratio:.2%}超过警告阈值"
                    f"（迭代{iteration}，已运行{time.monotonic()-start_time:.0f}秒）"
                )

            iteration += 1
            await asyncio.sleep(snapshot_interval)

        report = verifier.generate_report(snapshots)

        assert report.max_fragmentation_ratio < critical_threshold, \
            f"最大碎片化率{report.max_fragmentation_ratio:.2%}超过严重阈值{critical_threshold:.0%}"

        print("\n=== VRAM碎片化持续运行测试报告 ===")
        print(f"运行时长: {duration_seconds/3600:.1f}小时")
        print(f"快照数量: {len(snapshots)}")
        print(f"最大碎片化率: {report.max_fragmentation_ratio:.2%}")
        print(f"平均碎片化率: {report.avg_fragmentation_ratio:.2%}")
        print(f"最终碎片化率: {report.final_fragmentation_ratio:.2%}")
        print(f"警告阈值: {warning_threshold:.0%}")
        print(f"严重阈值: {critical_threshold:.0%}")

    async def test_quick_5min_fragmentation(self):
        if not QUICK_MODE and SUSTAINED_RUN_ENABLED:
            pytest.skip("快速模式未启用，设置SUSTAINED_RUN_QUICK=1")

        verifier = VRAMFragmentationVerifier(
            warning_threshold=0.30,
            critical_threshold=0.40,
        )

        snapshots = []
        for _ in range(10):
            snapshot = verifier.take_snapshot()
            snapshots.append(snapshot)
            await asyncio.sleep(1)

        report = verifier.generate_report(snapshots)
        assert report.max_fragmentation_ratio < 0.40
