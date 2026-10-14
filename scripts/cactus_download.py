"""CACTUS 数据集并发下载器（FRDR，CC BY 4.0）。

T2b 真模型头数据源：29,963 张五类心脏标准切面图（A4C/SC/PL/PSAV/PSMV + Random）
+ 专家 1~10 质量分级 CSV。下载跳过 Videos（M1/M6 不需要）。

用法（真机 ~/uai/src）：
    ~/uai/venv/bin/python scripts/cactus_download.py \
        --checksums /tmp/_cactus_ck.txt \
        --dest ~/uai/data/cactus \
        --workers 32
支持断点续传（已存在且非空的文件跳过）。
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

BASE = (
    "https://g-772fa5.cd4fe.0ec8.data.globus.org"
    "/1/published/publication_1479/submitted_data"
)

_print_lock = threading.Lock()
_stats = {"ok": 0, "skip": 0, "fail": 0, "bytes": 0}


def parse_checksums(path: Path) -> list[str]:
    rels: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("Filename/Fichier: "):
            rels.append(line.removeprefix("Filename/Fichier: ").strip())
    return rels


def wanted(rel: str) -> bool:
    """图片 + 分级 CSV + 元文档；跳过视频。"""
    low = rel.lower()
    if low.endswith((".mp4",)):
        return False
    return bool(low.endswith((".jpg", ".csv", ".txt")))


def fetch_one(rel: str, dest_root: Path, session: requests.Session) -> str:
    dest = dest_root / rel
    if dest.exists() and dest.stat().st_size > 0:
        with _print_lock:
            _stats["skip"] += 1
        return "skip"

    dest.parent.mkdir(parents=True, exist_ok=True)
    url = f"{BASE}/{urllib.parse.quote(rel)}"
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            with session.get(url, timeout=(10, 120)) as r:
                r.raise_for_status()
                data = r.content
            if not data:
                raise ValueError("empty body")
            dest.write_bytes(data)
            with _print_lock:
                _stats["ok"] += 1
                _stats["bytes"] += len(data)
            return "ok"
        except Exception as exc:
            logger.warning("fetch_one: 未预期异常，按原逻辑降级", exc_info=True)
            last_err = exc
            time.sleep(2**attempt)
    dest.unlink(missing_ok=True)
    with _print_lock:
        _stats["fail"] += 1
    return f"fail: {rel} ({last_err})"


def main() -> int:
    parser = argparse.ArgumentParser(description="CACTUS 并发下载器")
    parser.add_argument("--checksums", required=True)
    parser.add_argument("--dest", required=True)
    parser.add_argument("--workers", type=int, default=32)
    args = parser.parse_args()

    dest_root = Path(args.dest).expanduser()
    rels = [r for r in parse_checksums(Path(args.checksums)) if wanted(r)]
    print(f"[cactus-dl] 任务数 {len(rels)}，目标 {dest_root}，并发 {args.workers}", flush=True)

    session = requests.Session()
    failures: list[str] = []
    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(fetch_one, r, dest_root, session): r for r in rels}
        for fut in as_completed(futs):
            result = fut.result()
            done += 1
            if result.startswith("fail"):
                failures.append(result)
            if done % 500 == 0 or done == len(rels):
                elapsed = time.time() - t0
                mb = _stats["bytes"] / 1e6
                print(
                    f"[cactus-dl] {done}/{len(rels)} ok={_stats['ok']} "
                    f"skip={_stats['skip']} fail={_stats['fail']} "
                    f"{mb:.0f}MB {elapsed:.0f}s",
                    flush=True,
                )

    if failures:
        Path("/tmp/cactus_failures.txt").write_text("\n".join(failures), encoding="utf-8")
        print(f"[cactus-dl] 失败 {len(failures)} 条，清单 /tmp/cactus_failures.txt", flush=True)
        return 1
    print("[cactus-dl] 全部完成", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
