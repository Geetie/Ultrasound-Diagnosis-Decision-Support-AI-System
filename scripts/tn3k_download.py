"""TN3K 下载器（可复现 / 幂等 / sha256 逐文件校验）。

源：hf-mirror（huggingface.co 在本网络不通）
repo: haifan-gong/TN3K

流程：
  1. 走 HF tree API（分页跟随 Link: rel="next"）拿权威文件清单 + LFS oid/size
  2. 对每个文件：本地已存在且 sha256 == oid → 跳过；否则 GET resolve 下载
  3. 下载后按 LFS 声明的 size 与 sha256 双向校验，不符则重试，最终失败登记
  4. 结束时打印 fixed/ok/fail 与 PIL 可读性

用法：python tn3k_download.py [--target DIR] [--verify-only] [--workers N]
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ENDPOINT = "https://hf-mirror.com"
REPO = "haifan-gong/TN3K"
API_TREE = f"{ENDPOINT}/api/datasets/{REPO}/tree/main"
RESOLVE = f"{ENDPOINT}/datasets/{REPO}/resolve/main"
SUBDIRS = ["test-image", "test-mask", "trainval-image", "trainval-mask"]
META = [
    "README.md",
    ".gitattributes",
    "label4test.csv",
    "label4trainval.csv",
    "tn3k-trainval-fold0.json",
    "tn3k-trainval-fold1.json",
    "tn3k-trainval-fold2.json",
    "tn3k-trainval-fold3.json",
    "tn3k-trainval-fold4.json",
]


def list_dir(sub: str) -> list[dict]:
    """分页取子目录下全部文件，返回 [{name, size, sha256}]。"""
    url = f"{API_TREE}/{urllib.parse.quote(sub)}?recursive=true"
    out: list[dict] = []
    while url:
        req = urllib.request.Request(url, headers={"User-Agent": "tn3k-dl/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            items = json.loads(resp.read())
            nxt = None
            for part in (resp.headers.get("Link") or "").split(","):
                if 'rel="next"' in part:
                    nxt = part.split(">")[0].strip().lstrip("<")
        for it in items:
            if it.get("type") != "file":
                continue
            lfs = it.get("lfs") or {}
            out.append(
                {
                    "name": it["path"].split("/")[-1],
                    "size": lfs.get("size") or it.get("size") or 0,
                    # 有 LFS 时用 LFS 的 sha256；无 LFS 时 git blob 是 sha1，不能当 sha256 用
                    "sha256": lfs.get("oid") if lfs else None,
                }
            )
        url = nxt
    return out


def sha256_of(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url: str, attempts: int = 3) -> bytes | None:
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "tn3k-dl/1.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001 - 下载失败要重试并最终上报
            if i == attempts - 1:
                print(f"    FAIL {url}: {type(exc).__name__}: {exc}", flush=True)
                return None
            time.sleep(2 * (i + 1))
    return None


def handle(item: tuple[str, str, int, str | None], target: Path, verify_only: bool) -> tuple[str, str]:
    """返回 (状态, 说明)。状态 ∈ skip/ok/fail。"""
    sub, name, size, sha = item
    dest = target / sub / name
    if dest.exists() and dest.stat().st_size == size and (sha is None or sha256_of(dest) == sha):
        return ("skip", "")
    if verify_only:
        return ("fail", "missing/mismatch (verify-only)")
    data = fetch(f"{RESOLVE}/{sub}/{name}")
    if data is None:
        return ("fail", "download error")
    if len(data) != size:
        return ("fail", f"size {len(data)} != {size}")
    if sha is not None and hashlib.sha256(data).hexdigest() != sha:
        return ("fail", "sha256 mismatch")
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return ("ok", "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default=str(Path.home() / "uai" / "data" / "tn3k"))
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--verify-only", action="store_true")
    args = ap.parse_args()
    target = Path(args.target)

    print(f"target = {target}")
    print(f"endpoint = {ENDPOINT}  repo = {REPO}")
    target.mkdir(parents=True, exist_ok=True)

    # 元数据
    for m in META:
        p = target / m
        if p.exists() and p.stat().st_size > 0:
            continue
        if args.verify_only:
            continue
        d = fetch(f"{RESOLVE}/{m}")
        if d:
            p.write_bytes(d)
            print(f"  meta ok {m}")

    # 文件清单
    items: list[tuple[str, str, int, str | None]] = []
    for sub in SUBDIRS:
        (target / sub).mkdir(parents=True, exist_ok=True)
        listing = list_dir(sub)
        print(f"  listing {sub}: {len(listing)} files")
        items.extend((sub, it["name"], it["size"], it["sha256"]) for it in listing)

    total = len(items)
    print(f"total files = {total}, workers = {args.workers}")

    stat = {"skip": 0, "ok": 0, "fail": 0}
    fails: list[str] = []
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(handle, it, target, args.verify_only): it for it in items}
        for i, fut in enumerate(cf.as_completed(futs), 1):
            st, msg = fut.result()
            stat[st] += 1
            if st == "fail":
                it = futs[fut]
                fails.append(f"{it[0]}/{it[1]}: {msg}")
            if i % 200 == 0 or i == total:
                print(f"  {i}/{total} skip={stat['skip']} ok={stat['ok']} fail={stat['fail']}", flush=True)

    print(f"RESULT skip={stat['skip']} downloaded={stat['ok']} failed={stat['fail']}")
    for f in fails[:20]:
        print("  FAIL:", f)

    # PIL 验收 + 体积
    from PIL import Image

    print("-" * 60)
    grand_bad = grand_total = 0
    for sub in SUBDIRS:
        d = target / sub
        files = sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() == ".jpg")
        bad = 0
        for p in files:
            try:
                with Image.open(p) as im:
                    im.load()
            except Exception:  # noqa: BLE001 - 验收要抓全部坏文件
                bad += 1
        grand_bad += bad
        grand_total += len(files)
        print(f"  PIL {sub}: {len(files) - bad}/{len(files)} readable" + (f" BAD={bad}" if bad else ""))
    print(f"PIL readability TOTAL: {grand_total - grand_bad}/{grand_total}")
    size_bytes = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
    print(f"disk = {size_bytes / 1024 / 1024:.1f} MiB")
    return 0 if stat["fail"] == 0 and grand_bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
