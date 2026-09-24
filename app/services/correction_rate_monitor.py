"""AI 修正率监控（二期 §7）。

规格要求两个维度：
- **分级偏差方向**：AI 判级偏高还是偏低，单方向占比 >20% 即告警；
- **医生个体维度**：某位医生修正率 >50% 而其他医生 <20% 即告警。

为什么要有最小样本门槛
----------------------
没有门槛时，"某医生 1 次反馈全是修正"就是 100% 修正率——直接触发个体告警。这类
误报会把真实信号淹没（医生会很快学会忽略告警），所以低于 ``min_samples`` 的群体
只统计不判定，并在报告里标明"样本不足"。

为什么方向要解析而不是查表
--------------------------
修正反馈的载荷是 ``field/original_value/corrected_value``（自由文本，见
``FeedbackContent``）。分级值形如 ``TR4``/``TR3``，也可能出现 ``良性``/``恶性`` 这类
非分级表述。这里统一取"末尾整数"作为级别比较依据：能解析出明确高低才计入方向
统计，解析不出的记为 ``ungradable`` **单独报数**——静默丢弃会让"修正率"这个分母
失真，看板上看不出还有一批反馈根本没被纳入。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional

import structlog

logger = structlog.get_logger()

# 形如 TR4 / LIRADS-5 / grade 3：前缀任意（可含字母、数字外的符号），末尾取整数
_GRADE_PATTERN = re.compile(r"^\s*(?P<prefix>[A-Za-z\-_/ ]*?)\s*(?P<level>\d+)\s*$")


class GradeDirection(str, Enum):
    AI_HIGHER = "ai_higher"      # AI 判级偏高
    AI_LOWER = "ai_lower"        # AI 判级偏低
    SAME = "same"                # 同级别（属于"被修正但级别一致"，如表述修正）
    UNGRADABLE = "ungradable"    # 无法比较级别（非分级表述或格式不同）


@dataclass(frozen=True)
class CorrectionRecord:
    """一条规范化后的修正记录。"""

    reviewer_id: str
    field: str
    original_value: str
    corrected_value: str

    @classmethod
    def from_feedback_row(cls, row: Any) -> Optional["CorrectionRecord"]:
        """从 ``FeedbackRecord`` ORM 行构造；非修正类或载荷不完整返回 None。"""
        try:
            feedback_type = str(getattr(row, "feedback_type", "") or "")
            if feedback_type != "correction":
                return None
            content = getattr(row, "content", None)
            if not isinstance(content, dict):
                return None
            original = content.get("original_value")
            corrected = content.get("corrected_value")
            if original is None or corrected is None:
                return None
            return cls(
                reviewer_id=str(getattr(row, "reviewer_id", "") or "unknown"),
                field=str(content.get("field") or ""),
                original_value=str(original),
                corrected_value=str(corrected),
            )
        except Exception:  # noqa: BLE001 - 单条脏数据不得让整次统计失败
            return None


def _parse_level(value: str) -> Optional[int]:
    match = _GRADE_PATTERN.match(value or "")
    if match is None:
        return None
    return int(match.group("level"))


def classify_direction(original_value: str, corrected_value: str) -> GradeDirection:
    """判断 AI 判级相对医生修正值偏高还是偏低。"""
    ai_level = _parse_level(original_value)
    doctor_level = _parse_level(corrected_value)
    if ai_level is None or doctor_level is None:
        return GradeDirection.UNGRADABLE
    if ai_level > doctor_level:
        return GradeDirection.AI_HIGHER
    if ai_level < doctor_level:
        return GradeDirection.AI_LOWER
    return GradeDirection.SAME


@dataclass
class ReviewerStats:
    reviewer_id: str
    considered: int = 0
    corrected: int = 0
    ai_higher: int = 0
    ai_lower: int = 0

    @property
    def correction_rate(self) -> float:
        if self.considered == 0:
            return 0.0
        return self.corrected / self.considered

    def as_dict(self) -> Dict[str, Any]:
        return {
            "reviewer_id": self.reviewer_id,
            "considered": self.considered,
            "corrected": self.corrected,
            "correction_rate": round(self.correction_rate, 4),
            "ai_higher": self.ai_higher,
            "ai_lower": self.ai_lower,
        }


@dataclass
class CorrectionRateReport:
    """一次修正率评估的完整结果（可直接给看板）。"""

    considered: int = 0
    corrected: int = 0
    direction_counts: Dict[str, int] = field(default_factory=dict)
    reviewer_stats: Dict[str, ReviewerStats] = field(default_factory=dict)
    directional_bias_alert: Optional[Dict[str, Any]] = None
    reviewer_outlier_alerts: List[Dict[str, Any]] = field(default_factory=list)
    insufficient_samples: bool = False

    @property
    def correction_rate(self) -> float:
        return self.corrected / self.considered if self.considered else 0.0

    def direction_rate(self, direction: GradeDirection) -> float:
        """方向占比以**可分级**的修正条数为分母：非分级表述混进分母会让两个方向
        的占比被稀释，明明全是偏高也测不出 20% 越线。"""
        gradable = self.direction_counts.get(GradeDirection.AI_HIGHER.value, 0) + (
            self.direction_counts.get(GradeDirection.AI_LOWER.value, 0)
        )
        if gradable == 0:
            return 0.0
        return self.direction_counts.get(direction.value, 0) / gradable

    def as_dict(self) -> Dict[str, Any]:
        return {
            "considered": self.considered,
            "corrected": self.corrected,
            "correction_rate": round(self.correction_rate, 4),
            "direction_counts": dict(self.direction_counts),
            "direction_rates": {
                GradeDirection.AI_HIGHER.value: round(
                    self.direction_rate(GradeDirection.AI_HIGHER), 4
                ),
                GradeDirection.AI_LOWER.value: round(
                    self.direction_rate(GradeDirection.AI_LOWER), 4
                ),
            },
            "reviewers": [stats.as_dict() for stats in self.reviewer_stats.values()],
            "directional_bias_alert": self.directional_bias_alert,
            "reviewer_outlier_alerts": self.reviewer_outlier_alerts,
            "insufficient_samples": self.insufficient_samples,
        }


class CorrectionRateMonitor:
    """把一批"反馈 + 是否被修正"的观察聚合成功报告。

    观察集合由调用方一次性查出（``considered`` 为纳入统计的诊断总数，
    ``records`` 为其中的修正反馈）；本类只做内存聚合，不产生任何 I/O。
    """

    def __init__(
        self,
        *,
        considered_records: Iterable[Any] = (),
        correction_records: Iterable[CorrectionRecord] = (),
    ) -> None:
        self._considered = list(considered_records)
        self._records = list(correction_records)

    def assess(self) -> CorrectionRateReport:
        from app.config.settings import settings

        report = CorrectionRateReport()
        report.considered = len(self._considered)

        # 先按"被评估的诊断"建立医生分母。**不能只从修正记录里建**：一次修正都没有的
        # 医生会完全不出现在统计里，于是"个体偏离群体"的比较范围凭空少了一批人——
        # 极端情况下只剩一位医生，个体偏离永远测不出来（实测踩到过）。
        for row in self._considered:
            reviewer_id = getattr(row, "reviewer_id", None)
            if not reviewer_id:
                continue
            stats = report.reviewer_stats.setdefault(
                str(reviewer_id), ReviewerStats(reviewer_id=str(reviewer_id))
            )
            stats.considered += 1

        for record in self._records:
            report.corrected += 1
            direction = classify_direction(record.original_value, record.corrected_value)
            report.direction_counts[direction.value] = (
                report.direction_counts.get(direction.value, 0) + 1
            )

            stats = report.reviewer_stats.setdefault(
                record.reviewer_id, ReviewerStats(reviewer_id=record.reviewer_id)
            )
            stats.corrected += 1
            if direction == GradeDirection.AI_HIGHER:
                stats.ai_higher += 1
            elif direction == GradeDirection.AI_LOWER:
                stats.ai_lower += 1

        # 修正记录里出现但不在 considered 名单中的医生（历史数据/口径不一致）：
        # 分母退化为其修正条数，至少不让这类记录整条丢失
        for record in self._records:
            stats = report.reviewer_stats.get(record.reviewer_id)
            if stats is not None and stats.considered == 0:
                stats.considered = stats.corrected

        if report.considered < settings.correction_rate_min_samples:
            report.insufficient_samples = True
            return report

        report.directional_bias_alert = self._directional_alert(report)
        report.reviewer_outlier_alerts = self._reviewer_outliers(report)

        if report.directional_bias_alert or report.reviewer_outlier_alerts:
            logger.warning(
                "correction_rate_alert",
                considered=report.considered,
                corrected=report.corrected,
                correction_rate=round(report.correction_rate, 4),
                directional_bias=report.directional_bias_alert,
                reviewer_outliers=[item["reviewer_id"] for item in report.reviewer_outlier_alerts],
            )
        return report

    @staticmethod
    def _directional_alert(report: CorrectionRateReport) -> Optional[Dict[str, Any]]:
        """单方向占比越线**且确实是占优方向**才告警。

        只看"越线"会自相矛盾：50/50 时两个方向都 >20%，难道 AI 既系统性偏高又
        系统性偏低？所以还要求该方向占比高于另一方向，才称得上"系统性"。
        """
        from app.config.settings import settings

        threshold = settings.correction_rate_direction_alert_threshold
        for direction in (GradeDirection.AI_HIGHER, GradeDirection.AI_LOWER):
            rate = report.direction_rate(direction)
            other = (
                GradeDirection.AI_LOWER
                if direction == GradeDirection.AI_HIGHER
                else GradeDirection.AI_HIGHER
            )
            if rate > threshold and rate > report.direction_rate(other):
                return {
                    "direction": direction.value,
                    "rate": round(rate, 4),
                    "threshold": threshold,
                    "message": (
                        "AI 判级系统性偏高，建议复核分级阈值"
                        if direction == GradeDirection.AI_HIGHER
                        else "AI 判级系统性偏低，建议复核分级阈值"
                    ),
                }
        return None

    @staticmethod
    def _reviewer_outliers(report: CorrectionRateReport) -> List[Dict[str, Any]]:
        from app.config.settings import settings

        high = settings.correction_rate_reviewer_alert_threshold
        peer_max = settings.correction_rate_peer_baseline_threshold
        min_samples = settings.correction_rate_min_samples

        alerts: List[Dict[str, Any]] = []
        for stats in report.reviewer_stats.values():
            if stats.considered < min_samples:
                continue
            if stats.correction_rate <= high:
                continue
            peers = [
                other
                for other in report.reviewer_stats.values()
                if other.reviewer_id != stats.reviewer_id and other.considered >= min_samples
            ]
            if not peers:
                # 只有一位医生有足够样本时无法构成"个体偏离群体"的比较
                continue
            peer_rate = sum(peer.correction_rate for peer in peers) / len(peers)
            if peer_rate >= peer_max:
                continue
            alerts.append(
                {
                    "reviewer_id": stats.reviewer_id,
                    "correction_rate": round(stats.correction_rate, 4),
                    "peer_rate": round(peer_rate, 4),
                    "threshold": high,
                    "peer_threshold": peer_max,
                }
            )
        return alerts


async def collect_correction_records(
    session, *, since: Optional[datetime] = None
) -> tuple[List[Any], List[Any]]:
    """一次查询取出修正反馈与纳入统计的反馈全集（不做 N+1）。

    Returns:
        ``(considered_rows, correction_records)``
    """
    from sqlalchemy import select

    from app.models.database import FeedbackRecord

    stmt = select(FeedbackRecord)
    if since is not None:
        stmt = stmt.where(FeedbackRecord.created_at >= since)

    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    records = [
        record
        for record in (CorrectionRecord.from_feedback_row(row) for row in rows)
        if record is not None
    ]
    logger.info(
        "correction_records_collected",
        considered=len(rows),
        corrections=len(records),
        since=since.isoformat() if since else None,
    )
    return rows, records


async def assess_recent_corrections(
    session, *, window_days: Optional[int] = None
) -> CorrectionRateReport:
    """按时间窗评估最近修正率（供看板与定时作业调用）。"""
    from app.config.settings import settings

    days = window_days or settings.correction_rate_window_days
    since = datetime.now(timezone.utc) - timedelta(days=days)
    considered, records = await collect_correction_records(session, since=since)
    return CorrectionRateMonitor(
        considered_records=considered, correction_records=records
    ).assess()
