"""分割旁路 _safe_nodule_contour 单测（不碰真机磁盘/权重）。

覆盖的关键行为（都是"失败不许影响诊断结论"的语义）：
- 配置关闭 → skipped，且**不查库不读盘**（默认路径必须是零开销）
- 索引查不到 / 字节取不到 / DICOM 解码失败 / 器官未训练 → 各自明确 reason
- 成功 → 面积/bbox/概率齐全，且带 image_id 与帧信息
- 任何异常都被吞成 reason，**不外泄**（旁路语义）
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.diagnosis_pipeline import _safe_nodule_contour

pytestmark = [pytest.mark.asyncio]


def _row(**kw):
    base = {
        "organ": "thyroid",
        "image_ids": ["img_20261003_deadbeef"],
        "session_id": "sess-1",
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _indexed_row(storage_path="/tmp/x/img_1.dcm"):
    return SimpleNamespace(image_id="img_20261003_deadbeef", storage_path=storage_path)


class _FakeResult:
    """NoduleSegmenter.infer 的替身。"""

    def __init__(self):
        self.width, self.height = 512, 400
        self.lesion_area_ratio = 0.05
        self.bbox = (10, 20, 100, 120)
        self.mean_prob, self.max_prob = 0.42, 0.97
        self.threshold = 0.5
        self.model_dice_official = 0.7961
        self.device = "cpu"
        self.latency_ms = 12.3


class _FakeFrame:
    def __init__(self):
        self.png_bytes = b"\x89PNG-fake"
        self.frame_index = 9
        self.total_frames = 18


def _patch_pipeline(repo_rows=None, storage_bytes=b"DICM-bytes", seg_result=None,
                    pixel_error=None, seg_error=None):
    """把旁路的三处外部依赖全部打桩，返回可进 with 的 patch 上下文管理器列表。"""
    repo = MagicMock()
    repo.filter_existing_in_session = AsyncMock(return_value=repo_rows or [])

    storage = MagicMock()
    storage.retrieve = AsyncMock(return_value=storage_bytes)

    seg_instance = MagicMock()
    if seg_error is not None:
        seg_instance.infer = MagicMock(side_effect=seg_error)
    else:
        seg_instance.infer = MagicMock(return_value=seg_result or _FakeResult())

    pixels = MagicMock()
    if pixel_error is not None:
        pixels.extract_frame_from_bytes = MagicMock(side_effect=pixel_error)
    else:
        pixels.extract_frame_from_bytes = MagicMock(return_value=_FakeFrame())

    return [
        patch("app.services.diagnosis_pipeline.AsyncSessionLocal",
              MagicMock(**{
                  "__aenter__": AsyncMock(return_value=MagicMock()),
                  "__aexit__": AsyncMock(return_value=False),
              })),
        patch("app.services.diagnosis_pipeline.DicomImageRepository",
              MagicMock(return_value=repo)),
        patch("app.services.dicom_storage.get_dicom_storage",
              MagicMock(return_value=storage)),
        patch("app.inference.nodule_segmentation.NoduleSegmenter.get",
              MagicMock(return_value=seg_instance)),
        patch("app.imaging.dicom_pixels.extract_frame_from_bytes",
              pixels.extract_frame_from_bytes),
    ]


class _ExitStack:
    """进入顺序的 patch 列表；进入后可用下标取各 patch 的替身。"""

    def __init__(self, cms):
        self._cms = cms
        self._entered = []

    def __enter__(self):
        for cm in self._cms:
            self._entered.append(cm.__enter__())
        return self

    def __getitem__(self, i):
        return self._entered[i]

    def __exit__(self, *exc):
        for cm in reversed(self._cms):
            cm.__exit__(*exc)
        return False


async def _run(row, *, enabled=True, **kw):
    """跑旁路：打桩三处外部依赖 + 用 monkeypatch 风格改 settings 开关。

    注意：不能用 patch.object(settings, ...) —— pydantic Settings 对象是
    immutable（frozen），项目既有测试（test_nodule_segmentation.py:144）用
    monkeypatch.setattr(settings, ...) 也要靠 raising=False 之外的机制；
    这里改为直接 setattr 后在 finally 还原。
    """
    from app.config.settings import settings as s

    patches = _patch_pipeline(**kw)
    old = getattr(s, "nodule_contour_in_pipeline_enabled", None)
    object.__setattr__(s, "nodule_contour_in_pipeline_enabled", enabled)
    try:
        with _ExitStack(patches):
            return await _safe_nodule_contour(row)
    finally:
        object.__setattr__(s, "nodule_contour_in_pipeline_enabled", old)


class TestDisabled:
    async def test_returns_skipped_when_disabled(self):
        out = await _run(_row(), enabled=False)
        assert out == {"available": False, "reason": "not_enabled", "skipped": True}

    async def test_disabled_does_not_touch_db(self):
        """默认关闭必须是零开销：不查库、不读盘。"""
        from app.config.settings import settings as s

        patches = _patch_pipeline()
        old = getattr(s, "nodule_contour_in_pipeline_enabled", None)
        object.__setattr__(s, "nodule_contour_in_pipeline_enabled", False)
        try:
            with _ExitStack(patches) as st:
                out = await _safe_nodule_contour(_row())
        finally:
            object.__setattr__(s, "nodule_contour_in_pipeline_enabled", old)
        assert out["reason"] == "not_enabled"
        repo_cls = st[1]
        assert repo_cls.return_value.filter_existing_in_session.await_count == 0

    async def test_no_image_ids(self):
        out = await _run(_row(image_ids=[]), enabled=True)
        assert out["reason"] == "no_image_ids"
        assert out["skipped"] is True


class TestUnavailableReasons:
    async def test_image_not_indexed(self):
        out = await _run(_row(), repo_rows=[])
        assert out["available"] is False
        assert out["reason"] == "image_not_indexed"
        assert out["requested_image_ids"] == ["img_20261003_deadbeef"]

    async def test_bytes_unavailable(self):
        out = await _run(_row(), repo_rows=[_indexed_row()], storage_bytes=None)
        assert out["reason"] == "image_bytes_unavailable"
        assert out["image_id"] == "img_20261003_deadbeef"

    async def test_pixel_decode_failed(self):
        from app.imaging.dicom_pixels import DicomPixelExtractionError

        out = await _run(
            _row(),
            repo_rows=[_indexed_row()],
            pixel_error=DicomPixelExtractionError("坏文件"),
        )
        assert out["reason"].startswith("dicom_decode_failed")

    async def test_organ_untrained_is_explicit(self):
        """器官未训练必须给出可读原因（而不是"没结果"）。"""
        from app.inference.nodule_segmentation import SegmentationUnavailableError

        out = await _run(
            _row(organ="liver"),
            repo_rows=[_indexed_row()],
            seg_error=SegmentationUnavailableError("liver 无公开超声分割数据集"),
        )
        assert out["available"] is False
        assert "无公开" in out["reason"]
        assert out["organ"] == "liver"

    async def test_unexpected_exception_is_swallowed(self):
        out = await _run(
            _row(),
            repo_rows=[_indexed_row()],
            seg_error=RuntimeError("CUDA 掉了"),
        )
        assert out["available"] is False
        assert "CUDA 掉了" in out["reason"]
        assert out["reason"].startswith("RuntimeError")


class TestSuccess:
    async def test_full_payload(self):
        out = await _run(_row(), repo_rows=[_indexed_row()])
        assert out["available"] is True
        assert out["organ"] == "thyroid"
        assert out["image_id"] == "img_20261003_deadbeef"
        assert out["frame_index"] == 9 and out["total_frames"] == 18
        assert out["width"] == 512 and out["height"] == 400
        # 面积 = ratio × 宽 × 高
        assert out["area_px"] == round(0.05 * 512 * 400)
        assert out["lesion_area_ratio"] == 0.05
        assert out["bbox"] == [10, 20, 100, 120]
        assert out["model_dice_official"] == 0.7961
        assert out["device"] == "cpu"
        assert out["bypass_latency_ms"] >= 0

    async def test_no_bbox_when_nothing_detected(self):
        seg = _FakeResult()
        seg.bbox = None
        out = await _run(_row(), repo_rows=[_indexed_row()], seg_result=seg)
        assert out["available"] is True
        assert out["bbox"] is None
