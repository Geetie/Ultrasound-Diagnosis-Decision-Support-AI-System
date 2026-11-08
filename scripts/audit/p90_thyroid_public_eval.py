"""正式评测 · 甲状腺公开集三合一（TN5000 病理 / DDTI 分级 / TN3K 分割）。

## 为什么需要这个脚本

甲状腺是**主线科室**，但截至 2026-11-08 项目里**没有任何一份甲状腺的正式评测**：
- `p37_t2a_tn3k_parity.py` 只是"对齐探针"，不是评测；
- AUL(肝)/CACTUS(心)/CAMUS(心) 都有 p85-p89，唯独甲状腺是空白。

而甲状腺的三份公开集都已在本机（见 `docs/合规/评测数据集/下载清单.md`），
**有数据不评测 = 主线科室没有可辩护数字**。

## 三个子评测（各自独立，可单跑）

### 1. TN5000 —— 病理金标准 → 恶/良二分类
- 5,000 图，`0`=良性 1426 / `1`=恶性 3574，标签**来自 FNA 活检与病理**
  （论文原文："determined according to FNA biopsy and pathological results"）
- 用途：**这是甲状腺侧最硬的标签**。AUL 的良性侧只是增强影像，TN5000 是病理。
- 指标：恶性 vs 良性 AUC + 敏感性/特异性 + P/R/F1 + 混淆矩阵 + bootstrap CI

### 2. DDTI —— 医生 TI-RADS 分级 → 分级一致性
- 390 例 XML，`tirads` 字段 **298 例非空**（2/3/4a/4b/4c/5）
- **重要**：已实测「按 ACR 计数法从特征反推」与人标大类一致仅 62.3%
  （见 `docs/合规/评测数据集/DDTI分级反推验证-否定结论.md`）→ **本评测不做规则反推**，
  只做「系统输出 vs 医生分级」的一致性：
  - 大类一致率（1/2/3/4/5）
  - 加权 Cohen's kappa（考虑档位是有序的）
  - 混淆矩阵
- 限制：TCIA 的 T2a 分类器输出是 Benign/Malignant/Normal 三分类，**没有 TI-RADS 分级输出**。
  因此本子评测只能：①若 `/v1/classify` 有分级输出则用；②否则**如实报 blocked**，
  不拿"恶性概率"硬映射成"分级"（那是自欺）。

### 3. TN3K —— 结节分割 → Dice
- 3,493 图 + 3,493 掩码，但**当前分割模型是甲状腺 U-Net**（`unet_tn3k.pt`）
- 指标：Dice / IoU + per-image 分布 + bootstrap CI
- 依赖：分割权重存在且可加载

## 口径纪律（与 p85-p89 一致）

- **scope 必须写清**：公开集、无患者 ID、与训练集是否重叠不可证 → 「公开集泛化下界」
- **n 不足或字段缺失 → 报 blocked，不给数字**
- **必须同时报丑数字**（敏感性高但特异性低这类）
- **bootstrap 2000 次固定种子**，保证可复现

## 用法

    PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/audit/p90_thyroid_public_eval.py \
        --subset tn5000 --report reports/p90_thyroid_tn5000.json
    ... --subset ddti --report ...
    ... --subset tn3k --report ...
    ... --subset all --report reports/p90_thyroid_all.json
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

CLASSIFY_URL = "http://127.0.0.1:8003/v1/classify"
EXTS = {".jpg", ".jpeg", ".png", ".bmp"}
BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 20261108


# --------------------------------------------------------------------- 指标
def auc_binary(scores: list[float], labels: list[int]) -> float:
    """二分类 AUC（Mann-Whitney U / 秩和口径，与 p85 同实现）。"""
    pairs = sorted(zip(scores, labels), key=lambda x: x[0])
    pos = [i for i, (_, y) in enumerate(pairs) if y == 1]
    neg = [i for i, (_, y) in enumerate(pairs) if y == 0]
    if not pos or not neg:
        return float("nan")
    ranks: dict[int, float] = {}
    i = 0
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    r_pos = sum(ranks[i] for i in pos)
    n1, n0 = len(pos), len(neg)
    return (r_pos - n1 * (n1 + 1) / 2) / (n1 * n0)


def bootstrap_ci(
    scores: list[float], labels: list[int], metric, n: int = BOOTSTRAP_N
) -> tuple[float, float]:
    """bootstrap 置信区间（固定种子，可复现）。"""
    rng = random.Random(BOOTSTRAP_SEED)
    vals = []
    idx = list(range(len(scores)))
    for _ in range(n):
        pick = [rng.choice(idx) for _ in idx]
        s = [scores[i] for i in pick]
        y = [labels[i] for i in pick]
        v = metric(s, y)
        if v == v:  # 非 NaN
            vals.append(v)
    if not vals:
        return (float("nan"), float("nan"))
    vals.sort()
    return (round(vals[int(0.025 * len(vals))], 4), round(vals[int(0.975 * len(vals))], 4))


def binary_prf(scores: list[float], labels: list[int], thr: float = 0.5) -> dict:
    """二分类 P/R/F1 + 混淆矩阵（正类=恶性）。"""
    pred = [1 if s >= thr else 0 for s in scores]
    tp = sum(1 for p, y in zip(pred, labels) if p == 1 and y == 1)
    fp = sum(1 for p, y in zip(pred, labels) if p == 1 and y == 0)
    fn = sum(1 for p, y in zip(pred, labels) if p == 0 and y == 1)
    tn = sum(1 for p, y in zip(pred, labels) if p == 0 and y == 0)
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    spec = tn / (tn + fp) if (tn + fp) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return {
        "threshold": thr,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(prec, 4),
        "recall_sensitivity": round(rec, 4),
        "specificity": round(spec, 4),
        "f1": round(f1, 4),
        "accuracy": round((tp + tn) / max(len(labels), 1), 4),
    }


def weighted_kappa(true: list[str], pred: list[str], order: list[str]) -> float:
    """加权 Cohen's kappa（有序档位，线性权重）。"""
    idx = {k: i for i, k in enumerate(order)}
    n = len(true)
    if n == 0:
        return float("nan")
    k = len(order)
    obs = [[0.0] * k for _ in range(k)]
    for t, p in zip(true, pred):
        if t in idx and p in idx:
            obs[idx[t]][idx[p]] += 1
    total = sum(sum(r) for r in obs)
    if total == 0:
        return float("nan")
    rt = [sum(obs[i]) for i in range(k)]
    ct = [sum(obs[i][j] for i in range(k)) for j in range(k)]
    num = den = 0.0
    for i in range(k):
        for j in range(k):
            w = abs(i - j) / (k - 1) if k > 1 else 0.0
            expected = rt[i] * ct[j] / total
            num += w * obs[i][j]
            den += w * expected
    return round(1 - num / den, 4) if den else float("nan")


# --------------------------------------------------------------------- 推理
def post_image(path: Path, timeout: float = 60.0) -> dict | None:
    """把一张图 POST 到 8003 /v1/classify，返回解析后的 JSON（失败返回 None）。"""
    boundary = uuid.uuid4().hex
    data = path.read_bytes()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="images"; filename="{path.name}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        CLASSIFY_URL, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return None


def malignant_prob(resp: dict) -> float | None:
    """从 /v1/classify 响应里取恶性概率。"""
    if not isinstance(resp, dict):
        return None
    items = resp.get("results") or resp.get("images") or []
    if isinstance(items, list) and items:
        item = items[0]
        probs = item.get("probs") or item.get("probabilities") or {}
        if isinstance(probs, dict):
            for k in ("Malignant", "malignant", "MALIGNANT"):
                if k in probs:
                    return float(probs[k])
    for k in ("malignant_prob", "malignant_probability"):
        if k in resp:
            return float(resp[k])
    return None


# --------------------------------------------------------------------- TN5000
def run_tn5000(root: Path, limit: int | None) -> dict:
    """TN5000：病理金标准 恶/良 二分类。"""
    zips = sorted(root.rglob("TN5000*.zip"))
    if not zips:
        return {"subset": "tn5000", "status": "blocked",
                "reason": "未找到 TN5000*.zip（请先按下载清单下载）"}
    zpath = zips[0]
    images: list[tuple[str, Path | bytes, int]] = []
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        ann = {n for n in names if n.lower().endswith(".xml")}
        imgs = {n for n in names if Path(n).suffix.lower() in EXTS}
        # 用 xml 里的标签；xml 与图按同名 stem 配对
        for a in sorted(ann):
            stem = Path(a).stem
            cand = [i for i in imgs if Path(i).stem == stem]
            if not cand:
                continue
            try:
                node = ET.fromstring(z.read(a))
            except ET.ParseError:
                continue
            # TN5000 是 **Pascal VOC 格式**，良恶性标在 `<object><name>` 里：
            #   <annotation><object><name>0</name>...  ← 0=良性 / 1=恶性
            # （实测 2026-11-08：`000001.xml` 结构确认；论文原文说标签来自
            #   FNA biopsy 与病理结果）
            label = None
            for obj in node.findall(".//object"):
                nm = ((obj.findtext("name") or "").strip())
                if nm in ("0", "1"):
                    label = int(nm)
                    break
            if label is None:
                # 兜底：有些版本可能直接放 <label>
                for tag in ("label", "malignant"):
                    e = node.find(f".//{tag}")
                    if e is not None and (e.text or "").strip() in ("0", "1"):
                        label = int(e.text.strip())
                        break
            if label is None:
                continue
            images.append((stem, cand[0], label))
    if limit:
        images = images[:limit]
    if not images:
        return {"subset": "tn5000", "status": "blocked",
                "reason": "zip 内未解析出 (图,标签) 对；标签字段名可能与预期不同"}

    scores: list[float] = []
    labels: list[int] = []
    failed = 0
    t0 = time.monotonic()
    with zipfile.ZipFile(zpath) as z:
        for stem, member, label in images:
            data = z.read(member)
            tmp = Path("/tmp") / f"_p90_{stem}{Path(member).suffix}"
            tmp.write_bytes(data)
            resp = post_image(tmp)
            try:
                tmp.unlink()
            except OSError:  # silent-ok: 临时文件删不掉不影响结论
                pass
            p = malignant_prob(resp) if resp else None
            if p is None:
                failed += 1
                continue
            scores.append(p)
            labels.append(label)

    if not scores:
        return {"subset": "tn5000", "status": "blocked",
                "reason": f"全部 {len(images)} 张推理失败（8003 是否就绪？）"}

    auc = auc_binary(scores, labels)
    ci = bootstrap_ci(scores, labels, auc_binary)
    return {
        "subset": "tn5000",
        "status": "ok",
        "scope": "public_dataset_holdout_lower_bound",
        "scope_note": (
            "TN5000 为公开集（CC BY 4.0），标签来自 FNA 活检与病理。"
            "无患者 ID、与训练集是否重叠不可证 → 本数字是公开集泛化下界，"
            "不能当作注册申报级独立验证。恶性占比 71.5% 属人为富集，"
            "**患病率与派生指标不可外推**。"
        ),
        "dataset": {
            "name": "TN5000", "zip": str(zpath), "n_evaluated": len(scores),
            "n_failed": failed,
            "class_support": {"benign": labels.count(0), "malignant": labels.count(1)},
        },
        "metrics": {
            "auc_malignant_vs_benign": round(auc, 4),
            "auc_ci95": list(ci),
            "bootstrap_n": BOOTSTRAP_N,
            **binary_prf(scores, labels, 0.5),
        },
        "wall_s": round(time.monotonic() - t0, 1),
    }


# --------------------------------------------------------------------- DDTI
def run_ddti(root: Path) -> dict:
    """DDTI：医生 TI-RADS 分级 vs 系统的分级能力。

    诚实前置：当前 `/v1/classify` 只输出 Benign/Malignant/Normal 三分类，
    **没有 TI-RADS 分级**。所以本子评测要么 blocked，要么只能做"恶性概率的区分度"。
    """
    xmls = sorted(root.rglob("*.xml"))
    rows = []
    for f in xmls:
        try:
            node = ET.parse(f).getroot()
        except ET.ParseError:
            continue
        ti = ((node.findtext("tirads") or "").strip().lower())
        if ti:
            rows.append({"file": f.name, "tirads": ti})
    dist: dict[str, int] = {}
    for r in rows:
        dist[r["tirads"]] = dist.get(r["tirads"], 0) + 1

    return {
        "subset": "ddti",
        "status": "blocked",
        "reason": (
            "系统 /v1/classify 输出的是 Benign/Malignant/Normal 三分类，"
            "**没有 TI-RADS 分级**。拿恶性概率硬映射成分级属自欺，不做。"
        ),
        "unblock_condition": (
            "需要 T2a 增加分级头（TR1-TR5），或改用能输出分级的模型。"
            "另外已实测：按 ACR 计数法从 DDTI 特征反推与人标大类一致仅 62.3%，"
            "规则反推亦不可作为替代（见 DDTI分级反推验证-否定结论.md）。"
        ),
        "dataset": {
            "name": "DDTI", "n_xml": len(xmls), "n_with_tirads": len(rows),
            "tirads_distribution": dict(sorted(dist.items())),
        },
    }


# --------------------------------------------------------------------- TN3K
def run_tn3k(root: Path, limit: int | None) -> dict:
    """TN3K：结节分割 Dice/IoU。"""
    img_dirs = [d for d in root.rglob("*") if d.is_dir() and "image" in d.name.lower()]
    mask_dirs = [d for d in root.rglob("*") if d.is_dir() and "mask" in d.name.lower()]
    if not img_dirs or not mask_dirs:
        return {"subset": "tn3k", "status": "blocked",
                "reason": f"未找到图/掩码目录（img_dirs={len(img_dirs)}, mask_dirs={len(mask_dirs)}）"}
    pairs = []
    for idir in img_dirs:
        tag = "test" if "test" in idir.name.lower() else "trainval"
        mdir = next((m for m in mask_dirs if tag in m.name.lower()), None)
        if mdir is None:
            continue
        for p in sorted(idir.glob("*")):
            if p.suffix.lower() not in EXTS:
                continue
            m = next((q for q in mdir.glob(f"{p.stem}.*") if q.suffix.lower() in EXTS), None)
            if m:
                pairs.append((p, m))
    if limit:
        pairs = pairs[:limit]
    if not pairs:
        return {"subset": "tn3k", "status": "blocked", "reason": "图/掩码未配对成功"}

    return {
        "subset": "tn3k",
        "status": "blocked",
        "reason": (
            "分割推理端点未就绪：8003 只提供 /v1/classify（分类）与 /v1/analyze_view（切面），"
            "**没有分割端点**；甲状腺 U-Net 权重在 app 进程内加载，不经 HTTP 暴露。"
        ),
        "unblock_condition": (
            "需给 8003 加 /v1/segment 端点，或在 app 进程内直接跑分割并 dump 结果再比对。"
        ),
        "dataset": {"name": "TN3K", "n_pairs_available": len(pairs)},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="甲状腺公开集三合一正式评测（p90）")
    ap.add_argument("--subset", choices=["tn5000", "ddti", "tn3k", "all"], default="all")
    ap.add_argument("--tn5000-root", default=str(Path.home() / "uai/data/tn5000"))
    ap.add_argument("--ddti-root", default=str(Path.home() / "uai/data/ddti"))
    ap.add_argument("--tn3k-root", default=str(Path.home() / "uai/data/tn3k_dataset"))
    ap.add_argument("--limit", type=int, default=None, help="每子集最多几例（调试用）")
    ap.add_argument("--report", default="reports/p90_thyroid_public_eval.json")
    args = ap.parse_args(argv)

    out: dict = {
        "probe": "p90_thyroid_public_eval",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "subsets": {},
    }
    if args.subset in ("tn5000", "all"):
        out["subsets"]["tn5000"] = run_tn5000(Path(args.tn5000_root), args.limit)
    if args.subset in ("ddti", "all"):
        out["subsets"]["ddti"] = run_ddti(Path(args.ddti_root))
    if args.subset in ("tn3k", "all"):
        out["subsets"]["tn3k"] = run_tn3k(Path(args.tn3k_root), args.limit)

    rp = Path(args.report)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
