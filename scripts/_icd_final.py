import json

m = json.load(open("data/medical_resources/udt_icd_map.json", encoding="utf-8"))["mapping"]
missing = [k for k, v in m.items() if v["confidence"] == "missing"]
print("final missing:", missing)
