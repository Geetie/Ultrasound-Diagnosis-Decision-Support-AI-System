"""T-GA-EDG-01 边缘端 DICOM SCP（解码 + 帧交付 + 延迟埋点）单测。"""
import numpy as np
import pytest

from edge.dicom_scp import (
    STATUS_PROCESSING_FAILURE,
    STATUS_SUCCESS,
    DicomSCPService,
    PixelDecodeError,
    PydicomPixelDecoder,
)

pytestmark = pytest.mark.track_edg


class _FakeDataset:
    """最小可用的数据集替身（只覆盖 SCP 需要读取的字段）。"""

    def __init__(self, pixels=None, fail=False, **attrs):
        self.pixel_array = pixels if pixels is not None else np.zeros((8, 8), dtype=np.uint8)
        self._fail = fail
        for key, value in attrs.items():
            setattr(self, key, value)

    def __getattribute__(self, name):
        if name == "pixel_array":
            fail = object.__getattribute__(self, "_fail")
            if fail:
                raise ValueError("pixel decode failed")
        return object.__getattribute__(self, name)


def _dataset(**attrs):
    return _FakeDataset(
        StudyInstanceUID="1.2.3.4",
        SOPInstanceUID="1.2.3.4.5",
        AccessionNumber="acc_001",
        AcquisitionDate="20260923",
        AcquisitionTime="101112.500",
        **attrs,
    )


class _RecordingDecoder:
    name = "recording"

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def decode(self, dataset):
        self.calls += 1
        if self.fail:
            raise PixelDecodeError("boom")
        return np.ones((4, 4), dtype=np.uint8)


class TestHandleStore:
    def test_success_delivers_frame(self):
        delivered = []
        service = DicomSCPService(
            frame_handler=delivered.append, decoder=_RecordingDecoder()
        )

        status = service.handle_store(_dataset())

        assert status == STATUS_SUCCESS
        assert len(delivered) == 1

        frame = delivered[0]
        assert frame.frame_id == "1.2.3.4.5"
        assert frame.session_id == "acc_001"
        assert frame.index == 1
        assert frame.pixels.shape == (4, 4)
        assert frame.acquisition_datetime == "20260923101112"
        assert service.stats.frames_delivered == 1

    def test_decode_failure_returns_failure_status(self):
        delivered = []
        service = DicomSCPService(
            frame_handler=delivered.append, decoder=_RecordingDecoder(fail=True)
        )

        status = service.handle_store(_dataset())

        assert status == STATUS_PROCESSING_FAILURE
        assert delivered == []
        assert service.stats.stores_failed == 1
        assert service.stats.frames_delivered == 0

    def test_handler_exception_returns_failure_status(self):
        def _boom(frame):
            raise RuntimeError("handler down")

        service = DicomSCPService(frame_handler=_boom, decoder=_RecordingDecoder())

        status = service.handle_store(_dataset())

        assert status == STATUS_PROCESSING_FAILURE
        assert service.stats.stores_failed == 1

    def test_frame_index_increments_per_store(self):
        delivered = []
        service = DicomSCPService(
            frame_handler=delivered.append, decoder=_RecordingDecoder()
        )

        service.handle_store(_dataset())
        service.handle_store(_dataset())

        assert [frame.index for frame in delivered] == [1, 2]

    def test_works_without_frame_handler(self):
        service = DicomSCPService(decoder=_RecordingDecoder())

        status = service.handle_store(_dataset())

        assert status == STATUS_SUCCESS
        assert service.stats.frames_delivered == 1


class TestFieldExtraction:
    def test_falls_back_to_study_uid_when_accession_missing(self):
        delivered = []
        service = DicomSCPService(
            frame_handler=delivered.append, decoder=_RecordingDecoder()
        )
        dataset = _dataset()
        del dataset.AccessionNumber

        service.handle_store(dataset)

        assert delivered[0].session_id == "1.2.3.4"

    def test_falls_back_to_instance_number_for_frame_id(self):
        delivered = []
        service = DicomSCPService(
            frame_handler=delivered.append, decoder=_RecordingDecoder()
        )
        dataset = _dataset(InstanceNumber=42)
        del dataset.SOPInstanceUID

        service.handle_store(dataset)

        assert delivered[0].frame_id == "42"

    def test_missing_datetime_yields_none(self):
        delivered = []
        service = DicomSCPService(
            frame_handler=delivered.append, decoder=_RecordingDecoder()
        )
        dataset = _dataset()
        del dataset.AcquisitionDate
        del dataset.AcquisitionTime

        service.handle_store(dataset)

        assert delivered[0].acquisition_datetime is None


class TestLatencyStats:
    def test_stats_empty_before_any_store(self):
        service = DicomSCPService(decoder=_RecordingDecoder())

        assert service.latency_stats()["samples"] == 0

    def test_stats_populated_after_store(self):
        service = DicomSCPService(decoder=_RecordingDecoder())

        service.handle_store(_dataset())

        stats = service.latency_stats()
        assert stats["samples"] == 1
        assert stats["p50_ms"] is not None
        assert stats["p99_ms"] >= stats["p50_ms"]


class TestPydicomDecoder:
    def test_decodes_plain_pixel_array(self):
        decoder = PydicomPixelDecoder()
        dataset = _FakeDataset()

        pixels = decoder.decode(dataset)

        assert pixels.shape == (8, 8)

    def test_decode_error_is_wrapped(self):
        decoder = PydicomPixelDecoder()

        with pytest.raises(PixelDecodeError):
            decoder.decode(_FakeDataset(fail=True))
