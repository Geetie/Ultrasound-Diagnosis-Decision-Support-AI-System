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

import time
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from app.config.settings import settings

logger = structlog.get_logger(__name__)

#: 同一条告警只打一次（每请求都打会把日志淹掉，反而没人看得见）
_WARNED: set[str] = set()

_PROBE_TIMEOUT_SECONDS = 2.0

#: 能力探测结果缓存（避免每次判读都往 Qdrant 发一次 HTTP）。
_CAPABILITY_TTL_SECONDS = 300.0
_capability_cache: tuple[float, dict] | None = None


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


# ----------------------------------------------------------------------
# A.88 T4-①：影像检索腿**注入点契约**
# ----------------------------------------------------------------------
#
# ## 为什么需要这个函数
#
# `Retriever.retrieve` 早在 `app/rag/retriever.py` 就有影像腿分支，条件是
# ``request.use_multimodal and self._clip is not None and request.image_data is not None``
# —— 三个生产调用点（``app/agents/knowledge.py``、``app/fusion/consistency.py``、
# ``app/fusion/crag.py``）**没有一个传 ``image_data``**，所以这条腿结构性不可达：
# 开关打开也不会触发，日志里也看不出任何痕迹。
#
# 本函数把"取本次判读的影像字节"这一步做成**可被管线一行调用**的公开契约：
#
# ```python
# leg = await build_image_data_for_retrieval(
#     session_id=request.session_id,
#     image_ids=getattr(request, "image_ids", None),
#     organ=request.organ,
# )
# ```
#
# ## 两种"没送字节"必须分开留痕（AGENTS.md 硬规则 8）
#
# * **能力不可用**（集合缺失 / 维度不匹配 / 探测失败 / 开关关闭）—— 就算把字节
#   送进检索也没用，属于**配置/部署缺陷**，口径是 ``capability_*``；
# * **这次没取到字节**（没 image_ids / 没 session / 索引里没有 / 磁盘上没有 /
#   解码失败）—— 属于**本次请求的输入问题**，口径是 ``*_unavailable``。
#
# 把两者混成一个 ``image_bytes_unavailable`` 会重演 A.86 的错：医生被告知
# "系统取不到影像"，实际是"这个部署根本没建影像向量集合"。
#
# ## 病人隔离
#
# 没有 ``session_id`` 一律不取图（``no_session_id``）：影像只在"本次检查会话"
# 范围内有意义，跨会话取图会让影像腿召回别的病人的影像（A.78 红线）。

#: 影像腿跳过原因码（每个分支自己的原因，不许聚合到一个默认值）。
IMAGE_SKIP_LEG_DISABLED = "leg_disabled"
IMAGE_SKIP_COLLECTION_MISSING = "capability_collection_missing"
IMAGE_SKIP_DIM_MISMATCH = "capability_dimension_mismatch"
IMAGE_SKIP_PROBE_FAILED = "capability_probe_failed"
IMAGE_SKIP_NO_SESSION = "no_session_id"
IMAGE_SKIP_NO_IMAGE_IDS = "no_image_ids"
IMAGE_SKIP_NOT_INDEXED = "image_not_indexed"
IMAGE_SKIP_BYTES_UNAVAILABLE = "image_bytes_unavailable"
IMAGE_SKIP_DECODE_FAILED = "frame_decode_failed"


@dataclass(frozen=True)
class ImageLegInput:
    """影像腿输入（送 ``RetrievalRequest.image_data`` 的那份字节 + 为何有/没有）。

    Attributes:
        image_data: PNG 字节；``None`` 表示本次没有可送的数据（见 ``skip_reason``）。
        image_format: 与 ``image_data`` 配套的格式提示（成功时为 ``"png"``）。
        skip_reason: 跳过原因码；成功时为空串。
        detail: 结构化细节（``image_id`` / ``reason`` / ``note``），供日志与测试读取。
    """

    image_data: bytes | None = None
    image_format: str = "dicom"
    skip_reason: str = ""
    detail: dict | None = None

    @property
    def available(self) -> bool:
        return self.image_data is not None and self.skip_reason == ""


async def get_leg_capability(*, refresh: bool = False) -> dict:
    """取影像腿能力状态（带 TTL 缓存，避免每次判读都往 Qdrant 发 HTTP）。

    缓存只影响"能力判定"这一次探测的时效；``probe_image_leg`` 本身失败不抛。
    """
    global _capability_cache
    now = time.monotonic()
    if not refresh and _capability_cache is not None:
        stamp, payload = _capability_cache
        if now - stamp < _CAPABILITY_TTL_SECONDS:
            return payload
    payload = await probe_image_leg()
    _capability_cache = (now, payload)
    return payload


def reset_leg_capability_cache_for_tests() -> None:
    """仅供单测重置能力缓存（生产不调用）。"""
    global _capability_cache
    _capability_cache = None


def capability_skip_reason(capability: dict) -> str:
    """把能力探测结果翻译成**具体**跳过原因码（纯函数，守卫钉住）。

    ``ready=True`` 返回空串。四个分支各有自己的码，**不许**归并成一句
    "影像腿不可用"——那会让"集合没建"和"维度错配"在日志里无法区分。
    """
    if capability.get("ready"):
        return ""
    if not capability.get("enabled", True):
        return IMAGE_SKIP_LEG_DISABLED
    exists = capability.get("collection_exists")
    if exists is None:
        return IMAGE_SKIP_PROBE_FAILED
    if exists is False:
        return IMAGE_SKIP_COLLECTION_MISSING
    collection_dim = capability.get("collection_dim")
    image_dim = capability.get("image_dim")
    if collection_dim and image_dim and int(collection_dim) != int(image_dim):
        return IMAGE_SKIP_DIM_MISMATCH
    # 探测说"未就绪"但原因不在上面四种 → 不编造原因，原样上报未就绪。
    return IMAGE_SKIP_PROBE_FAILED


def middle_frame_png(content: bytes) -> bytes | None:
    """DICOM 字节 → **中间帧** PNG（ULC 只吃图片）。解码失败返回 ``None``。

    帧选取口径与 ``app/services/similar_cases.py::frame_png_of`` 一致
    （首/中/末里的中间那帧），两处共用一个纯函数
    ``app.imaging.dicom_structure.frame_indices``，不各自实现一遍。
    """
    try:
        from app.imaging.dicom_pixels import extract_frame_from_bytes
        from app.imaging.dicom_structure import frame_indices

        first = extract_frame_from_bytes(content, None)
        picks = frame_indices(first.total_frames)
        return extract_frame_from_bytes(content, picks[len(picks) // 2]).png_bytes
    except Exception as exc:
        logger.warning(
            "image_leg_frame_decode_failed",
            error=str(exc)[:160],
            error_type=type(exc).__name__,
        )
        return None


async def build_image_data_for_retrieval(
    *,
    session_id: str | None,
    image_ids: list[str] | None,
    organ: str | None = None,
    capability: dict | None = None,
    storage: Any = None,
) -> ImageLegInput:
    """取「本次判读的代表帧 PNG 字节」，供 ``RetrievalRequest.image_data``。

    **这是管线只需一行调用的注入点契约**（见本模块 A.88 T4-① 段注释）：

    ```python
    leg = await build_image_data_for_retrieval(
        session_id=request.session_id,
        image_ids=getattr(request, "image_ids", None),
        organ=request.organ,
    )
    ```

    之后把 ``leg.image_data`` / ``leg.image_format`` 传进知识查询
    （``KnowledgeAgent.query(image_data=...)``）即可让 ``retriever._safe_image``
    真正执行；``leg.skip_reason`` 非空时把它写进 ``augmentation_skip_reason``。

    本函数**永不抛异常**：任何一个环节失败都返回带原因码的 ``ImageLegInput``，
    并留 ``warning`` 日志（影像腿是增强能力，不能阻断判读）。

    Args:
        session_id: 本次检查会话（**必需**，病人隔离；空 → ``no_session_id``）。
        image_ids: ``diagnosis_results.image_ids``（客户端提交的影像 ID 列表）。
        organ: 目标器官（仅用于日志与后续过滤，不参与取图）。
        capability: 能力探测结果（默认走 ``get_leg_capability()``；测试可注入）。
        storage: ``DicomStorageService`` 兼容对象（默认 ``get_dicom_storage()``）。

    Returns:
        :class:`ImageLegInput`。``skip_reason`` 为空即成功。
    """
    # ① 能力闸：能力不可用时**先**判掉，而不是取完字节再发现没人要。
    #    这一步必须排在取字节之前：集合不存在时读盘/解码都属纯浪费，
    #    且日志里必须体现"是能力问题"而不是"影像取不到"。
    cap: dict = {}
    try:
        cap = dict(capability) if capability is not None else await get_leg_capability()
    except Exception as exc:  # 探测本身抛异常 → 归类为 probe_failed，不臆造成功
        logger.warning(
            "image_leg_capability_probe_error",
            error=str(exc)[:160],
            error_type=type(exc).__name__,
        )
        cap = {"ready": False, "enabled": True, "collection_exists": None}

    reason = capability_skip_reason(cap)
    if reason:
        logger.info(
            "image_retrieval_leg_skipped",
            reason=reason,
            collection=cap.get("collection"),
            collection_exists=cap.get("collection_exists"),
            collection_dim=cap.get("collection_dim"),
            image_dim=cap.get("image_dim"),
            note=cap.get("note"),
        )
        return ImageLegInput(
            skip_reason=reason,
            detail={
                "reason": reason,
                "note": cap.get("note", ""),
                "collection": cap.get("collection"),
            },
        )

    # ② 病人隔离：无 session 不取图（宁可这条腿空，也不跨会话取）。
    if not session_id or not str(session_id).strip():
        logger.info("image_retrieval_leg_skipped", reason=IMAGE_SKIP_NO_SESSION)
        return ImageLegInput(
            skip_reason=IMAGE_SKIP_NO_SESSION,
            detail={"reason": IMAGE_SKIP_NO_SESSION},
        )

    ids = [str(i) for i in (image_ids or []) if str(i).strip()]
    if not ids:
        logger.info(
            "image_retrieval_leg_skipped",
            reason=IMAGE_SKIP_NO_IMAGE_IDS,
            session_id=session_id,
            organ=organ,
        )
        return ImageLegInput(
            skip_reason=IMAGE_SKIP_NO_IMAGE_IDS,
            detail={"reason": IMAGE_SKIP_NO_IMAGE_IDS},
        )

    # ③ 索引 → storage_path（只认属于本次 session 的行）。
    try:
        from app.config.database import AsyncSessionLocal
        from app.repositories.dicom_image_repository import DicomImageRepository

        async with AsyncSessionLocal() as db:
            rows = await DicomImageRepository(db).filter_existing_in_session(
                str(session_id), ids
            )
            # 行对象在会话关闭后仍可读标量属性（已加载），先摘出需要的字段，
            # 避免把 ORM 实例带出 session 造成延迟加载问题。
            candidates = [
                {
                    "image_id": str(row.image_id),
                    "storage_path": str(row.storage_path),
                    "frame_count": int(getattr(row, "frame_count", 1) or 1),
                }
                for row in rows
            ]
    except Exception as exc:
        logger.warning(
            "image_leg_row_lookup_failed",
            session_id=session_id,
            error=str(exc)[:160],
            error_type=type(exc).__name__,
        )
        return ImageLegInput(
            skip_reason=IMAGE_SKIP_NOT_INDEXED,
            detail={"reason": IMAGE_SKIP_NOT_INDEXED, "error": type(exc).__name__},
        )

    if not candidates:
        logger.info(
            "image_retrieval_leg_skipped",
            reason=IMAGE_SKIP_NOT_INDEXED,
            session_id=session_id,
            requested=ids[:5],
        )
        return ImageLegInput(
            skip_reason=IMAGE_SKIP_NOT_INDEXED,
            detail={"reason": IMAGE_SKIP_NOT_INDEXED, "requested": ids[:5]},
        )

    # ④ 取字节 → 解码代表帧。逐张试，**每张失败都记自己的原因**（同 A.86 教训：
    #    用 continue 丢掉原因，最后统一报一个码，医生就被告知错的原因）。
    if storage is None:
        from app.services.dicom_storage import get_dicom_storage

        storage = get_dicom_storage()

    skips: list[dict] = []
    for cand in candidates:
        try:
            content = await storage.retrieve(cand["storage_path"])
        except Exception as exc:
            logger.warning(
                "image_leg_storage_retrieve_failed",
                image_id=cand["image_id"],
                error=str(exc)[:160],
                error_type=type(exc).__name__,
            )
            skips.append({"image_id": cand["image_id"], "reason": IMAGE_SKIP_BYTES_UNAVAILABLE})
            continue
        if not content:
            skips.append({"image_id": cand["image_id"], "reason": IMAGE_SKIP_BYTES_UNAVAILABLE})
            continue
        png = middle_frame_png(content)
        if png is None:
            skips.append({"image_id": cand["image_id"], "reason": IMAGE_SKIP_DECODE_FAILED})
            continue
        logger.info(
            "image_retrieval_leg_ready",
            image_id=cand["image_id"],
            organ=organ,
            bytes=len(png),
            skipped=len(skips),
        )
        return ImageLegInput(
            image_data=png,
            image_format="png",
            detail={
                "image_id": cand["image_id"],
                "bytes": len(png),
                "frame": "middle",
                "skipped": skips,
            },
        )

    # ⑤ 全部候选都失败：**逐条**原因都带上，最后一条作为主原因码。
    primary = skips[-1]["reason"] if skips else IMAGE_SKIP_BYTES_UNAVAILABLE
    logger.warning(
        "image_retrieval_leg_skipped",
        reason=primary,
        session_id=session_id,
        skips=skips,
    )
    return ImageLegInput(
        skip_reason=primary,
        detail={"reason": primary, "skips": skips},
    )


# ----------------------------------------------------------------------
# 请求级注入槽（为什么需要它）
# ----------------------------------------------------------------------
#
# 影像腿要求 ``RetrievalRequest.image_data``，而三个生产调用点都在**别人的文件**里
# （``app/agents/specialist/base.py`` / ``app/fusion/consistency.py`` /
# ``app/fusion/crag.py``），且 ``AgentContext`` 没有承载二进制的字段。本轮修复的
# 写权限只覆盖 ``app/rag/**`` / ``app/knowledge/**`` / ``app/observability``，
# 所以按任务约定把「注入点」做成**管线一行可调用的公开函数**：
#
# ```python
# # app/services/diagnosis_pipeline.py 里，dispatch 之前加这一行
# await attach_image_leg_for_request(request)
# ```
#
# ``Retriever._safe_image`` 在 ``request.image_data is None`` 时按
# ``request.request_id`` 回查本槽 —— 于是知识查询 / 一致性 / CRAG 三条检索入口
# **都不用改**就都能吃到这份字节（它们都透传了 request_id）。
#
# ## 代价（必须写清，不许当成"无痛接线"）
#
# 这是**请求级隐式状态**，不是显式参数传递：
# * 优点：一条产线调用即可生效，不必改 4 个文件；
# * 风险：① 管线漏调 → 影像腿照旧不触发（此时日志给 ``no_image_data``，
#   与"能力不可用"分得开）；② 槽按 request_id 索引，request_id 为空时不登记
#   （宁可不注入，也不按 None 串号）；③ 必须**有界**，否则长跑进程会涨内存
#   —— 故设 TTL + 容量上限，写入时顺手过期清理。
#
# 真正干净的长期方案是把字节经 ``AgentContext`` 显式传下去，那需要改
# ``app/agents/protocols.py`` 与 ``app/agents/specialist/base.py``（本轮写权限之外）。

#: 注入槽存活时间（秒）。一次判读从 pipeline 到检索是秒级，60s 足够且能自愈。
_SLOT_TTL_SECONDS = 60.0
#: 注入槽容量上限（超出按写入时间淘汰最旧的）。
_SLOT_MAX_ENTRIES = 256

#: request_id → (写入时刻, 注入内容)
_IMAGE_SLOTS: dict[str, tuple[float, ImageLegSlot]] = {}

#: **当前判读**的注入内容（ContextVar）。
#:
#: 为什么除了按 request_id 的槽还要有它：知识查询链路
#: （``app/agents/knowledge.py::_call_rag``）构造 ``RetrievalRequest`` 时**不带
#: ``request_id`` 也不带 ``session_id``**（``KnowledgeAgent.query`` 的签名里没有
#: 这两个参数，属协议层，本轮写权限之外）。ContextVar 在同一协程调用链内自动
#: 可见：管线 set 之后，``_safe_dispatch`` → specialist → KnowledgeAgent → RAG
#: → retriever 都是同一 task 的后代，天然继承；不同 worker 协程各自一份 context，
#: **并发判读之间不会串号**。
#:
#: **但同一 worker task 会被复用**（``diagnosis_worker._worker_loop`` 顺序消费
#: 多条请求），所以 set 之后必须 reset —— 否则上一次判读的槽会留在 context 里，
#: 下一条没有影像的请求可能读到它的字节（**跨病人串号**，A.78 红线）。
#: ``begin_image_leg_scope`` 返回 token，管线必须在 ``finally`` 里
#: ``end_image_leg_scope(token)``（见该函数 docstring 的接入示例）。
_CURRENT_SLOT: ContextVar[tuple[float, ImageLegSlot] | None] = ContextVar(
    "image_leg_current_slot", default=None
)


@dataclass(frozen=True)
class ImageLegSlot:
    """一条判读的影像腿注入内容（字节 + 该次请求的 session 范围）。"""

    image_data: bytes | None
    image_format: str
    session_id: str
    skip_reason: str
    detail: dict | None = None


def _prune_slots(now: float) -> None:
    """过期条目清理（写路径顺手做，不另起任务）。"""
    expired = [
        key
        for key, (stamp, _) in _IMAGE_SLOTS.items()
        if now - stamp >= _SLOT_TTL_SECONDS
    ]
    for key in expired:
        _IMAGE_SLOTS.pop(key, None)
    # 容量上限：按写入时刻淘汰最旧的（TTL 内的极端热并发也不至于无界）
    while len(_IMAGE_SLOTS) > _SLOT_MAX_ENTRIES:
        oldest = min(_IMAGE_SLOTS.items(), key=lambda kv: kv[1][0])[0]
        _IMAGE_SLOTS.pop(oldest, None)


def register_image_leg_slot(request_id: str | None, slot: ImageLegSlot) -> bool:
    """登记一条注入内容；``request_id`` 为空时**拒绝登记**并返回 ``False``。

    拒绝而不是回退到"全局单槽"：回退会在并发判读时把 A 病人的影像检索
    套到 B 病人身上（A.78 红线）。
    """
    key = str(request_id or "").strip()
    if not key:
        warn_once(
            "image_leg_slot_no_request_id",
            "image_leg_slot_rejected_no_request_id",
            hint="管线必须带 request_id 才能按请求隔离注入影像字节",
        )
        return False
    now = time.monotonic()
    _prune_slots(now)
    _IMAGE_SLOTS[key] = (now, slot)
    return True


def lookup_image_leg_slot(request_id: str | None) -> ImageLegSlot | None:
    """按 ``request_id`` 取注入内容；过期/未登记返回 ``None``。"""
    key = str(request_id or "").strip()
    if not key:
        return None
    entry = _IMAGE_SLOTS.get(key)
    if entry is None:
        return None
    stamp, slot = entry
    if time.monotonic() - stamp >= _SLOT_TTL_SECONDS:
        _IMAGE_SLOTS.pop(key, None)
        return None
    return slot


def clear_image_leg_slot(request_id: str | None) -> None:
    """清除一条注入内容（判读结束/失败时调用，避免槽位白占）。"""
    key = str(request_id or "").strip()
    if key:
        _IMAGE_SLOTS.pop(key, None)


def begin_image_leg_scope(slot: ImageLegSlot) -> Token:
    """把注入内容放进**当前任务上下文**（供不带 request_id 的知识查询链路读到）。

    必须与 :func:`end_image_leg_scope` 成对使用（``try/finally``）：

    ```python
    # app/services/diagnosis_pipeline.py（_safe_dispatch 之前）
    leg = await build_image_data_for_retrieval(...)
    token = begin_image_leg_scope(slot_of(leg, request.session_id))
    try:
        ...dispatch / fusion...
    finally:
        end_image_leg_scope(token)
    ```
    """
    return _CURRENT_SLOT.set((time.monotonic(), slot))


def end_image_leg_scope(token: Token) -> None:
    """恢复上一层上下文（漏调会让下一次判读读到上一次的字节）。"""
    try:
        _CURRENT_SLOT.reset(token)
    except ValueError as exc:
        # token 属于别的 context（跨任务 reset）：无法恢复，但要留痕并主动清空，
        # 宁可不注入也不能让残留槽串到下一次判读。
        logger.warning(
            "image_leg_scope_reset_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        _CURRENT_SLOT.set(None)


def current_image_leg_slot() -> ImageLegSlot | None:
    """取当前任务的注入内容；过期/未设置返回 ``None``。"""
    entry = _CURRENT_SLOT.get()
    if entry is None:
        return None
    stamp, slot = entry
    if time.monotonic() - stamp >= _SLOT_TTL_SECONDS:
        return None
    return slot


def resolve_image_leg_slot(request_id: str | None) -> ImageLegSlot | None:
    """检索侧取槽：**先按 request_id，再退回当前任务上下文**。

    两条路都留原因由调用方负责（``None`` = 管线压根没接线/已过期）。
    """
    slot = lookup_image_leg_slot(request_id)
    if slot is not None:
        return slot
    return current_image_leg_slot()


def reset_image_leg_slots_for_tests() -> None:
    """仅供单测清空注入槽（生产不调用）。"""
    _IMAGE_SLOTS.clear()
    _CURRENT_SLOT.set(None)


@asynccontextmanager
async def image_leg_scope_for_request(
    request: Any,
    *,
    capability: dict | None = None,
    storage: Any = None,
):
    """**管线唯一需要加的一处**：把本次判读的影像字节挂到请求作用域上。

    用法（``app/services/diagnosis_pipeline.py``，把 dispatch + fusion 段包起来）::

        async with image_leg_scope_for_request(request):
            ...  # dispatcher.invoke / fusion.fuse（原样不动）

    为什么用 ``async with`` 而不是"调一次就不管"：worker 协程会被复用
    （``diagnosis_worker._worker_loop`` 顺序消费多条请求），作用域漏退出会让
    下一次判读读到上一次的字节（**跨病人串号**，A.78 红线）。``async with``
    由解释器保证退出逻辑一定执行，比手写 try/finally 少一个出错点。

    ``yield`` 出 :class:`ImageLegInput`（取到或没取到都返回，带 ``skip_reason``）
    —— 需要把跳过原因写进报告/留痕的调用方可以读它。

    本函数永不抛异常（``build_image_data_for_retrieval`` 自身已把失败归到原因码）。
    """
    leg, _ignored = await attach_image_leg_for_request(
        request, capability=capability, storage=storage, enter_scope=False
    )
    scope_token = begin_image_leg_scope(
        ImageLegSlot(
            image_data=leg.image_data,
            image_format=leg.image_format,
            session_id=str(getattr(request, "session_id", "") or ""),
            skip_reason=leg.skip_reason,
            detail=leg.detail,
        )
    )
    try:
        yield leg
    finally:
        end_image_leg_scope(scope_token)
        clear_image_leg_slot(getattr(request, "request_id", None))


async def attach_image_leg_for_request(
    request: Any,
    *,
    capability: dict | None = None,
    storage: Any = None,
    enter_scope: bool = True,
) -> tuple[ImageLegInput, Token | None]:
    """**管线一行调用即可**的注入口：取字节、登记槽、并进入请求作用域。

    在 ``app/services/diagnosis_pipeline.py`` 的 ``_safe_dispatch`` 之前加：

    ```python
    leg, leg_token = await attach_image_leg_for_request(request)
    try:
        ...  # dispatch + fusion
    finally:
        end_image_leg_scope(leg_token)   # 缺失会让下一次判读读到上一次的字节
    ```

    语义：
    * 取到字节 → 登记按 ``request_id`` 的槽 + 进入 ContextVar 作用域
      （后者让不带 request_id 的知识查询链路也能读到，见 ``_CURRENT_SLOT`` 注释）；
    * 没取到 / 能力不可用 → **也登记**（带 ``skip_reason``）。这样检索侧能报出
      "这次没送字节，原因是 X"，而不是笼统一句"没有 image_data"。

    本函数永不抛异常（任何异常都归到 ``skip_reason`` 并留痕）。
    """
    try:
        leg = await build_image_data_for_retrieval(
            session_id=getattr(request, "session_id", None),
            image_ids=list(getattr(request, "image_ids", None) or []),
            organ=getattr(request, "organ", None),
            capability=capability,
            storage=storage,
        )
    except Exception as exc:
        logger.warning(
            "image_leg_attach_failed",
            error=str(exc)[:160],
            error_type=type(exc).__name__,
        )
        leg = ImageLegInput(
            skip_reason=IMAGE_SKIP_BYTES_UNAVAILABLE,
            detail={"reason": IMAGE_SKIP_BYTES_UNAVAILABLE, "error": type(exc).__name__},
        )

    slot = ImageLegSlot(
        image_data=leg.image_data,
        image_format=leg.image_format,
        session_id=str(getattr(request, "session_id", "") or ""),
        skip_reason=leg.skip_reason,
        detail=leg.detail,
    )
    register_image_leg_slot(getattr(request, "request_id", None), slot)
    token = begin_image_leg_scope(slot) if enter_scope else None
    logger.info(
        "image_leg_attached",
        request_id=getattr(request, "request_id", None),
        ready=leg.available,
        skip_reason=leg.skip_reason or None,
        bytes=len(leg.image_data) if leg.image_data else 0,
    )
    return leg, token


__all__ = [
    "IMAGE_SKIP_BYTES_UNAVAILABLE",
    "IMAGE_SKIP_COLLECTION_MISSING",
    "IMAGE_SKIP_DECODE_FAILED",
    "IMAGE_SKIP_DIM_MISMATCH",
    "IMAGE_SKIP_LEG_DISABLED",
    "IMAGE_SKIP_NOT_INDEXED",
    "IMAGE_SKIP_NO_IMAGE_IDS",
    "IMAGE_SKIP_NO_SESSION",
    "IMAGE_SKIP_PROBE_FAILED",
    "ImageLegInput",
    "ImageLegSlot",
    "attach_image_leg_for_request",
    "begin_image_leg_scope",
    "build_image_data_for_retrieval",
    "capability_skip_reason",
    "classify_image_leg",
    "clear_image_leg_slot",
    "current_image_leg_slot",
    "end_image_leg_scope",
    "get_leg_capability",
    "image_leg_scope_for_request",
    "lookup_image_leg_slot",
    "middle_frame_png",
    "probe_image_leg",
    "register_image_leg_slot",
    "reset_image_leg_slots_for_tests",
    "reset_leg_capability_cache_for_tests",
    "reset_warned_for_tests",
    "resolve_image_leg_slot",
    "warn_once",
]
