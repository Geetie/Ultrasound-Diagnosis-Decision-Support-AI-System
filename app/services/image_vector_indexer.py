"""新上传影像的**在线**向量索引（A.147 / G2 收口）。

## 缺口（端到端实测）

`build_image_vectors.py` 是**离线**脚本：只覆盖"脚本跑的时候库里已有的影像"。
新上传的影像（`POST /api/v1/imaging/dicom/upload`）永远不进 `ultrasound_chunks`，
影像腿按 session 过滤后**恒 0 命中**（本轮 e2e 实测：上传 img_20261103_d413aac7 →
`image_leg_no_hits`）——"批量灌了历史数据"不等于"新数据也能查"。

## 做法

上传成功后调 `index_image_async(image_id, session_id, patient_id, path, organ)`：
1. 读 DICOM → 取帧（与离线脚本同一 `frame_indices` 口径）→ 逐帧嵌 → 逐维平均；
2. **病人隔离闸与离线脚本同源**：缺 session_id / patient_id 拒绝写入；
3. 失败只记日志（不影响上传主流程——影像已入库，向量可以后补）；
4. 调用点：`imaging.py` 的 upload 端点在落库成功后 fire-and-forget（`asyncio.create_task`）。

point id 与离线脚本一致（`uuid5(image_id)`）→ 重复跑/先离线后在线都幂等。
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

logger = structlog.get_logger("app.services.image_vector_indexer")


def _point_id(image_id: str) -> str:
    """与离线脚本相同的确定性 point id（幂等 upsert）。"""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ultrasound_image:{image_id}"))


async def index_image_async(
    *,
    image_id: str,
    session_id: str,
    patient_id: str,
    storage_path: str,
    modality: str = "",
    organ: str = "",
) -> bool:
    """把单张已落库影像嵌入并写入 Qdrant。返回是否成功（失败只记日志）。"""
    if not (session_id and patient_id and image_id):
        logger.warning(
            "image_vector_index_skipped_missing_isolation_fields",
            image_id=image_id,
            has_session=bool(session_id),
            has_patient=bool(patient_id),
        )
        return False
    try:
        from pathlib import Path

        from app.config.settings import settings
        from app.embedding.protocols import ImageEmbeddingRequest, VectorRecord
        from app.embedding.qdrant_client import QdrantVectorIndex
        from app.embedding.ultrasound_clip import UltrasoundClipService
        from app.imaging.dicom_pixels import extract_frame_from_bytes

        raw = Path(storage_path).read_bytes()
        try:
            first = extract_frame_from_bytes(raw, None)
            indices = [0] if first.total_frames <= 1 else list(range(min(first.total_frames, 5)))
            vectors: list[list[float]] = []
            clip = UltrasoundClipService(
                base_url=f"http://127.0.0.1:{settings.ultrasound_clip_port}"
            )
            try:
                for idx in indices:
                    frame = extract_frame_from_bytes(raw, idx)
                    resp = await clip.embed_image(
                        ImageEmbeddingRequest(
                            image_data=frame.png_bytes,
                            image_format="png",
                            request_id=f"{image_id}#{idx}",
                            session_id=session_id,
                        )
                    )
                    if resp.embeddings:
                        vectors.append(list(resp.embeddings[0]))
            finally:
                closer = getattr(clip, "close", None)
                if closer is not None:
                    await closer()
            if not vectors:
                raise RuntimeError("no frame embedded")
            dim = len(vectors[0])
            emb = (
                vectors[0]
                if len(vectors) == 1
                else [sum(v[i] for v in vectors) / len(vectors) for i in range(dim)]
            )
        except Exception:
            # 非 DICOM（直接传 PNG/JPG）→ 整文件嵌一次
            from app.config.settings import settings
            from app.embedding.protocols import ImageEmbeddingRequest
            from app.embedding.ultrasound_clip import UltrasoundClipService

            clip = UltrasoundClipService(
                base_url=f"http://127.0.0.1:{settings.ultrasound_clip_port}"
            )
            try:
                resp = await clip.embed_image(
                    ImageEmbeddingRequest(
                        image_data=raw,
                        image_format=Path(storage_path).suffix.lstrip(".") or "png",
                        request_id=image_id,
                        session_id=session_id,
                    )
                )
            finally:
                closer = getattr(clip, "close", None)
                if closer is not None:
                    await closer()
            if not resp.embeddings:
                raise RuntimeError("no embedding")
            emb = list(resp.embeddings[0])

        payload: dict[str, Any] = {
            "image_id": image_id,
            "session_id": session_id,
            "patient_id": patient_id,
            "modality": modality,
            "storage_path": storage_path,
        }
        if organ:
            payload["organ"] = organ

        index = QdrantVectorIndex(
            host=settings.qdrant_host,
            port=settings.qdrant_port,
            api_key=settings.qdrant_api_key or None,
        )
        try:
            await index.upsert(
                collection=settings.rag_image_collection,
                vectors=[VectorRecord(id=_point_id(image_id), vector=emb, metadata=payload)],
            )
        finally:
            closer = getattr(index, "close", None)
            if closer is not None:
                await closer()
        logger.info(
            "image_vector_indexed",
            image_id=image_id,
            session_id=session_id,
            dim=len(emb),
        )
        return True
    except Exception as exc:  # silent-ok: 向量索引失败不影响上传主流程（可后补）
        logger.warning(
            "image_vector_index_failed",
            image_id=image_id,
            error=str(exc)[:160],
            error_type=type(exc).__name__,
        )
        return False
