"""A.90 守卫：影像腿过滤条件必须有「严格 → 宽松」降级，且**病人隔离不许破**。

## 真机缺陷（2026-10-25，A/B 十例全部 `new=0`）

A.88 把影像腿的**接线**接通了（字节真进到 `_resolve_image_leg`），A.89 真机也证实
`image_leg_attached bytes=99676 skip_reason=None`。但 A.90 做检索贡献 A/B 时发现：

    10 例全部：off=8 chunks / image=8 chunks / **new=0**
    qdrant_search_completed collection=ultrasound_chunks returned=0

也就是说这条腿"跑得通、永远查不到"。

**根因**（真机取证）：`image_search_filter` 返回 `{session_id, organ}` 双条件，
而 `ultrasound_chunks` 的 payload **大部分没有 `organ`**：

| 事实 | 数字 |
|---|---|
| 向量点数 | 580 |
| 涉及 session 数 | 316 |
| 其中能从 `diagnosis_results` 拿到 organ 的 | **24**（292 个 session 没有判读记录） |

建库脚本 `build_image_vectors.payload_for` 里 `if row.get("organ")` 才写该字段 →
没判读记录的 session 就没有 organ → 双条件查询**恒 0 命中**。

## 修法（不是放宽红线，而是不让过滤比数据更严）

`image_search_filters(organ, session_id)` 返回候选序列：
1. 严格档 `{session_id, organ}`（语义最准，优先）；
2. 严格档 0 命中时退到 `{session_id}`（**仍带 session**）。

**病人隔离红线不变**：每一档都必带 `session_id`；无 session 返回空列表，
调用方据此不发请求 —— 绝不退化成"放开全库"。

## 这份守卫钉什么

* 无 session → 空列表（反证：让它返回"只按 organ"或 None 即红）；
* 有 session + organ → 两档，且**第二档仍含 session**；
* 有 session 无 organ → 只一档（严格档本来就只有 session）；
* `_safe_image` 源码必须真的**遍历**候选序列并带降级留痕
  （反证：改回单次 `image_search_filter(...)` 调用即红）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.rag.retriever import image_search_filter, image_search_filters

pytestmark = [pytest.mark.track_rag]

RETRIEVER = Path(__file__).resolve().parents[3] / "app" / "rag" / "retriever.py"


class TestFilterCandidates:
    def test_no_session_returns_empty(self):
        """无 session → 绝不发请求（病人隔离最硬的一条）。"""
        assert image_search_filters("thyroid", None) == []
        assert image_search_filters(None, None) == []

    def test_strict_then_relaxed_keeps_session(self):
        """有 session + organ → 两档；**两档都必须带 session**。"""
        got = image_search_filters("thyroid", "sess_x")

        assert len(got) == 2
        assert got[0] == {"session_id": "sess_x", "organ": "thyroid"}
        assert got[1] == {"session_id": "sess_x"}
        for f in got:
            assert "session_id" in f, "宽松档丢了 session = 跨病人泄露"

    def test_no_organ_single_strict(self):
        got = image_search_filters(None, "sess_x")
        assert got == [{"session_id": "sess_x"}]

    def test_never_returns_organ_only(self):
        """任何输入都不许产出"只按 organ"或 None（那等于全库搜）。"""
        for organ in (None, "thyroid"):
            for sid in (None, "", "sess_x"):
                for f in image_search_filters(organ, sid):
                    assert f != {}
                    assert "session_id" in f

    def test_legacy_single_filter_is_subset_of_candidates(self):
        """旧函数保留，且它等于候选序列的**第一档**（口径不漂移）。"""
        assert image_search_filter("thyroid", "sess_x") == {
            "session_id": "sess_x",
            "organ": "thyroid",
        }
        assert image_search_filters("thyroid", "sess_x")[0] == image_search_filter(
            "thyroid", "sess_x"
        )


class TestSafeImageIteratesCandidates:
    """源码守卫：`_safe_image` 必须真的遍历候选序列（不只是"函数存在"）。"""

    def _src(self) -> str:
        return RETRIEVER.read_text(encoding="utf-8")

    def test_uses_candidates_function(self):
        """反证腿：改回单次 `image_search_filter(...)` 调用 → 本类红。"""
        src = self._src()
        assert "image_search_filters(" in src, (
            "_safe_image 没用候选序列 → 严格档 0 命中时不会降级，影像腿永远空"
        )

    def test_has_relaxation_trace(self):
        src = self._src()
        assert "image_leg_filter_relaxed" in src, "降级没有留痕 → 下次又查不出原因"
        assert "image_leg_no_hits" in src, "全档 0 命中没有留痕"

    def test_no_bare_single_filter_call_in_image_leg(self):
        """影像腿那段不许再出现"单次 filter=" 的写法（字面守卫）。"""
        src = self._src()
        tree = ast.parse(src)
        # 找到 _safe_image 函数体，确认里面没有 image_search_filter( 的调用
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_safe_image":
                names = {
                    n.func.id
                    for n in ast.walk(node)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                }
                assert "image_search_filter" not in names, (
                    "_safe_image 又改回单档过滤了"
                )
                assert "image_search_filters" in names
                return
        pytest.fail("没找到 _safe_image —— 函数被改名了？请同步更新本守卫")
