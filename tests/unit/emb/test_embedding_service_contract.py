"""守卫：薄嵌入/重排服务的响应体必须**能被真实客户端解析**。

## 为什么需要它

服务端（`scripts/start_embedding_services.py`）与客户端（`app/embedding/bge_m3.py`、
`app/rag/reranker.py`）是**两份独立实现**，中间只靠 HTTP 契约。契约写错的表现是
"服务活着、健康检查 200、但检索静默为空"——最难查的一类。

所以这里不自己造断言，而是**把服务的真实响应喂给真实客户端的解析函数**：
* `BgeM3MedicalService._parse_embeddings(...)` 会校验"向量数 == 文本数、每维 == 1024"；
* `_parse_sparse_weights(...)` 会校验稀疏结构的形状；
* rerank 的 `{index, relevance_score}` 语义（index 必须是 documents 的原始下标）
  由本测试按 `reranker.py:150-173` 的口径直接断言。

模型不加载：用 `backend_factory` 注入替身（`build_app` 为此保留的测试口子）。
"""
from __future__ import annotations

import base64
import importlib.util
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "start_embedding_services.py"


def _load_service_module():
    """按路径加载 scripts 下的脚本（它不是包的一部分）。"""
    spec = importlib.util.spec_from_file_location(
        "uai_embedding_services_under_test", SCRIPT_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def service_module():
    return _load_service_module()


class _StubBgeM3:
    dimension = 1024
    max_length = 512

    def encode(self, texts: list[str]) -> tuple[list[list[float]], int]:
        return [[0.01] * 1024 for _ in texts], sum(len(t) for t in texts)


class _StubReranker:
    max_length = 512

    def score(self, query: str, documents: list[str]) -> list[float]:
        # 故意让最后一个文档分数最高，用于验证排序与 index 回填
        return [float(i) for i in range(len(documents))]


class _StubUltrasoundClip:
    dimension = 768
    max_length = 77

    def encode_texts(self, texts: list[str]) -> tuple[list[list[float]], int]:
        return [[0.02] * 768 for _ in texts], sum(len(t) for t in texts)

    def encode_images(self, image_bytes_list: list[bytes]) -> list[list[float]]:
        return [[0.03] * 768 for _ in image_bytes_list]


def _client(service_module, service: str, stub) -> TestClient:
    app = service_module.build_app(
        service=service,
        model_dir="/nonexistent-not-loaded",
        device="cpu",
        dtype="float32",
        max_length=512,
        backend_factory=lambda _service: stub,
    )
    # 必须用上下文管理器，lifespan 才会跑（backend 在 lifespan 里装配）
    return TestClient(app)


class TestBgeM3Contract:
    def test_健康检查是200(self, service_module):
        with _client(service_module, "bge-m3", _StubBgeM3()) as client:
            response = client.get("/health")

            assert response.status_code == 200
            assert response.json()["status"] == "ok"

    def test_响应能被真实客户端解析(self, service_module):
        """关键：把响应喂给 BgeM3MedicalService 的真实解析函数。"""
        from app.embedding.bge_m3 import BgeM3MedicalService

        with _client(service_module, "bge-m3", _StubBgeM3()) as client:
            response = client.post(
                "/v1/embeddings", json={"texts": ["低回声结节", "TI-RADS 4A"]}
            )

        assert response.status_code == 200
        payload = response.json()

        # 真实客户端解析（维度/数量不符会抛 EmbeddingError）
        client_svc = BgeM3MedicalService(base_url="http://127.0.0.1:8004")
        vectors = client_svc._parse_embeddings(payload, expected_count=2)

        assert len(vectors) == 2
        assert all(vec.shape[0] == 1024 for vec in vectors)
        assert client_svc._parse_sparse_weights(payload) == []

    def test_模型信息字段是客户端读的那些(self, service_module):
        with _client(service_module, "bge-m3", _StubBgeM3()) as client:
            info = client.get("/v1/model/info").json()

        # _build_model_info 读这几个键（app/embedding/bge_m3.py:51-59）
        for key in ("model_name", "dimension", "max_input_length"):
            assert key in info
        assert info["dimension"] == 1024

    def test_空文本列表被拒绝(self, service_module):
        with _client(service_module, "bge-m3", _StubBgeM3()) as client:
            assert client.post("/v1/embeddings", json={"texts": []}).status_code == 400


class TestRerankerContract:
    def test_健康检查与重排结构(self, service_module):
        with _client(service_module, "reranker", _StubReranker()) as client:
            assert client.get("/health").status_code == 200
            response = client.post(
                "/v1/rerank",
                json={
                    "query": "甲状腺结节",
                    "documents": ["doc-a", "doc-b", "doc-c"],
                    "top_k": 2,
                    "model": "bge-reranker-base",
                },
            )

        assert response.status_code == 200
        results = response.json()["results"]

        # 按 reranker.py:150-173 的口径：index 是 documents 的原始下标、按分降序、遵守 top_k
        assert len(results) == 2
        assert results[0]["index"] == 2, "分数最高的应是最后一个文档（替身故意如此）"
        assert all(set(item) == {"index", "relevance_score"} for item in results)
        assert results[0]["relevance_score"] >= results[1]["relevance_score"]
        assert all(0 <= item["index"] < 3 for item in results)

    def test_不传top_k时返回全部文档(self, service_module):
        with _client(service_module, "reranker", _StubReranker()) as client:
            results = client.post(
                "/v1/rerank", json={"query": "q", "documents": ["a", "b"]}
            ).json()["results"]

        assert len(results) == 2

    def test_空文档列表返回空结果(self, service_module):
        with _client(service_module, "reranker", _StubReranker()) as client:
            response = client.post(
                "/v1/rerank", json={"query": "q", "documents": []}
            )

        assert response.status_code == 200
        assert response.json()["results"] == []


class TestServiceContractSurface:
    """三个服务都不能缺少客户端会打的端点。"""

    @pytest.mark.parametrize(
        ("service", "stub"),
        [("bge-m3", _StubBgeM3()), ("reranker", _StubReranker())],
    )
    def test_必需端点存在(self, service_module, service, stub):
        with _client(service_module, service, stub) as client:
            paths = set(client.get("/openapi.json").json()["paths"])

        assert "/health" in paths
        assert "/v1/model/info" in paths


# 1x1 透明 PNG（不依赖 Pillow，客户端上传只透传字节）
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQAB"
    "h6FO1AAAAABJRU5ErkJggg=="
)


class TestOpenClipBackendDimension:
    """守卫：open_clip 权重的真实维度必须以权重为准（BiomedCLIP=512，声明 768 无效）。"""

    def test_以权重真实维度为准(self, service_module, tmp_path, monkeypatch):
        import types

        # BiomedCLIP 这类 hf_text 模型是 CustomTextCLIP：没有顶层 embed_dim，
        # 维度挂在文本塔上（transformers 5.x 真机实测 AttributeError）。
        fake_model = types.SimpleNamespace(
            text=types.SimpleNamespace(output_dim=512), eval=lambda: None
        )
        fake_tokenizer = types.SimpleNamespace(return_tensors=None)
        fake_open_clip = types.SimpleNamespace(
            create_model_and_transforms=lambda *a, **k: (fake_model, None, None),
            get_tokenizer=lambda name: fake_tokenizer,
        )
        monkeypatch.setitem(sys.modules, "open_clip", fake_open_clip)

        backend = service_module.OpenClipBackend(
            str(tmp_path), "cpu", "float16", 256, dimension=768
        )
        assert backend.dimension == 512

    def test_顶层embed_dim优先(self, service_module, tmp_path, monkeypatch):
        import types

        fake_model = types.SimpleNamespace(embed_dim=768, eval=lambda: None)
        fake_open_clip = types.SimpleNamespace(
            create_model_and_transforms=lambda *a, **k: (fake_model, None, None),
            get_tokenizer=lambda name: types.SimpleNamespace(),
        )
        monkeypatch.setitem(sys.modules, "open_clip", fake_open_clip)

        backend = service_module.OpenClipBackend(
            str(tmp_path), "cpu", "float16", 256, dimension=768
        )
        assert backend.dimension == 768


class TestRerankerMaxLengthClamp:
    """守卫：max_length 必须钳制到模型位置嵌入上限内（真机 CUDA assert 死锁修复）。"""

    def test_超过位置上限时钳制并留特殊token余量(self, service_module):
        clamp = service_module.RerankerBackend._effective_max_length
        # bge-reranker-base：XLM-R，max_position_embeddings=512
        assert clamp(1024, 512) == 510

    def test_上限内不钳制(self, service_module):
        clamp = service_module.RerankerBackend._effective_max_length
        # bge-m3：max_position_embeddings=8192，默认 1024 不受影响
        assert clamp(1024, 8192) == 1024

    def test_配置缺失时原样返回(self, service_module):
        clamp = service_module.RerankerBackend._effective_max_length
        assert clamp(1024, None) == 1024
        assert clamp(1024, 0) == 1024

    def test_恰好等于上限时留余量(self, service_module):
        clamp = service_module.RerankerBackend._effective_max_length
        assert clamp(77, 77) == 75


class TestUltrasoundClipContract:
    def test_健康检查与模型信息(self, service_module):
        with _client(service_module, "ultrasound-clip", _StubUltrasoundClip()) as client:
            assert client.get("/health").status_code == 200
            info = client.get("/v1/model/info").json()

        # _build_model_info 读这几个键（app/embedding/ultrasound_clip.py:52-60）
        for key in ("model_name", "dimension", "max_input_length", "version"):
            assert key in info
        assert info["dimension"] == 768

    def test_文本响应能被真实客户端解析(self, service_module):
        """关键：把响应喂给 UltrasoundClipService 的真实解析函数。"""
        from app.embedding.ultrasound_clip import UltrasoundClipService

        with _client(service_module, "ultrasound-clip", _StubUltrasoundClip()) as client:
            response = client.post(
                "/v1/embeddings",
                json={"texts": ["甲状腺低回声结节"], "modality": "text"},
            )

        assert response.status_code == 200
        payload = response.json()

        client_svc = UltrasoundClipService(base_url="http://127.0.0.1:8003")
        # 客户端按 expected_count=len(texts) 校验（ultrasound_clip.py:76）
        vectors = client_svc._parse_embeddings(payload, expected_count=1)

        assert len(vectors) == 1
        assert vectors[0].shape[0] == 768

    def test_图像端点返回单条向量(self, service_module):
        from app.embedding.ultrasound_clip import UltrasoundClipService

        with _client(service_module, "ultrasound-clip", _StubUltrasoundClip()) as client:
            response = client.post(
                "/v1/embeddings/image",
                files={"image": ("image.png", _TINY_PNG, "image/png")},
                data={"modality": "image"},
            )

        assert response.status_code == 200
        payload = response.json()

        client_svc = UltrasoundClipService(base_url="http://127.0.0.1:8003")
        vectors = client_svc._parse_embeddings(payload, expected_count=1)
        assert vectors[0].shape[0] == 768

    def test_多模态端点条数为文本加图像(self, service_module):
        from app.embedding.ultrasound_clip import UltrasoundClipService

        with _client(service_module, "ultrasound-clip", _StubUltrasoundClip()) as client:
            response = client.post(
                "/v1/embeddings/multimodal",
                files=[
                    ("images", ("a.png", _TINY_PNG, "image/png")),
                    ("images", ("b.png", _TINY_PNG, "image/png")),
                ],
                # 客户端把 texts 以 JSON 字符串塞进表单（ultrasound_clip.py:154-157）
                data={"texts": '["甲状腺结节"]', "modality": "multimodal"},
            )

        assert response.status_code == 200
        payload = response.json()

        client_svc = UltrasoundClipService(base_url="http://127.0.0.1:8003")
        # 客户端按 expected_count=len(texts)+len(images) 校验（ultrasound_clip.py:167-168）
        vectors = client_svc._parse_embeddings(payload, expected_count=3)
        assert len(vectors) == 3

    def test_模态字段不符被拒绝(self, service_module):
        with _client(service_module, "ultrasound-clip", _StubUltrasoundClip()) as client:
            assert (
                client.post(
                    "/v1/embeddings/image",
                    files={"image": ("image.png", _TINY_PNG, "image/png")},
                    data={"modality": "text"},
                ).status_code
                == 400
            )
            assert (
                client.post(
                    "/v1/embeddings/multimodal",
                    files=[("images", ("a.png", _TINY_PNG, "image/png"))],
                    data={"texts": "[]", "modality": "image"},
                ).status_code
                == 400
            )

    def test_文本与图像全空的multimodal被拒绝(self, service_module):
        with _client(service_module, "ultrasound-clip", _StubUltrasoundClip()) as client:
            response = client.post(
                "/v1/embeddings/multimodal", data={"texts": "[]"}
            )

        assert response.status_code == 400

    def test_clip必需端点存在(self, service_module):
        with _client(service_module, "ultrasound-clip", _StubUltrasoundClip()) as client:
            paths = set(client.get("/openapi.json").json()["paths"])

        assert "/v1/embeddings" in paths
        assert "/v1/embeddings/image" in paths
        assert "/v1/embeddings/multimodal" in paths
