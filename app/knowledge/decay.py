"""知识库条目衰减（二期 §6 + 一期漏做的反馈衰减）。

两条衰减线
----------
1. **时间衰减**：条目连续 90 天未被引用 -> 置信度 ×0.9，并标记「低频引用，建议复审」；
2. **反馈衰减**：条目被医生质疑（修正反馈）≥2 次 -> 置信度 ×0.7。

为什么用"重新计算"而不是"反复乘"
--------------------------------
如果每次跑衰减都在当前值上乘 0.9，那么同一条目跑 5 次就会变成 0.59——同样的数据
因为"跑了几次"得到不同结果，无法复现也无法审计。这里改为**幂等**：每次都由常量基线
与客观事实（空闲天数、质疑次数）算出目标值，跑多少次结果都一样。

为什么置信度要落库而不是查询时算
--------------------------------
检索侧要按置信度加权，如果每次检索都实时计算，等于给每个候选项加一次表达式求值；
落库后检索只是一次读取。代价是需要一个周期性作业（见 ``apply_decay``）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

import structlog

logger = structlog.get_logger()

# 时间衰减：连续 N 天未被引用触发一次
IDLE_DECAY_DAYS = 90
IDLE_DECAY_FACTOR = 0.9
# 反馈衰减：被质疑次数达到阈值
FEEDBACK_DECAY_MIN_DISPUTES = 2
FEEDBACK_DECAY_FACTOR = 0.7

# 下限与期数上限：知识条目是临床决策依据，无界衰减会让一条老条目彻底失去话语权，
# 反而把结论推向"没有依据"；这里保底 0.3 并最多累计 10 期（约 2.5 年）。
# 两个数字必须配套：10 期 × 质疑 = 0.244，低于下限才会真正触发保底；若期数上限
# 取太小（如 5 期），最坏值 0.41 永远碰不到下限，保底就成了死代码。
CONFIDENCE_FLOOR = 0.3
MAX_DECAY_PERIODS = 10

# 基线置信度：条目入库即 1.0。若将来引入人工标注置信度，应由 base_confidence 承载，
# 而不是让衰减函数去猜。
BASE_CONFIDENCE = 1.0


@dataclass(frozen=True)
class DecayAssessment:
    """一次衰减评估的结果。"""

    confidence_score: float
    idle_days: int
    periods: int
    dispute_count: int
    is_low_frequency: bool
    reasons: tuple

    @property
    def needs_review(self) -> bool:
        """是否应提示「低频引用，建议复审」。"""
        return self.is_low_frequency or self.dispute_count >= FEEDBACK_DECAY_MIN_DISPUTES

    @property
    def applied_factors(self) -> tuple:
        factors = []
        if self.periods:
            factors.append(f"idle_{self.periods}x{IDLE_DECAY_FACTOR}")
        if self.dispute_count >= FEEDBACK_DECAY_MIN_DISPUTES:
            factors.append(f"disputed_{self.dispute_count}x{FEEDBACK_DECAY_FACTOR}")
        return tuple(factors)


def _as_aware(moment: datetime) -> datetime:
    """naive 时间戳补 UTC。上游可能写 naive，不补会在减法处直接 TypeError。"""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def compute_idle_periods(
    last_referenced_at: Optional[datetime], now: Optional[datetime] = None
) -> int:
    """返回空闲期数（每 ``IDLE_DECAY_DAYS`` 天为一期，上限见 ``MAX_DECAY_PERIODS``）。

    ``last_referenced_at`` 为 None 时记 0 期：没有引用时间不等于"长期未引用"，
    不能拿缺失数据当证据去压置信度。
    """
    if last_referenced_at is None:
        return 0
    current = _as_aware(now or datetime.now(timezone.utc))
    idle_days = (current - _as_aware(last_referenced_at)).days
    if idle_days < IDLE_DECAY_DAYS:
        return 0
    return min(idle_days // IDLE_DECAY_DAYS, MAX_DECAY_PERIODS)


def compute_confidence_score(
    *,
    last_referenced_at: Optional[datetime] = None,
    dispute_count: int = 0,
    now: Optional[datetime] = None,
) -> float:
    """由客观事实算出目标置信度（幂等：与当前值无关）。"""
    periods = compute_idle_periods(last_referenced_at, now)
    score = BASE_CONFIDENCE * (IDLE_DECAY_FACTOR**periods)
    if dispute_count >= FEEDBACK_DECAY_MIN_DISPUTES:
        score *= FEEDBACK_DECAY_FACTOR
    return round(max(score, CONFIDENCE_FLOOR), 4)


def assess_decay(
    *,
    last_referenced_at: Optional[datetime] = None,
    dispute_count: int = 0,
    now: Optional[datetime] = None,
) -> DecayAssessment:
    """给出完整评估（含复审提示与成因）。"""
    current = now or datetime.now(timezone.utc)
    periods = compute_idle_periods(last_referenced_at, current)
    idle_days = (
        (current - _as_aware(last_referenced_at)).days if last_referenced_at else 0
    )
    reasons = []
    if periods:
        reasons.append("idle_over_90d")
    if dispute_count >= FEEDBACK_DECAY_MIN_DISPUTES:
        reasons.append("disputed_twice")

    return DecayAssessment(
        confidence_score=compute_confidence_score(
            last_referenced_at=last_referenced_at,
            dispute_count=dispute_count,
            now=current,
        ),
        idle_days=idle_days,
        periods=periods,
        dispute_count=dispute_count,
        is_low_frequency=periods > 0,
        reasons=tuple(reasons),
    )


def should_update(current_score: Optional[float], target_score: float) -> bool:
    """只在真的变化时写库，避免周期性作业把整张表无谓刷一遍。"""
    if current_score is None:
        return True
    return abs(float(current_score) - target_score) > 1e-6


# ----------------------------------------------------------------------
# 落库：引用记录与周期性衰减（各一条批量语句，避免逐条 I/O）
# ----------------------------------------------------------------------

_RECORD_REFERENCES_SQL = """
UPDATE knowledge_chunks
SET reference_count = reference_count + 1,
    last_referenced_at = :now
WHERE chunk_id = ANY(:chunk_ids)
"""

# 单条语句完成全表重算：SET 与 WHERE 用同一表达式，只有目标值变化的行才会被写。
_APPLY_DECAY_SQL = """
UPDATE knowledge_chunks
SET confidence_score = GREATEST(
        :floor,
        :base * POWER(:idle_factor, LEAST(
            FLOOR(GREATEST(EXTRACT(EPOCH FROM (:now - last_referenced_at)), 0) / 86400 / :idle_days),
            :max_periods
        )) * CASE WHEN dispute_count >= :min_disputes THEN :feedback_factor ELSE 1.0 END
    )
WHERE confidence_score IS DISTINCT FROM GREATEST(
        :floor,
        :base * POWER(:idle_factor, LEAST(
            FLOOR(GREATEST(EXTRACT(EPOCH FROM (:now - last_referenced_at)), 0) / 86400 / :idle_days),
            :max_periods
        )) * CASE WHEN dispute_count >= :min_disputes THEN :feedback_factor ELSE 1.0 END
    )
"""


def decay_sql_params(now: Optional[datetime] = None) -> dict:
    return {
        "now": _as_aware(now or datetime.now(timezone.utc)),
        "floor": CONFIDENCE_FLOOR,
        "base": BASE_CONFIDENCE,
        "idle_factor": IDLE_DECAY_FACTOR,
        "idle_days": IDLE_DECAY_DAYS,
        "max_periods": MAX_DECAY_PERIODS,
        "min_disputes": FEEDBACK_DECAY_MIN_DISPUTES,
        "feedback_factor": FEEDBACK_DECAY_FACTOR,
    }


async def record_references(
    session, chunk_ids: Sequence[str], now: Optional[datetime] = None
) -> int:
    """批量记录一次检索引用。返回受影响行数。"""
    ids = [chunk_id for chunk_id in chunk_ids if chunk_id]
    if not ids:
        return 0
    from sqlalchemy import text

    result = await session.execute(
        text(_RECORD_REFERENCES_SQL),
        {"chunk_ids": ids, "now": _as_aware(now or datetime.now(timezone.utc))},
    )
    affected = getattr(result, "rowcount", None)
    return int(affected) if affected is not None else len(ids)


async def apply_decay(session, now: Optional[datetime] = None) -> int:
    """全表重算衰减目标值（幂等），返回被更新的行数。"""
    from sqlalchemy import text

    result = await session.execute(text(_APPLY_DECAY_SQL), decay_sql_params(now))
    affected = getattr(result, "rowcount", None)
    updated = int(affected) if affected is not None else 0
    logger.info("knowledge_decay_applied", updated=updated)
    return updated


async def record_retrieval_references(chunk_ids: Sequence[str]) -> int:
    """检索侧的 best-effort 引用记录：失败只记日志，绝不影响检索。

    单独开会话：检索可能在长事务或只读事务里执行，挂靠上去会污染调用方事务。
    """
    if not chunk_ids:
        return 0
    try:
        from app.config.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            affected = await record_references(session, chunk_ids)
            commit = getattr(session, "commit", None)
            if commit is not None:
                await commit()
            return affected
    except Exception as exc:  # noqa: BLE001 - 埋点失败不得影响检索
        logger.warning("knowledge_reference_record_failed", error=str(exc))
        return 0


def idle_deadline(now: Optional[datetime] = None) -> datetime:
    """空闲判定线（便于导出给看板/告警用）。"""
    return _as_aware(now or datetime.now(timezone.utc)) - timedelta(days=IDLE_DECAY_DAYS)
