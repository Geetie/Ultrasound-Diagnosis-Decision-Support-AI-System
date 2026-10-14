
import logging

import pytest

from app.speech.funasr import FunASROnnxEngine, find_terminology
from app.speech.protocols import (
    SpeechError,
    SpeechErrorCode,
    SpeechRequest,
    SpeechResponse,
)

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.track_edg, pytest.mark.track_llm]


class TestSpeechRequest:
    def test_valid_request(self):
        req = SpeechRequest(audio_data=b"\x00\x01\x02", sample_rate=16000)
        assert req.sample_rate == 16000
        assert req.language == "zh"

    def test_invalid_sample_rate(self):
        with pytest.raises(ValueError, match="sample_rate must be positive"):
            SpeechRequest(audio_data=b"\x00", sample_rate=0)

    def test_invalid_language(self):
        with pytest.raises(ValueError, match="language must be one of"):
            SpeechRequest(audio_data=b"\x00", language="fr")

    def test_default_values(self):
        req = SpeechRequest(audio_data=b"\x00")
        assert req.language == "zh"
        assert req.sample_rate == 16000
        assert req.extra == {}


class TestSpeechError:
    def test_error_creation(self):
        err = SpeechError(
            error_code=SpeechErrorCode.E3001_TIMEOUT,
            message="timeout",
            retryable=True,
        )
        assert err.error_code == SpeechErrorCode.E3001_TIMEOUT
        assert err.retryable is True
        assert "E3001_TIMEOUT" in str(err)

    def test_error_codes(self):
        assert SpeechErrorCode.E3001_TIMEOUT.value == "E3001_TIMEOUT"
        assert SpeechErrorCode.E3001_CONNECTION_FAILED.value == "E3001_CONNECTION_FAILED"
        assert SpeechErrorCode.E3001_RECOGNITION_FAILED.value == "E3001_RECOGNITION_FAILED"
        assert SpeechErrorCode.E3001_MODEL_LOAD_FAILED.value == "E3001_MODEL_LOAD_FAILED"


class TestSpeechResponse:
    def test_default_values(self):
        resp = SpeechResponse(text="甲状腺结节")
        assert resp.language == "zh"
        assert resp.confidence == 0.0
        assert resp.latency_ms == 0.0
        assert resp.segments == []
        assert resp.terminology_matches == []


class TestFindTerminology:
    def test_find_thyroid_terms(self):
        text = "甲状腺结节TI-RADS 4a类，可见微钙化"
        matches = find_terminology(text)
        terms = [m["term"] for m in matches]
        assert "甲状腺" in terms
        assert "结节" in terms
        assert "微钙化" in terms

    def test_find_liver_terms(self):
        text = "肝脏占位LI-RADS 4类，门静脉显示清晰"
        matches = find_terminology(text)
        terms = [m["term"] for m in matches]
        assert "肝脏" in terms
        assert "占位" in terms
        assert "门静脉" in terms

    def test_find_cardiac_terms(self):
        text = "左心室LVEF 55%，二尖瓣反流"
        matches = find_terminology(text)
        terms = [m["term"] for m in matches]
        assert "左心室" in terms
        assert "LVEF" in terms
        assert "二尖瓣" in terms

    def test_no_duplicates(self):
        text = "甲状腺甲状腺结节"
        matches = find_terminology(text)
        terms = [m["term"] for m in matches]
        assert terms.count("甲状腺") == 1

    def test_empty_text(self):
        matches = find_terminology("")
        assert matches == []

    def test_no_matches(self):
        matches = find_terminology("今天天气不错")
        assert matches == []

    def test_general_terms(self):
        text = "超声探头增益调整"
        matches = find_terminology(text)
        terms = [m["term"] for m in matches]
        assert "超声" in terms
        assert "探头" in terms
        assert "增益" in terms

    def test_category_assignment(self):
        text = "甲状腺结节"
        matches = find_terminology(text)
        thyroid_match = next(m for m in matches if m["term"] == "甲状腺")
        assert thyroid_match["category"] == "thyroid"

    def test_position_tracking(self):
        text = "甲状腺结节"
        matches = find_terminology(text)
        thyroid_match = next(m for m in matches if m["term"] == "甲状腺")
        assert thyroid_match["start"] == 0
        assert thyroid_match["end"] == 3


class TestFunASROnnxEngine:
    def test_initial_state(self):
        engine = FunASROnnxEngine()
        assert engine._initialized is False
        assert engine._model is None

    def test_custom_config(self):
        engine = FunASROnnxEngine(
            model_dir="/custom/path",
            device="cuda",
            num_threads=8,
        )
        assert engine._model_dir == "/custom/path"
        assert engine._device == "cuda"
        assert engine._num_threads == 8

    @pytest.mark.asyncio
    async def test_health_check_before_init(self):
        engine = FunASROnnxEngine()
        assert await engine.health_check() is False

    @pytest.mark.asyncio
    async def test_engine_info(self):
        engine = FunASROnnxEngine()
        info = await engine.get_engine_info()
        assert info.engine_name == "funasr_onnx"
        assert info.model_name == "paraformer-zh"
        assert info.sample_rate == 16000

    @pytest.mark.asyncio
    async def test_close_without_init(self):
        engine = FunASROnnxEngine()
        await engine.close()
        assert engine._initialized is False

    @pytest.mark.asyncio
    async def test_close_after_init_attempt(self):
        engine = FunASROnnxEngine(model_dir="/nonexistent")
        try:
            await engine.initialize()
        except SpeechError:
            logger.warning("TestFunASROnnxEngine.test_close_after_init_attempt: 未预期异常，按原逻辑降级", exc_info=True)
            pass
        await engine.close()
        assert engine._initialized is False
        assert engine._model is None
