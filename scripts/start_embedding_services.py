"""薄嵌入 / 重排 HTTP 服务（补上仓库里缺失的服务端实现）。

## 为什么需要它（2026-09-28 真机部署暴露）

`app/main.py:199-212` 把三个外部服务当 HTTP 客户端连：

    8004 bge-m3        ← BgeM3MedicalService
    8003 ultrasound-clip ← UltrasoundClipService（官方权重为 open_clip 格式，见
      `--clip-format open_clip`；HF 格式走默认后端。官方 Ultrasound-CLIP（CVPR 2026）
      权重在 OneDrive 无法自动化下载，先行底座为 BiomedCLIP（MIT，I2T@10 23.6%
      vs 通用基座 9.6%，US-365K test 500 样本，2026-09-29 真机实测））
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
* `POST /v1/embeddings`       （ultrasound-clip，JSON 文本）
      请求 `{"texts": [...], "modality": "text"}`（modality 由客户端附加，`ultrasound_clip.py:62-74`）
      响应 `{"embeddings": [[...]], "model": str, "token_count": int}`
* `POST /v1/embeddings/image` （ultrasound-clip，multipart）
      表单字段：文件 `image` + `modality=image`（`ultrasound_clip.py:104-118`）
      响应 `{"embeddings": [[...]], "model": str}`（恰好 1 条）
* `POST /v1/embeddings/multimodal`（ultrasound-clip，multipart）
      文件字段 `images`（可多个）+ 表单 `texts`（**JSON 字符串**）+ `modality=multimodal`
      （`ultrasound_clip.py:137-168`）
      响应 `{"embeddings": [[...]], "model": str, "token_count": int}`
      约束：向量数 == len(texts) + len(images)（客户端按此校验）

## 用法

    # bge-m3（1024d）
    python scripts/start_embedding_services.py --service bge-m3 \
        --model-dir ~/uai/models/bge-m3 --port 8004
    # 重排
    python scripts/start_embedding_services.py --service reranker \
        --model-dir ~/uai/models/bge-reranker-base --port 8005
    # 图像嵌入（768d，与 settings.embedding_dim 一致）
    python scripts/start_embedding_services.py --service ultrasound-clip \
        --model-dir ~/uai/models/ultrasound-clip --port 8003

GPU 需前置 `env CUDA_VISIBLE_DEVICES=0`（用户 fish 配置里的值是错的，见 skill）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import structlog
import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from transformers import (
    AutoModel,
    AutoModelForSequenceClassification,
    AutoProcessor,
    AutoTokenizer,
)

logger = structlog.get_logger("scripts.embedding_services")

SERVICE_DIMENSIONS = {"bge-m3": 1024, "reranker": 0, "ultrasound-clip": 768}


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
        # 与 RerankerBackend 同理：max_length 不得超过模型位置嵌入上限
        # （bge-m3 为 8192，默认 1024 不受影响；钳制是防御性兜底）。
        self.max_length = RerankerBackend._effective_max_length(
            max_length, getattr(self.model.config, "max_position_embeddings", None)
        )
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


class UltrasoundClipBackend:
    """ultrasound-clip 图文对齐嵌入（CLIP 双塔，768d 联合空间）。

    用 `AutoModel` 加载 HF CLIP 架构权重（官方 Ultrasound-CLIP 预训练权重或
    其 LoRA 合并产物），文本/图像分别过各自的塔取投影特征后 L2 归一化——
    归一化后点积即余弦相似度，与检索侧用法一致。
    """

    name = "ultrasound-clip"

    def __init__(
        self,
        model_dir: str,
        device: str,
        dtype: str,
        max_length: int,
        dimension: int = 768,
    ) -> None:
        from PIL import Image  # noqa: F401  前置校验：图像路径缺 Pillow 应在加载期报错

        self.dimension = dimension
        self.max_length = max_length
        self.device = torch.device(device)
        torch_dtype = {"float16": torch.float16, "float32": torch.float32}[dtype]
        self.processor = AutoProcessor.from_pretrained(model_dir)
        self.model = AutoModel.from_pretrained(model_dir, torch_dtype=torch_dtype)
        # CLIP 文本塔位置嵌入上限通常 77（如 ViT-L/14），同理钳制。
        text_config = getattr(self.model.config, "text_config", None)
        max_positions = getattr(text_config or self.model.config, "max_position_embeddings", None)
        self.max_length = RerankerBackend._effective_max_length(max_length, max_positions)
        self.model.to(self.device)
        self.model.eval()
        logger.info(
            "clip_service_loaded",
            service=self.name,
            device=str(self.device),
            dtype=dtype,
            dimension=self.dimension,
        )

    def _normalize(self, features: Any) -> list[list[float]]:
        # transformers v4 的 get_text/image_features 返回投影后的张量；
        # v5（≥5.17 实测）返回 BaseModelOutputWithPooling，投影特征在 pooler_output。
        tensor = (
            features.pooler_output if hasattr(features, "pooler_output") else features
        )
        return (
            torch.nn.functional.normalize(tensor, p=2, dim=1)
            .to(torch.float32)
            .cpu()
            .numpy()
            .tolist()
        )

    def encode_texts(self, texts: list[str]) -> tuple[list[list[float]], int]:
        batch = self.processor(
            text=texts,
            padding=True,
            truncation=True,
            max_length=self.max_length,
            return_tensors="pt",
        ).to(self.device)
        with torch.inference_mode():
            features = self.model.get_text_features(**batch)
        token_count = int(batch["attention_mask"].sum().item())
        return self._normalize(features), token_count

    def encode_images(self, image_bytes_list: list[bytes]) -> list[list[float]]:
        from io import BytesIO

        from PIL import Image

        images = [
            Image.open(BytesIO(raw)).convert("RGB") for raw in image_bytes_list
        ]
        batch = self.processor(images=images, return_tensors="pt").to(self.device)
        with torch.inference_mode():
            features = self.model.get_image_features(**batch)
        return self._normalize(features)


class OpenClipBackend:
    """open_clip 格式 CLIP 权重（如 BiomedCLIP 官方仓库）的图文嵌入。

    为什么存在：BiomedCLIP 官方只发 open_clip 格式（open_clip_pytorch_model.bin），
    其 HF 格式第三方转换版在 transformers 5.x 下加载即崩（CLIPConfig 位置参数
    不兼容，真机 2026-09-29 实测）→ 走 open_clip 库加载官方权重。
    与 UltrasoundClipBackend 同接口（encode_texts/encode_images）。
    """

    name = "ultrasound-clip"

    def __init__(
        self,
        model_dir: str,
        device: str,
        dtype: str,
        max_length: int,
        dimension: int = 768,
    ) -> None:
        import open_clip
        from PIL import Image  # noqa: F401  前置校验：图像路径缺 Pillow 应在加载期报错

        del dtype  # open_clip 自行管理精度（fp32 权重；推理可在 GPU 上）
        self.device = torch.device(device)
        model_dir_path = Path(model_dir)

        # open_clip 3.3 的 `local-dir:` schema：从目录里的 open_clip_config.json
        # 读模型配置与 tokenizer（text_cfg.hf_model_name 的 tokenizer 需可达 HF，
        # 真机经 HF_ENDPOINT=hf-mirror）。比临时注册 add_model_config 干净。
        uri = f"local-dir:{model_dir_path}"
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            uri, device=str(self.device)
        )
        self.model.eval()
        self.tokenizer = open_clip.get_tokenizer(uri)

        # 维度以权重真实值为准（如 BiomedCLIP=512）；与调用方声明不一致时告警并取真值。
        # 注意：BiomedCLIP 这类 hf_text 的模型是 CustomTextCLIP，embed_dim 挂在两座塔上
        # （CustomTextCLIP 没有顶层 embed_dim，真机 2026-09-29 实测 AttributeError）。
        actual_dim = getattr(self.model, "embed_dim", None)
        if actual_dim is None:
            # open_clip 3.x：CLIP 有顶层 embed_dim；CustomTextCLIP 没有，
            # 其文本塔（HFTextEncoder）的维度属性是 output_dim（真机实测）。
            text_tower = self.model.text
            actual_dim = int(
                getattr(text_tower, "embed_dim", None)
                or getattr(text_tower, "output_dim", None)
            )
        if dimension and actual_dim != dimension:
            logger.warning(
                "openclip_dimension_mismatch_declared_vs_actual",
                declared=dimension,
                actual=actual_dim,
            )
        self.dimension = actual_dim
        self.max_length = max_length
        logger.info(
            "clip_service_loaded",
            service=self.name,
            backend="open_clip",
            device=str(self.device),
            dimension=self.dimension,
        )

    def _normalize(self, tensor: Any) -> list[list[float]]:
        return (
            torch.nn.functional.normalize(tensor.float(), p=2, dim=1)
            .cpu()
            .numpy()
            .tolist()
        )

    def encode_texts(self, texts: list[str]) -> tuple[list[list[float]], int]:
        toks = self.tokenizer(texts)
        if hasattr(toks, "attention_mask"):
            # transformers BatchEncoding 形态
            token_count = int(toks["attention_mask"].sum().item())
            toks = toks.to(self.device)
        else:
            # open_clip 3.x 直接返回张量（无 attention_mask）
            toks = toks.to(self.device)
            token_count = int(toks.numel())
        with torch.inference_mode():
            features = self.model.encode_text(toks)
        return self._normalize(features), token_count

    def encode_images(self, image_bytes_list: list[bytes]) -> list[list[float]]:
        from io import BytesIO

        from PIL import Image

        images = [
            self.preprocess(Image.open(BytesIO(raw)).convert("RGB"))
            for raw in image_bytes_list
        ]
        batch = torch.stack(images).to(self.device)
        with torch.inference_mode():
            features = self.model.encode_image(batch)
        return self._normalize(features)


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
        # ⚠️ 真机 2026-09-29（CUDA device-side assert，进程粘性毒化）：
        # bge-reranker-base 是 XLM-R 架构，max_position_embeddings=512；
        # 服务默认 --max-length 1024 允许更长序列 → transformers 5 在
        # `torch.gather(buffered_token_type_ids, index=position_ids)` 处
        # scatter-gather 越界 assert → 此后所有请求 500（必须重启进程）。
        # 因此 max_length 必须钳制到模型位置嵌入上限以内（留 2 个特殊 token 位）。
        self.max_length = self._effective_max_length(
            max_length, getattr(self.model.config, "max_position_embeddings", None)
        )
        self.model.to(self.device)
        self.model.eval()
        logger.info(
            "rerank_service_loaded",
            service=self.name,
            device=str(self.device),
            dtype=dtype,
            max_length=self.max_length,
        )

    @staticmethod
    def _effective_max_length(requested: int, max_positions: int | None) -> int:
        if max_positions is None or max_positions <= 0:
            return requested
        hard_limit = max_positions - 2
        if requested > hard_limit:
            logger.warning(
                "reranker_max_length_clamped",
                requested=requested,
                effective=hard_limit,
                max_position_embeddings=max_positions,
            )
            return hard_limit
        return requested

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
    clip_format: str = "hf",
) -> FastAPI:
    """构造服务应用。`backend_factory` 仅供测试注入替身（不加载真模型）。"""
    state: dict[str, Any] = {"backend": None, "ready_at": None}

    def _load() -> Any:
        if backend_factory is not None:
            return backend_factory(service)
        if service == "bge-m3":
            return BgeM3Backend(model_dir, device, dtype, max_length)
        if service == "ultrasound-clip":
            if clip_format == "open_clip":
                return OpenClipBackend(
                    model_dir, device, dtype, max_length, dimension=dimension or 768
                )
            return UltrasoundClipBackend(
                model_dir, device, dtype, max_length, dimension=dimension or 768
            )
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
        # 后端上报的真实维度优先（open_clip 权重的 embed_dim 以权重为准，如 BiomedCLIP=512）
        dim = getattr(backend, "dimension", None) or dimension or 0
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

    if service == "ultrasound-clip":

        @app.post("/v1/embeddings")
        async def clip_text_embeddings(request: EmbeddingsRequest) -> dict[str, Any]:
            if not request.texts:
                raise HTTPException(status_code=400, detail="texts must not be empty")
            backend = _backend()
            vectors, token_count = await asyncio.to_thread(
                backend.encode_texts, request.texts
            )
            return {"embeddings": vectors, "model": service, "token_count": token_count}

        @app.post("/v1/embeddings/image")
        async def clip_image_embedding(
            image: UploadFile, modality: str = Form(default="image")
        ) -> dict[str, Any]:
            if modality != "image":
                raise HTTPException(
                    status_code=400, detail=f"modality must be 'image', got {modality!r}"
                )
            backend = _backend()
            raw = await image.read()
            if not raw:
                raise HTTPException(status_code=400, detail="image must not be empty")
            vectors = await asyncio.to_thread(backend.encode_images, [raw])
            return {"embeddings": vectors, "model": service}

        @app.post("/v1/embeddings/multimodal")
        async def clip_multimodal_embeddings(
            images: list[UploadFile] = File(default=[]),
            texts: str = Form(default="[]"),
            modality: str = Form(default="multimodal"),
        ) -> dict[str, Any]:
            if modality != "multimodal":
                raise HTTPException(
                    status_code=400,
                    detail=f"modality must be 'multimodal', got {modality!r}",
                )
            try:
                text_list: list[str] = json.loads(texts)
            except json.JSONDecodeError as exc:
                raise HTTPException(
                    status_code=400, detail=f"texts must be a JSON string: {exc}"
                ) from exc
            if not text_list and not images:
                raise HTTPException(
                    status_code=400,
                    detail="At least one of texts or images must be provided",
                )
            backend = _backend()
            image_bytes: list[bytes] = []
            for upload in images:
                raw = await upload.read()
                if not raw:
                    raise HTTPException(
                        status_code=400, detail="each image must not be empty"
                    )
                image_bytes.append(raw)
            text_vectors: list[list[float]] = []
            token_count = 0
            if text_list:
                text_vectors, token_count = await asyncio.to_thread(
                    backend.encode_texts, text_list
                )
            image_vectors = (
                await asyncio.to_thread(backend.encode_images, image_bytes)
                if image_bytes
                else []
            )
            # 客户端只校验"条数 == len(texts) + len(images)"（ultrasound_clip.py:167-168），
            # 这里按 文本在前、图像在后 的确定性顺序拼接。
            return {
                "embeddings": text_vectors + image_vectors,
                "model": service,
                "token_count": token_count,
            }

    return app


def main() -> int:
    parser = argparse.ArgumentParser(description="uai 薄嵌入/重排服务")
    parser.add_argument(
        "--service", choices=["bge-m3", "reranker", "ultrasound-clip"], required=True
    )
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--host", default="127.0.0.1", help="只绑回环（默认）")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--dtype", default="float16", choices=["float16", "float32"])
    parser.add_argument("--max-length", type=int, default=1024)
    parser.add_argument(
        "--dimension",
        type=int,
        default=None,
        help="覆盖服务默认维度（ultrasound-clip 默认 768，须与 settings.embedding_dim 一致）",
    )
    parser.add_argument(
        "--clip-format",
        choices=["hf", "open_clip"],
        default="hf",
        help="ultrasound-clip 权重格式：hf=transformers CLIP（默认），open_clip=open_clip 官方仓库格式（如 BiomedCLIP）",
    )
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    import uvicorn

    app = build_app(
        service=args.service,
        model_dir=args.model_dir,
        device=args.device,
        dtype=args.dtype,
        max_length=args.max_length,
        dimension=args.dimension,
        clip_format=args.clip_format,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
