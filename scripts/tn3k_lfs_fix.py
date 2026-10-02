"""TN3K test 集 LFS 指针修复：从 hf-mirror resolve 逐文件拉真实内容并 sha256 校验。

本地 test-image/test-mask 下 614+614 个文件是 Git LFS 指针（130B），
指针含 oid sha256:<hash> 与 size。源 repo = haifan-gong/TN3K。
逐文件 GET resolve URL（跟随 302 到 LFS 存储），sha256 对 oid 校验后
原子替换。16 并行 + 重试。最后 PIL 全量可读性验收。
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import time
import urllib.request
from pathlib import Path

REPO = "haifan-gong/TN3K"
BASE = f"https://hf-mirror.com/datasets/{REPO}/resolve/main"
DATA = Path("/mnt/ee16/home/student03/uai/data/tn3k_dataset")


def parse_pointer(p: Path) -> tuple[str, int] | None:
    """返回 (oid, size)；已是真实文件则 None。"""
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None  # 二进制 = 真实文件
    if not text.startswith("version https://git-lfs"):
        return None
    oid = size = None
    for line in text.splitlines():
        if line.startswith("oid sha256:"):
            oid = line.split(":", 1)[1].strip()
        elif line.startswith("size "):
            size = int(line.split()[1])
    if oid and size:
        return (oid, size)
    return None


def fetch_one(sub: str, name: str, oid: str, size: int, attempts: int = 3) -> tuple[str, bool, str]:
    url = f"{BASE}/{sub}/{name}"
    dest = DATA / sub / name
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()
            if len(data) != size:
                return (name, False, f"size mismatch {len(data)} != {size}")
            digest = hashlib.sha256(data).hexdigest()
            if digest != oid:
                return (name, False, f"sha256 mismatch {digest[:12]}")
            tmp.write_bytes(data)
            tmp.replace(dest)
            return (name, True, "ok")
        except Exception as exc:
            if attempt == attempts - 1:
                return (name, False, f"{type(exc).__name__}: {exc}")
            time.sleep(2 * (attempt + 1))
    return (name, False, "unreachable")


def main() -> int:
    subs = ["test-image", "test-mask"]
    jobs: list[tuple[str, str, str, int]] = []
    real = pointers = 0
    for sub in subs:
        for p in sorted((DATA / sub).iterdir()):
            info = parse_pointer(p)
            if info is None:
                real += 1
                continue
            pointers += 1
            oid, size = info
            jobs.append((sub, p.name, oid, size))
    print(f"real={real} pointer={pointers} jobs={len(jobs)}", flush=True)
    if not jobs:
        print("NOTHING TO FIX")
        return 0

    ok = fail = 0
    failures: list[str] = []
    with cf.ThreadPoolExecutor(max_workers=16) as pool:
        futs = [pool.submit(fetch_one, sub, name, oid, size) for sub, name, oid, size in jobs]
        for i, fut in enumerate(cf.as_completed(futs), 1):
            name, success, msg = fut.result()
            if success:
                ok += 1
            else:
                fail += 1
                failures.append(f"{name}: {msg}")
            if i % 100 == 0:
                print(f"  progress {i}/{len(jobs)} ok={ok} fail={fail}", flush=True)

    print(f"fixed={ok} failed={fail}", flush=True)
    for f in failures[:10]:
        print("  FAIL:", f)

    # PIL 可读性验收（test-image 全量）
    from PIL import Image

    bad = 0
    for p in sorted((DATA / "test-image").iterdir()):
        try:
            with Image.open(p) as img:
                img.load()
        except Exception:
            bad += 1
    print(f"PIL readability: {614 - bad}/614 ok, bad={bad}")
    return 0 if fail == 0 and bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
