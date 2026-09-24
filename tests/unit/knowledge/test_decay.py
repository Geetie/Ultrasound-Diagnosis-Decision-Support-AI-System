"""知识库条目衰减（二期 §6 时间衰减 + 一期漏做的反馈衰减）。

重点验证三件事：
1. **幂等**：同样的客观事实（空闲天数、质疑次数）跑几次结果都一样——衰减值不能
   因为"跑了几次"而变，否则无法复现也无法审计；
2. **缺失数据不当作证据**：没有引用时间不等于"长期未引用"，不得据此压置信度；
3. 落库走**单条批量语句**，且只写真正变化的行（周期性作业不能把全表刷一遍）。
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.knowledge import decay
from app.knowledge.decay import (
    CONFIDENCE_FLOOR,
    FEEDBACK_DECAY_FACTOR,
    MAX_DECAY_PERIODS,
    assess_decay,
    compute_confidence_score,
    compute_idle_periods,
    should_update,
)

NOW = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)


def _days_ago(days: int) -> datetime:
    return NOW - timedelta(days=days)


class TestIdlePeriods:
    def test_none_is_zero_periods(self):
        assert compute_idle_periods(None, NOW) == 0

    def test_just_under_one_period(self):
        assert compute_idle_periods(_days_ago(89), NOW) == 0

    def test_exactly_one_period(self):
        assert compute_idle_periods(_days_ago(90), NOW) == 1

    def test_two_periods(self):
        assert compute_idle_periods(_days_ago(180), NOW) == 2

    def test_capped_at_max_periods(self):
        assert compute_idle_periods(_days_ago(90 * 50), NOW) == MAX_DECAY_PERIODS

    def test_naive_timestamp_does_not_crash(self):
        naive = (NOW - timedelta(days=100)).replace(tzinfo=None)

        assert compute_idle_periods(naive, NOW) == 1

    def test_future_timestamp_is_zero(self):
        """时钟回拨/上游写未来时间时不得算出负期数。"""
        assert compute_idle_periods(NOW + timedelta(days=10), NOW) == 0


class TestTimeDecay:
    def test_fresh_entry_keeps_full_confidence(self):
        assert compute_confidence_score(last_referenced_at=_days_ago(1), now=NOW) == 1.0

    def test_ninety_days_applies_single_decay(self):
        assert compute_confidence_score(last_referenced_at=_days_ago(90), now=NOW) == 0.9

    def test_180_days_applies_twice(self):
        assert compute_confidence_score(last_referenced_at=_days_ago(180), now=NOW) == 0.81

    def test_no_reference_timestamp_keeps_full_confidence(self):
        """缺失引用时间不得被当作"长期未引用"的证据。"""
        assert compute_confidence_score(last_referenced_at=None, now=NOW) == 1.0


class TestFeedbackDecay:
    def test_single_dispute_does_not_decay(self):
        assert compute_confidence_score(dispute_count=1, last_referenced_at=NOW, now=NOW) == 1.0

    def test_two_disputes_apply_feedback_factor(self):
        score = compute_confidence_score(
            dispute_count=2, last_referenced_at=NOW, now=NOW
        )

        assert score == pytest.approx(FEEDBACK_DECAY_FACTOR)

    def test_time_and_feedback_multiply(self):
        score = compute_confidence_score(
            dispute_count=2, last_referenced_at=_days_ago(90), now=NOW
        )

        assert score == pytest.approx(0.63)

    def test_floor_applies_at_extreme(self):
        """下限必须真的会被触发，否则就是死代码。"""
        score = compute_confidence_score(
            dispute_count=5, last_referenced_at=_days_ago(90 * 30), now=NOW
        )

        assert score == pytest.approx(CONFIDENCE_FLOOR)
        assert score < 0.3 + 1e-9


class TestIdempotency:
    def test_repeated_assessment_is_stable(self):
        scores = {
            compute_confidence_score(
                last_referenced_at=_days_ago(200), dispute_count=3, now=NOW
            )
            for _ in range(10)
        }

        assert len(scores) == 1, "衰减值随调用次数变化，说明在当前值上反复相乘"

    def test_score_depends_only_on_facts(self):
        """同样的空闲天数与质疑次数，无论"历史跑过几次"都得出同一结果。"""
        first = compute_confidence_score(
            last_referenced_at=_days_ago(95), dispute_count=2, now=NOW
        )
        again = compute_confidence_score(
            last_referenced_at=_days_ago(95), dispute_count=2, now=NOW
        )

        assert first == again


class TestAssessment:
    def test_low_frequency_flag_after_90_days(self):
        assessment = assess_decay(last_referenced_at=_days_ago(120), now=NOW)

        assert assessment.is_low_frequency is True
        assert assessment.needs_review is True
        assert "idle_over_90d" in assessment.reasons
        assert assessment.idle_days == 120
        assert assessment.periods == 1

    def test_disputed_entry_needs_review_even_if_fresh(self):
        assessment = assess_decay(
            last_referenced_at=NOW, dispute_count=2, now=NOW
        )

        assert assessment.is_low_frequency is False
        assert assessment.needs_review is True
        assert "disputed_twice" in assessment.reasons

    def test_healthy_entry_needs_no_review(self):
        assessment = assess_decay(last_referenced_at=_days_ago(3), now=NOW)

        assert assessment.needs_review is False
        assert assessment.reasons == ()
        assert assessment.applied_factors == ()

    def test_applied_factors_describe_cause(self):
        assessment = assess_decay(
            last_referenced_at=_days_ago(200), dispute_count=2, now=NOW
        )

        assert "idle_2x0.9" in assessment.applied_factors
        assert "disputed_2x0.7" in assessment.applied_factors


class TestShouldUpdate:
    def test_unchanged_score_skips_write(self):
        assert should_update(0.81, 0.81) is False

    def test_changed_score_triggers_write(self):
        assert should_update(1.0, 0.9) is True

    def test_missing_current_score_triggers_write(self):
        assert should_update(None, 0.9) is True


class _FakeResult:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class _CapturingSession:
    def __init__(self, rowcount=1):
        self.statements: list = []
        self._rowcount = rowcount

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), params))
        return _FakeResult(self._rowcount)

    async def commit(self):
        return None


class TestBatchSql:
    async def test_record_references_uses_single_statement(self):
        session = _CapturingSession(rowcount=3)

        affected = await decay.record_references(session, ["a", "b", "c"], now=NOW)

        assert affected == 3
        assert len(session.statements) == 1, "逐条 UPDATE 会产生 N 次往返"
        sql, params = session.statements[0]
        assert "knowledge_chunks" in sql
        assert "reference_count = reference_count + 1" in sql
        assert params["chunk_ids"] == ["a", "b", "c"]
        assert params["now"] == NOW

    async def test_record_references_skips_empty_input(self):
        session = _CapturingSession()

        assert await decay.record_references(session, [], now=NOW) == 0
        assert session.statements == []

    async def test_record_references_filters_blank_ids(self):
        session = _CapturingSession()

        await decay.record_references(session, ["a", "", "b"], now=NOW)

        assert session.statements[0][1]["chunk_ids"] == ["a", "b"]

    async def test_apply_decay_is_single_statement(self):
        session = _CapturingSession(rowcount=7)

        updated = await decay.apply_decay(session, now=NOW)

        assert updated == 7
        assert len(session.statements) == 1

    async def test_apply_decay_only_writes_changed_rows(self):
        """缺少 IS DISTINCT FROM 会把全表刷一遍。"""
        session = _CapturingSession()

        await decay.apply_decay(session, now=NOW)

        sql = session.statements[0][0]
        assert "IS DISTINCT FROM" in sql
        assert sql.count("WHERE") == 1

    async def test_apply_decay_params_carry_policy_constants(self):
        session = _CapturingSession()

        await decay.apply_decay(session, now=NOW)

        params = session.statements[0][1]
        assert params["floor"] == CONFIDENCE_FLOOR
        assert params["max_periods"] == MAX_DECAY_PERIODS
        assert params["idle_days"] == decay.IDLE_DECAY_DAYS
        assert params["min_disputes"] == decay.FEEDBACK_DECAY_MIN_DISPUTES
        assert params["now"] == NOW

    async def test_naive_now_is_normalized(self):
        session = _CapturingSession()

        await decay.apply_decay(session, now=NOW.replace(tzinfo=None))

        assert session.statements[0][1]["now"].tzinfo is not None


class TestReferenceRecordingFailureIsolation:
    async def test_failure_is_swallowed(self, monkeypatch):
        """引用记录失败绝不能让检索失败。"""
        async def _boom(*args, **kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr(decay, "record_references", _boom)

        assert await decay.record_retrieval_references(["a"]) == 0

    async def test_empty_ids_short_circuit(self):
        assert await decay.record_retrieval_references([]) == 0

    async def test_success_path_commits(self, monkeypatch):
        session = _CapturingSession(rowcount=2)
        session.commit = AsyncMock()

        class _Ctx:
            async def __aenter__(self):
                return session

            async def __aexit__(self, *exc_info):
                return False

        import app.config.database as database_module

        monkeypatch.setattr(database_module, "AsyncSessionLocal", lambda: _Ctx())

        affected = await decay.record_retrieval_references(["a", "b"])

        assert affected == 2
        session.commit.assert_awaited()


class TestMigrationChain:
    def test_revision_follows_0011(self):
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[3] / "alembic" / "versions" / "0012_knowledge_decay.py"
        spec = importlib.util.spec_from_file_location("migration_0012", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        assert module.revision == "0012"
        assert module.down_revision == "0011"

    def test_migration_covers_every_decay_column(self):
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[3] / "alembic" / "versions" / "0012_knowledge_decay.py"
        spec = importlib.util.spec_from_file_location("migration_0012_cols", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        calls: list = []
        module.op = MagicMock()
        module.op.execute = lambda sql, *a, **k: calls.append(sql)

        module.upgrade()

        joined = " ".join(calls)
        for column in (
            "last_referenced_at",
            "reference_count",
            "dispute_count",
            "confidence_score",
        ):
            assert column in joined, f"迁移缺少 {column}"

    def test_orm_model_matches_migration(self):
        """ORM 与迁移必须同口径：只改一处会导致"代码能跑、迁移后查不到列"。"""
        from app.models.database import KnowledgeChunkModel

        columns = {column.name for column in KnowledgeChunkModel.__table__.columns}

        assert {
            "last_referenced_at",
            "reference_count",
            "dispute_count",
            "confidence_score",
        } <= columns

    def test_orm_model_keeps_confidence_check_constraint(self):
        from app.models.database import KnowledgeChunkModel

        constraints = {
            constraint.name for constraint in KnowledgeChunkModel.__table__.constraints
        }

        assert "ck_kc_confidence_score_range" in constraints

    def test_referential_integrity_of_decay_sql(self):
        """SQL 里的列名必须与迁移一致，否则作业上线即报错。"""
        sql = decay._APPLY_DECAY_SQL + decay._RECORD_REFERENCES_SQL

        assert "last_referenced_at" in sql
        assert "dispute_count" in sql
        assert "confidence_score" in sql
        assert "reference_count" in sql
