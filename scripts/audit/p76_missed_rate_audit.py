"""05-3 S1：漏诊率季度回顾审计（只读；绝不把代理指标冒充真值）。

职责切分（刻意设计）：
- 本脚本只做**算术与对账**：分母、缺失率、与人工审计登记表的交叉核验；
- "是否漏诊"的临床判断由季度人工审计产出 --missed-registry JSON
  （每条含 request_id + final_diagnosis_source），机器不做 AI初始vs最终
  的自动比对（自然语言结论比对不可靠，且单标记不得定罪——须
  "最终诊断 + 金标准来源"两条独立证据，见 04 号计划 §05-3 ②）。

口径（STARD 2015 约束：必须报告分母/参考标准/缺失处理）：
- 分母 = final_diagnosis 非空的诊断；
- 分子 = --missed-registry 中通过核验的条目（存在、有 final_diagnosis、
  source ∈ {pathology, followup, expert_consensus}）；
- 分母为 0 → 输出 status=not_measured_no_final_diagnosis（绝不输出 0）。

用法：python scripts/audit/p76_missed_rate_audit.py --as-of 2026-09-30 --dry-run [--missed-registry FILE]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

SOURCES = {"pathology", "followup", "expert_consensus"}


def compute_audit(denominator: int, total: int, registry: list[dict]) -> dict:
    """纯算术核验（单测钉住）：分母 0 绝不输出 0，缺金标准来源的登记拒收并计数。"""
    if denominator == 0:
        return {
            "status": "not_measured_no_final_diagnosis",
            "denominator": 0,
            "missed": None,
            "missed_rate": None,
            "missing_final_diagnosis_rate": None,
        }
    valid = [
        r
        for r in registry
        if r.get("request_id") and r.get("final_diagnosis_source") in SOURCES
    ]
    return {
        "status": "measured",
        "total_diagnoses": total,
        "denominator": denominator,
        "missing_final_diagnosis_rate": round((total - denominator) / total, 4)
        if total
        else None,
        "missed": len(valid),
        "missed_rate": round(len(valid) / denominator, 4),
        "rejected_registry_entries": len(registry) - len(valid),
    }


async def _count_final_diagnosed() -> tuple[int, int]:
    """只读统计：final_diagnosis 非空数与诊断总数（与 collect_correction_records
    同款懒加载依赖，单测不连库）。"""
    from sqlalchemy import func, select

    from app.config.database import AsyncSessionLocal
    from app.models.database import DiagnosisResult

    async with AsyncSessionLocal() as db:
        total = await db.scalar(select(func.count()).select_from(DiagnosisResult))
        denominator = await db.scalar(
            select(func.count())
            .select_from(DiagnosisResult)
            .where(DiagnosisResult.final_diagnosis.isnot(None))
        )
    return int(denominator or 0), int(total or 0)


def _load_registry(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit("--missed-registry 必须是 JSON 数组")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="05-3 S1 漏诊率季度审计（只读）")
    parser.add_argument("--as-of", required=True, help="审计基准日 YYYY-MM-DD（仅留痕）")
    parser.add_argument("--dry-run", action="store_true", help="当前恒只读，占位与 p 系脚本对齐")
    parser.add_argument("--missed-registry", default=None, help="人工审计登记表 JSON 文件")
    parser.add_argument("--output", default=None, help="结果写出到该 JSON 文件")
    args = parser.parse_args()

    denominator, total = asyncio.run(_count_final_diagnosed())
    registry = _load_registry(args.missed_registry)
    result = {"as_of": args.as_of, **compute_audit(denominator, total, registry)}
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
