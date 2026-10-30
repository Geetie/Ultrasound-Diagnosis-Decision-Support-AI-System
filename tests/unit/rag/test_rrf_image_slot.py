"""01-1 RRF 分槽缺陷修复的守卫：影像腿与 dense 腿各贡献各的 rank。

缺陷（01 号 §01-1 ②#3）：影像腿与 dense 腿共用 RetrievalMethod.DENSE 作
RRF 分槽 key，_rrf_fuse 把两条腿拼成同一条 rank 序列互相挤位 —— 高分
dense 块把影像块推到 rank 20+，k=60 下分差被压平，影像腿即便命中也在
top-5 之外（"总数不变"型假绿）。

验收判据（用户拍板）：
1. 分槽后两腿结果共存：top-5 同时含 dense 块与影像块；
2. 影像块名次由本腿内 rank 决定（1/(60+1)），不再被 dense 腿长度压扁。
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.knowledge.protocols import KnowledgeChunk
from app.rag import retriever as retriever_module
from app.rag.protocols import RetrievalMethod, RetrievalRequest
from app.rag.retriever import Retriever


def _hit(chunk_id: str, score: float, source: RetrievalMethod):
    return retriever_module._Hit(
        chunk_id=chunk_id,
        chunk=KnowledgeChunk(
            chunk_id=chunk_id,
            kb_version="kb_v1.0.0",
            organ="thyroid",
            content="c",
            metadata={},
            embedding=None,
            source_doc_id="d",
            section_path="",
            token_count=1,
        ),
        score=score,
        source=source,
    )


def _retriever() -> Retriever:
    return Retriever(vector_client=MagicMock(), embedding_service=MagicMock())


class TestRRFSlotSeparation:
    """影像腿分数**低于** dense 腿（跨编码器分数不同尺度，生产常态）。"""

    @staticmethod
    def _legs():
        dense_leg = [
            _hit(f"d{i}", 0.9 - i * 0.01, RetrievalMethod.DENSE) for i in range(20)
        ]
        image_leg = [
            _hit("img_a", 0.50, RetrievalMethod.IMAGE),
            _hit("img_b", 0.45, RetrievalMethod.IMAGE),
            _hit("img_c", 0.40, RetrievalMethod.IMAGE),
        ]
        return dense_leg, image_leg

    def test_image_and_dense_legs_coexist_in_top5(self):
        """判据 1（分槽后）：影像块独立成腿 → top-5 两腿共存。"""
        dense_leg, image_leg = self._legs()
        r = _retriever()
        fused = r._rrf_fuse(
            {RetrievalMethod.DENSE: dense_leg, RetrievalMethod.IMAGE: image_leg}
        )
        top5_ids = [h.chunk_id for h in fused[:5]]
        assert "img_a" in top5_ids, "影像块被 dense 腿挤出 top-5：分槽没生效"
        assert any(cid.startswith("d") for cid in top5_ids), "dense 块被挤掉：分槽改坏了两腿平衡"

    def test_image_rank_is_its_own_leg_rank(self):
        """判据 2：img_a 得分 = 1/(60+1)（本腿 rank1），不随 dense 腿长度变化。"""
        dense_leg, image_leg = self._legs()
        r = _retriever()
        fused = r._rrf_fuse(
            {RetrievalMethod.DENSE: dense_leg, RetrievalMethod.IMAGE: image_leg}
        )
        img_a = next(h for h in fused if h.chunk_id == "img_a")
        assert abs(img_a.score - 1.0 / 61) < 1e-9

    def test_shared_slot_regression_guard(self):
        """反证腿：同槽（旧实现）时 img_a 排 rank 21 → 1/81 < 1/61。

        反证：若有人把影像腿并回 DENSE 同槽（回归），本用例把
        "影像命中被挤位"数值化钉死。
        """
        dense_leg, image_leg = self._legs()
        r = _retriever()
        merged = r._rrf_fuse({RetrievalMethod.DENSE: dense_leg + image_leg})
        img_a = next(h for h in merged if h.chunk_id == "img_a")
        assert img_a.score < 1.0 / 61, "同槽时 img_a 必须被挤位（否则反证失效）"

    async def test_image_hits_tagged_image_in_retrieve(self, monkeypatch):
        """retrieve() 出口的影像块 retrieval_source 必须是 IMAGE（不再是 DENSE）。"""
        r = _retriever()

        async def fake_dense(query, request, drop_tally=None):
            return [_hit("d1", 0.8, RetrievalMethod.DENSE)]

        async def fake_image(request, drop_tally=None):
            return [_hit("img_x", 0.99, RetrievalMethod.IMAGE)]

        leg = type(
            "Leg", (), {"skip_reason": None, "image_data": b"x", "image_format": "png", "session_id": "s1"}
        )()
        monkeypatch.setattr(r, "_safe_dense", fake_dense)
        monkeypatch.setattr(r, "_safe_image", fake_image)
        monkeypatch.setattr(r, "_resolve_image_leg", lambda request: leg)

        result = await r.retrieve(
            RetrievalRequest(query="x", top_k=5), enhanced_query="x"
        )
        ids = [c.chunk.chunk_id for c in result.chunks]
        assert "img_x" in ids and "d1" in ids, "两腿结果必须共存于最终结果"
        img = next(c for c in result.chunks if c.chunk.chunk_id == "img_x")
        assert img.retrieval_source == RetrievalMethod.IMAGE
