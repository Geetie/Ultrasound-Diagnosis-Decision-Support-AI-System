"""8003 ultrasound-clip 功能冒烟：文本/图像各编码 2 条，验证形状与跨模态相似度可区分。

判据（只打布尔，不出具体医学数值）：
- 文本向量 768 维、条数==texts 数
- 图像向量 768 维、条数==images 数
- 匹配图文对的余弦相似度应高于不匹配对（CLIP 联合空间的基本语义）
"""
import io

import numpy as np
import requests

BASE = "http://127.0.0.1:8003"

texts = ["a hyperechoic liver lesion", "an ultrasound probe on a desk"]
# 造两张有区分度的合成图（非患者数据）：白底黑圆 vs 黑白棋盘
from PIL import Image, ImageDraw  # noqa: E402


def make_disc() -> bytes:
    img = Image.new("RGB", (224, 224), "white")
    d = ImageDraw.Draw(img)
    d.ellipse((62, 62, 162, 162), fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_checker() -> bytes:
    img = Image.new("RGB", (224, 224), "white")
    d = ImageDraw.Draw(img)
    for y in range(0, 224, 32):
        for x in range(0, 224, 32):
            if (x // 32 + y // 32) % 2 == 0:
                d.rectangle((x, y, x + 31, y + 31), fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def l2(x: np.ndarray) -> np.ndarray:
    return x / np.linalg.norm(x)


t = requests.post(
    f"{BASE}/v1/embeddings", json={"texts": texts, "modality": "text"}, timeout=60
).json()["embeddings"]
img_payload = {
    "embeddings": [],
}
r1 = requests.post(
    f"{BASE}/v1/embeddings/image",
    files={"image": ("disc.png", make_disc(), "image/png")},
    data={"modality": "image"},
    timeout=60,
).json()
r2 = requests.post(
    f"{BASE}/v1/embeddings/image",
    files={"image": ("checker.png", make_checker(), "image/png")},
    data={"modality": "image"},
    timeout=60,
).json()

tv = [l2(np.array(v, dtype=np.float32)) for v in t]
iv = [l2(np.array(r1["embeddings"][0], dtype=np.float32)),
      l2(np.array(r2["embeddings"][0], dtype=np.float32))]

assert len(tv) == 2 and tv[0].shape == (768,), "文本向量形状错误"
assert len(iv) == 2 and iv[0].shape == (768,), "图像向量形状错误"

sims = np.array([[float(a @ b) for b in iv] for a in tv])
diag_mean = (sims[0, 0] + sims[1, 1]) / 2
off_mean = (sims[0, 1] + sims[1, 0]) / 2
print(f"text.shape={np.array(t).shape} image.shape={np.array([r1['embeddings'][0]]).shape}")
print(f"matched_cos_mean={diag_mean:.4f} unmatched_cos_mean={off_mean:.4f}")
print(f"PASS={diag_mean > off_mean}")
