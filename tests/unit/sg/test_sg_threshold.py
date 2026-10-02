import pytest

from safety_gateway.threshold import (
    OrganThreshold,
    ThresholdEngine,
    get_threshold_engine,
)
from safety_gateway.validation_result import ThresholdCheckResult

pytestmark = [pytest.mark.track_sg, pytest.mark.track_sec, pytest.mark.sec_domain_sg]


class TestThresholdEngine:

    def setup_method(self):
        self.engine = ThresholdEngine()

    def test_default_thresholds_loaded(self):
        self.engine._load_sync_fallback()
        assert self.engine._loaded is True
        assert "thyroid" in self.engine._thresholds
        assert "liver" in self.engine._thresholds
        assert "cardiac" in self.engine._thresholds

    def test_thyroid_threshold_value(self):
        self.engine._load_sync_fallback()
        assert self.engine._thresholds["thyroid"].confidence_threshold == 0.85

    def test_liver_threshold_value(self):
        self.engine._load_sync_fallback()
        assert self.engine._thresholds["liver"].confidence_threshold == 0.88

    def test_cardiac_threshold_value(self):
        self.engine._load_sync_fallback()
        assert self.engine._thresholds["cardiac"].confidence_threshold == 0.92

    def test_check_passed(self):
        self.engine._load_sync_fallback()
        result = self.engine._check_sync("thyroid", 0.90)
        assert result.passed is True
        assert result.confidence == 0.90
        assert result.threshold == 0.85

    def test_check_failed(self):
        self.engine._load_sync_fallback()
        result = self.engine._check_sync("thyroid", 0.80)
        assert result.passed is False
        assert result.confidence == 0.80

    def test_check_exact_threshold(self):
        self.engine._load_sync_fallback()
        result = self.engine._check_sync("thyroid", 0.85)
        assert result.passed is True

    def test_check_unknown_organ(self):
        self.engine._load_sync_fallback()
        result = self.engine._check_sync("unknown_organ", 0.90)
        assert result.threshold == 0.95

    def test_check_multiple(self):
        self.engine._load_sync_fallback()
        conclusions = [
            {"organ": "thyroid", "confidence": 0.90},
            {"organ": "liver", "confidence": 0.80},
        ]
        results = self.engine._check_multiple_sync(conclusions)
        assert len(results) == 2
        assert results[0].passed is True
        assert results[1].passed is False

    def test_db_thresholds_override(self):
        self.engine._load_sync_fallback(db_thresholds={"thyroid": 0.95})
        assert self.engine._thresholds["thyroid"].confidence_threshold == 0.95

    def test_update_threshold(self):
        self.engine._load_sync_fallback()
        self.engine._update_threshold_sync("thyroid", 0.95)
        assert self.engine._thresholds["thyroid"].confidence_threshold == 0.95

    def test_get_all_thresholds(self):
        self.engine._load_sync_fallback()
        thresholds = self.engine._thresholds
        assert "thyroid" in thresholds
        assert "liver" in thresholds
        assert "cardiac" in thresholds


class TestOrganThresholdModel:

    def test_threshold_creation(self):
        threshold = OrganThreshold(
            organ="thyroid",
            confidence_threshold=0.85,
            description="甲状腺阈值",
        )
        assert threshold.organ == "thyroid"
        assert threshold.confidence_threshold == 0.85


class TestThresholdCheckResultModel:

    def test_result_passed(self):
        result = ThresholdCheckResult(
            organ="thyroid",
            confidence=0.90,
            threshold=0.85,
            passed=True,
            message="置信度达标",
        )
        assert result.passed is True

    def test_result_failed(self):
        result = ThresholdCheckResult(
            organ="thyroid",
            confidence=0.80,
            threshold=0.85,
            passed=False,
            message="置信度0.8低于阈值0.85",
        )
        assert result.passed is False


class TestThresholdHotReload:

    def test_threshold_engine_subscribe(self):
        engine = get_threshold_engine()
        assert engine._subscriber is None

    def test_threshold_engine_update(self):
        engine = get_threshold_engine()
        engine._load_sync_fallback()
        initial_threshold = engine._thresholds["thyroid"].confidence_threshold

        engine._update_threshold_sync("thyroid", 0.95)
        updated_threshold = engine._thresholds["thyroid"].confidence_threshold

        assert updated_threshold == 0.95
        assert updated_threshold != initial_threshold

    def test_threshold_engine_stats(self):
        engine = get_threshold_engine()
        engine._load_sync_fallback()
        stats = engine.get_stats()
        assert "loaded" in stats
        assert "threshold_count" in stats
        assert "subscribe_running" in stats


class TestMultiOrganDiagnosis:

    @pytest.mark.asyncio
    async def test_multiple_organs_threshold_check(self):
        engine = get_threshold_engine()
        engine._load_sync_fallback()
        conclusions = [
            {"organ": "thyroid", "confidence": 0.90},
            {"organ": "liver", "confidence": 0.80},
            {"organ": "cardiac", "confidence": 0.95},
        ]
        results = engine._check_multiple_sync(conclusions)
        assert len(results) == 3
        assert results[0].passed is True
        assert results[1].passed is False
        assert results[2].passed is True


class TestThresholdEngineHealthCheck:

    def test_threshold_engine_degraded_when_not_loaded(self):
        from safety_gateway.health import HealthChecker

        checker = HealthChecker()
        result = checker._check_threshold_engine()
        assert result["status"] in ["healthy", "degraded"]
        assert "loaded" in result


class TestThresholdAsyncInterface:

    @pytest.mark.asyncio
    async def test_check_async(self):
        engine = get_threshold_engine()
        engine._load_sync_fallback()
        result = await engine.check("thyroid", 0.90)
        assert result.passed is True
        assert result.confidence == 0.90
        assert result.threshold == 0.85

    @pytest.mark.asyncio
    async def test_check_async_below_threshold(self):
        engine = get_threshold_engine()
        engine._load_sync_fallback()
        result = await engine.check("thyroid", 0.80)
        assert result.passed is False
        assert result.confidence == 0.80

    @pytest.mark.asyncio
    async def test_check_multiple_async(self):
        engine = get_threshold_engine()
        engine._load_sync_fallback()
        conclusions = [
            {"organ": "thyroid", "confidence": 0.90},
            {"organ": "liver", "confidence": 0.80},
        ]
        results = await engine.check_multiple(conclusions)
        assert len(results) == 2
        assert results[0].passed is True
        assert results[1].passed is False

    @pytest.mark.asyncio
    async def test_ensure_loaded_triggers_lazy_load(self):
        engine = ThresholdEngine()
        assert engine._loaded is False
        engine._load_sync_fallback()
        await engine.ensure_loaded()
        assert engine._loaded is True
