"""AUL 肝脏逐图预测落盘（p104）—— 让「良性被判恶性 114 张」可逐图归因。

## 为什么要单独跑

p85 只写了汇总混淆矩阵（Benign→Malignant 114 张），**没落逐图预测**。
于是「这 114 张有什么共同特征」在现有产物里根本无法回答 —— 只能猜。
本脚本用与 p85 完全相同的机制（8003 /v1/classify，微调 visual + LR 探针）重跑，
把每张图的 probs 落盘，再做归因分析。

## 能回答的归因维度（AUL 数据实际有的字段）

- **类别**：Benign / Malignant / Normal（标签）
- **病灶轮廓**：`segmentation/mass/*.json` = 病灶（mass）多边形，
  `segmentation/liver/*.json` = 肝脏多边形，`outline/*.json` = 体表轮廓
  → 可算病灶面积、病灶/肝面积比、病灶中心位置
- **预测概率**：恶性概率、三分类 argmax

## 不能回答的（必须如实说）

AUL **没有良性亚型标注**（无血管瘤/局灶性结节增生/FNH 之类），
也**没有病理或随访结果**。故「这 114 张是不是某种特定良性病变」**结构性无法回答**。
本脚本给的是影像几何层面的可用替代表征（病灶大小/占比/位置/模型置信度分层）。

## 用法（服务器，需 8003 就绪）

    PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/audit/p113_aul_perimage.py \
        --report reports/p113_aul_perimage.json
"""
from __future__ import annotations

import argparse
import io
import json
import time
import urllib.request
import uuid
from collections import Counter
from pathlib import Path

LABELS = ("Benign", "Malignant", "Normal")
EXTS = {".jpg", ".jpeg", ".png", ".bmp"}


def shoelace_area(pts) -> float:
    if not isinstance(pts, list) or len(pts) < 3:
        return 0.0
    try:
        a = [(float(p[0]), float(p[1])) for p in pts]
    except (TypeError, ValueError, IndexError):
        return 0.0
    n = len(a)
    s = 0.0
    for i in range(n):
        x1, y1 = a[i]
        x2, y2 = a[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def load_contours(aul: Path, cls: str, stem: str) -> dict:
    out = {}
    for sub in ("mass", "liver", "outline"):
        p = aul / cls / "segmentation" / sub / f"{stem}.json"
        if not p.is_file():
            continue
        try:
            pts = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(pts, list) or len(pts) < 3:
            continue
        arr = [(float(q[0]), float(q[1])) for q in pts if isinstance(q, (list, tuple)) and len(q) >= 2]
        if len(arr) < 3:
            continue
        out[sub] = {
            "area": shoelace_area(pts),
            "cx": sum(q[0] for q in arr) / len(arr),
            "cy": sum(q[1] for q in arr) / len(arr),
            "n_pts": len(arr),
        }
    return out


def post_batch(clip_url: str, raws: list[bytes], names: list[str], timeout: float = 300) -> list[dict]:
    boundary = uuid.uuid4().hex
    body = io.BytesIO()
    for raw, nm in zip(raws, names):
        body.write(f"--{boundary}\r\n".encode())
        body.write((f'Content-Disposition: form-data; name="images"; '
                    f'filename="{nm}"\r\nContent-Type: image/jpeg\r\n\r\n').encode())
        body.write(raw)
        body.write(b"\r\n")
    body.write(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(f"{clip_url}/v1/classify", data=body.getvalue(), method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return (json.loads(resp.read().decode("utf-8")) or {}).get("results") or []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AUL 逐图预测（p104）")
    ap.add_argument("--aul-dir", default=str(Path.home() / "uai/data/aul"))
    ap.add_argument("--clip-url", default="http://127.0.0.1:8003")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--report", default="reports/p113_aul_perimage.json")
    args = ap.parse_args(argv)

    aul = Path(args.aul_dir).expanduser()
    items = []
    for cls in LABELS:
        d = aul / cls / "image"
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*")):
            if p.is_file() and p.suffix.lower() in EXTS:
                items.append({"cls": cls, "stem": p.stem, "path": str(p)})
    print(f"[aul] n={len(items)} support={dict(Counter(i['cls'] for i in items))}", flush=True)

    recs = []
    t0 = time.monotonic()
    for i in range(0, len(items), args.batch):
        chunk = items[i: i + args.batch]
        raws = [Path(c["path"]).read_bytes() for c in chunk]
        names = [Path(c["path"]).name for c in chunk]
        try:
            res = post_batch(args.clip_url, raws, names)
        except Exception as e:  # noqa: BLE001 — 端点不可用要显式记，不静默跳过
            print(f"[FATAL] 8003 调用失败：{e}")
            return 2
        if len(res) != len(chunk):
            print(f"[FATAL] 返回条数不符 {len(res)} vs {len(chunk)}")
            return 2
        for c, r in zip(chunk, res):
            probs = r.get("probs") or {}
            recs.append({**c, "probs": probs,
                         "prediction": r.get("prediction"),
                         "malignancy_risk": r.get("malignancy_risk")})
        if (i // args.batch) % 10 == 0:
            print(f"  {i + len(chunk)}/{len(items)}", flush=True)

    # 轮廓几何（AUL 用 shoe-lace 面积，单位像素²；无像素间距 → 不换算 mm²）
    for r in recs:
        r["contours"] = load_contours(aul, r["cls"], r["stem"])
        m = r["contours"].get("mass")
        lv = r["contours"].get("liver")
        r["mass_area"] = round(m["area"], 1) if m else None
        r["liver_area"] = round(lv["area"], 1) if lv else None
        r["mass_over_liver"] = (round(m["area"] / lv["area"], 6) if (m and lv and lv["area"] > 0) else None)

    def auc(scores, labels):
        pairs = sorted(zip(scores, labels), key=lambda x: x[0])
        pos = sum(1 for _, y in pairs if y == 1)
        neg = len(pairs) - pos
        if not pos or not neg:
            return float("nan")
        ranks, i = {}, 0
        while i < len(pairs):
            j = i
            while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                ranks[k] = avg
            i = j + 1
        rs = sum(ranks[i] for i, (_, y) in enumerate(pairs) if y == 1)
        return (rs - pos * (pos + 1) / 2) / (pos * neg)

    # ---- 归因：Benign 被判 Malignant 的那批 ----
    ben = [r for r in recs if r["cls"] == "Benign"]
    false_pos = [r for r in ben if r["prediction"] == "Malignant"]
    true_neg = [r for r in ben if r["prediction"] != "Malignant"]
    mal = [r for r in recs if r["cls"] == "Malignant"]

    def q(vals, p):
        v = sorted(x for x in vals if x is not None)
        return round(v[int(p * (len(v) - 1))], 4) if v else None

    def prof(rs, key):
        v = [r.get(key) for r in rs if r.get(key) is not None]
        return {"n": len(v), "median": q(v, 0.5), "p10": q(v, 0.1), "p90": q(v, 0.9)} if v else {"n": 0}

    # 置信度分层：这 114 张是"险胜"还是"斩钉截铁"
    fp_conf = sorted((r["malignancy_risk"] for r in false_pos if r["malignancy_risk"] is not None))
    tp_conf = sorted((r["malignancy_risk"] for r in mal if r["prediction"] == "Malignant"
                      and r["malignancy_risk"] is not None))

    report = {
        "probe": "p113_aul_perimage",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "why": ("p85 只落汇总混淆矩阵，未落逐图预测 → 「良性被判恶性的 114 张有什么共同点」无法回答。"
                "本脚本用同一机制重跑并落逐图记录。"),
        "endpoint": args.clip_url,
        "mechanism": "finetuned_visual_tower + logistic_probe（T2A v2，与 p85 相同）",
        "n_total": len(recs),
        "class_support": dict(Counter(r["cls"] for r in recs)),
        "auc_malignant_vs_benign_only": round(auc(
            [r["malignancy_risk"] for r in recs if r["cls"] in ("Benign", "Malignant")],
            [1 if r["cls"] == "Malignant" else 0 for r in recs if r["cls"] in ("Benign", "Malignant")]), 4),
        "auc_malignant_vs_rest": round(auc(
            [r["malignancy_risk"] for r in recs],
            [1 if r["cls"] == "Malignant" else 0 for r in recs]), 4),
        "per_image": recs,
        "attribution_benign_as_malignant": {
            "n_benign": len(ben),
            "n_benign_pred_malignant": len(false_pos),
            "n_benign_pred_other": len(true_neg),
            "fp_rate": round(len(false_pos) / max(1, len(ben)), 4),
            "confidence_split": {
                "note": ("若误报集中在 0.5~0.6 的"
                         "「险胜」区，说明是判别边界模糊；若集中在 >0.9，说明模型对良性有明显系统性偏好。"),
                "fp_risk_median": q(fp_conf, 0.5),
                "fp_risk_p10": q(fp_conf, 0.1), "fp_risk_p90": q(fp_conf, 0.9),
                "tp_risk_median": q(tp_conf, 0.5),
                "fp_in_0.5_0.6": sum(1 for x in fp_conf if 0.5 <= x < 0.6),
                "fp_in_0.6_0.8": sum(1 for x in fp_conf if 0.6 <= x < 0.8),
                "fp_in_0.8_1.0": sum(1 for x in fp_conf if x >= 0.8),
            },
            "lesion_geometry": {
                "fp_mass_area": prof(false_pos, "mass_area"),
                "tn_mass_area": prof(true_neg, "mass_area"),
                "malignant_mass_area": prof([r for r in mal if r["prediction"] == "Malignant"], "mass_area"),
                "fp_mass_over_liver": prof(false_pos, "mass_over_liver"),
                "tn_mass_over_liver": prof(true_neg, "mass_over_liver"),
                "note": ("单位像素²（鞋带公式），AUL 未给像素间距 → 无法换算 mm²。"
                         "对比 fp 与 tn 的病灶大小即可判断「被判恶性的良性是不是更小/更大」。"),
            },
            "n_with_mass_contour": {
                "fp": sum(1 for r in false_pos if r.get("mass_area")),
                "tn": sum(1 for r in true_neg if r.get("mass_area")),
            },
            "samples_fp_high_confidence": [
                {"stem": r["stem"], "risk": r["malignancy_risk"],
                 "mass_area": r["mass_area"], "mass_over_liver": r["mass_over_liver"]}
                for r in sorted(false_pos, key=lambda x: -(x["malignancy_risk"] or 0))[:20]],
        },
        "what_we_cannot_answer": (
            "**AUL 没有良性亚型标注**（无血管瘤 / 局灶性结节增生 / FNH / 腺瘤 之类的标签），"
            "也没有病理或随访。故「这 114 张是不是某种特定良性病变」"
            "**结构性无法回答**，任何这种结论都会是编的。"
            "本脚本能给的只是影像几何与置信度的替代表征。"),
        "wall_s": round(time.monotonic() - t0, 1),
    }
    rp = Path(args.report)
    if not rp.is_absolute():
        rp = Path.cwd() / rp
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    a = report["attribution_benign_as_malignant"]
    print(f"\n[AUL] benign→malignant {a['n_benign_pred_malignant']}/{a['n_benign']} "
          f"({a['fp_rate']:.4f})")
    print("  confidence:", json.dumps(a["confidence_split"], ensure_ascii=False))
    print("  geometry:", json.dumps(a["lesion_geometry"], ensure_ascii=False)[:500])
    print(f"  report: {rp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
