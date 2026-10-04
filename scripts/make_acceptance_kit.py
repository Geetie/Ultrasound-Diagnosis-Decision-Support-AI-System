"""生成交互核验用的测试影像包（多器官 / 多场景 / 含 ground truth）。

为什么需要
----------
交付核验要人跑，但核验人需要一组**可复现**的输入：同一份影像每次跑出的结论
应当一致，且要覆盖"正常 / 良性 / 恶性可疑 / 边界情况"与多个器官。

图像来源（全部公开或自造，无真实患者数据）
------------------------------------------
- 甲状腺：TN3K 官方 test 集（NC 许可，非商用演示）
- 心脏：CAMUS-Lite（CC-BY-NC-SA-4.0，学术非商用）
- 肝脏：**无公开超声数据** → 程序合成，仅验证界面与链路可走通，
  **不得用于评估肝脏模型精度**（合成纹理与真实超声差异极大）。

产出
----
``<out>/dicom/*.dcm``        合规 US DICOM（Modality=US、必需标签、PixelSpacing）
``<out>/manifest.json``      每张的器官/来源/许可/预期检查点
``<out>/clinical_scenarios.json``  配套临床情景文本（可直接粘进界面）

用法（在服务器上）：python scripts/make_acceptance_kit.py --out ~/acceptance_kit
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

OUT_DEFAULT = Path(os.path.expanduser("~/acceptance_kit"))
TN3K_DIR = Path(os.path.expanduser("~/uai/data/tn3k_dataset/test-image"))
CAMUS_DIR = Path(os.path.expanduser("~/uai/data/camus/Masks"))


def pick_tn3k(n: int):
    """从 TN3K 官方 test 集挑 n 张（分散取，避免都来自同一病人）。"""
    if not TN3K_DIR.is_dir():
        return []
    files = sorted(
        p for p in TN3K_DIR.iterdir()
        if p.suffix.lower() in (".jpg", ".png", ".jpeg")
    )
    if not files:
        return []
    step = max(1, len(files) // n)
    return [(f"thyroid_{i:02d}", files[i * step]) for i in range(min(n, len(files)))]


def make_synthetic_liver(path: Path, seed: int = 7) -> dict:
    """合成一张肝脏超声图（程序生成，非真实数据）。

    明确用途：**仅验证界面与链路可走通**，不得用于任何精度评估。
    """
    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(seed)
    h = w = 512
    img = rng.normal(38, 9, (h, w)).astype(np.float32)   # 散斑噪声底
    yy, xx = np.mgrid[0:h, 0:w]
    liver = ((xx - 150) ** 2 / 200**2 + (yy - 260) ** 2 / 150**2) < 1
    img[liver] += 22                                       # 肝实质
    for y0 in (150, 300):                                  # 血管（高回声管状）
        img[np.abs(yy - (y0 + 0.25 * xx)) < 7] += 95
    img[((xx - 330) ** 2 + (yy - 210) ** 2) < 34**2] -= 26  # 低回声结节
    arr = np.clip(img, 0, 255).astype(np.uint8)
    Image.fromarray(arr).save(path)
    return {
        "modality_note": "合成图（散斑 + 肝实质 + 血管 + 低回声结节）",
        "usable_for": "界面 / 链路走通验证",
        "not_usable_for": "任何精度评估——合成纹理与真实超声差异极大",
    }
