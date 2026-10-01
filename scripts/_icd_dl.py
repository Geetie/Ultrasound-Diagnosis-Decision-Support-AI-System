import urllib.request
import urllib.parse
from pathlib import Path

name = urllib.parse.quote("国家临床版2.0疾病诊断编码（ICD-10）.xlsx.7z")
url = (
    "https://raw.gitcode.com/open-source-toolkit/44edd/blobs/"
    "1d0f4c991ece21b938b75e07c511536c78ee1f0c/" + name
)
out = Path("data/medical_resources/icd10/icd10_clinical_v2.xlsx.7z")
out.parent.mkdir(parents=True, exist_ok=True)
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(req, timeout=120) as r, open(out, "wb") as f:
    f.write(r.read())
print("size:", out.stat().st_size)
