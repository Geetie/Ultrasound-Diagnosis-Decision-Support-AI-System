"""A.88 T4-① 守卫：影像检索腿**结构性可达**（不是"代码里有 image_data"）。

## 要修的真问题（架构真相 §3.2）

``Retriever.retrieve`` 的影像腿分支要求 ``request.image_data is not None``，
而三个生产调用点（``app/agents/knowledge.py``、``app/fusion/consistency.py``、
``app/fusion/crag.py``）**都不传字节** → 这条腿结构性不可达：开关打开也不触发，
日志里连"为什么没触发"都没有。

## 判据（不是"有代码"，是"真的进去了"）

1. **生产路径真的把字节送进 ``RetrievalRequest.image_data``**：一条路径
   （管线注入 → ContextVar/槽 → ``Retriever.retrieve``）走完，
   ``_safe_image`` 里的 ``self._clip.embed_image`` 必须**被 await 过**，
   且收到的 ``image_data`` 就是注入的那份字节。
2. **集合不存在时优雅跳过，且原因与"没送字节"分开**：
   ``probe_image_leg()`` 已知 ``collection_exists=false`` 时，
   ``build_image_data_for_retrieval`` 返回 ``capability_collection_missing``，
   **绝不**返回 ``image_bytes_unavailable`` / ``no_image_data``。
3. **病人隔离不被新接线破坏**：没有 session 一律不取图、不发检索。

## 反证腿（把实现改回去必须红）

本文件每条不变量都写了对反证：把 ``_resolve_image_leg`` 换回
``request.image_data is not None`` 直判，``TestLegIsActuallyReached`` 全组红；
把 ``capability_skip_reason`` 合并成一句默认值，``TestSkipReasons`` 红。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.track_rag]

from app.embedding.protocols import (  # noqa: E402
    EmbeddingModality,
    EmbeddingResponse,
    SearchResult,
)
from app.rag import image_leg as image_leg_module  # noqa: E402
from app.rag.image_leg import (  # noqa: E402
    IMAGE_SKIP_BYTES_UNAVAILABLE,
    IMAGE_SKIP_COLLECTION_MISSING,
    IMAGE_SKIP_DECODE_FAILED,
    IMAGE_SKIP_DIM_MISMATCH,
    IMAGE_SKIP_LEG_DISABLED,
    IMAGE_SKIP_NO_IMAGE_IDS,
    IMAGE_SKIP_NO_SESSION,
    IMAGE_SKIP_NOT_INDEXED,
    IMAGE_SKIP_PROBE_FAILED,
    ImageLegSlot,
    attach_image_leg_for_request,
    begin_image_leg_scope,
    build_image_data_for_retrieval,
    capability_skip_reason,
    end_image_leg_scope,
    reset_image_leg_slots_for_tests,
    resolve_image_leg_slot,
)
from app.rag.protocols import RetrievalRequest  # noqa: E402
from app.rag.retriever import (  # noqa: E402
    IMAGE_SKIP_CLIP_UNAVAILABLE,
    IMAGE_SKIP_MULTIMODAL_OFF,
    IMAGE_SKIP_NO_DATA,
    IMAGE_SKIP_NO_SESSION_FOR_LEG,
    Retriever,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n-a88-representative-frame"

_READY_CAP = {
    "enabled": True,
    "ready": True,
    "collection": "ultrasound_chunks",
    "collection_exists": True,
    "collection_dim": 512,
    "image_dim": 512,
}
_MISSING_CAP = {
    "enabled": True,
    "ready": False,
    "collection": "ultrasound_chunks",
    "collection_exists": False,
    "collection_dim": None,
    "image_dim": 512,
    "note": "集合不存在（ultrasound_chunks）",
}


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


# ----------------------------------------------------------------------
# 管线要加的**那一处**（交付契约的可执行形式）
# ----------------------------------------------------------------------


class TestPipelineContract:
    """``image_leg_scope_for_request`` 就是"管线里要加的那一处"。

    它把「取字节 + 登记槽 + 进入/退出作用域」收成一个 ``async with``，
    管线改动量 = 包一层缩进，不新增变量、不手写 try/finally。
    """

    async def test_scope_helper_attaches_bytes_and_exits_cleanly(self, monkeypatch):
        import app.config.database as database_module
        import app.repositories.dicom_image_repository as repo_module

        original_session_local = database_module.AsyncSessionLocal
        original_repo = repo_module.DicomImageRepository
        original_decode = image_leg_module.middle_frame_png

        class _FakeDb:
            async def __aenter__(self):
                return MagicMock()

            async def __aexit__(self, *exc_info):
                return False

        class _Repo:
            def __init__(self, db):
                self._db = db

            async def filter_existing_in_session(self, session_id, image_ids):
                return [
                    type(
                        "Row",
                        (),
                        {
                            "image_id": "img_1",
                            "storage_path": "/tmp/a88.png",
                            "frame_count": 1,
                        },
                    )()
                ]

        database_module.AsyncSessionLocal = lambda: _FakeDb()  # type: ignore[assignment]
        repo_module.DicomImageRepository = _Repo  # type: ignore[assignment]
        image_leg_module.middle_frame_png = lambda content: PNG_BYTES
        try:
            request = type(
                "Req",
                (),
                {
                    "request_id": "req-scope-1",
                    "session_id": "sess-a88",
                    "image_ids": ["img_1"],
                    "organ": "thyroid",
                },
            )()
            async with image_leg_module.image_leg_scope_for_request(
                request, capability=_READY_CAP, storage=_mk_storage(b"fake")
            ) as leg:
                assert leg.available is True
                # 作用域内：不带 request_id 的检索也能吃到字节
                clip = _mk_clip()
                r = Retriever(
                    vector_client=_mk_vector_client(),
                    embedding_service=_mk_embedding(),
                    ultrasound_clip=clip,
                )
                await r.retrieve(
                    RetrievalRequest(query="q", organ="thyroid", top_k=5),
                    enhanced_query="q",
                )
                clip.embed_image.assert_awaited_once()
                assert clip.embed_image.await_args.args[0].image_data == PNG_BYTES
            # 作用域结束后：既没有 ContextVar 残留，按 id 的槽也被清掉
            assert image_leg_module.current_image_leg_slot() is None
            assert image_leg_module.lookup_image_leg_slot("req-scope-1") is None
        finally:
            database_module.AsyncSessionLocal = original_session_local  # type: ignore[assignment]
            repo_module.DicomImageRepository = original_repo  # type: ignore[assignment]
            image_leg_module.middle_frame_png = original_decode

    async def test_scope_helper_cleans_up_even_when_body_raises(self):
        """``async with`` 的退出必须执行（否则下一次判读会串号）。"""

        class _Boom(Exception):
            pass

        request = type(
            "Req",
            (),
            {
                "request_id": "req-scope-2",
                "session_id": "sess-a88",
                "image_ids": [],
                "organ": "thyroid",
            },
        )()
        with pytest.raises(_Boom):
            async with image_leg_module.image_leg_scope_for_request(
                request, capability=_READY_CAP, storage=_mk_storage()
            ):
                raise _Boom()

        assert image_leg_module.current_image_leg_slot() is None
        assert image_leg_module.lookup_image_leg_slot("req-scope-2") is None


def _mk_clip() -> MagicMock:
    clip = MagicMock()
    clip.embed_image = AsyncMock(
        return_value=EmbeddingResponse(
            embeddings=[[0.5] * 8],
            model="ultrasound-clip",
            dimension=8,
            modality=EmbeddingModality.IMAGE,
        )
    )
    return clip


def _mk_embedding() -> MagicMock:
    emb = MagicMock()
    emb.embed_text = AsyncMock(
        return_value=EmbeddingResponse(
            embeddings=[[0.1] * 8],
            model="bge-m3-medical",
            dimension=8,
            modality=EmbeddingModality.TEXT,
        )
    )
    return emb


def _mk_vector_client() -> MagicMock:
    vc = MagicMock()
    vc.search = AsyncMock(return_value=[SearchResult(id="c1", score=0.9, metadata={})])
    return vc


def _mk_storage(content: bytes | None = PNG_BYTES) -> MagicMock:
    storage = MagicMock()
    storage.retrieve = AsyncMock(return_value=content)
    return storage


@pytest.fixture(autouse=True)
def _clean_slots():
    reset_image_leg_slots_for_tests()
    image_leg_module.reset_leg_capability_cache_for_tests()
    yield
    reset_image_leg_slots_for_tests()
    image_leg_module.reset_leg_capability_cache_for_tests()


class _FakeRepoRows:
    """模拟 ``DicomImageRepository.filter_existing_in_session`` 的返回行。"""

    def __init__(self, rows: list[dict]):
        self._rows = rows

    def filter_existing_in_session(self, session_id, image_ids):
        async def _go():
            return [type("Row", (), r) for r in self._rows]

        return _go()


# ----------------------------------------------------------------------
# ① 判据：一条路径真的把字节送进了 RetrievalRequest.image_data
# ----------------------------------------------------------------------


class TestLegIsActuallyReached:
    """**最有价值的一条**：不是"代码里有 image_data"，是分支真的进入。"""

    async def test_slot_bytes_reach_clip_embedding(self):
        """注入槽里的字节 → ``_safe_image`` → ``clip.embed_image`` 收到同一份字节。

        反证：把 ``_resolve_image_leg`` 换回 ``request.image_data is not None``
        直判 → 槽被忽略 → ``clip.embed_image`` 一次都不 await → 本用例红。
        """
        assert image_leg_module.register_image_leg_slot(
            "req-a88-1",
            ImageLegSlot(
                image_data=PNG_BYTES,
                image_format="png",
                session_id="sess-a88",
                skip_reason="",
            ),
        )
        clip = _mk_clip()
        r = Retriever(
            vector_client=_mk_vector_client(),
            embedding_service=_mk_embedding(),
            ultrasound_clip=clip,
        )
        request = RetrievalRequest(
            query="thyroid nodule",
            organ="thyroid",
            top_k=5,
            request_id="req-a88-1",
            # 关键：检索请求本身**不带** image_data（生产调用点就是这样）
        )

        result = await r.retrieve(request, enhanced_query="thyroid nodule")

        clip.embed_image.assert_awaited_once()
        sent = clip.embed_image.await_args.args[0]
        assert sent.image_data == PNG_BYTES, "送进影像腿的不是注入的那份字节"
        assert sent.image_format == "png"
        assert result.image_leg_skip_reason == ""

    async def test_context_scope_bytes_reach_clip_embedding(self):
        """不带 request_id 的知识查询链路（ContextVar 作用域）也能吃到字节。

        反证：删掉 ``resolve_image_leg_slot`` 的 ContextVar 回退 → 本用例红。
        """
        clip = _mk_clip()
        r = Retriever(
            vector_client=_mk_vector_client(),
            embedding_service=_mk_embedding(),
            ultrasound_clip=clip,
        )
        slot = ImageLegSlot(
            image_data=PNG_BYTES,
            image_format="png",
            session_id="sess-a88",
            skip_reason="",
        )
        token = begin_image_leg_scope(slot)
        try:
            request = RetrievalRequest(
                query="q", organ="thyroid", top_k=5
            )  # 无 request_id、无 image_data
            await r.retrieve(request, enhanced_query="q")
        finally:
            end_image_leg_scope(token)

        clip.embed_image.assert_awaited_once()
        assert clip.embed_image.await_args.args[0].image_data == PNG_BYTES

    async def test_end_scope_does_not_leak_to_next_request(self):
        """作用域结束后，下一次判读**不得**读到上一次的字节（跨病人串号红线）。

        反证：``end_image_leg_scope`` 改成不 reset（或注释掉）→ 本用例红。
        """
        slot = ImageLegSlot(
            image_data=PNG_BYTES, image_format="png", session_id="sess-A", skip_reason=""
        )
        token = begin_image_leg_scope(slot)
        end_image_leg_scope(token)

        clip = _mk_clip()
        r = Retriever(
            vector_client=_mk_vector_client(),
            embedding_service=_mk_embedding(),
            ultrasound_clip=clip,
        )
        request = RetrievalRequest(query="q", organ="thyroid", top_k=5)
        result = await r.retrieve(request, enhanced_query="q")

        clip.embed_image.assert_not_awaited()
        assert result.image_leg_skip_reason == IMAGE_SKIP_NO_DATA

    async def test_pipeline_entrypoint_attaches_and_scopes(self):
        """管线一行调用：``attach_image_leg_for_request`` 之后检索真的跑起来。

        这是"交付函数 + 一行接线"的端到端自证：request 对象上只有
        session_id / image_ids / organ / request_id，字节由函数自己取。
        """
        request = type(
            "Req",
            (),
            {
                "request_id": "req-a88-e2e",
                "session_id": "sess-a88",
                "image_ids": ["img_20260101_abcdef01"],
                "organ": "thyroid",
            },
        )()

        # 打桩：索引行 + 存储字节 + 解码（解码走真实 DICOM 太重，这里只验字节通路）
        import app.config.database as database_module

        original_session_local = database_module.AsyncSessionLocal

        class _FakeDb:
            async def __aenter__(self):
                return MagicMock()

            async def __aexit__(self, *exc_info):
                return False

        database_module.AsyncSessionLocal = lambda: _FakeDb()  # type: ignore[assignment]
        try:
            import app.repositories.dicom_image_repository as repo_module

            original_repo = repo_module.DicomImageRepository

            class _Repo:
                def __init__(self, db):
                    self._db = db

                async def filter_existing_in_session(self, session_id, image_ids):
                    return [
                        type(
                            "Row",
                            (),
                            {
                                "image_id": "img_20260101_abcdef01",
                                "storage_path": "/tmp/a88.png",
                                "frame_count": 1,
                            },
                        )()
                    ]

            repo_module.DicomImageRepository = _Repo  # type: ignore[assignment]
            # 解码：直接用预置 PNG（绕开 pydicom，本用例只验"字节有没有真送到"）
            original_decode = image_leg_module.middle_frame_png
            image_leg_module.middle_frame_png = lambda content: PNG_BYTES
            try:
                leg, token = await attach_image_leg_for_request(
                    request,
                    capability=_READY_CAP,
                    storage=_mk_storage(b"fake-dicom"),
                )
                assert leg.available is True
                assert leg.image_data == PNG_BYTES

                clip = _mk_clip()
                r = Retriever(
                    vector_client=_mk_vector_client(),
                    embedding_service=_mk_embedding(),
                    ultrasound_clip=clip,
                )
                # 知识查询链路就是这样调用的：**不带** image_data / request_id
                result = await r.retrieve(
                    RetrievalRequest(query="q", organ="thyroid", top_k=5),
                    enhanced_query="q",
                )
                clip.embed_image.assert_awaited_once()
                assert result.image_leg_skip_reason == ""
            finally:
                image_leg_module.middle_frame_png = original_decode
                if token is not None:
                    end_image_leg_scope(token)
        finally:
            database_module.AsyncSessionLocal = original_session_local  # type: ignore[assignment]
            repo_module.DicomImageRepository = original_repo  # type: ignore[assignment]

    async def test_explicit_image_data_still_wins(self):
        """显式 ``image_data`` 优先（老行为，直连调用方不被新机制改变）。"""
        clip = _mk_clip()
        r = Retriever(
            vector_client=_mk_vector_client(),
            embedding_service=_mk_embedding(),
            ultrasound_clip=clip,
        )
        request = RetrievalRequest(
            query="q",
            image_data=b"explicit-bytes",
            image_format="dicom",
            session_id="sess-x",
            top_k=5,
        )
        await r.retrieve(request, enhanced_query="q")
        clip.embed_image.assert_awaited_once()
        assert clip.embed_image.await_args.args[0].image_data == b"explicit-bytes"


# ----------------------------------------------------------------------
# ② 判据：每个跳过分支留**自己的**原因（不许聚合默认值）
# ----------------------------------------------------------------------


class TestSkipReasons:
    def test_capability_reasons_are_distinct(self):
        """能力不可用的四种成因各有各的码（AGENTS.md 硬规则 8）。"""
        assert capability_skip_reason(_READY_CAP) == ""
        assert (
            capability_skip_reason({**_MISSING_CAP}) == IMAGE_SKIP_COLLECTION_MISSING
        )
        assert (
            capability_skip_reason({**_MISSING_CAP, "collection_exists": None})
            == IMAGE_SKIP_PROBE_FAILED
        )
        assert (
            capability_skip_reason(
                {
                    **_READY_CAP,
                    "ready": False,
                    "collection_dim": 1024,
                    "image_dim": 512,
                }
            )
            == IMAGE_SKIP_DIM_MISMATCH
        )
        assert (
            capability_skip_reason({**_READY_CAP, "ready": False, "enabled": False})
            == IMAGE_SKIP_LEG_DISABLED
        )

    def test_capability_and_missing_bytes_are_not_the_same_code(self):
        """**核心反证**：能力不可用 ≠ 没送字节。

        反证：把 ``capability_skip_reason`` 的所有分支合并成一句
        ``image_bytes_unavailable`` → 本用例红（A.86 的原错）。
        """
        capability_codes = {
            IMAGE_SKIP_COLLECTION_MISSING,
            IMAGE_SKIP_PROBE_FAILED,
            IMAGE_SKIP_DIM_MISMATCH,
            IMAGE_SKIP_LEG_DISABLED,
        }
        request_codes = {
            IMAGE_SKIP_NO_DATA,
            IMAGE_SKIP_NO_IMAGE_IDS,
            IMAGE_SKIP_NOT_INDEXED,
            IMAGE_SKIP_BYTES_UNAVAILABLE,
            IMAGE_SKIP_DECODE_FAILED,
        }
        assert capability_codes.isdisjoint(request_codes), (
            "能力码与请求码重名 → 日志里分不清「这个部署没建集合」和「这次没送字节」"
        )
        assert all(code.startswith("capability_") for code in (
            IMAGE_SKIP_COLLECTION_MISSING,
            IMAGE_SKIP_PROBE_FAILED,
            IMAGE_SKIP_DIM_MISMATCH,
        ))

    async def test_collection_missing_skips_before_touching_storage(self):
        """集合不存在时**连盘都不读**，且原因码是能力码（不是取图失败）。

        反证：把能力闸挪到取字节之后（或删掉）→ storage.retrieve 被调 → 红。
        """
        storage = _mk_storage()
        leg = await build_image_data_for_retrieval(
            session_id="sess-a88",
            image_ids=["img_1"],
            organ="thyroid",
            capability=_MISSING_CAP,
            storage=storage,
        )

        assert leg.available is False
        assert leg.skip_reason == IMAGE_SKIP_COLLECTION_MISSING
        storage.retrieve.assert_not_awaited()
        assert leg.detail["reason"] == IMAGE_SKIP_COLLECTION_MISSING

    async def test_no_session_never_reads_image(self):
        """A.78：没有 session 一律不取图（病人隔离）。"""
        storage = _mk_storage()
        leg = await build_image_data_for_retrieval(
            session_id=None,
            image_ids=["img_1"],
            capability=_READY_CAP,
            storage=storage,
        )

        assert leg.skip_reason == IMAGE_SKIP_NO_SESSION
        storage.retrieve.assert_not_awaited()

    async def test_no_image_ids_is_its_own_reason(self):
        storage = _mk_storage()
        leg = await build_image_data_for_retrieval(
            session_id="sess-a88",
            image_ids=[],
            capability=_READY_CAP,
            storage=storage,
        )

        assert leg.skip_reason == IMAGE_SKIP_NO_IMAGE_IDS
        storage.retrieve.assert_not_awaited()

    async def test_storage_miss_reports_bytes_unavailable(self, monkeypatch):
        import app.repositories.dicom_image_repository as repo_module

        original_repo = repo_module.DicomImageRepository

        class _Repo:
            def __init__(self, db):
                self._db = db

            async def filter_existing_in_session(self, session_id, image_ids):
                return [
                    type(
                        "Row",
                        (),
                        {
                            "image_id": "img_1",
                            "storage_path": "/tmp/nope.dcm",
                            "frame_count": 1,
                        },
                    )()
                ]

        repo_module.DicomImageRepository = _Repo  # type: ignore[assignment]

        import app.config.database as database_module

        original_session_local = database_module.AsyncSessionLocal

        class _FakeDb:
            async def __aenter__(self):
                return MagicMock()

            async def __aexit__(self, *exc_info):
                return False

        database_module.AsyncSessionLocal = lambda: _FakeDb()  # type: ignore[assignment]
        try:
            leg = await build_image_data_for_retrieval(
                session_id="sess-a88",
                image_ids=["img_1"],
                capability=_READY_CAP,
                storage=_mk_storage(None),
            )
        finally:
            repo_module.DicomImageRepository = original_repo  # type: ignore[assignment]
            database_module.AsyncSessionLocal = original_session_local  # type: ignore[assignment]

        assert leg.skip_reason == IMAGE_SKIP_BYTES_UNAVAILABLE

    async def test_decode_failure_is_its_own_reason(self, monkeypatch):
        import app.repositories.dicom_image_repository as repo_module

        original_repo = repo_module.DicomImageRepository

        class _Repo:
            def __init__(self, db):
                self._db = db

            async def filter_existing_in_session(self, session_id, image_ids):
                return [
                    type(
                        "Row",
                        (),
                        {
                            "image_id": "img_1",
                            "storage_path": "/tmp/x.dcm",
                            "frame_count": 1,
                        },
                    )()
                ]

        repo_module.DicomImageRepository = _Repo  # type: ignore[assignment]

        import app.config.database as database_module

        original_session_local = database_module.AsyncSessionLocal

        class _FakeDb:
            async def __aenter__(self):
                return MagicMock()

            async def __aexit__(self, *exc_info):
                return False

        database_module.AsyncSessionLocal = lambda: _FakeDb()  # type: ignore[assignment]
        original_decode = image_leg_module.middle_frame_png
        image_leg_module.middle_frame_png = lambda content: None
        try:
            leg = await build_image_data_for_retrieval(
                session_id="sess-a88",
                image_ids=["img_1"],
                capability=_READY_CAP,
                storage=_mk_storage(b"not-a-dicom"),
            )
        finally:
            image_leg_module.middle_frame_png = original_decode
            repo_module.DicomImageRepository = original_repo  # type: ignore[assignment]
            database_module.AsyncSessionLocal = original_session_local  # type: ignore[assignment]

        assert leg.skip_reason == IMAGE_SKIP_DECODE_FAILED


# ----------------------------------------------------------------------
# ③ 检索侧解析：每个分支的码都要能到调用方手里
# ----------------------------------------------------------------------


class TestResolutionSurfacesReasons:
    async def test_multimodal_off_is_reported(self):
        r = Retriever(
            vector_client=_mk_vector_client(),
            embedding_service=_mk_embedding(),
            ultrasound_clip=_mk_clip(),
        )
        result = await r.retrieve(
            RetrievalRequest(query="q", use_multimodal=False, top_k=5),
            enhanced_query="q",
        )
        assert result.image_leg_skip_reason == IMAGE_SKIP_MULTIMODAL_OFF

    async def test_no_clip_service_is_reported(self):
        r = Retriever(
            vector_client=_mk_vector_client(),
            embedding_service=_mk_embedding(),
            ultrasound_clip=None,
        )
        result = await r.retrieve(RetrievalRequest(query="q", top_k=5), enhanced_query="q")
        assert result.image_leg_skip_reason == IMAGE_SKIP_CLIP_UNAVAILABLE

    async def test_image_without_session_is_reported_not_silent(self):
        """有字节但没 session：这条腿不跑，**结果里必须说为什么**。"""
        r = Retriever(
            vector_client=_mk_vector_client(),
            embedding_service=_mk_embedding(),
            ultrasound_clip=_mk_clip(),
        )
        result = await r.retrieve(
            RetrievalRequest(query="q", image_data=PNG_BYTES, top_k=5),
            enhanced_query="q",
        )
        assert result.image_leg_skip_reason == IMAGE_SKIP_NO_SESSION_FOR_LEG

    async def test_pipeline_skip_reason_is_passed_through_unchanged(self):
        """管线取图失败的原因码**原样**上报（不许被改写成 no_image_data）。"""
        image_leg_module.register_image_leg_slot(
            "req-a88-missing",
            ImageLegSlot(
                image_data=None,
                image_format="dicom",
                session_id="sess-a88",
                skip_reason=IMAGE_SKIP_COLLECTION_MISSING,
            ),
        )
        r = Retriever(
            vector_client=_mk_vector_client(),
            embedding_service=_mk_embedding(),
            ultrasound_clip=_mk_clip(),
        )
        result = await r.retrieve(
            RetrievalRequest(query="q", request_id="req-a88-missing", top_k=5),
            enhanced_query="q",
        )
        assert result.image_leg_skip_reason == IMAGE_SKIP_COLLECTION_MISSING

    async def test_resolve_prefers_request_id_slot_then_scope(self):
        """取槽顺序：先 request_id，后 ContextVar；两者都没有 → None。"""
        assert resolve_image_leg_slot("nope") is None

        image_leg_module.register_image_leg_slot(
            "req-1",
            ImageLegSlot(
                image_data=PNG_BYTES, image_format="png", session_id="s", skip_reason=""
            ),
        )
        assert resolve_image_leg_slot("req-1") is not None

        scoped = ImageLegSlot(
            image_data=b"other", image_format="png", session_id="s2", skip_reason=""
        )
        token = begin_image_leg_scope(scoped)
        try:
            # request_id 命中时优先返回按 id 的那条
            assert resolve_image_leg_slot("req-1").image_data == PNG_BYTES
            # 没有 id 时退回作用域
            assert resolve_image_leg_slot(None).image_data == b"other"
        finally:
            end_image_leg_scope(token)



class TestCacheCorrectnessWithImageLeg:
    """影像腿接线的**缓存安全**反证：带影像的检索不得走缓存。

    为什么这是一个安全问题而不是性能问题：影像腿贡献的 chunk 依赖
    ``session_id`` + 影像字节，而缓存键只看 ``request`` 的文本字段。
    一旦带影像的请求被缓存，同一句 query 会把 A 会话的检索结果发给 B 会话
    （A.78 病人隔离红线）。``RetrievalCache.is_cacheable`` 只看得见
    ``request.image_data``，而作用域注入时它是 None —— 所以 RAG 服务必须自己判。

    反证：把 ``RAGServiceImpl.retrieve`` 里的 ``image_leg_active`` 判断删掉
    （恢复"只看 request.image_data"）→ 本用例红。
    """

    async def test_image_scope_bypasses_retrieval_cache(self, monkeypatch):
        from app.knowledge.protocols import KnowledgeChunk
        from app.rag import service as service_module
        from app.rag.cache import RetrievalCache
        from app.rag.protocols import RetrievalMethod, RetrievalResult, ScoredChunk

        chunk = KnowledgeChunk(
            chunk_id="c1",
            kb_version="kb_v1.0.0",
            organ="thyroid",
            content="x",
            metadata={},
            embedding=None,
            source_doc_id="d",
            section_path="",
            token_count=4,
        )
        cache = RetrievalCache(redis_factory=None)
        lookups: list[str] = []
        original_get = cache.get
        original_set = cache.set

        async def _spy_get(key):
            lookups.append("get")
            return await original_get(key)

        async def _spy_set(key, result):
            lookups.append("set")
            return await original_set(key, result)

        cache.get = _spy_get  # type: ignore[method-assign]
        cache.set = _spy_set  # type: ignore[method-assign]

        enhancer = MagicMock()

        async def _enhance(query, organ_hint=None, use_llm=False):
            out = MagicMock()
            out.enhanced = query
            out.llm_enhanced = False
            return out

        enhancer.enhance = _enhance
        retriever = MagicMock()
        retriever.retrieve = AsyncMock(
            return_value=RetrievalResult(
                chunks=[
                    ScoredChunk(
                        chunk=chunk,
                        score=0.9,
                        retrieval_source=RetrievalMethod.DENSE,
                    )
                ]
            )
        )
        retriever.close = AsyncMock()
        retriever.health_check = AsyncMock(return_value={})
        reranker = MagicMock()
        reranker.close = AsyncMock()
        heuristic = MagicMock()
        heuristic.close = AsyncMock()
        bm25 = MagicMock()
        bm25.should_trigger = MagicMock(return_value=False)
        bm25._index = MagicMock()
        bm25._index.close = AsyncMock()
        svc = service_module.RAGServiceImpl(
            query_enhancer=enhancer,
            retriever=retriever,
            reranker=reranker,
            heuristic_reranker=heuristic,
            bm25_fallback=bm25,
            rerank_output_top_k=5,
            cache=cache,
        )

        slot = ImageLegSlot(
            image_data=PNG_BYTES, image_format="png", session_id="sess-A", skip_reason=""
        )
        token = begin_image_leg_scope(slot)
        try:
            await svc.retrieve(
                RetrievalRequest(query="q", organ="thyroid", top_k=5, use_reranking=False)
            )
        finally:
            end_image_leg_scope(token)

        assert lookups == [], (
            "带影像腿输入的检索走进了缓存（读或写）——会把 A 会话的结果发给 B 会话"
        )

    async def test_plain_text_request_still_uses_cache(self):
        """反证的另一半：不带影像时缓存照常工作（没把缓存整体关掉）。"""
        from app.rag import service as service_module
        from app.rag.cache import RetrievalCache
        from app.rag.protocols import RetrievalResult

        cache = RetrievalCache(redis_factory=None)
        lookups: list[str] = []
        original_get = cache.get
        original_set = cache.set

        async def _spy_get(key):
            lookups.append("get")
            return await original_get(key)

        async def _spy_set(key, result):
            lookups.append("set")
            return await original_set(key, result)

        cache.get = _spy_get  # type: ignore[method-assign]
        cache.set = _spy_set  # type: ignore[method-assign]

        enhancer = MagicMock()

        async def _enhance(query, organ_hint=None, use_llm=False):
            out = MagicMock()
            out.enhanced = query
            out.llm_enhanced = False
            return out

        enhancer.enhance = _enhance
        retriever = MagicMock()
        retriever.retrieve = AsyncMock(return_value=RetrievalResult(chunks=[]))
        retriever.close = AsyncMock()
        retriever.health_check = AsyncMock(return_value={})
        reranker = MagicMock()
        reranker.close = AsyncMock()
        heuristic = MagicMock()
        heuristic.close = AsyncMock()
        bm25 = MagicMock()
        bm25.should_trigger = MagicMock(return_value=False)
        bm25._index = MagicMock()
        bm25._index.close = AsyncMock()
        svc = service_module.RAGServiceImpl(
            query_enhancer=enhancer,
            retriever=retriever,
            reranker=reranker,
            heuristic_reranker=heuristic,
            bm25_fallback=bm25,
            rerank_output_top_k=5,
            cache=cache,
        )
        await svc.retrieve(
            RetrievalRequest(query="q", organ="thyroid", top_k=5, use_reranking=False)
        )
        assert "get" in lookups, "普通文本检索不再查缓存（缓存被整体关掉了）"
        assert "set" in lookups, "普通文本检索不再写缓存"


def test_no_module_level_asyncio_primitive_added():
    """新接线不许引入模块级 asyncio 原语（OBS 轨那道守卫的同口径自检）。"""
    source = image_leg_module.__file__
    with open(source, encoding="utf-8") as fh:
        text = fh.read()
    assert "asyncio.Lock()" not in text
    assert "asyncio.Event()" not in text
    assert asyncio.get_event_loop_policy  # 保持 asyncio 导入不被 lint 判成未使用
