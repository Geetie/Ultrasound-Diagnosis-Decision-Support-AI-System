import pydicom
from io import BytesIO
from typing import List, Optional, Tuple


REQUIRED_DICOM_TAGS = [
    (0x0010, 0x0010, "PatientName"),
    (0x0010, 0x0020, "PatientID"),
    (0x0020, 0x000D, "StudyInstanceUID"),
    (0x0020, 0x000E, "SeriesInstanceUID"),
    (0x0008, 0x0018, "SOPInstanceUID"),
    (0x0008, 0x0060, "Modality"),
    (0x0028, 0x0010, "Rows"),
    (0x0028, 0x0011, "Columns"),
    (0x0028, 0x0100, "BitsAllocated"),
    (0x7FE0, 0x0010, "PixelData"),
]


def validate_dicom_format(content: bytes) -> Tuple[bool, Optional[str]]:
    if len(content) < 132:
        return False, "File too small"
    if content[128:132] != b"DICM":
        return False, "Invalid DICOM format"
    return True, None


def parse_and_validate_dicom(content: bytes) -> Tuple[Optional[pydicom.Dataset], Optional[str], List[str]]:
    try:
        ds = pydicom.dcmread(BytesIO(content), force=True)
    except Exception as e:
        return None, f"Failed to parse DICOM: {str(e)}", []

    missing_tags = []
    for group, elem, name in REQUIRED_DICOM_TAGS:
        if (group, elem) not in ds:
            missing_tags.append(name)

    if missing_tags:
        return None, f"Missing required DICOM tags: {', '.join(missing_tags)}", missing_tags

    modality = str(ds[0x0008, 0x0060].value).strip()
    if modality != "US":
        return None, f"Modality must be US, got {modality}", [f"Modality={modality}"]

    bits_allocated = int(ds[0x0028, 0x0100].value)
    if bits_allocated <= 0 or bits_allocated > 16:
        return None, f"BitsAllocated must be 1-16, got {bits_allocated}", [f"BitsAllocated={bits_allocated}"]

    return ds, None, []
