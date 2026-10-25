"""A.88 T5-1：T2a 分类旁路在管线里的接线守卫。

## 为什么要这条测试

《架构真相-以代码为准-20261025.md》§3.3 第一条：T2a 分类器**生产代码完全不调用**，
只有审计脚本 ``p29_t2a_aul_holdout.py`` 会 POST ``8003 /v1/classify`` ——
于是"离线 AUC 0.8950"只是**离线模型指标**，不是系统能力。

## 钉住的四条不变量

1. **默认关 = 零开销**：开关关着时不查库、不读盘、不发请求；
2. **失败原因分开**（AGENTS.md 硬规则 8）：``no_image_ids`` /
   ``image_not_indexed`` / ``no_image_bytes`` / ``frame_decode_failed`` /
   ``service_unavailable`` / ``model_missing`` 各自独立，**不许统一写死**；
   反证腿：把所有分支改成同一个 reason 即红；
3. **只作辅助参考**：结果写入 ``findings["t2a_classification"]``，且
   ``_write_result_to_db`` 的 status/confidence **不受它影响**；
4. **永不抛异常**：旁路异常必须转成 ``available=False + reason``。
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import diagnosis_pipeline as mod
from app.services.diagnosis_pipeline import _safe_t2a_classification


def _row(**kw):
    base = {
        "organ": "thyroid",
        "image_ids": ["img_20261003_deadbeef"],
        "session_id": "sess-1",
        "request_id": "req-t2a",
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _indexed_row(frame_count: int = 1):
    return SimpleNamespace(
        image_id="img_20261003_deadbeef",
        storage_path="/tmp/x/img_1.dcm",
        frame_count=frame_count,
    )


class _FakeFrame:
    def __init__(self):
        self.png_bytes = b"\x89PNG-fake"
        self.frame_index = 9
        self.total_frames = 18


class _Switch:
    """临时改 ``t2a_classifier_in_pipeline_enabled``（pydantic Settings 是 frozen 模型）。"""

    def __init__(self, value: bool) -> None:
        self.value = value

    def __enter__(self):
        from app.config.settings import settings

        self._settings = settings
        self._old = settings.__dict__.get("t2a_classifier_in_pipeline_enabled", None)
        object.__setattr__(settings, "t2a_classifier_in_pipeline_enabled", self.value)
        return settings

    def __exit__(self, *exc):
        if self._old is None:
            self._settings.__dict__.pop("t2a_classifier_in_pipeline_enabled", None)
        else:
            object.__setattr__(
                self._settings, "t2a_classifier_in_pipeline_enabled", self._old
            )
        return False


def _patches(*, repo_rows=None, storage_bytes=b"DICM-bytes", pixel_error=None):
    repo = MagicMock()
    repo.filter_existing_in_session = AsyncMock(return_value=repo_rows or [])

    storage = MagicMock()
    storage.retrieve = AsyncMock(return_value=storage_bytes)

    extractor = MagicMock()
    if pixel_error is not None:
        extractor.side_effect = pixel_error
    else:
        extractor.return_value = _FakeFrame()

    return repo, storage, extractor


class _Stack:
    def __init__(self, cms):
        self._cms = cms

    def __enter__(self):
        self._entered = [cm.__enter__() for cm in self._cms]
        return self

    def __exit__(self, *exc):
        for cm in reversed(self._cms):
            cm.__exit__(*exc)
        return False


async def _run(row, *, enabled=True, classify=None, **kw):
    repo, storage, extractor = _patches(**kw)
    classify_mock = AsyncMock(
        return_value=classify
        or {
            "available": True,
            "probs": {"Benign": 0.7, "Malignant": 0.2, "Normal": 0.1},
            "predicted": "Benign",
            "malignant_prob": 0.2,
            "endpoint": "http://h:1/v1/classify",
        }
    )
    cms = [
        patch.object(mod, "AsyncSessionLocal", _aenter_ctx()),
        patch.object(mod, "DicomImageRepository", MagicMock(return_value=repo)),
        patch("app.services.dicom_storage.get_dicom_storage", MagicMock(return_value=storage)),
        patch("app.imaging.dicom_pixels.extract_frame_from_bytes", extractor),
        patch("app.inference.t2a_classifier.classify_frame", classify_mock),
    ]
    with _Switch(enabled), _Stack(cms):
        out = await _safe_t2a_classification(row)
    return out, repo, storage, extractor, classify_mock


def _aenter_ctx():
    holder = MagicMock()
    holder.__aenter__ = AsyncMock(return_value=MagicMock())
    holder.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=holder)


class TestDisabledIsZeroCost:
    async def test_disabled_returns_reason_without_touching_anything(self):
        out, repo, storage, _extractor, classify_mock = await _run(_row(), enabled=False)

        assert out == {"available": False, "reason": "disabled", "skipped": True}
        assert repo.filter_existing_in_session.await_count == 0
        assert storage.retrieve.await_count == 0
        assert classify_mock.await_count == 0


class TestReasonsAreDistinct:
    """反证腿集：每个"取不到"的分支必须报**自己的**原因。"""

    async def test_no_image_ids(self):
        out, *_ = await _run(_row(image_ids=[]))

        assert out["reason"] == "no_image_ids"

    async def test_image_not_indexed(self):
        out, *_ = await _run(_row(), repo_rows=[])

        assert out["reason"] == "image_not_indexed"
        assert out["requested_image_ids"] == ["img_20261003_deadbeef"]

    async def test_no_image_bytes(self):
        out, *_ = await _run(_row(), repo_rows=[_indexed_row()], storage_bytes=None)

        assert out["reason"] == "no_image_bytes"
        assert out["image_id"] == "img_20261003_deadbeef"

    async def test_frame_decode_failed(self):
        from app.imaging.dicom_pixels import DicomPixelExtractionError

        out, *_ = await _run(
            _row(),
            repo_rows=[_indexed_row()],
            pixel_error=DicomPixelExtractionError("坏文件"),
        )

        assert out["reason"] == "frame_decode_failed"
        assert "坏文件" in out["detail"]

    async def test_client_reason_is_passed_through_verbatim(self):
        """客户端已经分清 service_unavailable / model_missing —— 管线不许再收敛。"""
        out, *_ = await _run(
            _row(),
            repo_rows=[_indexed_row()],
            classify={"available": False, "reason": "model_missing", "detail": "x"},
        )

        assert out["reason"] == "model_missing"

    async def test_unexpected_exception_becomes_reason_not_raise(self):
        out, *_ = await _run(_row(), repo_rows=[_indexed_row()], storage_bytes=b"x", classify=None)

        assert out["available"] is True  # 先确认正常路径可用
        # 再构造一个"查库就炸"的场景
        broken_repo = MagicMock()
        broken_repo.filter_existing_in_session = AsyncMock(side_effect=RuntimeError("db down"))
        cms = [
            patch.object(mod, "AsyncSessionLocal", _aenter_ctx()),
            patch.object(mod, "DicomImageRepository", MagicMock(return_value=broken_repo)),
        ]
        with _Switch(True), _Stack(cms):
            failed = await _safe_t2a_classification(_row())

        assert failed["available"] is False
        assert failed["reason"] == "service_unavailable"
        assert "RuntimeError" in failed["detail"]


class TestSuccessPayload:
    async def test_success_is_marked_auxiliary_and_carries_frame_info(self):
        out, *_ = await _run(_row(), repo_rows=[_indexed_row(frame_count=18)])

        assert out["available"] is True
        assert out["predicted"] == "Benign"
        assert out["malignant_prob"] == pytest.approx(0.2)
        assert out["role"] == "auxiliary_reference", (
            "必须显式标注'只作辅助参考'，否则读路径只能靠约定"
        )
        assert out["image_id"] == "img_20261003_deadbeef"
        assert out["frame_index"] == 9 and out["total_frames"] == 18
        assert out["bypass_latency_ms"] >= 0

    async def test_single_frame_uses_default_frame(self):
        """单帧影像不指定 frame_index（解码默认帧），不硬塞一个下标。"""
        out, _repo, _storage, extractor, _classify = await _run(
            _row(), repo_rows=[_indexed_row(frame_count=1)]
        )

        assert out["available"] is True
        assert extractor.call_args.kwargs["frame_index"] is None


class TestWritePathDoesNotLetAuxiliaryChangeTheConclusion:
    """红线：旁路结果不得改 status / confidence。"""

    def test_write_signature_accepts_t2a_without_affecting_status_logic(self):
        source = inspect.getsource(mod._write_result_to_db)
        # 只取「决定 status」那一段（从 1) 到 2)），签名/docstring 不参与判定
        status_block = source.split("# 1) 决定 status", 1)[1].split(
            "# 2) 组装 findings", 1
        )[0]

        assert "t2a" not in status_block, (
            "T2a 进入了 status/confidence 判定段 —— 它会越权改诊断结论"
        )
        assert '"t2a_classification": t2a_classification' in source, (
            "T2a 结果必须真的写进 findings"
        )
