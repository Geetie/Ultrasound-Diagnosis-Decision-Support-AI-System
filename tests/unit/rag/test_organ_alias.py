"""P0：器官名归一化 —— 心脏检索全断的修复。

## 缺陷

`app/knowledge/vocabulary.py:32` 定义 `Organ.HEART = "heart"`，
而 `app/agents/specialist/base.py` 十余处以 `organ=organ.value` 传给检索层。

但知识库与向量索引里该字段的值是 `"cardiac"`（PG 实测：heart=0 行 / cardiac=1255 行）。
两条检索腿都做**精确相等**过滤：
- dense：Qdrant `filter={"organ": request.organ}`
- sparse：`bm25_fallback.py:233` `self._built.organs[idx] != organ_filter`

→ **心脏请求 dense 0 命中 + sparse 0 命中，RAG 事实上不工作**。
长期未暴露，因为评测一直跨器官混检（不带 organ 过滤）。
"""

from __future__ import annotations

import pytest

from app.knowledge.vocabulary import Organ
from app.rag.organ_alias import CANONICAL, normalize_organ, organs_equivalent
from app.rag.protocols import RetrievalRequest

pytestmark = pytest.mark.track_rag


def test_heart_alias_maps_to_cardiac():
    """核心修复：枚举值 heart → 存储值 cardiac。"""
    assert normalize_organ("heart") == "cardiac"


def test_enum_value_is_normalized_by_request():
    """端到端：用 Organ.HEART.value 构造请求，落到 request.organ 的必须是 cardiac。"""
    assert Organ.HEART.value == "heart"  # 枚举值本身没改（避免影响写库/前端契约）
    req = RetrievalRequest(query="心脏瓣膜反流评估", organ=Organ.HEART.value)
    assert req.organ == "cardiac"


def test_canonical_values_pass_through():
    for v in CANONICAL:
        assert normalize_organ(v) == v


def test_case_insensitive():
    assert normalize_organ("HEART") == "cardiac"
    assert normalize_organ("CARDIAC") == "cardiac"
    assert normalize_organ("  Heart  ") == "cardiac"


def test_chinese_names():
    assert normalize_organ("心脏") == "cardiac"
    assert normalize_organ("甲状腺") == "thyroid"
    assert normalize_organ("肝脏") == "liver"


def test_none_passthrough():
    assert normalize_organ(None) is None
    assert normalize_organ("") is None


def test_unknown_value_returned_as_is_not_none():
    """未知值**原样返回**，不变成 None。

    为什么：变成 None 会让检索退化成"不过滤"，把错误悄悄放大；
    原样返回则命中 0 行，缺陷仍然可见。**诚实的失败优于静默的降级。**
    """
    assert normalize_organ("spleen") == "spleen"
    assert normalize_organ("unknown_organ") == "unknown_organ"


def test_organs_equivalent():
    assert organs_equivalent("heart", "cardiac") is True
    assert organs_equivalent("thyroid", "liver") is False
    assert organs_equivalent(None, "liver") is False


def test_all_enum_values_normalize_to_canonical():
    """钉住：Organ 枚举里的每个值归一化后都必须落在 CANONICAL 里。

    若将来新增器官但忘了加别名，这条会红。
    """
    for o in Organ:
        assert normalize_organ(o.value) in CANONICAL, (
            f"{o.name}={o.value!r} 归一化后不在 CANONICAL 里 → "
            f"该器官的检索会 0 命中"
        )
