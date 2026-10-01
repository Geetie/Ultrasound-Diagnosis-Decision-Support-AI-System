import py7zr

with py7zr.SevenZipFile("data/medical_resources/icd10/icd10_clinical_v2.xlsx.7z") as z:
    z.extractall("data/medical_resources/icd10/")
import os
for f in os.listdir("data/medical_resources/icd10"):
    print(f, os.path.getsize(os.path.join("data/medical_resources/icd10", f)))
