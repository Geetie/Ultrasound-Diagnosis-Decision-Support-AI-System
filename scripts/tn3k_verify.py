"""TN3K 全量 sha256 校验（带 HF tree API 分页）。

以 hf-mirror tree API 的 LFS oid(sha256) 为权威，逐文件比对本地文件。
只读，不改任何数据。

用法: python tn3k_verify.py [--target DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ENDPOINT = "https://hf-mirror.com"
REPO = "haifan-gong/TN3K"
SUBDIRS = ["test-image", "test-mask", "trainval-image", "trainval-mask"]


def list_all(sub: str) -> list[dict]:
    """分页跟随 Link rel=next，取全部文件条目。"""
    url = f"{ENDPOINT}/api/datasets/{REPO}/tree/main/{urllib.parse.quote(sub)}?recursive=true"
    out: list[dict] = []
    while url:
        req = urllib.request.Request(url, headers={"User-Agent": "tn3k-verify/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            items = json.loads(resp.read())
            nxt = None
            for part in (resp.headers.get("Link") or "").split(","):
                if 'rel="next"' in part:
                    nxt = part.split(">")[0].strip().lstrip("<")
        out.extend(it for it in items if it.get("type") == "file")
        url = nxt
    return out


def sha256_of(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for c in iter(lambda: fh.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default=str(Path.home() / "uai" / "data" / "tn3k_dataset"))
    args = ap.parse_args()
    data = Path(args.target)
    print(f"target = {data}")

    tot = ok = missing = mismatch = nolfs = 0
    bad: list[str] = []
    for sub in SUBDIRS:
        entries = list_all(sub)
        print(f"  manifest {sub}: {len(entries)} entries")
        tot += len(entries)
        for it in entries:
            name = it["path"].split("/")[-1]
            lfs = it.get("lfs") or {}
            oid = lfs.get("oid")
            p = data / sub / name
            if not p.exists():
                missing += 1
                bad.append(f"{sub}/{name}: MISSING")
                continue
            if not oid:
                nolfs += 1
                continue
            got = sha256_of(p)
            if got == oid:
                ok += 1
            else:
                mismatch += 1
                bad.append(f"{sub}/{name}: sha {got[:12]} != {oid[:12]}")

    print("-" * 60)
    print(f"TOTAL manifest={tot}  sha256_match={ok}  no_lfs_oid={nolfs}  "
          f"missing={missing}  mismatch={mismatch}")
    for b in bad[:20]:
        print("  FAIL:", b)
    verdict = "PASS" if (missing == 0 and mismatch == 0 and tot == ok + nolfs) else "FAIL"
    print("RESULT:", verdict)
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
