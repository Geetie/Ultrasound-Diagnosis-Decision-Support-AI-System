"""A.82：**相似病例通道**（影像向量的正确用法：独立输出，不进知识上下文）。

**为什么单独做一条通道**（A.80/A.81 的结论）：
影像向量原先被硬接在"文本知识块"管线上 —— `_wrap_search_results()` 按 `chunk_id`
回填正文，而影像点 id 是 `img_…`，知识表里没有 → **命中被静默丢弃**，贡献恒为 0。

**合规边界（写死在代码里）**
* 默认 `scope="self"`：**只查同一病人的历史影像**。这是同一患者自己的资料，
  不构成跨病人披露，也不需要额外授权。
* `scope="deidentified"`（跨病人、仅返回脱敏字段）**默认关闭**，必须显式配置
  （`settings.similar_cases_scope`）——跨病人比对属于数据使用决策，不该由代码默认打开。
* 输出**只含** `image_id / score / organ / date`，**绝不带 `patient_id` / `session_id`**。

**失败语义**：任何异常都返回 `status="unavailable"` + 原因，**绝不影响主判读**
（相似病例是参考信息，不是诊断依据）。
"""

from __future__ import annotations

import contextlib
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

DEFAULT_TOP_K = 3


def cases_filter(scope: str, patient_id: str | None, organ: str | None) -> dict | None:
    """构造向量检索过滤条件。纯函数（守卫钉住）。

    * `self`：**必须**按 `patient_id` 过滤（缺 patient_id 就不查，绝不退化成全库）；
    * `deidentified`：按器官过滤（跨病人，需显式开启）。
    """
    if scope == "self":
        pid = str(patient_id or "").strip()
        if not pid:
            return None
        return {"patient_id": pid}
    if scope == "deidentified":
        return {"organ": organ} if organ else None
    return None


def date_of(image_id: str, meta: dict | None = None) -> str | None:
    """检查日期：优先取 payload 的 `date`，否则从 `img_YYYYMMDD_…` 推导。纯函数（守卫钉住）。

    为什么需要兜底：`img_20261009_6e366343` 这种命名里已含日期，
    而建库时 payload 没写 `date` —— 为了一个展示字段重建 580 条向量不值得。
    """
    raw = str((meta or {}).get("date") or "").strip()
    if raw:
        return raw
    parts = str(image_id or "").split("_")
    if len(parts) >= 2 and len(parts[1]) == 8 and parts[1].isdigit():
        ymd = parts[1]
        return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
    return None


def public_case(hit: Any, *, keep_fields: tuple[str, ...] = ("image_id", "organ")) -> dict:
    """把向量命中转成**可对外**的字段（脱敏：无 patient_id / session_id）。纯函数（守卫钉住）。"""
    meta = getattr(hit, "metadata", None) or {}
    out: dict[str, Any] = {"score": round(float(getattr(hit, "score", 0.0) or 0.0), 4)}
    for field in keep_fields:
        value = meta.get(field)
        if value not in (None, ""):
            out[field] = str(value)
    date = date_of(str(meta.get("image_id") or ""), meta)
    if date:
        out["date"] = date
    # 兜底：即便 payload 里有，也绝不透出
    out.pop("patient_id", None)
    out.pop("session_id", None)
    return out


def frame_png_of(content: bytes) -> bytes | None:
    """DICOM → **中间帧** PNG（ULC 只吃图片）。失败返回 None（不抛）。"""
    try:
        from app.imaging.dicom_pixels import extract_frame_from_bytes
        from app.imaging.dicom_structure import frame_indices

        first = extract_frame_from_bytes(content, None)
        picks = frame_indices(first.total_frames)
        idx = picks[len(picks) // 2]  # 取首/中/末里的中间那帧
        return extract_frame_from_bytes(content, idx).png_bytes
    except Exception:  # silent-ok: 解码失败由调用方返回 unavailable（相似病例是参考信息）
        return None


async def find_similar_cases(
    *,
    image_content: bytes,
    patient_id: str | None,
    organ: str | None,
    scope: str = "self",
    top_k: int = DEFAULT_TOP_K,
    exclude_image_id: str | None = None,
) -> dict:
    """查相似病例。**永不抛异常**，失败返回 `status="unavailable"`。"""
    from app.config.settings import settings

    flt = cases_filter(scope, patient_id, organ)
    if flt is None:
        return {
            "status": "skipped",
            "reason": "no_scope_filter",
            "cases": [],
            "scope": scope,
        }

    png = frame_png_of(image_content)
    if png is None:
        return {"status": "unavailable", "reason": "frame_decode_failed", "cases": [], "scope": scope}

    clip = index = None
    try:
        from app.embedding.protocols import ImageEmbeddingRequest
        from app.embedding.qdrant_client import QdrantVectorIndex
        from app.embedding.ultrasound_clip import UltrasoundClipService

        clip = UltrasoundClipService(
            base_url=f"http://127.0.0.1:{settings.ultrasound_clip_port}"
        )
        resp = await clip.embed_image(
            ImageEmbeddingRequest(
                image_data=png, image_format="png", request_id="similar_cases", session_id=None
            )
        )
        if not resp.embeddings:
            return {
                "status": "unavailable",
                "reason": "no_embedding",
                "cases": [],
                "scope": scope,
            }
        index = QdrantVectorIndex(
            host=settings.qdrant_host, port=settings.qdrant_port,
            api_key=settings.qdrant_api_key or None,
        )
        hits = await index.search(
            collection=settings.rag_image_collection,
            query_vector=list(resp.embeddings[0]),
            top_k=top_k + 1,
            filter=flt,
        )
    except Exception as exc:  # 相似病例是参考信息，失败绝不影响主判读
        logger.warning(
            "similar_cases_unavailable", reason=type(exc).__name__, error=str(exc)[:160]
        )
        return {
            "status": "unavailable",
            "reason": f"{type(exc).__name__}",
            "cases": [],
            "scope": scope,
        }
    finally:
        for obj in (clip, index):
            closer = getattr(obj, "close", None) if obj is not None else None
            if closer is not None:
                # silent-ok: 关闭失败不影响已拿到的结果（连接由进程退出回收）
                with contextlib.suppress(Exception):
                    await closer()

    cases = [
        public_case(hit)
        for hit in hits
        if str((getattr(hit, "metadata", None) or {}).get("image_id") or "") != exclude_image_id
    ][:top_k]
    return {"status": "ok", "scope": scope, "cases": cases}


def summary_of(result: dict) -> dict:
    """给报告用的一句话摘要（纯函数，便于测试）。"""
    if result.get("status") != "ok":
        return {"status": str(result.get("status") or "unknown"), "count": 0}
    cases = result.get("cases") or []
    return {
        "status": "ok",
        "count": len(cases),
        "top_score": cases[0]["score"] if cases else None,
        "scope": result.get("scope"),
    }


__all__ = [
    "DEFAULT_TOP_K",
    "cases_filter",
    "find_similar_cases",
    "frame_png_of",
    "public_case",
    "summary_of",
]
