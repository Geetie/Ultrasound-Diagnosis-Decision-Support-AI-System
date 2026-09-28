"""薄嵌入 / 重排 HTTP 服务（补上仓库里缺失的服务端实现）。

## 为什么需要它（2026-09-28 真机部署暴露）

`app/main.py:199-212` 把三个外部服务当 HTTP 客户端连：

    8004 bge-m3        ← BgeM3MedicalService
    8003 ultrasound-clip ← UltrasoundClipService（权重为项目自有产物，公开源没有）
    8005 bge-reranker  ← BgeRerankerService

但**全仓库没有任何服务端实现**（scripts/、docker-compose*.yml、deploy/supervisor 全无），
于是 `rag_health_partial_failure embedding=False reranker=False`，检索的向量腿与重排
静默缺失 —— 这正是"能力已实现但从未被调用/从未跑通"的又一例。

本脚本用 transformers 加载**已下好的官方权重**，按客户端契约暴露端点，只绑回环。

## 端点契约（严格照客户端源码，勿随意改）

* `GET  /health`              → 200（客户端只判状态码，:`base.py:318-344`）
* `GET  /v1/model/info`       → `{"model_name","dimension","max_input_length","version"}`
* `POST /v1/embeddings`       （bge-m3）
      请求 `{"texts": [...], "return_dense": true, "return_sparse": bool}`
      响应 `{"dense_embeddings": [[...]], "embeddings": 同, "sparse_embeddings": [],
            "model": str, "token_count": int}`
      约束：向量数必须等于 texts 数、每维必须等于 `--dimension`（`base.py:133-153`）
* `POST /v1/rerank`           （bge-reranker）
      请求 `{"query": str, "documents": [...], "top_k": int, "model": str}`
      响应 `{"results": [{"index": int, "relevance_score": float}, ...]}`
      `index` 是**在 documents 里的原始下标**（客户端据此回填 chunk，`reranker.py:150-173`）

## 用法

    # bge-m3（1024d）
    python scripts/start_embedding_services.py --service bge-m3 \
        --model-dir ~/uai/models/bge-m3 --port 8004
    # 重排
    python scripts/start_embedding_services.py --service reranker \
        --model-dir ~/uai/models/bge-reranker-base --port 8005

GPU 需前置 `env CUDA_VISIBLE_DEVICES=0`（用户 fish 配置里的值是错的，见 skill）。
"""
from __future__ import annotations

import argparse
import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any

import structlog
import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from transformers import AutoModel, AutoModelForSequenceClassification, AutoTokenizer

logger = structlog.get_logger("scripts.embedding_services")

SERVICE_DIMENSIONS = {"bge-m3": 1024, "reranker": 0}


# ----------------------------------------------------------------------
# 请求模型（字段名与客户端 payload 一致）
# ----------------------------------------------------------------------
class EmbeddingsRequest(BaseModel):
    texts: list[str]
    return_dense: bool = True
    return_sparse: bool = False


class RerankRequest(BaseModel):
    query: str
    documents: list[str]
    top_k: int = Field(default=0, ge=0)
    model: str | None = None


# ----------------------------------------------------------------------
# 后端
# ----------------------------------------------------------------------
class BgeM3Backend:
    """bge-m3 文本嵌入（dense）。

    dense 取法照 FlagEmbedding/BGE-M3 官方口径：**取 `last_hidden_state[:, 0]`（CLS）
    后做 L2 归一化**。仓库里的 `flagembedding` 包未安装，这里用 transformers 等价实现。
    """

    name = "bge-m3"

    def __init__(
        self, model_dir: str, device: str, dtype: str, max_length: int
    ) -> None:
        self.dimension = SERVICE_DIMENSIONS["bge-m3"]
        self.max_length = max_length
        self.device = torch.device(device)
        torch_dtype = {"float16": torch.float16, "float32": torch.float32}[dtype]
        self.tokenizer = AutoTokenizer.from_pretrained(model_dir)
        self.model = AutoModel.from_pretrained(model_dir, torch_dtype=torch_dtype)
        self.model.to(self.device)
        self.model.eval()
        logger.info(
            "embedding_service_loaded",
            service=self.name,
            device=str(self.device),
            dtype=dtype,
            dimension=self.dimension,
        )

    def encode(self, texts: list[str]) -> tuple[list[list[float]], int]:
        batch = self.tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=self.max_length,
            return_tensors="pt",
        ).to(self.device)
        with torch.inference_mode():
            output = self.model(**batch)
            dense = output.last_hidden_state[:, 0]
            dense = torch.nn.functional.normalize(dense, p=2, dim=1)
        vectors = dense.to(torch.float32).cpu().numpy().tolist()
        token_count = int(batch["attention_mask"].sum().item())
        return vectors, token_count


class RerankerBackend:
    """bge-reranker-base 交叉编码器打分（返回原始 logit，供排序使用）。"""

    name = "bge-reranker"

    def __init__(
        self, model_dir: str, device: str, dtype: str, max_length: int
    ) -> None:
        self.max_length = max_length
        self.device = torch.device(device)
        torch_dtype = {"float16": torch.float16, "float32": torch.float32}[dtype]
        self.tokenizer = AutoTokenizer.from_pretrained(model_dir)
        self.model = AutoModelForSequenceClassification.from_pretrained(
            model_dir, torch_dtype=torch_dtype
        )
        self.model.to(self.device)
        self.model.eval()
        logger.info(
            "rerank_service_loaded",
            service=self.name,
            device=str(self.device),
            dtype=dtype,
        )

    def score(self, query: str, documents: list[str], batch_size: int = 16) -> list[float]:
        scores: list[float] = []
        for start in range(0, len(documents), batch_size):
            chunk = documents[start : start + batch_size]
            batch = self.tokenizer(
                [query] * len(chunk),
                chunk,
                padding=True,
                truncation=True,
                max_length=self.max_length,
                return_tensors="pt",
            ).to(self.device)
            with torch.inference_mode():
                logits = self.model(**batch).logits.view(-1)
            scores.extend(logits.to(torch.float32).cpu().tolist())
        return scores


# ----------------------------------------------------------------------
# 应用
# ----------------------------------------------------------------------
def build_app(
    service: str,
    model_dir: str,
    device: str,
    dtype: str,
    max_length: int,
    dimension: int | None = None,
    backend_factory: Any | None = None,
) -> FastAPI:
    """构造服务应用。`backend_factory` 仅供测试注入替身（不加载真模型）。"""
    state: dict[str, Any] = {"backend": None, "ready_at": None}

    def _load() -> Any:
        if backend_factory is not None:
            return backend_factory(service)
        if service == "bge-m3":
            return BgeM3Backend(model_dir, device, dtype, max_length)
        return RerankerBackend(model_dir, device, dtype, max_length)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # 启动期就把模型加载好：客户端只按 `/health` 是否 200 判定可用，
        # 若懒加载，健康检查会在模型未就绪时谎报"可用"。
        started = time.monotonic()
        state["backend"] = await asyncio.to_thread(_load)
        state["ready_at"] = time.monotonic()
        logger.info(
            "embedding_service_ready",
            service=service,
            load_seconds=round(state["ready_at"] - started, 2),
        )
        yield
        state["backend"] = None

    app = FastAPI(title=f"uai-{service}-service", lifespan=lifespan)

    def _backend() -> Any:
        backend = state["backend"]
        if backend is None:
            raise HTTPException(status_code=503, detail="model not loaded")
        return backend

    @app.get("/health")
    async def health() -> dict[str, Any]:
        backend = state["backend"]
        return {
            "status": "ok" if backend is not None else "loading",
            "service": service,
            "model_loaded": backend is not None,
        }

    @app.get("/v1/model/info")
    async def model_info() -> dict[str, Any]:
        backend = _backend()
        dim = dimension or getattr(backend, "dimension", 0)
        return {
            "model_name": service,
            "dimension": dim,
            "max_input_length": getattr(backend, "max_length", max_length),
            "version": "1.0",
        }

    if service == "bge-m3":

        @app.post("/v1/embeddings")
        async def embeddings(request: EmbeddingsRequest) -> dict[str, Any]:
            if not request.texts:
                raise HTTPException(status_code=400, detail="texts must not be empty")
            backend = _backend()
            vectors, token_count = await asyncio.to_thread(backend.encode, request.texts)
            if request.return_sparse:
                # 稀疏权重需要 bge-m3 的 sparse 线性头（FlagEmbedding 才有）；
                # 本服务只提供 dense。生产链路的稀疏腿走进程内 BM25
                # （retriever.py 用 BM25Index.search，不调本接口），故这里显式留空
                # 并告警，避免"悄悄返回全零稀疏向量"这种更难查的失真。
                logger.warning("sparse_embeddings_not_supported_returning_empty")
            return {
                "dense_embeddings": vectors,
                "embeddings": vectors,
                "sparse_embeddings": [],
                "model": service,
                "token_count": token_count,
            }

    if service == "reranker":

        @app.post("/v1/rerank")
        async def rerank(request: RerankRequest) -> dict[str, Any]:
            if not request.documents:
                return {"results": []}
            backend = _backend()
            scores = await asyncio.to_thread(
                backend.score, request.query, request.documents
            )
            results = [
                {"index": index, "relevance_score": float(score)}
                for index, score in enumerate(scores)
            ]
            results.sort(key=lambda item: item["relevance_score"], reverse=True)
            # 客户端自己也会按 top_k 截断（reranker.py:173），这里同样遵守请求语义。
            if request.top_k:
                results = results[: request.top_k]
            return {"results": results}

    return app


def main() -> int:
    parser = argparse.ArgumentParser(description="uai 薄嵌入/重排服务")
    parser.add_argument("--service", choices=["bge-m3", "reranker"], required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--host", default="127.0.0.1", help="只绑回环（默认）")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--dtype", default="float16", choices=["float16", "float32"])
    parser.add_argument("--max-length", type=int, default=1024)
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    import uvicorn

    app = build_app(
        service=args.service,
        model_dir=args.model_dir,
        device=args.device,
        dtype=args.dtype,
        max_length=args.max_length,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
