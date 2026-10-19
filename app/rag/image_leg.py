"""图像检索腿的能力探测（A.68 / R1）。

**为什么需要它**：`settings.rag_image_collection` 指向的集合在真机上根本不存在
（Qdrant 里只有 `ultrasound_knowledge_chunks`，1024 维；而 ULC 服务报 512 维），
但 `rag_use_multimodal` 默认开着 → 每个带图请求都会在 `_safe_image` 里走
`image_collection_not_found_skipping` 然后返回空；日志只有 **info** 级 → 生产上看不见
"这条腿一直在空转"。这与 A.63 的"图谱邻居被阈值静默吃掉"是同一类病：
**看起来在工作，实际零贡献**。

本模块做两件事：
1. `classify_image_leg(...)`：纯函数，把「开关 / 集合是否存在 / 两边维度」判成
   `ready` 与一句人话原因 —— 可单测，不需要网络；
2. `probe_image_leg()`：真去 Qdrant 问一次（2 秒超时），供 `/health` 与启动日志使用。

裁决口径（A.68 R1 选 C）：**默认关闭**（`rag_use_multimodal=False`），
只有显式请求 + 集合就绪时才跑；能力状态必须在 health 里可见，**不许静默空转**。
"""

from __future__ import annotations

import httpx
import structlog

from app.config.settings import settings

logger = structlog.get_logger(__name__)

#: 同一条告警只打一次（每请求都打会把日志淹掉，反而没人看得见）
_WARNED: set[str] = set()

_PROBE_TIMEOUT_SECONDS = 2.0


def warn_once(key: str, event: str, **fields: object) -> None:
    """按 key 去重的告警（用于"每请求都会发生"的降级路径）。"""
    if key in _WARNED:
        return
    _WARNED.add(key)
    logger.warning(event, **fields)


def reset_warned_for_tests() -> None:
    """仅供单测重置去重集合（生产不调用）。"""
    _WARNED.clear()


def classify_image_leg(
    *,
    enabled: bool,
    collection: str,
    collection_exists: bool | None,
    collection_dim: int | None,
    image_dim: int | None,
) -> dict:
    """把探测结果判成「能不能用 + 一句人话原因」。纯函数。

    ``collection_exists=None`` 表示探测失败（例如 Qdrant 不可达），与"确实不存在"分开，
    否则会把"探不到"读成"没有集合"。
    """
    result: dict = {
        "enabled": enabled,
        "collection": collection,
        "collection_exists": collection_exists,
        "collection_dim": collection_dim,
        "image_dim": image_dim,
        "ready": False,
        "note": "",
    }
    if not enabled:
        result["note"] = (
            "开关关闭（rag_use_multimodal=false）：带图请求不会触发图像检索，这是当前默认"
        )
        return result
    if collection_exists is None:
        result["note"] = "无法探测集合（Qdrant 不可达或超时）→ 按不可用处理"
        return result
    if not collection_exists:
        result["note"] = f"集合不存在（{collection}）→ 带图请求会跳过这条腿，结果里没有它的贡献"
        return result
    if collection_dim and image_dim and collection_dim != image_dim:
        result["note"] = (
            f"维度不匹配（集合 {collection_dim} 维 / 图像 {image_dim} 维）→ 检索会失败或返回空"
        )
        return result
    result["ready"] = True
    result["note"] = "可用：集合存在且维度一致"
    return result


async def probe_image_leg() -> dict:
    """真去 Qdrant 查一次集合信息（只读，2 秒超时；失败不抛）。

    返回结构与 `classify_image_leg` 一致，另带 ``probe_error``（探测失败原因，便于运维）。
    """
    name = settings.rag_image_collection
    enabled = bool(settings.rag_use_multimodal)
    base = f"http://{settings.qdrant_host}:{settings.qdrant_port}"
    headers = {"api-key": settings.qdrant_api_key} if settings.qdrant_api_key else {}

    exists: bool | None = None
    dim: int | None = None
    probe_error = ""
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_SECONDS, headers=headers) as client:
            resp = await client.get(f"{base}/collections/{name}")
            if resp.status_code == 404:
                exists = False
            elif resp.status_code == 200:
                exists = True
                info = (resp.json() or {}).get("result") or {}
                vectors = ((info.get("config") or {}).get("params") or {}).get("vectors")
                if isinstance(vectors, dict) and "size" in vectors:
                    dim = int(vectors["size"])
                elif isinstance(vectors, dict) and vectors:
                    # 命名向量：取第一个的 size（只用于展示，不参与判定）
                    first = next(iter(vectors.values()))
                    if isinstance(first, dict) and "size" in first:
                        dim = int(first["size"])
            else:
                probe_error = f"HTTP {resp.status_code}"
    except Exception as exc:
        probe_error = f"{type(exc).__name__}: {exc}"

    result = classify_image_leg(
        enabled=enabled,
        collection=name,
        collection_exists=exists,
        collection_dim=dim,
        image_dim=int(getattr(settings, "embedding_dim", 0) or 0) or None,
    )
    if probe_error:
        result["probe_error"] = probe_error
    return result
