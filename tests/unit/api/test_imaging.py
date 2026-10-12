
import pytest

from app.api.v1.imaging import (
    REQUIRED_DICOM_TAGS,
    _desensitize_patient_id,
    _desensitize_patient_name,
    generate_frame_id,
    generate_image_id,
    parse_and_validate_dicom,
    validate_dicom_format,
)

pytestmark = pytest.mark.track_api


class TestValidateDICOMFormat:
    def test_valid_dicom_magic_word(self):
        content = b"\x00" * 128 + b"DICM" + b"\x00" * 100
        is_valid, error = validate_dicom_format(content)
        assert is_valid is True
        assert error is None

    def test_file_too_small(self):
        content = b"\x00" * 50
        is_valid, error = validate_dicom_format(content)
        assert is_valid is False
        # A.52：文案中文化（原文 File too small 会原样显示给医生）
        assert "文件过小" in error

    def test_invalid_magic_word(self):
        content = b"\x00" * 128 + b"XXXX" + b"\x00" * 100
        is_valid, error = validate_dicom_format(content)
        assert is_valid is False
        assert "不是 DICOM 文件" in error

    def test_exactly_132_bytes_valid(self):
        content = b"\x00" * 128 + b"DICM"
        is_valid, error = validate_dicom_format(content)
        assert is_valid is True

    def test_exactly_131_bytes_invalid(self):
        content = b"\x00" * 131
        is_valid, error = validate_dicom_format(content)
        assert is_valid is False


class TestDesensitizePatientInfo:
    def test_patient_id_normal(self):
        result = _desensitize_patient_id("P12345")
        assert result == "P******"
        assert len(result) == 7

    def test_patient_id_empty(self):
        result = _desensitize_patient_id("")
        assert result == ""

    def test_patient_name_normal(self):
        result = _desensitize_patient_name("张三")
        assert result == "张******"

    def test_patient_name_empty(self):
        result = _desensitize_patient_name("")
        assert result == ""

    def test_patient_id_single_char(self):
        result = _desensitize_patient_id("A")
        assert result == "A******"


class TestRequiredDICOMTags:
    def test_all_10_tags_defined(self):
        assert len(REQUIRED_DICOM_TAGS) == 10

    def test_modality_tag_present(self):
        modality_tags = [t for t in REQUIRED_DICOM_TAGS if t[2] == "Modality"]
        assert len(modality_tags) == 1
        assert modality_tags[0][0:2] == (0x0008, 0x0060)

    def test_pixel_data_tag_present(self):
        pixel_tags = [t for t in REQUIRED_DICOM_TAGS if t[2] == "PixelData"]
        assert len(pixel_tags) == 1
        assert pixel_tags[0][0:2] == (0x7FE0, 0x0010)


class TestGenerateIds:
    def test_image_id_format(self):
        iid = generate_image_id()
        assert iid.startswith("img_")
        parts = iid.split("_")
        assert len(parts) == 3
        assert len(parts[1]) == 8
        assert len(parts[2]) == 8

    def test_frame_id_format(self):
        fid = generate_frame_id()
        assert fid.startswith("frame_")
        parts = fid.split("_")
        assert len(parts) == 3

    def test_image_id_uniqueness(self):
        ids = {generate_image_id() for _ in range(50)}
        assert len(ids) == 50

    def test_frame_id_uniqueness(self):
        ids = {generate_frame_id() for _ in range(50)}
        assert len(ids) == 50


class TestParseAndValidateDICOM:
    def test_invalid_dicom_content(self):
        content = b"\x00" * 200
        dataset, error, missing = parse_and_validate_dicom(content)
        assert dataset is None
        assert error is not None

    def test_required_tags_count(self):
        tag_names = [t[2] for t in REQUIRED_DICOM_TAGS]
        assert "PatientName" in tag_names
        assert "PatientID" in tag_names
        assert "Modality" in tag_names
        assert "PixelData" in tag_names
        assert "BitsAllocated" in tag_names
