"""把超声数据策展目录（ultrasound-open-access.nidusai.ca）快照成 CSV 归档。

对应 14 号文档待办 B1："把策展目录 CSV 快照归档进仓库，形成数据源台账"。

页面是 Next.js SSG，数据集清单嵌在 `__NEXT_DATA__` JSON 里；本脚本递归找到
"Dataset Name" 键的条目数组，平铺导出 CSV（含 refreshedAt 记录在文件头注释列）。

用法：
    python scripts/snapshot_open_access_catalog.py
输出：
    docs/合规/数据源台账/open-access-catalog-<YYYYMMDD>.csv
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import pathlib
import sys
import urllib.request

CATALOG_URL = "https://ultrasound-open-access.nidusai.ca/"
OUT_DIR = (
    pathlib.Path(__file__).resolve().parent.parent
    / "docs" / "合规" / "数据源台账"
)
NAME_KEY = "Dataset Name"


def _find_dataset_arrays(node: object, found: list[list[dict]]) -> None:
    if isinstance(node, list):
        if any(isinstance(item, dict) and NAME_KEY in item for item in node):
            found.append(node)
        for item in node:
            _find_dataset_arrays(item, found)
    elif isinstance(node, dict):
        for value in node.values():
            _find_dataset_arrays(value, found)


def main() -> int:
    req = urllib.request.Request(
        CATALOG_URL, headers={"User-Agent": "uai-ledger/1.0"}
    )
    html = urllib.request.urlopen(req, timeout=60).read().decode("utf-8")

    marker = '<script id="__NEXT_DATA__" type="application/json">'
    start = html.find(marker)
    if start < 0:
        print("ERROR: 未找到 __NEXT_DATA__ 脚本，页面结构可能已变化", file=sys.stderr)
        return 1
    start += len(marker)
    end = html.find("</script>", start)
    payload = json.loads(html[start:end])

    arrays: list[list[dict]] = []
    _find_dataset_arrays(payload, arrays)
    if not arrays:
        print("ERROR: 未找到数据集条目数组", file=sys.stderr)
        return 1
    # 取条目最多的一组（避免命中页面上其他零散数组）
    datasets = max(arrays, key=len)

    def _refreshed_at(node: object) -> str:
        if isinstance(node, dict):
            if "refreshedAt" in node:
                return str(node["refreshedAt"])
            for value in node.values():
                got = _refreshed_at(value)
                if got:
                    return got
        elif isinstance(node, list):
            for item in node:
                got = _refreshed_at(item)
                if got:
                    return got
        return ""

    refreshed_at = _refreshed_at(payload)

    fieldnames: list[str] = []
    for item in datasets:
        for key in item:
            if key not in fieldnames:
                fieldnames.append(key)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().strftime("%Y%m%d")
    out_path = OUT_DIR / f"open-access-catalog-{today}.csv"
    with out_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(datasets)

    print(f"条目数：{len(datasets)}  站点refreshedAt：{refreshed_at or '未标注'}")
    print(f"已写出：{out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
