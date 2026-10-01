import json

d = json.load(open("data/medical_resources/udt_icd_map.json", encoding="utf-8"))
m = d["mapping"]
print("=== missing:")
for k, v in m.items():
    if v["confidence"] == "missing":
        print(" ", k)
print("=== multi 示例（前 3）:")
n = 0
for k, v in m.items():
    if v["confidence"] == "multi" and n < 3:
        print(" ", k, "->", v["icd10"], v["name"][:30])
        n += 1
