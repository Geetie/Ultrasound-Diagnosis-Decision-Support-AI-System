"""性能基准统一入口（T-G1-INT-03）。

把仓库里散落的基准脚本收拢成一个入口，输出统一报告（可选 JSON），
并按阈值给出通过/失败结论，供 CI 与 Gate 1 性能验收使用。

用法::

    python scripts/bench_suite.py --list
    python scripts/bench_suite.py --suite inline              # 纯函数基准（无外部依赖）
    python scripts/bench_suite.py --suite sg                  # SafetyGateway P99（等价 bench_p99.py）
    python scripts/bench_suite.py --suite http --base-url http://localhost:8000
    python scripts/bench_suite.py --suite memory              # 内存画像（scripts/memory_profile.py）
    python scripts/bench_suite.py --suite all --json bench_report.json

退出码：0 = 全部达标；1 = 有基准未达标（CI 以此判定）。
真机相关基准（TensorRT 导航、FunASR 识别）不在本入口内——需现场硬件。
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass
class BenchResult:
    """单个基准项的结果。"""

    name: str
    suite: str
    samples: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    threshold_ms: float
    passed: bool
    note: str = ""

    def as_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class MetricResult:
    """非延迟型指标（并发放大倍数、显存占用等）。

    延迟基准用 P50/P95/P99 表达，但"并发下退化倍数""显存占用"这类量纲不同的
    指标硬塞进 BenchResult 会让报告表头失去意义（P99 是多少 MB？），故单列。
    """

    name: str
    suite: str
    value: float
    unit: str
    threshold: float
    passed: bool
    note: str = ""

    def as_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class SuiteReport:
    passed: bool = True
    results: List[BenchResult] = field(default_factory=list)
    metrics: List[MetricResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)


def _percentile(ordered: List[float], pct: float) -> float:
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, int(len(ordered) * pct / 100.0))
    return ordered[index]


def measure(
    name: str,
    *,
    suite: str,
    func: Callable[[], object],
    iterations: int,
    threshold_ms: float,
    warmup: int = 20,
    note: str = "",
) -> BenchResult:
    """测量一个可调用对象的耗时分布（毫秒）。"""
    for _ in range(warmup):
        func()

    samples: List[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        func()
        samples.append((time.perf_counter() - started) * 1000.0)

    ordered = sorted(samples)
    p99 = _percentile(ordered, 99)
    return BenchResult(
        name=name,
        suite=suite,
        samples=len(samples),
        p50_ms=round(_percentile(ordered, 50), 4),
        p95_ms=round(_percentile(ordered, 95), 4),
        p99_ms=round(p99, 4),
        max_ms=round(max(ordered), 4),
        threshold_ms=threshold_ms,
        passed=p99 <= threshold_ms,
        note=note,
    )


# ----------------------------------------------------------------------
# inline：无外部依赖的纯函数基准
# ----------------------------------------------------------------------


def suite_inline(iterations: int) -> SuiteReport:
    """报告生成、摘要、冷归档序列化与检索路由的纯函数基准。

    阈值取"远高于实测、但足以挡住数量级退化"的量级：
    这些操作都在请求热路径上，一旦退化成秒级会直接拖垮接口 P99。
    """
    from app.services import report_service
    from app.services.cold_archive import CATEGORY_AUDIT_LOGS, _serialize_row, _to_jsonl
    from app.models.database import AuditLog

    report = SuiteReport()
    content = report_service.build_report_content(
        type(
            "_R",
            (),
            {
                "request_id": "req_bench",
                "session_id": "sess_bench",
                "clinical_text": "甲状腺右叶低回声结节，边界不清，微钙化" * 20,
                "recommendations": "TI-RADS 4a，建议细针穿刺活检",
                "organ": "thyroid",
                "confidence": 0.88,
                "knowledge_ids": ["kb_1", "kb_2"],
                "findings": {"fusion_result": {"needs_review": False}},
                "status": "completed",
            },
        )()
    )
    template = report_service.load_template()
    rendered = report_service.render_report(content)
    payload = report_service.report_payload(rendered)

    rows = [
        AuditLog(
            id=i,
            log_id=f"log_{i}",
            session_id="sess_bench",
            action="diagnosis_request",
            actor="doc_1",
            target_type="diagnosis",
            target_id="req_bench",
            detail={"index": i},
            log_type="operation",
            hash_prev="h_prev",
            hash_self=f"h_{i}",
            is_critical_operation=False,
        )
        for i in range(1, 201)
    ]
    records = [_serialize_row(row, ("id", "log_id", "action", "detail")) for row in rows]

    from app.services.diagnosis_pipeline import get_routing_router

    router = get_routing_router()
    query = "低回声结节，边界不清，微钙化，血流信号丰富"

    report.results.append(
        measure(
            "报告模板渲染",
            suite="inline",
            func=lambda: report_service.render_report(content),
            iterations=iterations,
            threshold_ms=20.0,
            note="规格：报告草稿 ≤ 2 分钟（纯内存步骤应达毫秒级）",
        )
    )
    report.results.append(
        measure(
            "报告内容摘要 sha256",
            suite="inline",
            func=lambda: report_service.compute_digest(payload),
            iterations=iterations,
            threshold_ms=5.0,
        )
    )
    report.results.append(
        measure(
            "冷归档 JSONL 序列化(200 行)",
            suite="inline",
            func=lambda: _to_jsonl(records),
            iterations=iterations,
            threshold_ms=20.0,
            note=f"category={CATEGORY_AUDIT_LOGS}",
        )
    )
    from app.models.schemas import ResponseTier

    report.results.append(
        measure(
            "Adaptive RAG 档位判定",
            suite="inline",
            func=lambda: router.assess(
                query=query, requested_tier=ResponseTier.STANDARD, target_organ="thyroid"
            ),
            iterations=iterations,
            threshold_ms=10.0,
            note="热路径：每次诊断请求都会判定档位",
        )
    )
    report.passed = all(item.passed for item in report.results)
    return report


# ----------------------------------------------------------------------
# 并发维度（T-G1-INT-03 缺的一维：延迟达标不等于并发达标）
# ----------------------------------------------------------------------

CONCURRENCY_LEVEL = 8
CONCURRENCY_AMPLIFICATION_LIMIT = 6.0


def suite_concurrency(
    iterations: int, concurrency: int = CONCURRENCY_LEVEL
) -> SuiteReport:
    """同一热路径在 C 路并发下的单次耗时放大倍数。

    为什么单看 P99 不够：热路径若在并发下退化为近似串行（共享锁、GIL 密集
    段、同步 I/O），**单次**延迟依然漂亮，而吞吐掉 C 倍——线上表现是"每个请求
    都不慢，但排队排到超时"。这里把并发 P99 与串行 P99 的比值作为门槛，直接
    暴露这类退化。

    阈值 6.0 的由来：纯 CPU 密集段在 GIL 下并发 C=8 的理论最坏放大就是 8x，
    取 6.0 留出噪声余量，同时能挡住"整段被串行化"（会到 8x 以上）。
    """
    import asyncio

    from app.agents.routing import RoutingDecision  # noqa: F401  (保持与 inline 同源导入)
    from app.models.schemas import ResponseTier
    from app.services.diagnosis_pipeline import get_routing_router

    report = SuiteReport()
    router = get_routing_router()
    query = "低回声结节，边界不清，微钙化，血流信号丰富"

    def _hot_path() -> None:
        router.assess(query=query, requested_tier=ResponseTier.STANDARD, target_organ="thyroid")

    serial_batches = max(1, iterations // concurrency)

    async def _run_concurrent() -> List[float]:
        samples: List[float] = []
        for _ in range(serial_batches):
            started = time.perf_counter()
            await asyncio.gather(*[asyncio.to_thread(_hot_path) for _ in range(concurrency)])
            samples.append((time.perf_counter() - started) * 1000.0 / concurrency)
        return samples

    for _ in range(5):
        _hot_path()  # warmup，避免把首次导入/缓存填充算进并发基线

    serial = measure(
        f"档位判定串行基线(C=1)",
        suite="concurrency",
        func=_hot_path,
        iterations=iterations,
        threshold_ms=50.0,
        note="并发放大倍数的分母",
    )
    report.results.append(serial)

    concurrent_samples = sorted(asyncio.run(_run_concurrent()))
    concurrent_p99 = _percentile(concurrent_samples, 99)
    amplification = concurrent_p99 / max(serial.p99_ms, 1e-6)

    report.metrics.append(
        MetricResult(
            name=f"热路径并发放大倍数(C={concurrency})",
            suite="concurrency",
            value=round(amplification, 3),
            unit="x",
            threshold=CONCURRENCY_AMPLIFICATION_LIMIT,
            passed=amplification <= CONCURRENCY_AMPLIFICATION_LIMIT,
            note="并发 P99 / 串行 P99；接近 C 说明热路径在并发下退化为串行",
        )
    )
    report.results.append(
        BenchResult(
            name=f"档位判定并发 P99(C={concurrency})",
            suite="concurrency",
            samples=len(concurrent_samples),
            p50_ms=round(_percentile(concurrent_samples, 50), 4),
            p95_ms=round(_percentile(concurrent_samples, 95), 4),
            p99_ms=round(concurrent_p99, 4),
            max_ms=round(max(concurrent_samples), 4),
            threshold_ms=round(serial.p99_ms * CONCURRENCY_AMPLIFICATION_LIMIT, 4),
            passed=amplification <= CONCURRENCY_AMPLIFICATION_LIMIT,
            note="每样本为 C 路并发的平均单次耗时",
        )
    )
    report.passed = all(item.passed for item in report.results) and all(
        item.passed for item in report.metrics
    )
    return report


# ----------------------------------------------------------------------
# 显存维度（T-G1-INT-03 缺的另一维：真机项，无 GPU 时显式跳过而非静默通过）
# ----------------------------------------------------------------------


def suite_vram() -> SuiteReport:
    """显存占用与碎片率快照。

    真机项：本机无可用 GPU 时记为 SKIP 而不是 PASS——静默通过会让人误以为
    "显存维度已验证"，而实际上什么都没测。
    """
    report = SuiteReport()
    from app.inference.gpu_memory_monitor import GPUMemoryMonitor

    monitor = GPUMemoryMonitor()
    if not monitor.is_gpu_available():
        report.skipped.append("vram（本机无可用 GPU；显存维度需真机验证）")
        return report

    snapshot = monitor.take_snapshot()
    if snapshot is None:
        report.skipped.append("vram（nvidia-smi 未返回快照）")
        return report

    data = snapshot.to_dict()
    total = float(data.get("total_memory_mb") or 0.0)
    used = float(data.get("used_memory_mb") or 0.0)
    utilization = (used / total * 100.0) if total else 0.0

    report.metrics.append(
        MetricResult(
            name="显存占用率",
            suite="vram",
            value=round(utilization, 2),
            unit="%",
            threshold=90.0,
            passed=utilization <= 90.0,
            note=f"used={used:.0f}MB / total={total:.0f}MB",
        )
    )
    fragmentation = _vram_fragmentation_rate(data)
    if fragmentation is not None:
        report.metrics.append(
            MetricResult(
                name="显存碎片率",
                suite="vram",
                value=round(fragmentation, 2),
                unit="%",
                threshold=30.0,
                passed=fragmentation <= 30.0,
                note="碎片率过高会触发模型热重载（见 gpu_memory_monitor）",
            )
        )
    report.passed = all(item.passed for item in report.metrics)
    return report


def _vram_fragmentation_rate(snapshot: Dict[str, object]) -> Optional[float]:
    """从快照里取碎片率；缺字段或估算不可用时返回 None。"""
    for key in ("fragmentation_rate", "fragmentation", "fragmentation_percent"):
        raw = snapshot.get(key)
        if isinstance(raw, (int, float)):
            return float(raw) * 100.0 if raw <= 1.0 else float(raw)
    return None


# ----------------------------------------------------------------------
# 子脚本基准（复用既有脚本，避免重复实现）
# ----------------------------------------------------------------------

SUB_SCRIPTS: Dict[str, List[str]] = {
    "sg": ["bench_p99.py"],
    "http": ["bench_http_p99.py"],
    "memory": ["scripts/memory_profile.py"],
    "model": ["scripts/model_benchmark.py"],
}


def suite_subprocess(name: str, extra_args: List[str]) -> SuiteReport:
    """运行既有的基准脚本（它们自带阈值与报告）。"""
    report = SuiteReport()
    scripts = SUB_SCRIPTS[name]
    for script in scripts:
        path = ROOT / script
        if not path.exists():
            report.skipped.append(f"{script}（文件不存在）")
            continue
        command = [sys.executable, str(path), *extra_args]
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        tail = "\n".join(line for line in completed.stdout.splitlines()[-25:])
        print(f"\n--- {script} (exit={completed.returncode}) ---\n{tail}")
        if completed.stderr.strip():
            print(f"[stderr] {completed.stderr.strip()[:1000]}")
        if completed.returncode != 0:
            report.passed = False
            report.errors.append(f"{script} 退出码 {completed.returncode}")
    return report


# ----------------------------------------------------------------------


def print_report(report: SuiteReport) -> None:
    print("\n" + "=" * 78)
    print("性能基准汇总")
    print("=" * 78)
    for item in report.results:
        flag = "PASS" if item.passed else "FAIL"
        print(
            f"  [{flag}] {item.name:<32} P50={item.p50_ms:>8.3f}ms "
            f"P95={item.p95_ms:>8.3f}ms P99={item.p99_ms:>8.3f}ms "
            f"(阈值 {item.threshold_ms}ms, n={item.samples})"
        )
        if item.note:
            print(f"         备注：{item.note}")
    for metric in report.metrics:
        flag = "PASS" if metric.passed else "FAIL"
        print(
            f"  [{flag}] {metric.name:<32} {metric.value:>8.3f}{metric.unit} "
            f"(阈值 {metric.threshold}{metric.unit})"
        )
        if metric.note:
            print(f"         备注：{metric.note}")
    for skipped in report.skipped:
        print(f"  [SKIP] {skipped}")
    for error in report.errors:
        print(f"  [ERR ] {error}")
    print("-" * 78)
    print(f"结论：{'全部达标' if report.passed else '存在未达标项'}（真机基准需现场硬件，不在此入口）")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="统一性能基准入口")
    parser.add_argument(
        "--suite",
        default="inline",
        choices=["inline", "concurrency", "vram", "sg", "http", "memory", "model", "all"],
        help="要运行的基准套件（默认 inline，无外部依赖）",
    )
    parser.add_argument("--iterations", type=int, default=500, help="inline 套件的采样次数")
    parser.add_argument("--json", dest="json_path", help="把结果写入 JSON 文件")
    parser.add_argument("--base-url", default="", help="http 套件的目标地址")
    parser.add_argument("--list", action="store_true", help="列出可用套件后退出")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="保留业务 INFO 日志（默认静默，避免每次迭代打一行日志掩盖基准结果）",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    # 基准循环里每次迭代都会触发业务日志（如档位判定 INFO），默认静默，
    # 否则几百行日志会把基准结论淹没；--verbose 可恢复。
    if not args.verbose:
        import logging

        logging.disable(logging.INFO)

    if args.list:
        print("可用套件：")
        for name, scripts in SUB_SCRIPTS.items():
            print(f"  {name:<8} -> {', '.join(scripts)}")
        print("  inline   -> 内置纯函数基准（报告渲染/摘要/冷归档序列化/档位判定）")
        print("  concurrency -> 热路径并发放大倍数（随 inline 一起跑）")
        print("  vram     -> 显存占用率与碎片率（真机项，无 GPU 记为 SKIP）")
        print("  all      -> inline + concurrency + vram + sg（http/memory/model 需额外参数，未纳入 all）")
        return 0

    report = SuiteReport()
    if args.suite in ("inline", "all"):
        report.results.extend(suite_inline(args.iterations).results)
    if args.suite in ("inline", "concurrency", "all"):
        # 并发维度与 inline 一样无外部依赖，随 inline 一起跑，CI 无需改配置
        concurrent = suite_concurrency(args.iterations)
        report.results.extend(concurrent.results)
        report.metrics.extend(concurrent.metrics)
        report.skipped.extend(concurrent.skipped)
        if not concurrent.passed:
            report.passed = False
    if args.suite in ("vram", "all"):
        vram = suite_vram()
        report.metrics.extend(vram.metrics)
        report.skipped.extend(vram.skipped)
        if not vram.passed:
            report.passed = False
    if args.suite == "sg":
        sub = suite_subprocess("sg", [])
        report.errors.extend(sub.errors)
        report.skipped.extend(sub.skipped)
        if sub.errors:
            report.passed = False
    if args.suite == "http":
        extra = ["--base-url", args.base_url] if args.base_url else []
        sub = suite_subprocess("http", extra)
        report.errors.extend(sub.errors)
        report.skipped.extend(sub.skipped)
        if sub.errors:
            report.passed = False
    if args.suite == "memory":
        sub = suite_subprocess("memory", [])
        report.errors.extend(sub.errors)
        report.skipped.extend(sub.skipped)
    if args.suite == "model":
        sub = suite_subprocess("model", [])
        report.errors.extend(sub.errors)
        report.skipped.extend(sub.skipped)

    if args.suite == "all":
        sub = suite_subprocess("sg", [])
        report.errors.extend(sub.errors)
        report.skipped.extend(sub.skipped)
        if sub.errors:
            report.passed = False

    if report.results:
        report.passed = report.passed and all(item.passed for item in report.results)
    if report.metrics:
        report.passed = report.passed and all(item.passed for item in report.metrics)

    print_report(report)

    if args.json_path:
        payload = {
            "passed": report.passed,
            "results": [item.as_dict() for item in report.results],
            "metrics": [item.as_dict() for item in report.metrics],
            "errors": report.errors,
            "skipped": report.skipped,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        Path(args.json_path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"报告已写入 {args.json_path}")

    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
