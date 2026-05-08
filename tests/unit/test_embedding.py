import time

import httpx
import numpy as np
import pytest
from freezegun import freeze_time
from pydantic import ValidationError

from app.embedding.base import BaseHttpEmbeddingService, get_mime_type
from app.embedding.bge_m3 import BGE_M3_DIM, BgeM3MedicalService
from app.embedding.protocols import (
    EmbeddingError,
    EmbeddingErrorCode,
    EmbeddingModality,
    EmbeddingModelInfo,
    EmbeddingModelType,
    EmbeddingResponse,
    EmbeddingService,
    ImageEmbeddingRequest,
    MultimodalEmbeddingRequest,
    SparseEmbeddingResponse,
    TextEmbeddingRequest,
)
from app.embedding.ultrasound_clip import ULTRASOUND_CLIP_DIM, UltrasoundClipService


def _make_mock_handler(response: httpx.Response | None = None, exc: Exception | None = None):
    async def handler(request: httpx.Request) -> httpx.Response:
        if exc is not None:
            raise exc
        return response
    return handler


def _make_768_embedding() -> list[float]:
    return np.random.randn(ULTRASOUND_CLIP_DIM).astype(np.float32).tolist()


def _make_1024_embedding() -> list[float]:
    return np.random.randn(BGE_M3_DIM).astype(np.float32).tolist()


@pytest.fixture
async def clip_svc():
    svc = UltrasoundClipService()
    yield svc
    await svc.close()


@pytest.fixture
async def bge_svc():
    svc = BgeM3MedicalService()
    yield svc
    await svc.close()


def _inject_client(svc: BaseHttpEmbeddingService, handler) -> None:
    svc._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=svc._base_url,
    )


class TestGetMimeType:
    def test_dicom(self):
        assert get_mime_type("dicom") == "application/dicom"

    def test_jpeg(self):
        assert get_mime_type("jpeg") == "image/jpeg"

    def test_jpg(self):
        assert get_mime_type("jpg") == "image/jpeg"

    def test_png(self):
        assert get_mime_type("png") == "image/png"

    def test_bmp(self):
        assert get_mime_type("bmp") == "image/bmp"

    def test_tiff(self):
        assert get_mime_type("tiff") == "image/tiff"

    def test_unknown_fallback(self):
        assert get_mime_type("custom") == "application/custom"

    def test_case_insensitive(self):
        assert get_mime_type("JPEG") == "image/jpeg"
        assert get_mime_type("Png") == "image/png"


class TestTextEmbeddingRequest:
    def test_valid(self):
        req = TextEmbeddingRequest(texts=["hello"])
        assert req.texts == ["hello"]
        assert req.request_id is None
        assert req.extra == {}

    def test_custom_values(self):
        req = TextEmbeddingRequest(
            texts=["低回声结节", "边界模糊"],
            request_id="req_emb_001",
            session_id="sess_001",
            extra={"organ": "thyroid"},
        )
        assert len(req.texts) == 2
        assert req.request_id == "req_emb_001"

    def test_empty_texts_rejected(self):
        with pytest.raises(ValidationError, match="texts must not be empty"):
            TextEmbeddingRequest(texts=[])


class TestImageEmbeddingRequest:
    def test_valid(self):
        req = ImageEmbeddingRequest(image_data=b"\x00\x01\x02")
        assert req.image_format == "dicom"
        assert req.request_id is None

    def test_custom_format(self):
        req = ImageEmbeddingRequest(image_data=b"\xff\xd8\xff", image_format="jpeg")
        assert req.image_format == "jpeg"

    def test_empty_image_data_rejected(self):
        with pytest.raises(ValidationError, match="image_data must not be empty"):
            ImageEmbeddingRequest(image_data=b"")


class TestMultimodalEmbeddingRequest:
    def test_valid(self):
        req = MultimodalEmbeddingRequest(texts=["甲状腺结节"], images=[b"\x00\x01"])
        assert req.image_formats == ["dicom"]

    def test_multiple_images(self):
        req = MultimodalEmbeddingRequest(
            texts=["a", "b"],
            images=[b"\x00", b"\x01"],
            image_formats=["dicom", "jpeg"],
        )
        assert len(req.images) == 2


class TestEmbeddingResponse:
    def test_with_embeddings(self):
        vec = np.random.randn(768).astype(np.float32)
        resp = EmbeddingResponse(
            embeddings=[vec],
            model="ultrasound-clip",
            dimension=768,
            modality=EmbeddingModality.TEXT,
            latency_ms=12.5,
            token_count=10,
        )
        assert len(resp.embeddings) == 1
        assert resp.embeddings[0].shape == (768,)
        assert resp.dimension == 768


class TestSparseEmbeddingResponse:
    def test_with_sparse_weights(self):
        dense = [np.random.randn(1024).astype(np.float32)]
        sparse = [{100: 0.5, 200: 0.3}]
        resp = SparseEmbeddingResponse(
            dense_embeddings=dense,
            sparse_weights=sparse,
            model="bge-m3-medical",
            dimension=1024,
        )
        assert resp.sparse_weights[0][100] == pytest.approx(0.5)


class TestEmbeddingError:
    def test_error_code_in_str(self):
        err = EmbeddingError(error_code=EmbeddingErrorCode.TIMEOUT, message="timed out")
        assert "E2002_EMB_TIMEOUT" in str(err)
        assert err.retryable is False

    def test_retryable_error(self):
        err = EmbeddingError(
            error_code=EmbeddingErrorCode.CONNECTION_FAILED,
            message="Connection failed",
            retryable=True,
            retry_strategy="retry_after_5s",
        )
        assert err.retryable is True

    def test_message_attribute_exists(self):
        err = EmbeddingError(error_code=EmbeddingErrorCode.TIMEOUT, message="test")
        assert hasattr(err, "message")
        assert err.message == "test"


class TestEmbeddingErrorCode:
    def test_all_error_codes_defined(self):
        expected = [
            "E2002_EMB_TIMEOUT",
            "E2002_EMB_CONNECTION_FAILED",
            "E2002_EMB_MODEL_LOAD_FAILED",
            "E2002_EMB_ENCODING_FAILED",
            "E2002_EMB_INVALID_INPUT",
            "E2002_EMB_IMAGE_NOT_SUPPORTED",
            "E2002_EMB_SPARSE_NOT_SUPPORTED",
            "E2002_EMB_HEALTH_CHECK_FAILED",
            "E2002_EMB_DIMENSION_MISMATCH",
        ]
        actual = [code.value for code in EmbeddingErrorCode]
        for code in expected:
            assert code in actual, f"Missing: {code}"


class TestEmbeddingModelInfo:
    def test_default_values(self):
        info = EmbeddingModelInfo()
        assert info.model_name == ""
        assert info.model_type == EmbeddingModelType.ULTRASOUND_CLIP
        assert info.dimension == 768

    def test_clip_info(self):
        info = EmbeddingModelInfo(model_type=EmbeddingModelType.ULTRASOUND_CLIP, supports_image=True)
        assert info.supports_image is True

    def test_bge_m3_info(self):
        info = EmbeddingModelInfo(model_type=EmbeddingModelType.BGE_M3_MEDICAL, dimension=1024)
        assert info.supports_image is False


class TestEmbeddingServiceProtocol:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            EmbeddingService()

    def test_clip_implements_protocol(self):
        assert isinstance(UltrasoundClipService(), EmbeddingService)

    def test_bge_m3_implements_protocol(self):
        assert isinstance(BgeM3MedicalService(), EmbeddingService)

    def test_subclasses_must_implement_abstract_methods(self):
        with pytest.raises(TypeError, match="abstract method"):
            class BrokenService(BaseHttpEmbeddingService):
                pass
            BrokenService(base_url="http://test", logger_name="broken")


class TestUltrasoundClipService:
    def test_init_default_values(self):
        svc = UltrasoundClipService()
        assert svc._base_url == "http://localhost:8003"
        assert svc._dimension == ULTRASOUND_CLIP_DIM
        assert svc._closed is False

    def test_init_custom_values(self):
        svc = UltrasoundClipService(base_url="http://gpu-server:8003/", timeout_seconds=60.0)
        assert svc._base_url == "http://gpu-server:8003"
        assert svc._timeout_seconds == 60.0

    @pytest.mark.asyncio
    async def test_embed_text_success(self, clip_svc):
        mock_resp = httpx.Response(
            200,
            json={"embeddings": [_make_768_embedding()], "model": "ultrasound-clip-v1", "token_count": 8},
            request=httpx.Request("POST", "http://localhost:8003/v1/embeddings"),
        )
        _inject_client(clip_svc, _make_mock_handler(mock_resp))

        response = await clip_svc.embed_text(
            TextEmbeddingRequest(texts=["甲状腺右叶低回声结节"], request_id="req_001", session_id="sess_001")
        )
        assert len(response.embeddings) == 1
        assert response.embeddings[0].shape == (768,)
        assert response.embeddings[0].dtype == np.float32
        assert response.model == "ultrasound-clip-v1"
        assert response.dimension == 768
        assert response.modality == EmbeddingModality.TEXT
        assert response.request_id == "req_001"
        assert response.latency_ms > 0
        assert response.token_count == 8

    @pytest.mark.asyncio
    async def test_embed_text_multiple_texts(self, clip_svc):
        fake = [_make_768_embedding() for _ in range(3)]
        mock_resp = httpx.Response(
            200,
            json={"embeddings": fake, "model": "ultrasound-clip-v1", "token_count": 24},
            request=httpx.Request("POST", "http://localhost:8003/v1/embeddings"),
        )
        _inject_client(clip_svc, _make_mock_handler(mock_resp))

        response = await clip_svc.embed_text(TextEmbeddingRequest(texts=["低回声", "边界模糊", "微钙化"]))
        assert len(response.embeddings) == 3

    @pytest.mark.asyncio
    async def test_embed_text_server_error(self, clip_svc):
        mock_resp = httpx.Response(500, text="Internal Server Error", request=httpx.Request("POST", "http://localhost:8003/v1/embeddings"))
        _inject_client(clip_svc, _make_mock_handler(mock_resp))

        with pytest.raises(EmbeddingError) as exc_info:
            await clip_svc.embed_text(TextEmbeddingRequest(texts=["test"]))
        assert exc_info.value.error_code == EmbeddingErrorCode.ENCODING_FAILED
        assert exc_info.value.retryable is True

    @pytest.mark.asyncio
    async def test_embed_text_client_error_not_retryable(self, clip_svc):
        mock_resp = httpx.Response(400, text="Bad Request", request=httpx.Request("POST", "http://localhost:8003/v1/embeddings"))
        _inject_client(clip_svc, _make_mock_handler(mock_resp))

        with pytest.raises(EmbeddingError) as exc_info:
            await clip_svc.embed_text(TextEmbeddingRequest(texts=["test"]))
        assert exc_info.value.retryable is False

    @pytest.mark.asyncio
    async def test_embed_text_timeout(self, clip_svc):
        _inject_client(clip_svc, _make_mock_handler(exc=httpx.TimeoutException("timeout")))

        with pytest.raises(EmbeddingError) as exc_info:
            await clip_svc.embed_text(TextEmbeddingRequest(texts=["test"]))
        assert exc_info.value.error_code == EmbeddingErrorCode.TIMEOUT
        assert exc_info.value.retryable is True

    @pytest.mark.asyncio
    async def test_embed_text_connection_failed(self, clip_svc):
        _inject_client(clip_svc, _make_mock_handler(exc=httpx.ConnectError("refused")))

        with pytest.raises(EmbeddingError) as exc_info:
            await clip_svc.embed_text(TextEmbeddingRequest(texts=["test"]))
        assert exc_info.value.error_code == EmbeddingErrorCode.CONNECTION_FAILED

    @pytest.mark.asyncio
    async def test_embed_text_dimension_mismatch(self, clip_svc):
        wrong_dim = np.random.randn(1024).astype(np.float32).tolist()
        mock_resp = httpx.Response(
            200,
            json={"embeddings": [wrong_dim], "model": "wrong"},
            request=httpx.Request("POST", "http://localhost:8003/v1/embeddings"),
        )
        _inject_client(clip_svc, _make_mock_handler(mock_resp))

        with pytest.raises(EmbeddingError) as exc_info:
            await clip_svc.embed_text(TextEmbeddingRequest(texts=["test"]))
        assert exc_info.value.error_code == EmbeddingErrorCode.DIMENSION_MISMATCH

    @pytest.mark.asyncio
    async def test_embed_text_count_mismatch(self, clip_svc):
        mock_resp = httpx.Response(
            200,
            json={"embeddings": [_make_768_embedding()], "model": "clip"},
            request=httpx.Request("POST", "http://localhost:8003/v1/embeddings"),
        )
        _inject_client(clip_svc, _make_mock_handler(mock_resp))

        with pytest.raises(EmbeddingError) as exc_info:
            await clip_svc.embed_text(TextEmbeddingRequest(texts=["a", "b"]))
        assert exc_info.value.error_code == EmbeddingErrorCode.ENCODING_FAILED
        assert "2" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_embed_text_invalid_json(self, clip_svc):
        mock_resp = httpx.Response(
            200,
            text="<html>not json</html>",
            headers={"content-type": "text/html"},
            request=httpx.Request("POST", "http://localhost:8003/v1/embeddings"),
        )
        _inject_client(clip_svc, _make_mock_handler(mock_resp))

        with pytest.raises(EmbeddingError) as exc_info:
            await clip_svc.embed_text(TextEmbeddingRequest(texts=["test"]))
        assert exc_info.value.error_code == EmbeddingErrorCode.ENCODING_FAILED
        assert "invalid JSON" in exc_info.value.detail or "Expecting" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_embed_text_sparse_raises_sparse_not_supported(self, clip_svc):
        with pytest.raises(EmbeddingError) as exc_info:
            await clip_svc.embed_text_sparse(TextEmbeddingRequest(texts=["test"]))
        assert exc_info.value.error_code == EmbeddingErrorCode.SPARSE_NOT_SUPPORTED

    @pytest.mark.asyncio
    async def test_embed_image_success(self, clip_svc):
        mock_resp = httpx.Response(
            200,
            json={"embeddings": [_make_768_embedding()], "model": "ultrasound-clip-v1"},
            request=httpx.Request("POST", "http://localhost:8003/v1/embeddings/image"),
        )
        _inject_client(clip_svc, _make_mock_handler(mock_resp))

        response = await clip_svc.embed_image(
            ImageEmbeddingRequest(image_data=b"\x00\x01\x02\x03", image_format="dicom", request_id="req_img_001")
        )
        assert len(response.embeddings) == 1
        assert response.modality == EmbeddingModality.IMAGE

    @pytest.mark.asyncio
    async def test_embed_image_jpeg_mime_type(self, clip_svc):
        captured_body = None

        async def capture_handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_body
            captured_body = request.content.decode("utf-8", errors="replace")
            return httpx.Response(
                200,
                json={"embeddings": [_make_768_embedding()], "model": "clip"},
                request=request,
            )

        _inject_client(clip_svc, capture_handler)
        await clip_svc.embed_image(ImageEmbeddingRequest(image_data=b"\xff\xd8\xff", image_format="jpeg"))

        assert captured_body is not None
        assert "image/jpeg" in captured_body

    @pytest.mark.asyncio
    async def test_embed_image_server_error(self, clip_svc):
        mock_resp = httpx.Response(500, text="Error", request=httpx.Request("POST", "http://localhost:8003/v1/embeddings/image"))
        _inject_client(clip_svc, _make_mock_handler(mock_resp))

        with pytest.raises(EmbeddingError) as exc_info:
            await clip_svc.embed_image(ImageEmbeddingRequest(image_data=b"\x00"))
        assert exc_info.value.error_code == EmbeddingErrorCode.ENCODING_FAILED
        assert exc_info.value.retryable is True

    @pytest.mark.asyncio
    async def test_embed_image_timeout(self, clip_svc):
        _inject_client(clip_svc, _make_mock_handler(exc=httpx.TimeoutException("timeout")))

        with pytest.raises(EmbeddingError) as exc_info:
            await clip_svc.embed_image(ImageEmbeddingRequest(image_data=b"\x00"))
        assert exc_info.value.error_code == EmbeddingErrorCode.TIMEOUT

    @pytest.mark.asyncio
    async def test_embed_image_connection_failed(self, clip_svc):
        _inject_client(clip_svc, _make_mock_handler(exc=httpx.ConnectError("refused")))

        with pytest.raises(EmbeddingError) as exc_info:
            await clip_svc.embed_image(ImageEmbeddingRequest(image_data=b"\x00"))
        assert exc_info.value.error_code == EmbeddingErrorCode.CONNECTION_FAILED

    @pytest.mark.asyncio
    async def test_embed_multimodal_success(self, clip_svc):
        mock_resp = httpx.Response(
            200,
            json={"embeddings": [_make_768_embedding(), _make_768_embedding()], "model": "clip", "token_count": 5},
            request=httpx.Request("POST", "http://localhost:8003/v1/embeddings/multimodal"),
        )
        _inject_client(clip_svc, _make_mock_handler(mock_resp))

        response = await clip_svc.embed_multimodal(
            MultimodalEmbeddingRequest(texts=["甲状腺结节"], images=[b"\x00\x01\x02"], request_id="req_mm_001")
        )
        assert len(response.embeddings) == 2
        assert response.modality == EmbeddingModality.MULTIMODAL

    @pytest.mark.asyncio
    async def test_embed_multimodal_empty_raises(self, clip_svc):
        with pytest.raises(EmbeddingError) as exc_info:
            await clip_svc.embed_multimodal(MultimodalEmbeddingRequest(texts=[], images=[]))
        assert exc_info.value.error_code == EmbeddingErrorCode.INVALID_INPUT

    @pytest.mark.asyncio
    async def test_embed_multimodal_server_error(self, clip_svc):
        mock_resp = httpx.Response(500, text="Error", request=httpx.Request("POST", "http://localhost:8003/v1/embeddings/multimodal"))
        _inject_client(clip_svc, _make_mock_handler(mock_resp))

        with pytest.raises(EmbeddingError) as exc_info:
            await clip_svc.embed_multimodal(MultimodalEmbeddingRequest(texts=["test"], images=[b"\x00"]))
        assert exc_info.value.retryable is True

    @pytest.mark.asyncio
    async def test_embed_multimodal_timeout(self, clip_svc):
        _inject_client(clip_svc, _make_mock_handler(exc=httpx.TimeoutException("timeout")))

        with pytest.raises(EmbeddingError) as exc_info:
            await clip_svc.embed_multimodal(MultimodalEmbeddingRequest(texts=["test"], images=[b"\x00"]))
        assert exc_info.value.error_code == EmbeddingErrorCode.TIMEOUT

    @pytest.mark.asyncio
    async def test_embed_multimodal_connection_failed(self, clip_svc):
        _inject_client(clip_svc, _make_mock_handler(exc=httpx.ConnectError("refused")))

        with pytest.raises(EmbeddingError) as exc_info:
            await clip_svc.embed_multimodal(MultimodalEmbeddingRequest(texts=["test"], images=[b"\x00"]))
        assert exc_info.value.error_code == EmbeddingErrorCode.CONNECTION_FAILED

    @pytest.mark.asyncio
    async def test_health_check_success(self, clip_svc):
        mock_resp = httpx.Response(200, request=httpx.Request("GET", "http://localhost:8003/health"))
        _inject_client(clip_svc, _make_mock_handler(mock_resp))
        assert await clip_svc.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self, clip_svc):
        _inject_client(clip_svc, _make_mock_handler(exc=httpx.ConnectError("refused")))
        assert await clip_svc.health_check() is False

    @pytest.mark.asyncio
    async def test_get_model_info_success(self, clip_svc):
        mock_resp = httpx.Response(
            200,
            json={"model_name": "ultrasound-clip-v1", "dimension": 768, "max_input_length": 512, "version": "1.0.0"},
            request=httpx.Request("GET", "http://localhost:8003/v1/model/info"),
        )
        _inject_client(clip_svc, _make_mock_handler(mock_resp))

        info = await clip_svc.get_model_info()
        assert info.model_name == "ultrasound-clip-v1"
        assert info.supports_image is True

        cached = await clip_svc.get_model_info()
        assert cached.model_name == info.model_name

    @pytest.mark.asyncio
    async def test_get_model_info_connection_error(self, clip_svc):
        _inject_client(clip_svc, _make_mock_handler(exc=httpx.ConnectError("refused")))
        info = await clip_svc.get_model_info()
        assert info.model_type == EmbeddingModelType.ULTRASOUND_CLIP
        assert info.supports_image is True

    @pytest.mark.asyncio
    async def test_get_model_info_invalid_json_raises(self, clip_svc):
        mock_resp = httpx.Response(
            200,
            text="<html>not json</html>",
            headers={"content-type": "text/html"},
            request=httpx.Request("GET", "http://localhost:8003/v1/model/info"),
        )
        _inject_client(clip_svc, _make_mock_handler(mock_resp))

        with pytest.raises(EmbeddingError) as exc_info:
            await clip_svc.get_model_info()
        assert "invalid JSON" in exc_info.value.detail or "Expecting" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_close_sets_closed_flag(self, clip_svc):
        await clip_svc.close()
        assert clip_svc._closed is True

    @pytest.mark.asyncio
    async def test_embed_after_close_raises(self):
        svc = UltrasoundClipService()
        await svc.close()

        with pytest.raises(EmbeddingError) as exc_info:
            await svc.embed_text(TextEmbeddingRequest(texts=["test"]))
        assert "closed" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_get_client_lazy_initialization(self, clip_svc):
        assert clip_svc._client is None
        client = await clip_svc._get_client()
        assert clip_svc._client is not None
        client2 = await clip_svc._get_client()
        assert client2 is client

    @pytest.mark.asyncio
    async def test_get_client_after_close_raises(self, clip_svc):
        await clip_svc._get_client()
        await clip_svc.close()

        with pytest.raises(EmbeddingError) as exc_info:
            await clip_svc._get_client()
        assert "closed" in str(exc_info.value).lower()


class TestBgeM3MedicalService:
    def test_init_default_values(self):
        svc = BgeM3MedicalService()
        assert svc._base_url == "http://localhost:8004"
        assert svc._dimension == BGE_M3_DIM

    @pytest.mark.asyncio
    async def test_embed_text_success(self, bge_svc):
        mock_resp = httpx.Response(
            200,
            json={"dense_embeddings": [_make_1024_embedding()], "model": "bge-m3-medical-v1", "token_count": 12},
            request=httpx.Request("POST", "http://localhost:8004/v1/embeddings"),
        )
        _inject_client(bge_svc, _make_mock_handler(mock_resp))

        response = await bge_svc.embed_text(TextEmbeddingRequest(texts=["肝脏占位性病变"], request_id="req_bge_001"))
        assert len(response.embeddings) == 1
        assert response.embeddings[0].shape == (1024,)
        assert response.model == "bge-m3-medical-v1"
        assert response.latency_ms > 0

    @pytest.mark.asyncio
    async def test_embed_text_timeout(self, bge_svc):
        _inject_client(bge_svc, _make_mock_handler(exc=httpx.TimeoutException("timeout")))

        with pytest.raises(EmbeddingError) as exc_info:
            await bge_svc.embed_text(TextEmbeddingRequest(texts=["test"]))
        assert exc_info.value.error_code == EmbeddingErrorCode.TIMEOUT

    @pytest.mark.asyncio
    async def test_embed_text_connection_failed(self, bge_svc):
        _inject_client(bge_svc, _make_mock_handler(exc=httpx.ConnectError("refused")))

        with pytest.raises(EmbeddingError) as exc_info:
            await bge_svc.embed_text(TextEmbeddingRequest(texts=["test"]))
        assert exc_info.value.error_code == EmbeddingErrorCode.CONNECTION_FAILED

    @pytest.mark.asyncio
    async def test_embed_text_dimension_mismatch(self, bge_svc):
        wrong_dim = np.random.randn(768).astype(np.float32).tolist()
        mock_resp = httpx.Response(
            200,
            json={"dense_embeddings": [wrong_dim], "model": "wrong"},
            request=httpx.Request("POST", "http://localhost:8004/v1/embeddings"),
        )
        _inject_client(bge_svc, _make_mock_handler(mock_resp))

        with pytest.raises(EmbeddingError) as exc_info:
            await bge_svc.embed_text(TextEmbeddingRequest(texts=["test"]))
        assert exc_info.value.error_code == EmbeddingErrorCode.DIMENSION_MISMATCH

    @pytest.mark.asyncio
    async def test_embed_text_sparse_success(self, bge_svc):
        fake_sparse = [{"index": 100, "weight": 0.8}, {"index": 250, "weight": 0.5}]
        mock_resp = httpx.Response(
            200,
            json={
                "dense_embeddings": [_make_1024_embedding()],
                "sparse_embeddings": [fake_sparse],
                "model": "bge-m3-medical-v1",
                "token_count": 12,
            },
            request=httpx.Request("POST", "http://localhost:8004/v1/embeddings"),
        )
        _inject_client(bge_svc, _make_mock_handler(mock_resp))

        response = await bge_svc.embed_text_sparse(TextEmbeddingRequest(texts=["肝脏占位性病变"]))
        assert len(response.dense_embeddings) == 1
        assert response.dense_embeddings[0].shape == (1024,)
        assert response.sparse_weights[0][100] == pytest.approx(0.8)

    @pytest.mark.asyncio
    async def test_embed_text_sparse_token_id_value_format(self, bge_svc):
        fake_sparse = [{"token_id": 500, "value": 0.6}]
        mock_resp = httpx.Response(
            200,
            json={
                "dense_embeddings": [_make_1024_embedding()],
                "sparse_embeddings": [fake_sparse],
                "model": "bge-m3-medical-v1",
            },
            request=httpx.Request("POST", "http://localhost:8004/v1/embeddings"),
        )
        _inject_client(bge_svc, _make_mock_handler(mock_resp))

        response = await bge_svc.embed_text_sparse(TextEmbeddingRequest(texts=["test"]))
        assert response.sparse_weights[0][500] == pytest.approx(0.6)

    @pytest.mark.asyncio
    async def test_embed_text_sparse_count_mismatch(self, bge_svc):
        mock_resp = httpx.Response(
            200,
            json={
                "dense_embeddings": [_make_1024_embedding(), _make_1024_embedding()],
                "sparse_embeddings": [[{"index": 1, "weight": 0.5}]],
                "model": "bge-m3-medical-v1",
            },
            request=httpx.Request("POST", "http://localhost:8004/v1/embeddings"),
        )
        _inject_client(bge_svc, _make_mock_handler(mock_resp))

        with pytest.raises(EmbeddingError) as exc_info:
            await bge_svc.embed_text_sparse(TextEmbeddingRequest(texts=["a", "b"]))
        assert "mismatch" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_embed_text_sparse_server_error(self, bge_svc):
        mock_resp = httpx.Response(500, text="Error", request=httpx.Request("POST", "http://localhost:8004/v1/embeddings"))
        _inject_client(bge_svc, _make_mock_handler(mock_resp))

        with pytest.raises(EmbeddingError) as exc_info:
            await bge_svc.embed_text_sparse(TextEmbeddingRequest(texts=["test"]))
        assert exc_info.value.retryable is True

    @pytest.mark.asyncio
    async def test_embed_image_raises_image_not_supported(self, bge_svc):
        with pytest.raises(EmbeddingError) as exc_info:
            await bge_svc.embed_image(ImageEmbeddingRequest(image_data=b"\x00\x01"))
        assert exc_info.value.error_code == EmbeddingErrorCode.IMAGE_NOT_SUPPORTED

    @pytest.mark.asyncio
    async def test_embed_multimodal_raises_image_not_supported(self, bge_svc):
        with pytest.raises(EmbeddingError) as exc_info:
            await bge_svc.embed_multimodal(MultimodalEmbeddingRequest(texts=["test"], images=[b"\x00"]))
        assert exc_info.value.error_code == EmbeddingErrorCode.IMAGE_NOT_SUPPORTED

    @pytest.mark.asyncio
    async def test_health_check_success(self, bge_svc):
        mock_resp = httpx.Response(200, request=httpx.Request("GET", "http://localhost:8004/health"))
        _inject_client(bge_svc, _make_mock_handler(mock_resp))
        assert await bge_svc.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self, bge_svc):
        _inject_client(bge_svc, _make_mock_handler(exc=httpx.ConnectError("refused")))
        assert await bge_svc.health_check() is False

    @pytest.mark.asyncio
    async def test_get_model_info_success(self, bge_svc):
        mock_resp = httpx.Response(
            200,
            json={"model_name": "bge-m3-medical-v1", "dimension": 1024, "max_input_length": 8192, "version": "1.0.0"},
            request=httpx.Request("GET", "http://localhost:8004/v1/model/info"),
        )
        _inject_client(bge_svc, _make_mock_handler(mock_resp))

        info = await bge_svc.get_model_info()
        assert info.model_name == "bge-m3-medical-v1"
        assert info.supports_image is False
        assert info.max_input_length == 8192

    @pytest.mark.asyncio
    async def test_get_model_info_connection_error(self, bge_svc):
        _inject_client(bge_svc, _make_mock_handler(exc=httpx.ConnectError("refused")))
        info = await bge_svc.get_model_info()
        assert info.model_type == EmbeddingModelType.BGE_M3_MEDICAL

    @pytest.mark.asyncio
    async def test_close_sets_closed_flag(self, bge_svc):
        await bge_svc.close()
        assert bge_svc._closed is True

    @pytest.mark.asyncio
    async def test_embed_after_close_raises(self):
        svc = BgeM3MedicalService()
        await svc.close()

        with pytest.raises(EmbeddingError) as exc_info:
            await svc.embed_text(TextEmbeddingRequest(texts=["test"]))
        assert "closed" in str(exc_info.value).lower()


class TestModelInfoCacheTTL:
    @pytest.mark.asyncio
    async def test_cache_expires_after_ttl(self):
        mock_resp = httpx.Response(
            200,
            json={"model_name": "fresh-clip", "dimension": 768},
            request=httpx.Request("GET", "http://localhost:8003/v1/model/info"),
        )
        call_count = 0

        async def counting_handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return mock_resp

        svc = UltrasoundClipService()
        _inject_client(svc, counting_handler)

        info1 = await svc.get_model_info()
        assert info1.model_name == "fresh-clip"
        assert call_count == 1

        info2 = await svc.get_model_info()
        assert info2.model_name == "fresh-clip"
        assert call_count == 1

        from unittest.mock import patch
        with patch.object(time, 'monotonic', return_value=time.monotonic() + 301):
            info3 = await svc.get_model_info()
            assert info3.model_name == "fresh-clip"
            assert call_count == 2

        await svc.close()

    @pytest.mark.asyncio
    async def test_cache_not_expired_at_ttl_minus_one(self):
        mock_resp = httpx.Response(
            200,
            json={"model_name": "fresh-clip", "dimension": 768},
            request=httpx.Request("GET", "http://localhost:8003/v1/model/info"),
        )
        call_count = 0

        async def counting_handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return mock_resp

        svc = UltrasoundClipService()
        _inject_client(svc, counting_handler)

        info1 = await svc.get_model_info()
        assert info1.model_name == "fresh-clip"
        assert call_count == 1

        from unittest.mock import patch
        with patch.object(time, 'monotonic', return_value=time.monotonic() + 299):
            info2 = await svc.get_model_info()
            assert info2.model_name == "fresh-clip"
            assert call_count == 1

        await svc.close()

    @pytest.mark.asyncio
    async def test_cache_expired_at_exact_ttl(self):
        mock_resp = httpx.Response(
            200,
            json={"model_name": "fresh-clip", "dimension": 768},
            request=httpx.Request("GET", "http://localhost:8003/v1/model/info"),
        )
        call_count = 0

        async def counting_handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return mock_resp

        svc = UltrasoundClipService()
        _inject_client(svc, counting_handler)

        info1 = await svc.get_model_info()
        assert info1.model_name == "fresh-clip"
        assert call_count == 1

        from unittest.mock import patch
        with patch.object(time, 'monotonic', return_value=time.monotonic() + 300):
            info2 = await svc.get_model_info()
            assert info2.model_name == "fresh-clip"
            assert call_count == 2

        await svc.close()


class TestEmbeddingModality:
    def test_values(self):
        assert EmbeddingModality.TEXT == "text"
        assert EmbeddingModality.IMAGE == "image"
        assert EmbeddingModality.MULTIMODAL == "multimodal"


class TestEmbeddingModelType:
    def test_values(self):
        assert EmbeddingModelType.ULTRASOUND_CLIP == "ultrasound-clip"
        assert EmbeddingModelType.BGE_M3_MEDICAL == "bge-m3-medical"


class TestDimensionConfigSwitch:
    def test_clip_default_dim(self):
        assert UltrasoundClipService()._dimension == 768

    def test_bge_m3_default_dim(self):
        assert BgeM3MedicalService()._dimension == 1024

    @pytest.mark.asyncio
    async def test_clip_768_dim_response(self):
        svc = UltrasoundClipService(dimension=768)
        mock_resp = httpx.Response(
            200,
            json={"embeddings": [_make_768_embedding()], "model": "clip"},
            request=httpx.Request("POST", "http://localhost:8003/v1/embeddings"),
        )
        _inject_client(svc, _make_mock_handler(mock_resp))

        resp = await svc.embed_text(TextEmbeddingRequest(texts=["test"]))
        assert resp.embeddings[0].shape == (768,)
        await svc.close()

    @pytest.mark.asyncio
    async def test_bge_m3_1024_dim_response(self):
        svc = BgeM3MedicalService(dimension=1024)
        mock_resp = httpx.Response(
            200,
            json={"dense_embeddings": [_make_1024_embedding()], "model": "bge-m3"},
            request=httpx.Request("POST", "http://localhost:8004/v1/embeddings"),
        )
        _inject_client(svc, _make_mock_handler(mock_resp))

        resp = await svc.embed_text(TextEmbeddingRequest(texts=["test"]))
        assert resp.embeddings[0].shape == (1024,)
        await svc.close()
