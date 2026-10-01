import json

icd = json.load(open("data/medical_resources/icd10/icd10_clinical_v2.json", encoding="utf-8"))
# 人工补录 2 个 missing：底表前缀检索确认有无近似编码
for prefix, label in (("E05.1", "毒性甲状腺肿/腺瘤"), ("I50.1", "右心衰竭")):
    hits = [e for e in icd if e["code"].startswith(prefix)][:4]
    print(f"--- {prefix} ({label}):")
    for h in hits:
        print("  ", h["code"], h["name"][:40])
