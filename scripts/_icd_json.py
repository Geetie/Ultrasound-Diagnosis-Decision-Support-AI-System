import json

import openpyxl

wb = openpyxl.load_workbook(
    "data/medical_resources/icd10/国家临床版2.0疾病诊断编码（ICD-10）.xlsx",
    read_only=True,
)
ws = wb.active
out = []
for code, name in ws.iter_rows(min_row=2, values_only=True):
    if code and name:
        out.append({"code": str(code).strip(), "name": str(name).strip()})
p = "data/medical_resources/icd10/icd10_clinical_v2.json"
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=0)
print("exported:", len(out), "entries ->", p)
