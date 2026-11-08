"""DDTI 解析与验收：解压 zip、解析 XML 标注、生成分割掩码、PIL 验收。

标注格式: <case><number>..<age><sex><composition><echogenicity><margins>
          <calcifications><tirads>...<mark><image>N</image><svg>[...points...]</svg></mark>
image N 对应文件 {case}_{N}.jpg
"""
from __future__ import annotations

import json
import sys
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image, ImageDraw

DATA = Path.home() / "uai" / "data" / "ddti"
ZIP = DATA / "ddti.zip"
RAW = DATA / "raw"
OUT = DATA / "meta"


def parse_case(xml_path: Path) -> dict:
    root = ET.parse(xml_path).getroot()
    get = lambda tag: (root.findtext(tag) or "").strip()  # noqa: E731
    marks = []
    for mark in root.findall("mark"):
        img_no = (mark.findtext("image") or "").strip()
        svg = mark.findtext("svg") or ""
        polys = []
        try:
            for region in json.loads(svg) if svg.strip() else []:
                pts = [(float(p["x"]), float(p["y"])) for p in region.get("points", [])]
                if len(pts) >= 3:
                    polys.append(pts)
        except json.JSONDecodeError:
            pass
        marks.append({"image": img_no, "polygons": polys})
    return {
        "case": get("number") or xml_path.stem,
        "age": get("age"),
        "sex": get("sex"),
        "composition": get("composition"),
        "echogenicity": get("echogenicity"),
        "margins": get("margins"),
        "calcifications": get("calcifications"),
        "tirads": get("tirads"),
        "marks": marks,
    }


def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(ZIP) as z:
        z.extractall(RAW)

    xmls = sorted(RAW.glob("*.xml"))
    jpgs = sorted(RAW.glob("*.jpg"))
    print(f"xml={len(xmls)} jpg={len(jpgs)}")

    # ---- PIL 验收 ----
    bad: list[str] = []
    sizes: dict[str, tuple[int, int]] = {}
    for p in jpgs:
        try:
            with Image.open(p) as im:
                im.load()
                sizes[p.name] = im.size
        except Exception as exc:
            bad.append(f"{p.name}: {type(exc).__name__}: {exc}")
    print(f"PIL readable {len(jpgs) - len(bad)}/{len(jpgs)}" + ("" if not bad else f" BAD={len(bad)}"))
    for b in bad[:10]:
        print("  BAD:", b)

    # ---- 解析标注 ----
    cases = []
    for x in xmls:
        try:
            cases.append(parse_case(x))
        except Exception as exc:
            print(f"  XML FAIL {x.name}: {type(exc).__name__}: {exc}")

    # image -> case 映射，检查标注图是否都存在
    ann_files: set[str] = set()
    orphan_marks = 0
    for c in cases:
        for m in c["marks"]:
            fn = f"{c['case']}_{m['image']}.jpg"
            if fn in sizes:
                ann_files.add(fn)
            else:
                orphan_marks += 1

    # 字段覆盖
    def cov(field: str) -> int:
        return sum(1 for c in cases if c[field])

    print("-" * 60)
    print("标注覆盖:")
    for f in ["age", "sex", "composition", "echogenicity", "margins", "calcifications", "tirads"]:
        print(f"  {f}: {cov(f)}/{len(cases)}")
    print(f"  XML 声明标注图 {len(ann_files)}/{len(jpgs)} 张有对应 jpg；孤儿 mark={orphan_marks}")
    nopoly = sum(1 for c in cases for m in c["marks"] if not m["polygons"])
    print(f"  多边形为空/不可解析的 mark = {nopoly}")
    tirads = Counter(c["tirads"] for c in cases)
    print(f"  TIRADS 分布: {dict(sorted(tirads.items(), key=lambda kv: str(kv[0])))}")

    # ---- 生成掩码 ----
    mask_dir = DATA / "mask"
    mask_dir.mkdir(exist_ok=True)
    made = skipped = 0
    for c in cases:
        for m in c["marks"]:
            fn = f"{c['case']}_{m['image']}.jpg"
            if fn not in sizes or not m["polygons"]:
                skipped += 1
                continue
            w, h = sizes[fn]
            mask = Image.new("L", (w, h), 0)
            dr = ImageDraw.Draw(mask)
            for poly in m["polygons"]:
                dr.polygon([(x, y) for x, y in poly], fill=255)
            mask.save(mask_dir / fn.replace(".jpg", ".png"))
            made += 1
    print(f"掩码生成: made={made} skipped={skipped}")

    # ---- 落盘清单 ----
    (OUT / "ddti_cases.json").write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"清单: {OUT / 'ddti_cases.json'}")

    size_bytes = sum(f.stat().st_size for f in DATA.rglob("*") if f.is_file())
    print(f"disk = {size_bytes / 1024 / 1024:.1f} MiB")
    ok = not bad and orphan_marks == 0 and made > 0
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
