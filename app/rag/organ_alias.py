"""器官名归一化（P0 修复，2026-11-08）。

## 缺陷

`app/knowledge/vocabulary.py:32` 定义 `Organ.HEART = "heart"`，
而 `app/agents/specialist/base.py` 十余处以 `organ=organ.value` 传给检索层
→ 心脏请求带的是 `"heart"`。

但**知识库与向量索引里这个字段的值是 `"cardiac"`**：

| 存储 | `heart` | `cardiac` |
|---|---|---|
| PG `knowledge_chunks.organ` | **0** | **1255** |
| Qdrant `vector_index_knowledge_chunks` payload.organ | **0** | 566 |

两条检索腿都做**精确相等**过滤：
- dense：`app/rag/retriever.py` → Qdrant `filter={"organ": request.organ}`
- sparse：`app/rag/bm25_fallback.py:233` → `self._built.organs[idx] != organ_filter`

**后果：任何被识别为心脏的请求，dense 0 命中 + sparse 0 命中，只剩影像腿
→ 心脏方向的 RAG 事实上完全不工作。**

这个缺陷长期没暴露，因为评测一直用跨器官混检（不带 organ 过滤）。

## 为什么不改 `Organ.HEART` 的值

那会连带影响：写库时的 organ 字段、前端契约、历史数据（1255 条 `cardiac` 需迁移）、
既有测试。**改一处存储标签的性价比远低于加一层查询侧归一化。**

## 本模块职责

在**查询侧**把各种写法归一到存储侧使用的规范值。纯函数、可单测、无副作用。
"""

from __future__ import annotations

#: 规范值 = 知识库/向量索引里实际使用的值。
CANONICAL: tuple[str, ...] = ("thyroid", "liver", "cardiac")

#: 别名 → 规范值。键一律小写。
_ALIASES: dict[str, str] = {
    # 心脏：枚举值是 heart，存储值是 cardiac —— 这是 P0 缺陷的根源
    "heart": "cardiac",
    "cardiac": "cardiac",
    "心脏": "cardiac",
    "heart_": "cardiac",
    # 甲状腺
    "thyroid": "thyroid",
    "thyroid_gland": "thyroid",
    "甲状腺": "thyroid",
    # 肝脏
    "liver": "liver",
    "hepatic": "liver",
    "肝脏": "liver",
    "肝": "liver",
}


def normalize_organ(value: str | None) -> str | None:
    """把器官名归一化成存储侧使用的规范值（纯函数）。

    Args:
        value: 任意来源的器官名（枚举值 / 中文 / 大小写不同的写法）。

    Returns:
        规范值（`thyroid` / `liver` / `cardiac` 之一）；
        ``None`` 输入返回 ``None``；**未知值原样返回**（不猜，也不吞）。

    设计取舍：未知值**原样返回而不是返回 None**。因为把未知值变 None 会让
    检索退化成"不过滤"，那会悄悄把错误放大；原样返回则会让过滤命中 0 行，
    缺陷仍然可见（诚实的失败优于静默的降级）。
    """
    if value is None:
        return None
    key = str(value).strip().lower()
    if not key:
        return None
    return _ALIASES.get(key, key)


def organs_equivalent(a: str | None, b: str | None) -> bool:
    """两个器官名是否指同一个器官（归一化后比较）。"""
    na, nb = normalize_organ(a), normalize_organ(b)
    return na is not None and na == nb


__all__ = ["CANONICAL", "normalize_organ", "organs_equivalent"]
