"""A.51 多帧分期③：分割旁路「择优帧」帧级归属。

旧行为：固定 `rows[0]` + `extract_frame_from_bytes(None)`（恒取中间帧）——
多帧栈/多影像时无择优语义，报告与前端也无帧归属。
新行为：
  - 多张影像 → 每张取代表帧（中间帧），至多前 2 张；
  - 单张多帧 → 中间帧 ±2 采样，至多 3 帧；
  - 按 mean_prob 择优，落 `frame_index/total_frames/selection_policy/frames_evaluated`。
"""
import types
from unittest.mock import MagicMock

import pytest

from app.services import diagnosis_pipeline as dp


class _FakeSession:
    async def __aenter__(self):
        return MagicMock()

    async def __aexit__(self, *exc):
        return False


def _row(image_id: str, frame_count: int) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        image_id=image_id,
        storage_path=f"/store/{image_id}.dcm",
        frame_count=frame_count,
    )


def _install_stubs(monkeypatch, rows, probs_by_frame, frames_total):
    """装配旁路依赖：repo / storage / 解码 / 分割器（全部可控）。"""
    from app.config.settings import settings

    monkeypatch.setattr(settings, "nodule_contour_in_pipeline_enabled", True, raising=False)
    monkeypatch.setattr(dp, "AsyncSessionLocal", lambda: _FakeSession())

    class _Repo:
        def __init__(self, db):
            pass

        async def filter_existing_in_session(self, session_id, image_ids):
            return rows

    # dp 在模块级 `from ... import DicomImageRepository`——必须打在 dp 命名空间上
    monkeypatch.setattr(dp, "DicomImageRepository", _Repo)

    # storage：任意 image_id 都返回可读 bytes（内容无意义，解码被替身接管）
    class _Storage:
        async def retrieve(self, path):
            return b"fake-dicom-bytes"

    import app.services.dicom_storage as ds

    monkeypatch.setattr(ds, "get_dicom_storage", lambda: _Storage())

    # 解码替身：按 frame_index 返回带 png_bytes 的帧描述
    import app.imaging.dicom_pixels as dpx

    def _fake_extract(content, frame_index=None):
        idx = 0 if frame_index is None else int(frame_index)
        return types.SimpleNamespace(
            png_bytes=b"png", frame_index=idx, total_frames=frames_total
        )

    monkeypatch.setattr(dpx, "extract_frame_from_bytes", _fake_extract)

    # 分割器替身：按被解码帧号返回可控 mean_prob
    class _Segmenter:
        def infer(self, *, image_bytes):
            idx = calls["n"]
            calls["n"] += 1
            seq = probs_by_frame[image_bytes if image_bytes in probs_by_frame else "seq"]
            prob = seq[min(idx, len(seq) - 1)]
            return types.SimpleNamespace(
                lesion_area_ratio=0.1, width=100, height=100, bbox=[1, 2, 3, 4],
                mean_prob=prob, max_prob=prob, threshold=0.5,
                model_dice_official=None, device="cpu", latency_ms=1.0,
            )

    calls = {"n": 0}
    monkeypatch.setattr(dp.NoduleSegmenter, "get", classmethod(lambda cls, organ: _Segmenter()))
    # mm² 换算走真实 DICOM 解析——替身内容必失败，直接置空（与无 PixelSpacing 同义）
    monkeypatch.setattr(dp, "_area_mm2_from_dicom", lambda content, area: None)
    monkeypatch.setattr(dp, "_pixel_spacing_from_dicom", lambda content: None)


class TestBestFrameSelection:
    async def test_single_multiframe_picks_best_of_three(self, monkeypatch):
        """单张 9 帧影像 → 评估 **首/中/末（0/4/8）** 三帧，取 mean_prob 最高者。

        A.81 起帧策略统一到 `dicom_structure.frame_indices()`（首/中/末）：
        旧策略是"中间帧 ±2"（2/4/6），只覆盖 cine loop 的中间一小段，
        首尾（心脏舒张/收缩两端）永远选不到。
        """
        rows = [_row("img_multi", 9)]
        # 三帧概率：首帧最低 → 应选到中间帧（第 2 个候选）
        _install_stubs(monkeypatch, rows, {"seq": [0.30, 0.91, 0.55]}, frames_total=9)

        out = await dp._safe_nodule_contour(
            types.SimpleNamespace(organ="thyroid", image_ids=["img_multi"], session_id="s1")
        )

        assert out["available"] is True
        assert out["selection_policy"] == "best_mean_prob"
        assert out["candidates_evaluated"] == 3
        assert len(out["frames_evaluated"]) == 3
        assert out["frame_index"] == 4  # 首/中/末 = 0/4/8，中间帧胜出
        assert out["mean_prob"] == 0.91
        assert out["total_frames"] == 9

    async def test_single_frame_image_uses_single_frame_policy(self, monkeypatch):
        """单帧影像 → 不做择优（policy=single_frame），帧号 0。"""
        rows = [_row("img_single", 1)]
        _install_stubs(monkeypatch, rows, {"seq": [0.8]}, frames_total=1)

        out = await dp._safe_nodule_contour(
            types.SimpleNamespace(organ="thyroid", image_ids=["img_single"], session_id="s1")
        )

        assert out["selection_policy"] == "single_frame"
        assert out["candidates_evaluated"] == 1
        assert out["frame_index"] == 0

    async def test_two_images_best_image_wins(self, monkeypatch):
        """两张影像 → 各取代表帧，择优帧来自概率更高的那张（image_id 必须跟着走）。"""
        rows = [_row("img_a", 1), _row("img_b", 1)]
        _install_stubs(monkeypatch, rows, {"seq": [0.20, 0.77]}, frames_total=1)

        out = await dp._safe_nodule_contour(
            types.SimpleNamespace(organ="thyroid",
                                  image_ids=["img_a", "img_b"], session_id="s1")
        )

        assert out["selection_policy"] == "best_mean_prob"
        assert out["candidates_evaluated"] == 2
        assert out["image_id"] == "img_b"
        assert out["mean_prob"] == 0.77


class TestReportFrameAttribution:
    def test_report_mentions_frame_when_multiframe(self):
        """报告文本必须写明「第 X/Y 帧」——医生据此回看核对。"""
        from app.services.report_service import _format_nodule_segmentation

        text = _format_nodule_segmentation({
            "available": True,
            "organ": "cardiac",
            "area_px": 1234,
            "area_mm2": 12.3,
            "total_frames": 20,
            "frame_index": 9,
            "selection_policy": "best_mean_prob",
        })
        assert "第 10/20 帧" in text
        assert "多帧候选择优" in text

    def test_report_omits_frame_when_single(self):
        from app.services.report_service import _format_nodule_segmentation

        text = _format_nodule_segmentation({
            "available": True,
            "organ": "cardiac",
            "area_px": 100,
            "total_frames": 1,
            "frame_index": 0,
        })
        assert "帧" not in text


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
