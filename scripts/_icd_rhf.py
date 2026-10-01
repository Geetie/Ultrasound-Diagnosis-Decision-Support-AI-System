import json

icd = json.load(open("data/medical_resources/icd10/icd10_clinical_v2.json", encoding="utf-8"))
hits = [e for e in icd if "右心" in e["name"]]
for h in hits[:5]:
    print(h["code"], h["name"][:40])
