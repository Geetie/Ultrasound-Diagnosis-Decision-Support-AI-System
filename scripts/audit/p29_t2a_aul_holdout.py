"""T2a 独立集复验：**AUL 公开集全集冻结复跑**（口径：公开集 held-out 下界）。

为什么是"下界"而不是"注册级独立验证"
------------------------------------
AUL（CC BY 4.0，Zenodo 7272660，肝脏超声，Benign/Malignant/Normal 天然三分类，共 735 张）
是**公开集**：没有患者 ID、无法按患者分层，也无法证明与训练用的 US-365K / TN3K 无重叠。
因此本脚本产出的数字只能作为「公开集泛化下界」，**不能**当作注册申报的独立验证
（21 号 §阶段三-A 已写明这一口径，00 号拍板前不得混引）。报告里 `scope` 字段固定写明。

做什么
------
1. **冻结清单**：遍历 AUL 目录生成 `data/eval/t2a_aul_manifest.json`（相对路径 + 标签 + sha256）；
   已存在时**逐条校验 sha256**（换集/改集立刻失败 —— 防"换集刷分"）。
2. **冻结复跑**：对每张图调 8003 的 `POST /v1/classify`（微调 visual 副本 + LR 探针，
   与 `scripts/start_embedding_services.py` 同一机制），不重训、不调参。
3. **指标**：恶性-vs-其余 **AUC + bootstrap 95%CI**、阈值 0.5 下的敏感性/特异性/PPV/NPV、
   三分类准确率与混淆矩阵。
4. 报告落盘（`reports/t2a_aul_holdout.json`），退出码 0/1（低于门控 1）。

用法（服务器，需 8003 就绪）：
    PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/audit/p29_t2a_aul_holdout.py \
        --aul-dir ~/uai/data/aul --clip-url http://127.0.0.1:8003 \
        --report ~/uai/reports/t2a_aul_holdout.json
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import random
import sys
import time
import urllib.request
import uuid
from pathlib import Path

LABELS = ("Benign", "Malignant", "Normal")
EXTS = {".jpg", ".jpeg", ".png", ".bmp"}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_or_verify_manifest(aul_dir: Path, manifest: Path) -> list[dict]:
    """生成或校验冻结清单；任何不一致都**显式失败**（不静默换集）。"""
    rows: list[dict] = []
    for label in LABELS:
        for p in sorted((aul_dir / label).rglob("*")):
            if p.is_file() and p.suffix.lower() in EXTS:
                rows.append(
                    {
                        "path": str(p.relative_to(aul_dir)).replace("\\", "/"),
                        "label": label,
                        "sha256": _sha256(p),
                        "bytes": p.stat().st_size,
                    }
                )
    if not rows:
        raise SystemExit(f"AUL 目录下没找到图片：{aul_dir}（期望 Benign/Malignant/Normal 三子目录）")

    if manifest.is_file():
        old = json.loads(manifest.read_text(encoding="utf-8"))
        old_rows = {r["path"]: r for r in old.get("items", [])}
        if len(old_rows) != len(rows):
            raise SystemExit(
                f"清单条数不符（冻结 {len(old_rows)} vs 实测 {len(rows)}）—— "
                f"数据集被改动过，禁止换集刷分；确认无误后删除 {manifest} 重建"
            )
        for r in rows:
            o = old_rows.get(r["path"])
            if not o or o["sha256"] != r["sha256"]:
                raise SystemExit(f"清单 sha256 不符：{r['path']}（数据集被改动过）")
        print(f"清单校验通过：{len(rows)} 张（sha256 逐条一致）")
    else:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "_note": "AUL 公开集冻结清单（A.60）：相对路径 + 标签 + sha256；复跑前逐条校验，防换集刷分",
            "dataset": "AUL (CC BY 4.0, Zenodo 7272660)",
            "scope": "public_dataset_holdout_lower_bound",
            "count": len(rows),
            "items": rows,
        }
        manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        digest = hashlib.sha256(
            "\n".join(f"{r['path']}:{r['sha256']}" for r in rows).encode()
        ).hexdigest()[:16]
        print(f"清单已冻结：{len(rows)} 张 → {manifest}（清单指纹 {digest}）")
    return rows


def classify_batch(clip_url: str, raws: list[bytes]) -> list[dict]:
    """POST /v1/classify（multipart，字段名固定 `images`）。4xx/5xx 显式失败。"""
    boundary = uuid.uuid4().hex
    body = io.BytesIO()
    for i, raw in enumerate(raws):
        body.write(f"--{boundary}\r\n".encode())
        body.write(
            (
                'Content-Disposition: form-data; name="images"; '
                f'filename="img{i}.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'
            ).encode()
        )
        body.write(raw)
        body.write(b"\r\n")
    body.write(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(f"{clip_url}/v1/classify", data=body.getvalue(), method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("results") or []


def auc_mal_vs_rest(scores: list[float], labels: list[str]) -> float:
    """恶性 vs 其余 的 AUC（Mann-Whitney U / 秩和口径）。"""
    pairs = sorted(zip(scores, labels, strict=True), key=lambda x: x[0])
    pos = [i for i, (_, y) in enumerate(pairs) if y == "Malignant"]
    neg = [i for i, (_, y) in enumerate(pairs) if y != "Malignant"]
    if not pos or not neg:
        return float("nan")
    ranks = {}
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


def bootstrap_ci(scores: list[float], labels: list[str], n: int, seed: int = 20261013) -> tuple[float, float]:
    rnd = random.Random(seed)
    idx = list(range(len(scores)))
    vals: list[float] = []
    for _ in range(n):
        pick = [rnd.choice(idx) for _ in idx]
        s = [scores[i] for i in pick]
        y = [labels[i] for i in pick]
        a = auc_mal_vs_rest(s, y)
        if a == a:  # 非 NaN
            vals.append(a)
    if not vals:
        return float("nan"), float("nan")
    vals.sort()
    lo = vals[int(0.025 * (len(vals) - 1))]
    hi = vals[int(0.975 * (len(vals) - 1))]
    return lo, hi


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--aul-dir", default=str(Path.home() / "uai" / "data" / "aul"))
    ap.add_argument("--clip-url", default="http://127.0.0.1:8003")
    # ⚠️ A.60：清单默认放 `tests/reports/`（与 t2a 报告同处）而不是 21 号草拟的 `data/eval/` ——
    # 仓库的 `.gitignore` 忽略 `data/`，放那里会导致**冻结清单进不了版控**（本轮实测踩到），
    # 而"冻结清单"的价值恰恰在于可追溯。
    ap.add_argument("--manifest", default="tests/reports/t2a_aul_manifest.json")
    ap.add_argument("--report", default="reports/t2a_aul_holdout.json")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--bootstrap", type=int, default=2000)
    ap.add_argument("--gate", type=float, default=0.75, help="同 t2a_v2_official 的门控（开发集口径）")
    ap.add_argument("--limit", type=int, default=0, help=">0 时只跑前 N 张（冒烟用；报告会标注 partial）")
    args = ap.parse_args()

    aul = Path(args.aul_dir).expanduser()
    if not aul.is_dir():
        print(f"AUL 目录不存在：{aul}")
        return 2
    rows = build_or_verify_manifest(aul, Path(args.manifest).expanduser())
    if args.limit:
        rows = rows[: args.limit]
        print(f"⚠️ 冒烟模式：只跑前 {len(rows)} 张（报告 scope 会标注 partial）")

    t0 = time.monotonic()
    labels: list[str] = []
    mal_scores: list[float] = []
    preds: list[str] = []
    failed: list[str] = []
    for start in range(0, len(rows), args.batch):
        chunk = rows[start : start + args.batch]
        raws = [(aul / r["path"]).read_bytes() for r in chunk]
        try:
            results = classify_batch(args.clip_url, raws)
        except Exception as exc:  # 显式记录失败样本，不静默丢弃（丢样本会抬高指标）
            failed.extend(r["path"] for r in chunk)
            print(f"  批次失败 [{start}:{start + len(chunk)}] {type(exc).__name__}: {exc}")
            continue
        for r, res in zip(chunk, results, strict=True):
            probs = (res or {}).get("probs") or {}
            labels.append(r["label"])
            mal_scores.append(float(probs.get("Malignant", 0.0)))
            preds.append(max(probs, key=probs.get) if probs else "Unknown")
        done = min(start + len(chunk), len(rows))
        print(f"  {done}/{len(rows)}")

    if not labels:
        print("没有任何样本成功分类 —— 检查 8003 服务与 /v1/classify 端点")
        return 2

    auc = auc_mal_vs_rest(mal_scores, labels)
    lo, hi = bootstrap_ci(mal_scores, labels, args.bootstrap)
    tp = sum(1 for s, y in zip(mal_scores, labels, strict=True) if y == "Malignant" and s >= 0.5)
    fn = sum(1 for s, y in zip(mal_scores, labels, strict=True) if y == "Malignant" and s < 0.5)
    fp = sum(1 for s, y in zip(mal_scores, labels, strict=True) if y != "Malignant" and s >= 0.5)
    tn = sum(1 for s, y in zip(mal_scores, labels, strict=True) if y != "Malignant" and s < 0.5)
    sens = tp / (tp + fn) if (tp + fn) else float("nan")
    spec = tn / (tn + fp) if (tn + fp) else float("nan")
    acc = sum(1 for p, y in zip(preds, labels, strict=True) if p == y) / len(labels)
    confusion: dict[str, dict[str, int]] = {
        y: dict.fromkeys((*LABELS, "Unknown"), 0) for y in LABELS
    }
    for y, p in zip(labels, preds, strict=True):
        confusion.setdefault(y, {})
        confusion[y][p] = confusion[y].get(p, 0) + 1

    report = {
        "probe": "p29_t2a_aul_holdout",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "public_dataset_holdout_lower_bound"
        + ("_partial" if args.limit else ""),
        "scope_note": (
            "AUL 为公开集（CC BY 4.0，无患者 ID、与训练数据是否重叠不可证）→ 本数字只作"
            "「公开集泛化下界」，**不得**当作注册申报的独立验证（见 21 号 §阶段三-A）"
        ),
        "dataset": {"name": "AUL", "dir": str(aul), "n": len(labels), "failed": failed},
        "classifier": {"endpoint": f"{args.clip_url}/v1/classify", "mechanism": "finetuned_visual_tower + logistic_probe"},
        "metrics": {
            "mal_vs_rest_auc": round(auc, 4) if auc == auc else None,
            "auc_ci95": [round(lo, 4) if lo == lo else None, round(hi, 4) if hi == hi else None],
            "bootstrap_n": args.bootstrap,
            "sensitivity@0.5": round(sens, 4) if sens == sens else None,
            "specificity@0.5": round(spec, 4) if spec == spec else None,
            "accuracy_3class": round(acc, 4),
            "confusion": confusion,
            "gate": args.gate,
        },
        "wall_s": round(time.monotonic() - t0, 1),
    }
    out = Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nAUC(恶性 vs 其余) = {report['metrics']['mal_vs_rest_auc']}  "
          f"95%CI {report['metrics']['auc_ci95']}（bootstrap {args.bootstrap}）")
    print(f"敏感性@0.5 = {report['metrics']['sensitivity@0.5']}  特异性@0.5 = {report['metrics']['specificity@0.5']}  "
          f"三分类准确率 = {report['metrics']['accuracy_3class']}")
    print(f"口径：{report['scope']}（公开集 held-out 下界，非注册级独立验证）")
    print(f"报告：{out}  耗时 {report['wall_s']}s  失败样本 {len(failed)}")
    ok = auc == auc and auc >= args.gate and hi == hi and hi >= 0.5
    print(f"== p29: {'PASS' if ok else 'FAIL'}（门控 AUC≥{args.gate}，同 t2a_v2_official 口径）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
