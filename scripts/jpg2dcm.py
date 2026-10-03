# -*- coding: utf-8 -*-
"""把 US-365K 的真实超声 JPEG 转成合规的单帧灰度 DICOM。

为什么需要转：仓库里 dicom_storage/*.dcm 那些文件是**人造桩**，只有 7 个标签，
缺 PhotometricInterpretation / BitsStored / PixelRepresentation / SamplesPerPixel，
任何 DICOM 阅读器都渲染不出来（实测 cornerstone 直接报"DICOM 加载失败"）。
US-365K 的是 .jpg，不是 DICOM，所以这里补齐必需标签封装成 US 影像。

输出写到 dicom_storage/dev-us365k/ —— 真实临床数据不该进前端产物，
dev-dicom 中间件只伺服这个子目录。
"""
import os

import numpy as np
from PIL import Image
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage, generate_uid

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "us365k_sample.jpg")
OUT_DIR = os.path.abspath(os.path.join(HERE, "..", "dicom_storage", "dev-us365k"))

os.makedirs(OUT_DIR, exist_ok=True)

img = Image.open(SRC).convert("L")  # 8-bit 灰度
arr = np.asarray(img, dtype=np.uint8)
rows, cols = arr.shape
print("source:", img.size, "mode L", rows, "cols")

ds = FileDataset(None, {}, file_meta=FileMetaDataset(), preamble=b"\0" * 128)
ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
ds.file_meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
ds.file_meta.ImplementationClassUID = generate_uid()

# --- 必需标签（桩文件缺的正是这些）---
ds.SOPClassUID = SecondaryCaptureImageStorage
ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID
ds.StudyInstanceUID = generate_uid()
ds.SeriesInstanceUID = generate_uid()
ds.Modality = "US"
ds.PhotometricInterpretation = "MONOCHROME2"
ds.SamplesPerPixel = 1
ds.BitsAllocated = 8
ds.BitsStored = 8
ds.HighBit = 7
ds.PixelRepresentation = 0
ds.Rows = rows
ds.Columns = cols
ds.PixelData = arr.tobytes()

# --- 超声相关 ---
ds.PatientName = "US365K^Sample"
ds.PatientID = "US365K-0001"
ds.PatientSex = "F"
ds.PatientAge = "052Y"
ds.StudyDate = "20261002"
ds.StudyTime = "142641"
ds.SeriesDescription = "US-365K abdominal sample"
ds.Manufacturer = "US-365K dataset"
ds.InstitutionName = "JJY-0823/US-365K"
ds.StudyDescription = (
    "A mass is identified in the gallbladder and bile ducts on sonographic "
    "evaluation of the abdomen and retroperitoneum."
)
# 窗外宽：给一个合理的显示窗，便于 cornerstone 直接出图
ds.WindowCenter = 128
ds.WindowWidth = 256

out = os.path.join(OUT_DIR, "us365k_sample.dcm")
ds.save_as(out, enforce_file_format=True)
print("SAVED", out, os.path.getsize(out), "bytes")

import pydicom  # 回读校验

chk = pydicom.dcmread(out)
print("verify: TS =", chk.file_meta.TransferSyntaxUID.name)
print("verify: Photo =", chk.PhotometricInterpretation, " Rows =", chk.Rows, " Cols =", chk.Columns)
print("verify: BitsStored =", chk.BitsStored, " PixelData len =", len(chk.PixelData))
