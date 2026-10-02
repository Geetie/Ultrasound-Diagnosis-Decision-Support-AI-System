
import pydicom

from app.api.v1.imaging import (
    REQUIRED_DICOM_TAGS,
    _desensitize_patient_id,
    _desensitize_patient_name,
    extract_metadata_from_dataset,
)


class TestDesensitization:
    def test_desensitize_patient_id_normal(self):
        assert _desensitize_patient_id("PAT001") == "P******"

    def test_desensitize_patient_id_single_char(self):
        assert _desensitize_patient_id("A") == "A******"

    def test_desensitize_patient_id_empty(self):
        assert _desensitize_patient_id("") == ""

    def test_desensitize_patient_name_normal(self):
        assert _desensitize_patient_name("张三") == "张******"

    def test_desensitize_patient_name_empty(self):
        assert _desensitize_patient_name("") == ""

    def test_extract_metadata_patient_id_desensitized(self):
        ds = pydicom.Dataset()
        ds.PatientID = "PAT001"
        ds.PatientName = "TestPatient"
        ds.Modality = "US"
        ds.StudyInstanceUID = "1.2.3.4.5"
        ds.SeriesInstanceUID = "1.2.3.4.5.6"
        ds.SOPInstanceUID = "1.2.3.4.5.6.7"
        ds.Rows = 480
        ds.Columns = 640
        ds.BitsAllocated = 8

        response_metadata, internal_metadata, raw_patient_id = extract_metadata_from_dataset(ds, b"test_content")
        assert response_metadata["patient_id"] == "P******"
        assert internal_metadata["patient_name"] == "T******"
        assert raw_patient_id == "PAT001"

    def test_extract_metadata_raw_patient_id_preserved(self):
        ds = pydicom.Dataset()
        ds.PatientID = "SECRET123"
        ds.PatientName = "SecretName"
        ds.Modality = "US"
        ds.StudyInstanceUID = "1.2.3.4.5"
        ds.SeriesInstanceUID = "1.2.3.4.5.6"
        ds.SOPInstanceUID = "1.2.3.4.5.6.7"
        ds.Rows = 480
        ds.Columns = 640
        ds.BitsAllocated = 8

        response_metadata, internal_metadata, raw_patient_id = extract_metadata_from_dataset(ds, b"test_content")
        assert raw_patient_id == "SECRET123"
        assert response_metadata["patient_id"] != "SECRET123"


class TestDICOMUploadValidation:
    def test_validate_dicom_missing_required_tag(self):
        ds = pydicom.Dataset()
        ds.PatientID = "PAT001"
        ds.Modality = "US"
        content = b"\x00" * 128 + b"DICM" + b"\x00" * 100

        required_tag_names = [name for _, _, name in REQUIRED_DICOM_TAGS]
        missing = [tag for tag in required_tag_names if not hasattr(ds, tag)]
        assert len(missing) > 0
        assert "StudyInstanceUID" in missing

    def test_validate_dicom_wrong_modality(self):
        ds = pydicom.Dataset()
        ds.PatientID = "PAT001"
        ds.Modality = "CT"
        ds.StudyInstanceUID = "1.2.3"
        ds.SeriesInstanceUID = "1.2.3.4"
        ds.SOPInstanceUID = "1.2.3.4.5"
        ds.Rows = 480
        ds.Columns = 640
        ds.BitsAllocated = 8
        ds.PixelData = b"\x00" * 100

        assert ds.Modality != "US"

    def test_validate_dicom_bits_allocated_over_16(self):
        ds = pydicom.Dataset()
        ds.PatientID = "PAT001"
        ds.Modality = "US"
        ds.StudyInstanceUID = "1.2.3"
        ds.SeriesInstanceUID = "1.2.3.4"
        ds.SOPInstanceUID = "1.2.3.4.5"
        ds.Rows = 480
        ds.Columns = 640
        ds.BitsAllocated = 32
        ds.PixelData = b"\x00" * 100

        assert ds.BitsAllocated > 16

    def test_validate_dicom_valid(self):
        ds = pydicom.Dataset()
        ds.PatientID = "PAT001"
        ds.PatientName = "Test"
        ds.Modality = "US"
        ds.StudyInstanceUID = "1.2.3"
        ds.SeriesInstanceUID = "1.2.3.4"
        ds.SOPInstanceUID = "1.2.3.4.5"
        ds.Rows = 480
        ds.Columns = 640
        ds.BitsAllocated = 8
        ds.PixelData = b"\x00" * 100

        required_tag_names = [name for _, _, name in REQUIRED_DICOM_TAGS]
        missing = [tag for tag in required_tag_names if not hasattr(ds, tag)]
        assert len(missing) == 0
        assert ds.Modality == "US"
        assert ds.BitsAllocated <= 16
