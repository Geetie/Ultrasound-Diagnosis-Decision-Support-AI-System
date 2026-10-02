"""T2a：检索式超声图像良恶性判别评测（真机）。

任务定义（T2a）：以图像嵌入检索为判别机制——查询图像经 8003（BiomedCLIP 底座）
嵌入，在 US-365K 参考图库（caption 含良恶性关键词的样本）中做 kNN 检索，
由近邻 caption 的恶性线索加权投票产出恶性概率，与标注对照产出
敏感性/特异性/PPV/NPV/AUC 与漏诊/误诊分解。

评测集：AUL（CC BY 4.0，Zenodo 7272660，肝脏超声，目录天然三分类：
Benign 200 / Malignant 435 / Normal 100）。原计划用 TNSCUI（CC BY-ND），
但其 Zenodo 记录仅含论文 PDF、hf-mirror 无镜像、GitHub 竞赛仓库无公开
数据链接（渠道受阻证据见 16 号 A.7）——AUL 为同性质替代（真实公开、
带专家良恶性标注、许可宽松）。

用法（真机 ~/uai/src，需 8003 就绪）：
    python scripts/server_t2a_malignancy_eval.py \
        --aul-dir ~/uai/data/aul \
        --us365k-jsonl ~/uai/data/us365k/test.resolved.jsonl \
        --us365k-image-root ~/uai/data/us365k/images \
        --output reports/t2a_malignancy_report.json
"""
from __future__ import annotations

import argparse
import io
import json
import random
import re
import time
import urllib.request
import uuid
from pathlib import Path

_MALIGNANT_PAT = re.compile(
    r"\b(malignant|cancer|carcinoma|metastas|tumor|tumour|neoplas|lymphoma|"
    r"malignancy|oncolog)\w*", re.IGNORECASE
)
_BENIGN_PAT = re.compile(
    r"\b(benign|cyst|hemangioma|haemangioma|adenoma|fibroadenoma|"
    r"no evidence of malignancy|unremarkable|normal)\w*", re.IGNORECASE
)


def _clip_batch_embeddings(
    image_bytes_list: list[bytes], clip_url: str, batch: int = 16
) -> list[list[float]]:
    """经 8003 /v1/embeddings/multimodal 端点批量嵌入（images 文件组）。"""
    vectors: list[list[float]] = []
    boundary = uuid.uuid4().hex
    for start in range(0, len(image_bytes_list), batch):
        chunk = image_bytes_list[start : start + batch]
        body = io.BytesIO()
        for i, raw in enumerate(chunk):
            body.write(f"--{boundary}\r\n".encode())
            body.write(
                (
                    'Content-Disposition: form-data; name="images"; '
                    f'filename="img{i}.jpg"\r\n'
                ).encode()
            )
            body.write(b"Content-Type: image/jpeg\r\n\r\n")
            body.write(raw)
            body.write(b"\r\n")
        body.write(f"--{boundary}\r\n".encode())
        body.write(b'Content-Disposition: form-data; name="modality"\r\n\r\n')
        body.write(b"multimodal\r\n")
        body.write(f"--{boundary}\r\n".encode())
        body.write(b'Content-Disposition: form-data; name="texts"\r\n\r\n')
        body.write(b'[]\r\n')
        body.write(f"--{boundary}--\r\n".encode())
        req = urllib.request.Request(
            f"{clip_url}/v1/embeddings/multimodal",
            data=body.getvalue(),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.load(resp)
        embs = data["embeddings"]
        if len(embs) != len(chunk):
            raise RuntimeError(f"batch {start}: got {len(embs)} != {len(chunk)}")
        vectors.extend(embs)
        print(f"  embedded {start + len(chunk)}/{len(image_bytes_list)}", flush=True)
    return vectors


def _read_image(path: Path) -> bytes:
    return path.read_bytes()


def _l2norm(vecs: list[list[float]]) -> list[list[float]]:
    out = []
    for v in vecs:
        n = sum(x * x for x in v) ** 0.5 or 1.0
        out.append([x / n for x in v])
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="T2a 检索式良恶性判别评测")
    parser.add_argument("--aul-dir", required=True)
    parser.add_argument("--us365k-jsonl", required=True, help="test.resolved.jsonl")
    parser.add_argument("--us365k-image-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--clip-url", default="http://127.0.0.1:8003")
    parser.add_argument("--ref-size", type=int, default=3000, help="参考库样本数上限")
    parser.add_argument("--n-benign", type=int, default=150)
    parser.add_argument("--n-malignant", type=int, default=150)
    parser.add_argument("--n-normal", type=int, default=50)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)
    root = Path(args.us365k_image_root)

    # ① 参考库：caption 含良恶性关键词的 US-365K 样本（两类均衡）
    print("[1/4] 构建参考库（筛选 caption 带良恶性线索）...")
    mal_samples: list[tuple[Path, str]] = []
    ben_samples: list[tuple[Path, str]] = []
    with open(args.us365k_jsonl, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            cap = rec.get("caption", "")
            img = rec.get("image", "")
            if not img:
                continue
            # resolved.jsonl 的 image 为绝对路径；相对路径则拼 image_root
            p = Path(img) if Path(img).is_absolute() else root / img
            if not p.exists():
                continue
            is_mal = bool(_MALIGNANT_PAT.search(cap))
            is_ben = bool(_BENIGN_PAT.search(cap)) and not is_mal
            if is_mal and len(mal_samples) < args.ref_size // 2:
                mal_samples.append((p, cap))
            elif is_ben and len(ben_samples) < args.ref_size // 2:
                ben_samples.append((p, cap))
            if (
                len(mal_samples) >= args.ref_size // 2
                and len(ben_samples) >= args.ref_size // 2
            ):
                break
    ref = mal_samples + ben_samples
    print(f"  参考库: 恶性线索 {len(mal_samples)} + 良性线索 {len(ben_samples)}")

    # ② 查询集：AUL 三类抽样
    print("[2/4] 抽样 AUL 查询集...")
    queries: list[tuple[Path, str]] = []  # (path, true_label: benign/malignant/normal)
    aul = Path(args.aul_dir)
    for cls, n in (("Benign", args.n_benign), ("Malignant", args.n_malignant), ("Normal", args.n_normal)):
        imgs = sorted((aul / cls / "image").glob("*.jpg"))
        picked = random.sample(imgs, min(n, len(imgs)))
        queries.extend((p, cls.lower()) for p in picked)
    print(f"  查询: {len(queries)} 例")

    # ③ 批量嵌入
    print("[3/4] 8003 批量嵌入（参考库 + 查询）...")
    t0 = time.perf_counter()
    ref_vecs = _l2norm(
        _clip_batch_embeddings([_read_image(p) for p, _ in ref], args.clip_url)
    )
    query_vecs = _l2norm(
        _clip_batch_embeddings([_read_image(p) for p, _ in queries], args.clip_url)
    )
    print(f"  嵌入耗时 {time.perf_counter() - t0:.0f}s")

    # ④ kNN 检索判别 + 指标
    print("[4/4] kNN 判别与指标...")

    ref_meta = [
        {"path": str(p), "label": "malignant" if i < len(mal_samples) else "benign",
         "caption": c[:160]}
        for i, (p, c) in enumerate(ref)
    ]
    results: list[dict] = []
    for qi, ((qpath, truth), qv) in enumerate(zip(queries, query_vecs, strict=True)):
        sims = []
        for ri, rv in enumerate(ref_vecs):
            dot = sum(a * b for a, b in zip(qv, rv, strict=True))
            sims.append((dot, ri))
        sims.sort(reverse=True)
        topk = sims[: args.top_k]
        # 恶性分数：近邻恶性占比（余弦相似度加权）
        w_mal = sum(max(0.0, s) for s, ri in topk if ref_meta[ri]["label"] == "malignant")
        total = sum(max(0.0, s) for s, _ in topk) or 1.0
        mal_score = w_mal / total
        results.append(
            {
                "query_id": f"aul-{qi:04d}",
                "path": str(qpath),
                "truth": truth,
                "malignancy_score": round(mal_score, 4),
                "neighbors": [
                    {
                        "label": ref_meta[ri]["label"],
                        "sim": round(s, 4),
                        "caption": ref_meta[ri]["caption"][:100],
                    }
                    for s, ri in topk[:5]
                ],
            }
        )

    # 二分类（良性 vs 恶性；Normal 不进主指标，单独列出）
    binary = [r for r in results if r["truth"] in ("benign", "malignant")]
    threshold = 0.5
    tp = sum(1 for r in binary if r["truth"] == "malignant" and r["malignancy_score"] >= threshold)
    fn = sum(1 for r in binary if r["truth"] == "malignant" and r["malignancy_score"] < threshold)
    tn = sum(1 for r in binary if r["truth"] == "benign" and r["malignancy_score"] < threshold)
    fp = sum(1 for r in binary if r["truth"] == "benign" and r["malignancy_score"] >= threshold)

    def _auc(scores_labels: list[tuple[float, int]]) -> float:
        pos = [s for s, y in scores_labels if y == 1]
        neg = [s for s, y in scores_labels if y == 0]
        if not pos or not neg:
            return float("nan")
        wins = ties = 0
        for p in pos:
            for n in neg:
                if p > n:
                    wins += 1
                elif p == n:
                    ties += 1
        return (wins + 0.5 * ties) / (len(pos) * len(neg))

    auc = _auc(
        [(r["malignancy_score"], 1 if r["truth"] == "malignant" else 0) for r in binary]
    )
    # AUC 0.5 分界下的"最优"阈值（约登指数）与默认 0.5 对照
    grid = []
    for thr in [i / 20 for i in range(21)]:
        se = sum(1 for r in binary if r["truth"] == "malignant" and r["malignancy_score"] >= thr)
        sp = sum(1 for r in binary if r["truth"] == "benign" and r["malignancy_score"] < thr)
        grid.append(
            {"threshold": thr, "sensitivity": se / (tp + fn), "specificity": sp / (tn + fp)}
        )
    best = max(grid, key=lambda g: g["sensitivity"] + g["specificity"] - 1)

    normal_scores = [r["malignancy_score"] for r in results if r["truth"] == "normal"]

    report = {
        "task": "T2a 检索式良恶性判别（检索机制：BiomedCLIP 图像嵌入 kNN + caption 线索投票）",
        "dataset": "AUL (CC BY 4.0, Zenodo 7272660)；TNSCUI 渠道受阻（Zenodo 仅 PDF/无镜像/无公开链接）",
        "reference_library": {
            "size": len(ref),
            "malignant_hint": len(mal_samples),
            "benign_hint": len(ben_samples),
        },
        "query_set": {"benign": args.n_benign, "malignant": args.n_malignant, "normal": args.n_normal},
        "top_k": args.top_k,
        "binary_metrics_at_0.5": {
            "tp": tp, "fn": fn, "tn": tn, "fp": fp,
            "sensitivity": round(tp / (tp + fn), 4),
            "specificity": round(tn / (tn + fp), 4),
            "ppv": round(tp / (tp + fp), 4) if tp + fp else None,
            "npv": round(tn / (tn + fn), 4) if tn + fn else None,
            "accuracy": round((tp + tn) / len(binary), 4),
            "missed_cases": fn,
            "false_alarm_cases": fp,
        },
        "auc": round(auc, 4),
        "youden_best_threshold": best,
        "normal_class_scores": {
            "mean": round(sum(normal_scores) / len(normal_scores), 4) if normal_scores else None,
            "n": len(normal_scores),
        },
        "results": results,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    bm = report["binary_metrics_at_0.5"]
    print(
        f"完成: Se={bm['sensitivity']} Sp={bm['specificity']} PPV={bm['ppv']} "
        f"AUC={report['auc']} 漏诊={fn} 误诊={fp} → {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
