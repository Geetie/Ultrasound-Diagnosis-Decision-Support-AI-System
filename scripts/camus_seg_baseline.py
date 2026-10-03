"""CAMUS 心脏超声分割基线（左心室腔 + 心肌），多器官扩展的第一个新器官。

数据：CAMUS-Lite（hf-mirror，1000 张，CC-BY-NC-SA-4.0）
  - 标注 labels_map: 1=左室心肌 myocardium / 2=左心室 left ventricle / 3=左心房(LA)
  - 文件名形如 patient001_2CH_ED.png（同患者有 2CH/4CH × ED/ES 多帧）

协议（与甲状腺 TN3K 配方对齐，便于横向比较）：
  - **按患者划分** 80/20（CAMUS 每患者多帧，按图随机划分会泄漏 —— 这是本脚本最关键的设计）
  - 方形缩放 256、AdamW lr 1e-3 cosine、bs 16、25 epoch、bf16
  - 指标：左心室腔 DSC/IoU（主）+ 心肌 DSC（辅）；不做容积/EF（不在本轮范围）
  - 权重落 ~/uai/models/cardiac_seg/unet_camus.pt，注册表 cardiac 状态改 trained
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

DATA = Path.home() / "uai" / "data" / "camus"
OUT_JSON = Path.home() / "uai" / "reports" / "camus_seg_baseline.json"
CKPT = Path.home() / "uai" / "models" / "cardiac_seg" / "unet_camus.pt"
SIZE = 256
BS = 16
EPOCHS = 25
LR = 1e-3
LV_LABEL = 2      # left ventricle（腔）
FRAMES_PER_ITEM = 3   # 每个 half_sequence 取中间 3 个心动时相帧
MYO_LABEL = 1     # myocardium


def list_pairs():
    """列 (image_nii, mask_nii, frame_idx) —— CAMUS half_sequence 含 18 个心动时相帧。

    取中间 FRAMES_PER_ITEM 帧（覆盖收缩期）；患者键从文件名取（patient0001），
    划分严格按患者，绝不把同患者不同帧/不同视图分到两侧。
    """
    img_dir, mask_dir = DATA / "Images", DATA / "Masks"
    items = []
    for p in sorted(img_dir.glob("*.nii.gz")):
        stem = p.name.replace("_half_sequence.nii.gz", "")
        m = mask_dir / f"{stem}_half_sequence_gt.nii.gz"
        if not m.exists():
            continue
        try:
            import nibabel as nib

            mk = np.asarray(nib.load(str(m)).dataobj)
        except Exception:
            continue
        n_frames = mk.shape[2] if mk.ndim == 3 else 1
        mid = n_frames // 2
        offsets = [0] if FRAMES_PER_ITEM == 1 else [
            max(0, mid + d) for d in range(-(FRAMES_PER_ITEM // 2),
                                            FRAMES_PER_ITEM // 2 + 1)
        ]
        for fi in offsets:
            if (np.asarray(mk[:, :, fi]) == LV_LABEL).sum() >= 50:
                items.append((p, m, fi))
    return items


def load_pair(ip, mp, frame_idx):
    import nibabel as nib

    try:
        arr = np.asarray(nib.load(str(ip)).dataobj)[:, :, frame_idx]
        mk = np.asarray(nib.load(str(mp)).dataobj)[:, :, frame_idx]
    except Exception:
        return None
    if arr.max() > 1.5:
        arr = arr / 255.0
    img = Image.fromarray(np.clip(arr, 0, 1).astype(np.float32) * 255).convert("RGB")
    img = img.resize((SIZE, SIZE), Image.BILINEAR)
    x = torch.from_numpy(np.asarray(img).copy()).permute(2, 0, 1).float() / 255.0
    # 注意：比较结果是 bool 张量，interpolate(nearest) 不支持 bool（须先 .float()）
    y = (torch.from_numpy(mk.copy()).float() == LV_LABEL).float().unsqueeze(0)
    y = F.interpolate(y.unsqueeze(0), size=(SIZE, SIZE), mode="nearest")[0]
    return x, y


def augment(x, y, rng):
    if rng.random() < 0.5:
        x, y = torch.flip(x, [-1]), torch.flip(y, [-1])
    if rng.random() < 0.5:
        x, y = torch.flip(x, [-2]), torch.flip(y, [-2])
    k = int(rng.integers(0, 4))
    if k:
        x, y = torch.rot90(x, k, [-2, -1]), torch.rot90(y, k, [-2, -1])
    if rng.random() < 0.3:
        x = (x * float(rng.uniform(0.85, 1.15))).clamp(0, 1)
    return x, y


def dice_bce(logits, target):
    bce = F.binary_cross_entropy_with_logits(logits.float(), target.float())
    pred = torch.sigmoid(logits.float())
    inter = (pred * target).sum(dim=(2, 3))
    union = pred.sum(dim=(2, 3)) + target.sum(dim=(2, 3))
    dice = 1 - ((2 * inter + 1.0) / (union + 1.0)).mean()
    return bce + dice


def patient_of(path: Path) -> str:
    """CAMUS 文件名形如 patient001_2CH_ED.png → 患者键 patient001。"""
    return path.stem.split("_")[0]


def build_unet():
    from monai.networks.nets import UNet

    return UNet(
        spatial_dims=2, in_channels=3, out_channels=1,
        channels=(32, 64, 128, 256, 320), strides=(2, 2, 2, 2),
        num_res_units=2, dropout=0.1,
    )


@torch.no_grad()
def evaluate(net, pairs, device, need_myo=True):
    net.eval()
    lv_d, lv_i, myo_d = [], [], []
    for i in range(0, len(pairs), BS):
        batch = [load_pair(p[0], p[1], p[2]) for p in pairs[i:i + BS]]
        batch = [b for b in batch if b is not None]
        if not batch:
            continue
        xs = torch.stack([b[0] for b in batch]).to(device)
        yl = torch.stack([(b[1] == LV_LABEL).float() for b in batch]).to(device)
        ym = torch.stack([(b[1] == MYO_LABEL).float() for b in batch]).to(device)
        with torch.autocast("cuda", dtype=torch.bfloat16):
            logits = net(xs)
        pr = (torch.sigmoid(logits.float()) > 0.5).float()
        for tgt, store_d, store_i in ((yl, lv_d, lv_i), (ym, myo_d, None)):
            inter = (pr * tgt).sum(dim=(1, 2, 3))
            card = pr.sum(dim=(1, 2, 3)) + tgt.sum(dim=(1, 2, 3))
            union = card - inter
            for di, ui, ci in zip(inter, union, card, strict=False):
                d = float(2 * di / ci) if ci > 0 else 1.0
                store_d.append(d)
                if store_i is not None:
                    store_i.append(float(di / ui) if ui > 0 else 1.0)
    net.train()
    return lv_d, lv_i, (myo_d if need_myo else [])


def main() -> int:
    device = "cuda"
    torch.manual_seed(42)
    np.random.seed(42)
    rng = np.random.default_rng(42)
    t0 = time.time()

    pairs = list_pairs()
    if not pairs:
        print("NO_DATA —— 先跑 scripts/camus_download.sh")
        return 2
    by_pat = defaultdict(list)
    for p in pairs:
        by_pat[patient_of(p[0])].append(p)
    pats = sorted(by_pat)
    rng.shuffle(pats)
    n_test = max(1, int(len(pats) * 0.2))
    test_pats = set(pats[:n_test])
    train_pairs = [p for p in pairs if patient_of(p[0]) not in test_pats]
    test_pairs = [p for p in pairs if patient_of(p[0]) in test_pats]
    print(f"pairs={len(pairs)} patients={len(pats)} "
          f"train={len(train_pairs)}/{len(pats)-n_test}pat test={len(test_pairs)}/{n_test}pat", flush=True)

    net = build_unet().to(device)
    n_params = sum(p.numel() for p in net.parameters())
    opt = torch.optim.AdamW(net.parameters(), lr=LR, weight_decay=0.01)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=EPOCHS)

    best = {"lv_dice": 0.0}
    for epoch in range(EPOCHS):
        net.train()
        perm = rng.permutation(len(train_pairs))
        losses = []
        for i in range(0, len(perm), BS):
            xs, ys = [], []
            for j in perm[i:i + BS]:
                im = load_pair(train_pairs[j][0], train_pairs[j][1], train_pairs[j][2])
                if im is not None:
                    # 关键：图像与掩码必须**同一次**增强（两次调用会各自抽随机数
                    # → 掩码与图像错位，训练直接失效）
                    ax, ay = augment(im[0], (im[1] == LV_LABEL).float(), rng)
                    xs.append(ax)
                    ys.append(ay)
            if not xs:
                continue
            xs = torch.stack(xs).to(device)
            ys = torch.stack(ys).to(device)
            with torch.autocast("cuda", dtype=torch.bfloat16):
                loss = dice_bce(net(xs), ys)
            opt.zero_grad()
            loss.backward()
            opt.step()
            losses.append(float(loss.detach()))
        sched.step()
        if (epoch + 1) % 5 == 0 or epoch == EPOCHS - 1:
            lv_d, lv_i, myo_d = evaluate(net, test_pairs, device)
            d = float(np.mean(lv_d))
            if d > best["lv_dice"]:
                best = {"lv_dice": d, "lv_iou": float(np.mean(lv_i)),
                        "myo_dice": float(np.mean(myo_d)), "epoch": epoch + 1}
            print(f"  epoch{epoch+1} loss={np.mean(losses):.4f} "
                  f"LV_DSC={d:.4f} LV_IoU={np.mean(lv_i):.4f} myo_DSC={np.mean(myo_d):.4f} "
                  f"({time.time()-t0:.0f}s)", flush=True)

    lv_d, lv_i, myo_d = evaluate(net, test_pairs, device)
    CKPT.parent.mkdir(parents=True, exist_ok=True)
    torch.save({
        "state_dict": {k: v.cpu() for k, v in net.state_dict().items()},
        "arch": "monai.UNet(spatial_dims=2, channels=(32,64,128,256,320), "
                "strides=(2,2,2,2), num_res_units=2, dropout=0.1)",
        "input_size": SIZE,
        "task": "CAMUS 左心室腔二值分割（label=2）",
        "train_split": f"CAMUS-Lite 按患者 80/20（{len(pats)-n_test}/{n_test} 患者，"
                       f"{len(train_pairs)}/{len(test_pairs)} 张）",
        "test_lv_dice": round(float(np.mean(lv_d)), 4),
        "test_lv_iou": round(float(np.mean(lv_i)), 4),
        "test_myo_dice": round(float(np.mean(myo_d)), 4),
        "epochs": EPOCHS,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, CKPT)

    result = {
        "dataset": "CAMUS-Lite (hf-mirror, 1000 张, CC-BY-NC-SA-4.0)",
        "protocol": f"按患者 80/20 划分（防同患者多帧泄漏）、方形 256、AdamW {LR} cosine、"
                    f"bs {BS}、{EPOCHS} epoch、bf16，自设配方",
        "patients_total": len(pats),
        "patients_test": n_test,
        "images_total": len(pairs),
        "images_train": len(train_pairs),
        "images_test": len(test_pairs),
        "params_M": round(n_params / 1e6, 1),
        "lv_dice_mean": round(float(np.mean(lv_d)), 4),
        "lv_dice_std": round(float(np.std(lv_d)), 4),
        "lv_iou_mean": round(float(np.mean(lv_i)), 4),
        "myo_dice_mean": round(float(np.mean(myo_d)), 4),
        "best_during_training": {k: (round(v, 4) if isinstance(v, float) else v)
                                 for k, v in best.items()},
        "checkpoint": str(CKPT),
        "wall_seconds": round(time.time() - t0, 1),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    print(f"-> {OUT_JSON}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
