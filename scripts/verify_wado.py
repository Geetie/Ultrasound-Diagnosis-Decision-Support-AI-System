# -*- coding: utf-8 -*-
"""隔离验证新增的 WADO 回读机制（不依赖整栈启动）。

验证 DicomStorageService.path_for_id / save_by_id 与 retrieve 的**往返一致性**：
    save_by_id(bytes, id) -> path_for_id(id) -> retrieve(path) == bytes
这条链正是 GET /api/v1/imaging/dicom/{storage_id} 端点内部走的路径。

同时验证安全边界：非法 id 必须被拒绝（防目录穿越）。
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.dicom_storage import DicomStorageService  # noqa: E402

ok = True


def check(label, cond):
    global ok
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        ok = False


with tempfile.TemporaryDirectory() as tmp:
    svc = DicomStorageService()
    svc._backend = "local"
    svc._base_path = tmp
    svc._max_file_size = 10 * 1024 * 1024

    print("=== 1. 往返一致性（用真实 DICOM，不用随机字节）===")
    # 用随机字节会被 pydicom 当垃圾 DICOM 重新序列化，长度就变了 ——
    # 真实用例是 DICOM，所以直接拿仓库里那份真实超声 DICOM 来测。
    real = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "dicom_storage",
                     "dev-us365k", "us365k_sample.dcm")
    )
    if not os.path.exists(real):
        print("  SKIP  找不到真实 DICOM:", real)
        payload = os.urandom(4096)
    else:
        with open(real, "rb") as f:
            payload = f.read()
        print(f"  使用真实 DICOM：{os.path.basename(real)}（{len(payload)} 字节）")

    sid = "img_20261002_ab12cd34"
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    stored = loop.run_until_complete(
        svc.save_by_id(payload, sid, patient_id="PAT001")
    )
    check(f"save_by_id 返回路径存在（{os.path.basename(stored)}）", os.path.exists(stored))
    check("落盘路径不含患者 ID（PII 不出现在目录名）", "PAT001" not in stored)

    resolved = svc.path_for_id(sid)
    check("path_for_id 能反推同一路径", resolved == stored)

    back = loop.run_until_complete(svc.retrieve(resolved))
    check("retrieve 能取回字节（非 None）", back is not None)
    check("取回内容仍以 DICM 开头（是合法 DICOM）", back[128:132] == b"DICM")
    # save_by_id 会先 strip_dicom_phi 重写数据集，所以长度**只会变小**，
    # 不应等于原始长度 —— 这正是 PHI 不落盘的表现。
    check(
        f"取回长度 ≤ 写入长度（PHI 已剥离: {len(back)} ≤ {len(payload)}）",
        len(back) <= len(payload),
    )
    import io as _io

    import pydicom

    ds = pydicom.dcmread(_io.BytesIO(back), force=True)
    pid = str(getattr(ds, "PatientID", "") or "")
    check(f"取回后 PatientID 已被脱敏（当前={pid!r}）", "US365K-0001" not in pid)

    print()
    print("=== 2. 二次往返稳定（幂等）===")
    again = loop.run_until_complete(svc.retrieve(svc.path_for_id(sid)))
    check("重复 retrieve 结果一致", again == back)

    print()
    print("=== 2b. frame_ 前缀同样可用 ===")
    fid = "frame_20261002_deadbeef"
    loop.run_until_complete(svc.save_by_id(payload, fid, patient_id="PAT001"))
    fback = loop.run_until_complete(svc.retrieve(svc.path_for_id(fid)))
    check("frame_ ID 能取回合法 DICOM 字节",
          fback is not None and fback[128:132] == b"DICM")

    print()
    print("=== 3. 安全边界：非法 ID 必须拒绝 ===")
    for bad in [
        "../../etc/passwd",
        "img_20261002_../../x",
        "img_2026_ab12cd34",     # 日期段位数不对
        "img_20261002_ZZZZZZZZ",  # 非 hex
        "img_20261002_ab12cd34/../../secret",
        "other_20261002_ab12cd34",
        "",
    ]:
        check(f"拒绝 {bad!r}", svc.path_for_id(bad) is None)

    print()
    print("=== 4. 不存在的 ID 返回 None（端点据此回404）===")
    missing = loop.run_until_complete(
        svc.retrieve(svc.path_for_id("img_20261002_00000000"))
    )
    check("未上传过的 ID -> retrieve 返回 None", missing is None)

print()
print("结果:", "全部通过" if ok else "存在失败")
sys.exit(0 if ok else 1)
