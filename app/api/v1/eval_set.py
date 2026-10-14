"""评测集进度**只读**端点（19 号计划：≥700 例进度条 / ≥50 例医生标注坏例）。

数据来源
--------
服务器上由 `scripts/langfuse/collect_eval_set.py`（业务库采集）写出**报告 JSON**
（默认 `~/uai/reports/langfuse_eval_set.json`）。本端点只**读该报告**，不碰数据库、
不触发采集 —— 采集是离线批处理（可能扫全表 + 逐条算 hash），不该挂在 HTTP 请求里。

🔒 权限口径
-----------
用 `get_current_active_user`（**任意已登录用户**），而不是 `RoleChecker(["SYSTEM_ADMIN",...])`：

* 进度区块挂在**反馈页**（医生日常使用），若要求管理员角色，医生看到的将是一个 403
  区块 —— 那就等于没做（A.58「405/403 要在界面上有落点」的口径）；
* 返回内容只有**聚合计数**（样本数 / 标注数 / 目标值），**不含任何患者或结论内容**，
  不是 PHI，也不是可被利用的运维信息。

缺报告（还没采集过）**返回 200 + `available=false`**，不是 404/500：这是正常状态
（采集尚未排期），界面据此显示「尚未采集」的可读提示 —— 零静默口径。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.security.auth import get_current_active_user

logger = structlog.get_logger()

_REPO_ROOT = Path(__file__).resolve().parents[3]


class EvalSetStats(BaseModel):
    """评测集进度（只读快照）。"""

    available: bool = Field(description="报告是否存在（false = 尚未采集，或报告损坏）")
    reason: str | None = Field(default=None, description="available=false 时的可读原因")
    generated_at: str | None = None
    source: str | None = None
    total: int = 0
    deduped: int = 0
    undedupable: int = 0
    skipped_no_image: int = Field(default=0, description="无影像的判读（纯文本腿）未入集的数量")
    # A.62 批 3：**不同患者数**才是"700 例"的真实分母刻度（168 条可能只来自 12 位患者）。
    # 只给聚合计数，仍不涉及任何患者标识 —— 不是 PHI。
    patient_count: int = Field(default=0, description="不同患者数（样本数的真实刻度）")
    patient_id_missing: int = Field(
        default=0, description="取不到患者 ID 的判读数（>0 表示去重已降级为按影像）"
    )
    dedup_reason: dict[str, int] = Field(
        default_factory=dict, description="去重口径分布：patient_image / image_only / no_basis"
    )
    doctor_labeled: int = 0
    targets: dict[str, int] = Field(default_factory=dict)
    progress: dict[str, float] = Field(default_factory=dict)
    dedup_basis: dict[str, int] = Field(default_factory=dict)
    report_path: str | None = None


def _candidates() -> list[Path]:
    """按优先级给出报告候选路径（环境变量可覆盖，便于部署与测试）。"""
    env = os.environ.get("UAI_EVAL_SET_REPORT")
    paths = [Path(env).expanduser()] if env else []
    paths.append(Path.home() / "uai" / "reports" / "langfuse_eval_set.json")
    paths.append(_REPO_ROOT / "reports" / "langfuse_eval_set.json")
    return paths


def read_stats() -> EvalSetStats:
    """读报告 → 统计（缺文件/损坏都返回 available=false + 可读原因，绝不抛）。"""
    for path in _candidates():
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "eval_set_report_unreadable",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return EvalSetStats(
                available=False,
                reason=f"采集报告损坏：{type(exc).__name__}",
                report_path=str(path),
            )
        targets = data.get("targets") or {}
        t_total = int(targets.get("total") or 0)
        t_doc = int(targets.get("doctor_labeled") or 0)
        deduped = int(data.get("deduped") or 0)
        doctor_labeled = int(data.get("doctor_labeled") or 0)
        return EvalSetStats(
            available=True,
            generated_at=data.get("generated_at"),
            source=data.get("source"),
            total=int(data.get("total") or 0),
            deduped=deduped,
            undedupable=int(data.get("undedupable") or 0),
            skipped_no_image=int(data.get("skipped_no_image") or 0),
            patient_count=int(data.get("patient_count") or 0),
            patient_id_missing=int(data.get("patient_id_missing") or 0),
            dedup_reason=data.get("dedup_reason") or {},
            doctor_labeled=doctor_labeled,
            targets=targets,
            progress={
                "samples": round(deduped / t_total, 4) if t_total else 0.0,
                "doctor_labeled": round(doctor_labeled / t_doc, 4) if t_doc else 0.0,
            },
            dedup_basis=data.get("dedup_basis") or {},
            report_path=str(path),
        )
    return EvalSetStats(
        available=False,
        reason="尚未采集（在服务器跑 scripts/langfuse/collect_eval_set.py）",
        report_path=str(_candidates()[-1]),
    )


router = APIRouter(
    tags=["eval-set"],
    dependencies=[Depends(get_current_active_user)],
)


@router.get("/stats", response_model=EvalSetStats)
async def eval_set_stats() -> EvalSetStats:
    """评测集进度（只读）。缺报告时返回 `available=false` + 可读原因。"""
    return read_stats()
