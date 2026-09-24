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
class SuiteReport:
    passed: bool = True
    results: List[BenchResult] = field(default_factory=list)
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
        choices=["inline", "sg", "http", "memory", "model", "all"],
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
        print("  all      -> inline + sg（http/memory/model 需额外参数，未纳入 all）")
        return 0

    report = SuiteReport()
    if args.suite in ("inline", "all"):
        report.results.extend(suite_inline(args.iterations).results)
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

    print_report(report)

    if args.json_path:
        payload = {
            "passed": report.passed,
            "results": [item.as_dict() for item in report.results],
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
