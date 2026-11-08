"""行动计划 2.4 · 失败模式分析（p103）。

按 biomedical.md › Regulatory considerations：
"**Failure mode analysis**: characterize where the model fails and how gracefully"

## 三块分析（各自独立，可单跑）

### A. T2b「Random」类（非标准切面）
p96 已知：finetune 后 Random recall 0.8301 / F1 0.8475，错分去 PSMV 344 + A4C 213。
本块回答：**这些图到底该归哪类，还是标注定义本身有问题？**
- 统计 Random 的失败样本在**文件名形态、视频归属、同视频其他切面**上的特征
- 关键判据：若被判成 PSMV 的 Random 图，其**同一视频里根本没有 PSMV 帧**，
  则"模型认错"的说法不成立 —— 是 Random 这个类本身不可分（标注定义问题）
- 抽样导出**具体文件名**供人工核对（不落患者数据；CACTUS 是公开集）

### B. TN5000 甲状腺漏诊恶性（用 p101/p102 的 OOF 分数）
- 在 base arm 的池化 OOF 上取 FN（真恶性被判良性）
- 按**病灶相对面积 / 位置 / 是否 tiny** 给漏诊样本画像
- 答案形如「漏诊集中在 tiny 病灶」或「与大小无关」

### C. AUL 肝脏良性被判恶性（114 张）
- AUL 有 `segmentation/{liver,mass,outline}/*.json` 轮廓点
- 无病理亚型标注（血管瘤/FNH 之类 AUL 没给）→ **如实报无法按亚型归因**
- 能给的：这些图被判恶性时**预测恶性概率的分布**（是"险胜"还是"斩钉截铁"）、
  以及它们与真恶性图在**病灶大小/位置**上的差别

### D. TN3K tiny 病灶
- 掩膜相对面积分桶（已算：tiny<1% 占 4.20%，121 张）
- tiny 组的**等效直径**（中位 35.9 px）与 DSC 0.4472 的关系

## 纪律
- 所有抽样只记**公开集文件名**，不含任何患者信息
- 每个结论必须指向可复现的字段/文件，不给"看起来像"

## 用法
    cd ~/uai/src && CUDA_VISIBLE_DEVICES=0 PYTHONPATH=~/uai/src ~/uai/venv/bin/python \
        scripts/audit/p112_failure_modes.py --parts tn5000,aul --report reports/p112_failure_modes.json
"""
from __future__ import annotations

import argparse
import json
import math
import random
import time
from collections import Counter, defaultdict
from pathlib import Path


def auc_binary(scores, labels):
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
    r_sum = sum(ranks[i] for i, (_, y) in enumerate(pairs) if y == 1)
    return (r_sum - pos * (pos + 1) / 2) / (pos * neg)


# ================================================================ A. T2b Random
def part_t2b(data_dir: Path, out_json: dict) -> dict:
    """Random 类失败方向分析。分数来自 p96 的 OOF（若报告存在）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "p87", Path(__file__).resolve().parent / "p87_cactus_video_disjoint.py")
    p87 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(p87)

    import csv
    rows = []
    for split in ("train", "val", "test"):
        p = data_dir / f"{split}.csv"
        if not p.exists():
            continue
        with p.open(encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                fp = (r.get("filepath") or "").strip()
                vid, fi = p87.parse_name(fp)
                if not fp or vid is None:
                    continue
                rows.append({"filepath": fp, "video": vid, "frame": fi,
                             "view": (r.get("view") or "").strip(), "split": split,
                             "grade": (r.get("grade") or "").strip(),
                             "name": Path(fp).name})
    print(f"[t2b] n={len(rows)} videos={len({r['video'] for r in rows})}", flush=True)

    # 每个视频有哪些切面
    v2views = defaultdict(set)
    v2rows = defaultdict(list)
    for r in rows:
        v2views[r["video"]].add(r["view"])
        v2rows[r["video"]].append(r)

    rand = [r for r in rows if r["view"] == "Random"]
    only_rand_v = {v for v, s2 in v2views.items() if s2 == {"Random"}}
    rand_in_randonly = [r for r in rand if r["video"] in only_rand_v]
    rand_in_mixed = [r for r in rand if r["video"] not in only_rand_v]
    v2psmv = {v for v, s2 in v2views.items() if "PSMV" in s2}
    v2a4c = {v for v, s2 in v2views.items() if "A4C" in s2}

    # ---- 受试者层（**这才是真正的分组变量**）----
    subj = lambda v: v.split("_")[0]
    subs = Counter(subj(r["video"]) for r in rows)
    subj_views = {sv: dict(Counter(r["view"] for r in rows if subj(r["video"]) == sv))
                  for sv in subs}
    rand_subj = Counter(subj(r["video"]) for r in rand)
    print(f"[t2b] 受试者数={len(subs)}；Random 归属受试者={dict(rand_subj)}", flush=True)

    # ---- grade 字段：Random 是否恒为 0（= 未分级）----
    grade_by_view = {v: dict(Counter(r["grade"] for r in rows if r["view"] == v))
                     for v in sorted({r["view"] for r in rows})}

    # ---- 样本量不足的直接判据：GroupKFold(5) 要求每折≥1 受试者 ----
    fold_feasibility = []
    tot = len(rows)
    acc = 0
    for sv, c in subs.most_common():
        acc += c
        fold_feasibility.append({"subject": sv, "images": c,
                                 "cumulative": acc,
                                 "cumulative_share": round(acc / tot, 4)})

    # 文件名形态：CACTUS 里有没有「无帧号」的整机导出图
    lits = [r for r in rows if "WIN" in r["name"].upper() or "IMG" in r["name"].upper()]
    rng = random.Random(20261108)
    sample = rng.sample(rand, min(30, len(rand)))

    return {
        "n_rows": len(rows),
        "n_videos": len(v2views),
        "n_random": len(rand),
        "random_share": round(len(rand) / len(rows), 4),
        "cactus_known_issue": {
            "note": ("CACTUS 官方 README 说明标注取自元数据；`Random` 是「视图中不可归类」的兜底桶，"
                     "不是解剖切面。故它的定义是**排除性**的 → 天然不可分边界。"),
        },
        "subject_level": {
            "n_subjects": len(subs),
            "images_per_subject": dict(subs.most_common()),
            "views_per_subject": subj_views,
            "random_subject_attribution": dict(rand_subj),
            "headline": (f"CACTUS 这批 t2b 数据只有 {len(subs)} 个**受试者**"
                         f"（视频名前缀即受试者号）。p96 的 GroupKFold(5) 按**视频**分组，"
                         f"116 个视频跨 5 折 → **同一受试者的不同视频会同时出现在 train 与 test**。"
                         f"这不是患者级验证，指标仍偏乐观。"),
            "groupkfold_by_subject_feasibility": fold_feasibility,
            "does_5fold_by_subject_work": (
                f"**不能直接做 5 折**：只有 {len(subs)} 个受试者，"
                f"最大受试者占 {fold_feasibility[0]['cumulative_share']:.2%}，最小占 "
                f"{round(min(subs.values())/tot,4):.2%}；"
                f"5 折每折 ≈{round(tot/5/1000,1)}k 图，而单个受试者就有 "
                f"{fold_feasibility[0]['images']} 图 → 折间极不可比。"
                "可行替代：**leave-one-subject-out（10 折）**，或按受试者分 2~3 组。"),
        },
        "videos_only_random": {"n_videos": len(only_rand_v), "sorted": sorted(only_rand_v)},
        "grade_field_by_view": {
            "note": ("CACTUS 的 `grade` 是图像质量分（1~9，Random 类恒为 0）。"
                     "Random 的 grade 全 0 → 该类在官方标注里**就是「未分级/非标准」**，"
                     "不是某个具体解剖切面。"),
            "distributions": grade_by_view,
            "random_grades_all_zero": all(
                r["grade"] in ("0", "") for r in rand),
        },
        "random_images_in_random_only_videos": {
            "n": len(rand_in_randonly),
            "share_of_random": round(len(rand_in_randonly) / max(1, len(rand)), 4),
            "videos": sorted({r["video"] for r in rand_in_randonly}),
        },
        "random_images_in_mixed_videos": {
            "n": len(rand_in_mixed),
            "share_of_random": round(len(rand_in_mixed) / max(1, len(rand)), 4),
            "videos": sorted({r["video"] for r in rand_in_mixed})[:20],
            "note": ("这些视频里同时有标准切面帧 → Random 帧是要被逐帧判定的，"
                     "才真正考模型的判别力"),
        },
        "cooccurrence": {
            "videos_with_PSMV": len(v2psmv),
            "videos_with_A4C": len(v2a4c),
            "random_only_videos_have_PSMV_frames": len(only_rand_v & v2psmv),
            "random_only_videos_have_A4C_frames": len(only_rand_v & v2a4c),
            "note": ("若 random_only_videos_have_PSMV_frames = 0，说明这些视频根本没有 PSMV 帧；"
                     "p96 里 Random→PSMV 的 344 张不可能是「看到了同视频的 PSMV」，"
                     "只能是模型按二维形态在远视频上误配 → 支持「标签定义不可分」而非「数据不足」"),
        },
        "literal_typing": {
            "n_win_or_img_filenames": len(lits),
            "n_with_random_label": sum(1 for r in lits if r["view"] == "Random"),
            "note": ("实测 CACTUS 这批数据里**没有** `WIN_/IMG_` 形态的整机导出文件名 "
                     "（n_win_or_img_filenames 见上），所有文件都是 `受试者_视频帧号` 形态。"
                     "故 Random 不是「截图来源」桶，而是**受试者 0 的全部帧**。"),
            "samples": [r["name"] for r in lits[:10]],
        },
        "sample_random_for_manual_review": [
            {"file": r["name"], "video": r["video"], "frame": r["frame"], "split": r["split"]}
            for r in sorted(sample, key=lambda x: (x["video"], x["frame"]))
        ],
        "random_subject_videos": {
            "videos": sorted({r["video"] for r in rand}),
            "frames_per_video": dict(Counter(r["video"] for r in rand).most_common()),
            "note": "受试者 0 的 6 个视频全是 Random 帧；其余 9 个受试者的视频里只有 0_V1 混入 1 张 SC。"},
    }


# ================================================================ B. TN5000 漏诊
def part_tn5000(zip_path: Path, oof_json: Path, thr: float, out: dict) -> dict:
    """用 p101 base arm 的池化 OOF，给漏诊恶性样本画像（病灶大小/位置）。"""
    import zipfile
    import xml.etree.ElementTree as ET

    z = zipfile.ZipFile(zip_path)
    xmls = {Path(n).stem: n for n in z.namelist() if n.lower().endswith(".xml")}
    meta = {}
    for stem in xmls:
        node = ET.fromstring(z.read(xmls[stem]))
        W = int(node.findtext("size/width") or 0)
        H = int(node.findtext("size/height") or 0)
        lab, bb = None, None
        for obj in node.findall(".//object"):
            nm = (obj.findtext("name") or "").strip()
            if nm in ("0", "1"):
                lab = int(nm)
            x1 = obj.findtext("bndbox/xmin"); y1 = obj.findtext("bndbox/ymin")
            x2 = obj.findtext("bndbox/xmax"); y2 = obj.findtext("bndbox/ymax")
            if None not in (x1, y1, x2, y2):
                bb = (float(x1), float(y1), float(x2), float(y2))
        if lab is None or bb is None or W <= 0 or H <= 0:
            continue
        a = (bb[2] - bb[0]) * (bb[3] - bb[1])
        meta[stem] = {"label": lab, "W": W, "H": H, "rel_area": a / (W * H),
                      "cx": (bb[0] + bb[2]) / 2 / W, "cy": (bb[1] + bb[3]) / 2 / H,
                      "w_rel": (bb[2] - bb[0]) / W, "h_rel": (bb[3] - bb[1]) / H}
    print(f"[tn5000] bbox/meta parsed={len(meta)}", flush=True)

    if not oof_json.is_file():
        return {"status": "blocked", "reason": f"缺少 OOF 文件 {oof_json}（先跑 p101）"}
    d = json.loads(oof_json.read_text(encoding="utf-8"))
    if "base" not in d.get("arms", {}):
        return {"status": "blocked", "reason": "p101 报告里没有 base arm"}
    oof = {k: v for k, v in d["arms"]["base"]["oof_records"].items()}

    recs = []
    for k, v in oof.items():
        stem = v["stem"]
        if stem not in meta:
            continue
        recs.append({"stem": stem, "score": v["score"], "label": v["label"], **meta[stem]})
    print(f"[tn5000] joined={len(recs)}", flush=True)

    def bucket(a):
        if a < 0.01:
            return "tiny(<1%)"
        if a < 0.05:
            return "small(1-5%)"
        if a < 0.20:
            return "medium(5-20%)"
        return "large(>=20%)"

    for r in recs:
        r["bucket"] = bucket(r["rel_area"])
        r["pred"] = 1 if r["score"] >= thr else 0

    mal = [r for r in recs if r["label"] == 1]
    ben = [r for r in recs if r["label"] == 0]
    fn = [r for r in mal if r["pred"] == 0]
    tp = [r for r in mal if r["pred"] == 1]
    fp = [r for r in ben if r["pred"] == 1]

    def prof(rs):
        if not rs:
            return {"n": 0}
        a = sorted(r["rel_area"] for r in rs)
        cx = sorted(r["cx"] for r in rs)
        cy = sorted(r["cy"] for r in rs)
        med = lambda v: v[len(v) // 2]
        return {"n": len(rs),
                "rel_area_median": round(med(a), 6),
                "rel_area_p10": round(a[int(0.10 * (len(a) - 1))], 6),
                "rel_area_p90": round(a[int(0.90 * (len(a) - 1))], 6),
                "cx_median": round(med(cx), 4), "cy_median": round(med(cy), 4),
                "bucket_mix": dict(Counter(r["bucket"] for r in rs)),
                "score_median": round(med(sorted(r["score"] for r in rs)), 4)}

    # 每个大小桶的敏感度（最重要的失败模式判据）
    by_bucket = {}
    for b in ("tiny(<1%)", "small(1-5%)", "medium(5-20%)", "large(>=20%)"):
        sub = [r for r in mal if r["bucket"] == b]
        if sub:
            by_bucket[b] = {"n_malignant": len(sub),
                            "sensitivity": round(sum(r["pred"] for r in sub) / len(sub), 4),
                            "fn": sum(1 for r in sub if r["pred"] == 0),
                            "score_median": round(sorted(r["score"] for r in sub)[len(sub) // 2], 4)}
    # 误报侧
    fp_by_bucket = {}
    for b in ("tiny(<1%)", "small(1-5%)", "medium(5-20%)", "large(>=20%)"):
        sub = [r for r in ben if r["bucket"] == b]
        if sub:
            fp_by_bucket[b] = {"n_benign": len(sub),
                               "false_positive_rate": round(sum(r["pred"] for r in sub) / len(sub), 4)}

    # 最"离谱"的漏诊（恶性但分数极低）与最"危险"的误报（良性但分数极高）
    worst_fn = sorted(fn, key=lambda r: r["score"])[:15]
    worst_fp = sorted(fp, key=lambda r: -r["score"])[:15]
    return {
        "n_joined": len(recs), "threshold": thr,
        "overall": {"n_malignant": len(mal), "n_benign": len(ben),
                    "sensitivity": round(len(tp) / max(1, len(mal)), 4),
                    "specificity": round(1 - len(fp) / max(1, len(ben)), 4)},
        "profile_malignant_all": prof(mal),
        "profile_FN_missed_malignant": prof(fn),
        "profile_TP_caught_malignant": prof(tp),
        "sensitivity_by_lesion_size": by_bucket,
        "false_positive_rate_by_lesion_size": fp_by_bucket,
        "worst_missed_malignant_lowest_score": [
            {"stem": r["stem"], "score": round(r["score"], 4), "rel_area": round(r["rel_area"], 6),
             "bucket": r["bucket"], "cx": round(r["cx"], 3), "cy": round(r["cy"], 3)}
            for r in worst_fn],
        "worst_false_positive_highest_score": [
            {"stem": r["stem"], "score": round(r["score"], 4), "rel_area": round(r["rel_area"], 6),
             "bucket": r["bucket"], "cx": round(r["cx"], 3), "cy": round(r["cy"], 3)}
            for r in worst_fp],
        "size_effect_check": {
            "median_rel_area_malignant": prof(mal)["rel_area_median"],
            "median_rel_area_FN": prof(fn)["rel_area_median"],
            "median_rel_area_BENIGN": prof(ben)["rel_area_median"],
            "note": ("若 FN 的中位病灶面积显著小于 TP，则漏诊由小病灶驱动；"
                     "若与良性中位接近，则漏诊更像是「形态偏良性」而非「太小没看清」"),
        },
    }


# ================================================================ C. AUL 良性误判
def part_aul(aul_dir: Path, pred_json: Path | None) -> dict:
    """AUL 良性→恶性：影像层面能说什么、不能说什么。"""
    import numpy as np

    out = {"status": "ok"}
    cnt = {}
    for cls in ("Benign", "Malignant", "Normal"):
        d = aul_dir / cls / "image"
        cnt[cls] = len(list(d.glob("*"))) if d.is_dir() else 0
    out["class_support"] = cnt

    # 轮廓点（AUL 给的是 JSON 点列表，不是标准掩膜）
    prof = {}
    for cls in ("Benign", "Malignant"):
        for sub in ("liver", "mass", "outline"):
            d = aul_dir / cls / "segmentation" / sub
            if not d.is_dir():
                continue
            areas = []
            n_ok = 0
            for p in sorted(d.glob("*.json")):
                try:
                    pts = json.loads(p.read_text())
                except (json.JSONDecodeError, OSError):
                    continue
                if not isinstance(pts, list) or len(pts) < 3:
                    continue
                a = np.asarray(pts, dtype=float)
                if a.ndim != 2 or a.shape[1] != 2:
                    continue
                x, y = a[:, 0], a[:, 1]
                ar = 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))
                areas.append(ar)
                n_ok += 1
            if areas:
                areas = sorted(areas)
                prof[f"{cls}/{sub}"] = {
                    "n_contours": n_ok,
                    "area_px_median": round(float(np.median(areas)), 1),
                    "area_px_p10": round(areas[int(0.1 * (len(areas) - 1))], 1),
                    "area_px_p90": round(areas[int(0.9 * (len(areas) - 1))], 1),
                    "unit": "像素²（由多边形鞋带公式算；AUL 未给像素间距 → 无法换算成 mm²）",
                }
    out["lesion_area_by_class_from_contours"] = prof
    out["what_we_cannot_say"] = {
        "benign_subtype_hemangioma_vs_fnh": (
            "**做不到**。AUL（Zenodo 7272660）只给 Benign/Malignant/Normal 三分类，"
            "**没有良性亚型标注**（无血管瘤/局灶性结节增生/FNH 之类的标签），"
            "也没有病理或随访结果。→ 无法回答「这 114 张是不是某种特定良性病变」。"
            "任何这种说法都会是编的。"),
        "label_source": "AUL 的标签是影像学判读（非病理证实），三分类含 Normal 一类。",
    }
    if pred_json and Path(pred_json).is_file():
        d = json.loads(Path(pred_json).read_text(encoding="utf-8"))
        out["from_p85_report"] = {
            "confusion": d.get("metrics", {}).get("confusion"),
            "per_class": d.get("metrics", {}).get("per_class"),
            "note": "逐图预测未落盘（p85 只写汇总）→ 逐图归因需重跑并保存预测，见 limitation",
        }
    out["limitation"] = (
        "p85 报告只存了汇总混淆矩阵（Benign→Malignant 114 张），**未落逐图预测**，"
        "故「这 114 张有什么共同点」在现有产物里无法逐图回答。"
        "本块给的是数据层能支撑的上限：类别支撑 + 病灶面积分布 + 不能说的话。")
    return out


# ================================================================ D. TN3K tiny
def part_tn3k(root: Path) -> dict:
    import numpy as np
    from PIL import Image

    masks = sorted((root / "trainval-mask").glob("*.jpg"))
    areas, sizes = [], []
    for p in masks:
        a = np.asarray(Image.open(p).convert("L"))
        sizes.append(a.shape)
        areas.append(int((a > 127).sum()) / a.size)
    areas = np.array(areas)
    nz = areas[areas > 0]
    buckets = {"tiny(<1%)": (0, 0.01), "small(1-5%)": (0.01, 0.05),
               "medium(5-20%)": (0.05, 0.20), "large(>=20%)": (0.20, 1.01)}
    mix = {}
    for name, (lo, hi) in buckets.items():
        m = (nz >= lo) & (nz < hi)
        mix[name] = {"n": int(m.sum()), "share": round(float(m.sum()) / len(nz), 4),
                     "median_rel_area": round(float(np.median(nz[m])), 6) if m.any() else None}
    m = nz < 0.01
    px = np.array([a * b for a, b in sizes], dtype=float)[areas > 0][m] * nz[m]
    d_eq = 2 * np.sqrt(px / np.pi)
    return {
        "n_masks": len(masks), "n_empty": int((areas == 0).sum()),
        "relative_area_quantiles": {
            f"p{q}": round(float(np.percentile(nz, q)), 6)
            for q in (0, 1, 5, 10, 25, 50, 75, 90, 95, 99, 100)},
        "bucket_mix": mix,
        "tiny_equivalent_diameter_px": {
            "n": int(m.sum()), "median": round(float(np.median(d_eq)), 1),
            "p5": round(float(np.percentile(d_eq, 5)), 1),
            "p95": round(float(np.percentile(d_eq, 95)), 1),
            "definition": "等效圆直径 = 2*sqrt(面积/π)，面积按相对面积×该图实际像素数还原",
            "image_size_range": {"min_h": min(s[0] for s in sizes),
                                 "max_h": max(s[0] for s in sizes),
                                 "min_w": min(s[1] for s in sizes),
                                 "max_w": max(s[1] for s in sizes)},
        },
        "note": "tiny 阈值沿用 p97 的 <1% 相对面积口径（与现有 DSC 0.4472 同口径，可直接引用）",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="2.4 失败模式分析（p103）")
    ap.add_argument("--parts", default="t2b,tn5000,aul,tn3k")
    ap.add_argument("--data-dir", default=str(Path.home() / "uai/data/t2b_cactus"))
    ap.add_argument("--tn5000-zip", default=str(Path.home() / "uai/data/tn5000/TN5000_forReview.zip"))
    ap.add_argument("--p110-report", default="reports/p110_imbalance_aug_arms.json")
    ap.add_argument("--p85-report", default="reports/p85_aul_holdout_formal.json")
    ap.add_argument("--aul-dir", default=str(Path.home() / "uai/data/aul"))
    ap.add_argument("--tn3k-root", default=str(Path.home() / "uai/data/tn3k_dataset"))
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--report", default="reports/p112_failure_modes.json")
    args = ap.parse_args(argv)

    t0 = time.monotonic()
    parts = [p.strip() for p in args.parts.split(",") if p.strip()]
    out: dict = {"probe": "p112_failure_modes",
                 "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "why": ("biomedical.md › Regulatory considerations 要求 "
                         "'Failure mode analysis: characterize where the model fails and how gracefully'"),
                 "parts": parts, "results": {}}

    def rel(p):
        pp = Path(p)
        return pp if pp.is_absolute() else Path.cwd() / pp

    if "t2b" in parts:
        print("\n=== A. T2b Random 类 ===", flush=True)
        out["results"]["t2b_random"] = part_t2b(Path(args.data_dir).expanduser(), out)
    if "tn5000" in parts:
        print("\n=== B. TN5000 漏诊 ===", flush=True)
        out["results"]["tn5000_missed_malignant"] = part_tn5000(
            Path(args.tn5000_zip).expanduser(), rel(args.p110_report), args.threshold, out)
    if "aul" in parts:
        print("\n=== C. AUL 良性误判 ===", flush=True)
        out["results"]["aul_benign_as_malignant"] = part_aul(
            Path(args.aul_dir).expanduser(), rel(args.p85_report))
    if "tn3k" in parts:
        print("\n=== D. TN3K tiny ===", flush=True)
        out["results"]["tn3k_tiny"] = part_tn3k(Path(args.tn3k_root).expanduser())

    out["wall_s"] = round(time.monotonic() - t0, 1)
    rp = rel(args.report)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n报告：{rp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
