#!/usr/bin/env python3
"""真 PACS 对端（A.50.9 E13）：pynetdicom C-STORE Storage SCP。

pynetdicom = DICOM 上层协议参考实现（IHE 标准栈）：接收本系统推送的
DICOM 影像，按 SOPInstanceUID 归档到 ~/uai/pacs_store/。
「真实对端」语义：独立进程 + 标准 DICOM 协议 + 持久化归档 + 可独立读回。

运行（真机）：
    PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/pacs_scp.py
    # AE Title: UAI_PACS，监听 127.0.0.1:11112
"""
from __future__ import annotations

import sys
from pathlib import Path

from pydicom.uid import generate_uid
from pynetdicom import AE, AllStoragePresentationContexts, evt

STORE = Path.home() / "uai" / "pacs_store"
STORE.mkdir(parents=True, exist_ok=True)


def handle_store(event) -> int:
    """C-STORE 处理：按 SOPInstanceUID 归档（真 PACS 归档行为）。"""
    ds = event.dataset
    sop_uid = str(ds.get("SOPInstanceUID", "") or generate_uid())
    path = STORE / f"{sop_uid}.dcm"
    # 带 File Meta 归档（真 PACS 行为：DICM 前缀 + TransferSyntaxUID）
    ds.save_as(path, enforce_file_format=True)
    print(f"[pacs-scp] archived {sop_uid} ({path.stat().st_size}B)", flush=True)
    return 0x0000  # Success


def main() -> int:
    ae = AE(ae_title=b"UAI_PACS")
    ae.supported_contexts = AllStoragePresentationContexts
    scp = ae.start_server(
        ("127.0.0.1", 11112),
        evt_handlers=[(evt.EVT_C_STORE, handle_store)],
        block=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
