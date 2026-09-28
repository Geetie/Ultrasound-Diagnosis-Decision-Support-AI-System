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
    """两个服务都不能缺少客户端会打的端点。"""

    @pytest.mark.parametrize(
        ("service", "stub"),
        [("bge-m3", _StubBgeM3()), ("reranker", _StubReranker())],
    )
    def test_必需端点存在(self, service_module, service, stub):
        with _client(service_module, service, stub) as client:
            paths = set(client.get("/openapi.json").json()["paths"])

        assert "/health" in paths
        assert "/v1/model/info" in paths
