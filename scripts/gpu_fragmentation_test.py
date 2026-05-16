import argparse
import asyncio
import json
import sys

from app.inference.gpu_monitor import GPUMemoryMonitor


async def main() -> None:
    parser = argparse.ArgumentParser(description="GPU fragmentation 4-hour continuous test")
    parser.add_argument("--duration-hours", type=float, default=4.0, help="Test duration in hours")
    parser.add_argument("--interval-seconds", type=float, default=60.0, help="Snapshot interval")
    parser.add_argument("--warning-threshold", type=float, default=0.30, help="Warning fragmentation threshold")
    parser.add_argument("--critical-threshold", type=float, default=0.40, help="Critical fragmentation threshold")
    parser.add_argument("--output", default="fragmentation_report.json")
    args = parser.parse_args()

    monitor = GPUMemoryMonitor(
        warning_threshold=args.warning_threshold,
        critical_threshold=args.critical_threshold,
        snapshot_interval_seconds=args.interval_seconds,
    )

    print(f"Starting GPU fragmentation test for {args.duration_hours} hours...")
    print(f"  Warning threshold: {args.warning_threshold:.0%}")
    print(f"  Critical threshold: {args.critical_threshold:.0%}")
    print(f"  Snapshot interval: {args.interval_seconds}s")

    report = await monitor.run_continuous_monitor(duration_hours=args.duration_hours)

    report_dict = {
        "duration_hours": report.duration_hours,
        "total_snapshots": report.total_snapshots,
        "max_fragmentation_rate": f"{report.max_fragmentation_rate:.4f}",
        "avg_fragmentation_rate": f"{report.avg_fragmentation_rate:.4f}",
        "warning_threshold": f"{report.warning_threshold:.2f}",
        "critical_threshold": f"{report.critical_threshold:.2f}",
        "exceeded_threshold_count": report.fragmentation_exceeded_threshold_count,
        "passed": report.passed,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report_dict, f, indent=2, ensure_ascii=False)

    print("\nFragmentation Report:")
    print(f"  Duration: {report.duration_hours:.1f} hours")
    print(f"  Snapshots: {report.total_snapshots}")
    print(f"  Max fragmentation: {report.max_fragmentation_rate:.1%} (target: <{report.critical_threshold:.0%})")
    print(f"  Avg fragmentation: {report.avg_fragmentation_rate:.1%}")
    print(f"  Exceeded threshold: {report.fragmentation_exceeded_threshold_count} times")
    print(f"  PASSED: {report.passed}")
    print(f"\nReport saved to {args.output}")

    sys.exit(0 if report.passed else 1)


if __name__ == "__main__":
    asyncio.run(main())
