"""向量集合名必须指向真实存在的集合（2026-11-08）。

## 缺陷

`settings.rag_collection` 默认值是 `'knowledge_chunks'`，而 Qdrant 里：

    GET /collections/knowledge_chunks            → 404（不存在）
    GET /collections/ultrasound_knowledge_chunks → 200（5042 点）

`.env` 里**没有** `RAG_COLLECTION` → 生产一直用默认值 → **dense 腿查 404 集合**。

而 `_dense_search` 的异常被上层吞掉（检索退化为融合排序），
所以这个缺陷**不报错、只让 dense 腿恒空** —— 与"BM25 单独兜底"的现象一致。

本守卫防止默认值再漂到不存在的名字。
"""

from __future__ import annotations

import pytest

from app.config.settings import settings

pytestmark = pytest.mark.track_rag

#: Qdrant 里实际存在的知识库集合名（2026-11-08 实测 GET 返回 200）。
KNOWN_GOOD_COLLECTIONS = {
    "ultrasound_knowledge_chunks",  # 知识库主集合（5042 点）
    "ultrasound_chunks",            # 影像集合
    "entity_matching",
}


def test_rag_collection_is_a_known_collection():
    """默认集合名必须是 Qdrant 里真实存在的那个。"""
    assert settings.rag_collection in KNOWN_GOOD_COLLECTIONS, (
        f"rag_collection={settings.rag_collection!r} 不在已知存在的集合里 "
        f"{sorted(KNOWN_GOOD_COLLECTIONS)} —— dense 腿会查 404"
    )


def test_rag_collection_is_not_the_old_broken_default():
    """钉住：不允许回到 `knowledge_chunks`（实测 404）。"""
    assert settings.rag_collection != "knowledge_chunks", (
        "rag_collection 回到 'knowledge_chunks' —— 该集合在 Qdrant 里不存在（404），"
        "dense 腿会恒空且不报错"
    )


def test_kb_version_and_collection_are_different_concepts():
    """`kb_version` 是版本号（kb_vX.Y.Z），`collection` 是集合名 —— 不能混。

    代码里 `collection = request.kb_version or settings.rag_collection`
    把两者当同一个变量用；`request.kb_version` 默认 None 时退回 collection 是对的，
    但如果哪天有人把 kb_version 塞进 collection 参数，就会查一个叫 `kb_v1.4.0` 的集合（必然 404）。
    """
    assert settings.rag_kb_version.startswith("kb_v"), (
        f"rag_kb_version={settings.rag_kb_version!r} 不像版本号"
    )
    assert not settings.rag_collection.startswith("kb_v"), (
        f"rag_collection={settings.rag_collection!r} 看起来是版本号而非集合名"
    )
