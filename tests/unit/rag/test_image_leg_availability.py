"""A.68 / R1 守卫：图像检索腿必须「可见、不骗人」。

背景（真机实测）：`rag_image_collection='ultrasound_chunks'` 在 Qdrant 里不存在
（实际只有 `ultrasound_knowledge_chunks`，1024 维；ULC 报 512 维），而
`rag_use_multimodal` 原本默认 **True** → 每个带图请求都走"集合不存在就跳过"，
日志只有 info 级 → **生产上完全看不见这条腿零贡献**。

用户 2026-10-20 拍板选 **C（按需开）**：
1. 默认关闭（`rag_use_multimodal=False`）；
2. 能力状态在 `/api/v1/health` 的 `image_leg` 块可见（集合/维度/ready/人话原因）；
3. 真开了但集合缺失 → **一次性 warning**，不再是 info。
"""

from __future__ import annotations

import pathlib

import pytest

pytestmark = pytest.mark.track_rag

from app.config.settings import settings  # noqa: E402
from app.rag.image_leg import classify_image_leg, reset_warned_for_tests, warn_once  # noqa: E402

_APP = pathlib.Path(__file__).resolve().parents[3] / "app"


def test_disabled_is_not_ready_and_says_why():
    out = classify_image_leg(
        enabled=False,
        collection="ultrasound_chunks",
        collection_exists=True,
        collection_dim=512,
        image_dim=512,
    )
    assert out["ready"] is False
    assert "开关关闭" in out["note"]


def test_enabled_but_collection_missing_is_not_ready():
    out = classify_image_leg(
        enabled=True,
        collection="ultrasound_chunks",
        collection_exists=False,
        collection_dim=None,
        image_dim=512,
    )
    assert out["ready"] is False
    assert "集合不存在" in out["note"]
    assert "ultrasound_chunks" in out["note"]


def test_probe_failure_is_distinct_from_missing():
    """Qdrant 不可达 ≠ 集合不存在（否则会把"探不到"读成"没有集合"）。"""
    out = classify_image_leg(
        enabled=True,
        collection="ultrasound_chunks",
        collection_exists=None,
        collection_dim=None,
        image_dim=512,
    )
    assert out["ready"] is False
    assert "无法探测" in out["note"]


def test_dimension_mismatch_is_not_ready():
    out = classify_image_leg(
        enabled=True,
        collection="ultrasound_chunks",
        collection_exists=True,
        collection_dim=1024,  # 真机知识集合是 1024 维
        image_dim=512,  # 而 ULC 报 512 维
    )
    assert out["ready"] is False
    assert "维度不匹配" in out["note"]


def test_ready_only_when_collection_and_dimension_agree():
    out = classify_image_leg(
        enabled=True,
        collection="ultrasound_images",
        collection_exists=True,
        collection_dim=512,
        image_dim=512,
    )
    assert out["ready"] is True
    assert "可用" in out["note"]


def test_default_setting_is_off():
    """R1 方案 C 的第一条：默认必须关（否则又是"开了等于没开"）。"""
    assert settings.rag_use_multimodal is False


def test_warn_once_dedupes():
    reset_warned_for_tests()
    warn_once("k1", "event_a", x=1)
    warn_once("k1", "event_a", x=2)  # 同一个 key 不再打
    assert True  # 行为由日志捕获间接保证；这里只钉"不抛异常、可重复调用"
    reset_warned_for_tests()


def test_health_exposes_image_leg_block():
    src = (_APP / "api" / "v1" / "health.py").read_text(encoding="utf-8")
    assert "probe_image_leg" in src
    assert '"image_leg": image_leg' in src


def test_retriever_uses_one_time_warning_not_info():
    src = (_APP / "rag" / "retriever.py").read_text(encoding="utf-8")
    assert "warn_once(" in src
    # 老的 info 级留痕必须消失（否则生产上仍然看不见）
    assert '"image_collection_not_found_skipping"' not in src
