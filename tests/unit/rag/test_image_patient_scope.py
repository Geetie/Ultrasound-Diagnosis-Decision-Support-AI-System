"""A.78 守卫：**病人隔离**（影像只能读本次会话的，不允许跨病人检索/提交）。

两处红线：
1. **检索侧**：影像向量检索必须带 `session_id` 过滤；没有 session 就不发请求
   （旧实现只按 organ 过滤 —— 一旦灌入多病人向量，就会召回别人的影像）；
2. **提交侧**：`image_ids` 必须属于该 session，否则显式 400
   （旧实现原样落库，管线才用 `filter_existing_in_session` 兜底，库里留脏 ID）。
"""

from __future__ import annotations

from pathlib import Path

from app.rag.retriever import image_search_filter

_REPO = Path(__file__).resolve().parents[3]


def test_image_filter_always_scopes_to_session():
    flt = image_search_filter("thyroid", "sess-123")
    assert flt == {"session_id": "sess-123", "organ": "thyroid"}


def test_image_filter_without_organ_keeps_session():
    assert image_search_filter(None, "sess-123") == {"session_id": "sess-123"}


def test_image_filter_without_session_does_not_offer_global_search():
    """没有 session 时**不能**退化成"只按器官查全库"。"""
    assert image_search_filter("thyroid", None) == {"organ": "thyroid"}
    assert image_search_filter(None, None) is None


def test_safe_image_refuses_search_without_session():
    """`_safe_image` 在没有 session 时必须提前返回，不得发检索。"""
    src = (_REPO / "app" / "rag" / "retriever.py").read_text(encoding="utf-8")
    idx = src.index("async def _safe_image")
    body = src[idx : idx + 2000]
    assert "if not request.session_id:" in body, "无 session 时没有短路"
    assert "image_search_filter(request.organ, request.session_id)" in body
    # 旧写法（只按器官过滤）不许回来
    assert 'filter={"organ": request.organ}' not in src


def test_submit_endpoint_validates_image_ownership():
    src = (_REPO / "app" / "api" / "v1" / "diagnosis.py").read_text(encoding="utf-8")
    idx = src.index("async def submit_diagnosis_request")
    body = src[idx : idx + 8000]
    assert "missing_from_session" in body, "提交接口没有校验影像归属"
    assert "不属于本次检查会话" in body, "拒绝时没有给医生中文原因"
