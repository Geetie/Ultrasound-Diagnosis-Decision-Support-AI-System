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
    """`warn_once` 必须真的按 key 去重（A.91 修：原用例只有 `assert True`）。

    ## 原来的问题是"假测试"

    原实现：
    ```
    warn_once("k1", "event_a", x=1)
    warn_once("k1", "event_a", x=2)
    assert True  # 行为由日志捕获间接保证
    ```
    —— 名为"验证去重"，实际**什么都没验证**（恒真断言）。去重坏了它也绿。
    这类用例的价值是负的：它让"该能力有测试覆盖"这句话不成立，却占着覆盖率的位。

    现在直接断言**日志调用次数**（去重的本质就是"同一个 key 只打一次"）。
    只断言 `_WARNED` 集合内容不够：即使去重失效，集合里也只有一个 `k1`
    （重复 add 同一元素是幂等的）—— 那样改了实现测试仍绿，等于假测试。

    做法：patch `logger.warning`，数它被调了几次。
    """
    from unittest.mock import patch

    from app.rag import image_leg as mod

    reset_warned_for_tests()
    assert set() == mod._WARNED

    with patch.object(mod.logger, "warning") as spy:
        warn_once("k1", "event_a", x=1)
        warn_once("k1", "event_a", x=2)  # 同一个 key 不再打
        warn_once("k1", "event_a", x=3)
        assert spy.call_count == 1, (
            f"同 key 应只打 1 次日志，实际 {spy.call_count} 次 —— 去重失效"
        )

        warn_once("k2", "event_b", y=1)
        assert spy.call_count == 2, "不同 key 必须各打一次"

    assert {"k1", "k2"} == mod._WARNED

    reset_warned_for_tests()
    assert set() == mod._WARNED


def test_health_exposes_image_leg_block():
    src = (_APP / "api" / "v1" / "health.py").read_text(encoding="utf-8")
    assert "probe_image_leg" in src
    assert '"image_leg": image_leg' in src


def test_retriever_uses_one_time_warning_not_info():
    src = (_APP / "rag" / "retriever.py").read_text(encoding="utf-8")
    assert "warn_once(" in src
    # 老的 info 级留痕必须消失（否则生产上仍然看不见）
    assert '"image_collection_not_found_skipping"' not in src
