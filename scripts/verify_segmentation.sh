#!/usr/bin/env bash
# 服务器同步 + 重启主服务 + 端到端验证结节分割端点
set -u
cd ~/uai/src

echo "=== 1. 端点健康（重启前）==="
curl -s -o /dev/null -w "seg_health=%{http_code}\n" -m 5 http://127.0.0.1:18000/api/v1/segmentation/health

echo "=== 2. 真机推理（TN3K test 首图，与 GT 比对）==="
~/uai/venv/bin/python - <<'PY'
import io, json
from pathlib import Path
from PIL import Image
import numpy as np

from app.config.database import settings  # noqa  (确保配置加载)
from app.inference.nodule_segmentation import NoduleSegmenter

DATA = Path.home() / "uai" / "data" / "tn3k_dataset"
seg = NoduleSegmenter.get()
print("health:", seg.health())

imgs = sorted((DATA / "test-image").glob("*.jpg"))[:5]
dscs, ious = [], []
for p in imgs:
    gt_path = DATA / "test-mask" / p.name
    r = seg.infer(image_path=p)
    pred = np.array(Image.open(io.BytesIO(r.mask_png))) > 127
    gt = np.array(Image.open(gt_path).convert("L").resize((pred.shape[1], pred.shape[0]))) > 127
    inter = float((pred & gt).sum())
    denom = float(pred.sum() + gt.sum())
    dsc = (2 * inter / denom) if denom else 1.0
    union = float((pred | gt).sum())
    iou = (inter / union) if union else 1.0
    dscs.append(dsc); ious.append(iou)
    print(f"{p.name}: DSC={dsc:.4f} IoU={iou:.4f} area={r.lesion_area_ratio:.4f} "
          f"bbox={r.bbox} lat={r.latency_ms}ms dev={r.device} official_dice={r.model_dice_official}")
print(f"MEAN DSC={np.mean(dscs):.4f} MEAN IoU={np.mean(ious):.4f} (n={len(dscs)})")
PY
