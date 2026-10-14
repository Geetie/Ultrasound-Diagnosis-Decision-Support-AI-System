#!/usr/bin/env python
"""独立验证集**冻结/校验流程预置**（注册级独立验证；独立数据一到即可套用）。

为什么要预置
------------
21 号阶段三-A 要求甲状腺判别按「开发集 / 公开集 / 独立集」三口径分列，且独立集一旦拿到就**必须冻结**
（清单 + 逐条 sha256），否则"独立验证"随时可能被换成更好看的那一批（换集刷分）。
本脚本把这件事**提前写成可执行流程**：数据一到直接跑，而不是届时再讨论口径。

与 `p29`（AUL 公开集）的关系
----------------------------
`p29` 冻结的是**公开集**：无患者 ID、无法证明与训练集无重叠 → 报告固定写
`scope=public_dataset_holdout_lower_bound`（只能当"下界"）。
本脚本处理**独立集**，硬性要求**患者级可切分**：
* 缺 `patient_id` 的条目**直接报错**（不许把"不知道是谁"的数据当独立集用）；
* 必须与开发集**患者级零交集**（否则是"自己考自己"）。

用法
----
```
# ① 冻结（输入：清单 JSONL，每行 {patient_id, path, label[, organ]}）
python scripts/audit/p34_freeze_independent_manifest.py freeze \
    --input <labels.jsonl> --out data/eval/t2a_independent_manifest.json \
    [--base-dir <影像根目录>] [--dev-manifest data/eval/t2a_aul_manifest.json]
    [--dev-patients <dev_patients.txt>]

# ② 校验（数据方换了一批 / 想确认没被改过）
python scripts/audit/p34_freeze_independent_manifest.py verify \
    --manifest data/eval/t2a_independent_manifest.json [--dev-patients <dev_patients.txt>]
```

退出码：0 = 通过；1 = 校验失败（缺患者 ID / 交集非空 / sha256 不符）；2 = 输入缺失或不可读。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

#: 冻结清单口径标签（写死，防止与"公开集下界"混引）
SCOPE = "clinical_independent_validation"
#: 恶性比例与开发集的最大允许偏差（切分跑偏了要能看出来）
DEFAULT_RATIO_TOLERANCE = 0.15


def file_sha256(path: Path, chunk: int = 1 << 20) -> str:
    """文件内容 sha256（分块读，避免大影像吃内存）。"""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def load_items(input_path: Path) -> list[dict]:
    """读清单（JSONL；每行一个对象）。畸形行**直接报错**，不静默跳过。"""
    items: list[dict] = []
    for lineno, line in enumerate(input_path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"第 {lineno} 行不是合法 JSON：{exc}") from exc
        if not isinstance(obj, dict):
            raise ValueError(f"第 {lineno} 行不是对象：{type(obj).__name__}")
        items.append(obj)
    return items


def validate_items(
    items: list[dict], base_dir: Path, *, require_patient: bool = True
) -> list[dict]:
    """逐条校验并补 sha256；任何缺件都抛错（不静默）。"""
    if not items:
        raise ValueError("清单为空 —— 空清单不是「独立集」，请先给数据")
    out: list[dict] = []
    missing_patient: list[int] = []
    for i, it in enumerate(items, 1):
        rel = str(it.get("path") or "").strip()
        label = str(it.get("label") or "").strip()
        patient = str(it.get("patient_id") or "").strip()
        if not rel:
            raise ValueError(f"第 {i} 条缺 path")
        if not label:
            raise ValueError(f"第 {i} 条缺 label（独立验证必须有标签，否则算不出指标）")
        f = base_dir / rel
        if not f.is_file():
            raise ValueError(f"第 {i} 条影像不存在：{f}")
        if not patient:
            missing_patient.append(i)
        out.append(
            {
                "patient_id": patient,
                "path": rel,
                "label": label,
                "organ": str(it.get("organ") or "").strip(),
                "sha256": file_sha256(f),
            }
        )
    if require_patient and missing_patient:
        raise ValueError(
            f"{len(missing_patient)}/{len(items)} 条缺 patient_id（行号 {missing_patient[:5]}）—— "
            "独立验证必须能按患者切分；缺 ID 的数据只能当「公开集下界」，不能当独立集（21 号阶段三-A）"
        )
    return out


def ratios(items: list[dict], key: str = "label") -> dict[str, float]:
    """分层比例（用于确认切分没跑偏，例如恶性比例与开发集量级一致）。"""
    c = Counter(str(it.get(key) or "?") for it in items)
    total = sum(c.values()) or 1
    return {k: round(v / total, 4) for k, v in sorted(c.items())}


def patients(items: list[dict]) -> set[str]:
    return {str(it.get("patient_id")) for it in items if it.get("patient_id")}


def check_disjoint(indep: set[str], dev: set[str]) -> list[str]:
    """返回交集（**必须为空**）。"""
    return sorted(indep & dev)


def read_dev_patients(path: Path) -> set[str]:
    """开发集患者名单：`.json` 走 manifest 的 items，其余按每行一个。"""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        items = data.get("items") or []
        return {str(it.get("patient_id")) for it in items if it.get("patient_id")}
    return {line.strip() for line in text.splitlines() if line.strip()}


def ratio_deviation(actual: dict[str, float], expect: dict[str, float]) -> float:
    """返回各标签比例的最大绝对偏差（缺标签按 0 计）。"""
    keys = set(actual) | set(expect)
    return max((abs(actual.get(k, 0.0) - expect.get(k, 0.0)) for k in keys), default=0.0)


def _load_dev(args: argparse.Namespace) -> set[str]:
    dev: set[str] = set()
    if args.dev_manifest:
        p = Path(args.dev_manifest).expanduser()
        if p.is_file():
            dev |= read_dev_patients(p)
    if args.dev_patients:
        p = Path(args.dev_patients).expanduser()
        if p.is_file():
            dev |= read_dev_patients(p)
    return dev


def _cmd_freeze(args: argparse.Namespace) -> int:
    in_path = Path(args.input).expanduser()
    if not in_path.is_file():
        print(f"[2] 输入清单不存在：{in_path}")
        return 2
    base = Path(args.base_dir).expanduser() if args.base_dir else in_path.parent
    try:
        items = validate_items(load_items(in_path), base)
    except (ValueError, OSError) as exc:
        print(f"[1] 冻结失败：{exc}")
        return 1

    dev = _load_dev(args)
    overlap = check_disjoint(patients(items), dev)
    if overlap:
        print(f"[1] 与开发集存在 {len(overlap)} 个患者交集（自己考自己）：{overlap[:5]}")
        return 1

    label_ratio = ratios(items)
    diff = ratio_deviation(label_ratio, dict(args.expect or {}))
    if args.expect and diff > args.tolerance:
        print(f"[1] 分层比例与期望偏差 {diff:.3f} > 容差 {args.tolerance}（切分可疑）")
        return 1

    manifest = {
        "_note": (
            "注册级独立验证集冻结清单（A.62 批 3）：患者级切分 + 逐条 sha256；"
            "复跑前先 verify，防换集刷分"
        ),
        "scope": SCOPE,
        "count": len(items),
        "patient_count": len(patients(items)),
        "stratified_ratio": label_ratio,
        "organ_ratio": ratios(items, "organ"),
        "label_ratio_deviation": round(diff, 4),
        "items": items,
    }
    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[0] 已冻结：{out}（{len(items)} 条 / {manifest['patient_count']} 位患者；"
        f"分层 {label_ratio}；与开发集交集 0）"
    )
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    path = Path(args.manifest).expanduser()
    if not path.is_file():
        print(f"[2] 清单不存在：{path}")
        return 2
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("items") or []
    if not items:
        print("[1] 清单里没有条目 —— 空清单不算通过")
        return 1
    if data.get("scope") != SCOPE:
        print(f"[1] scope 不是 {SCOPE}（实为 {data.get('scope')}）：不能当独立验证集跑")
        return 1

    base = Path(args.base_dir).expanduser() if args.base_dir else path.parent.parent
    bad_sha: list[str] = []
    missing_file: list[str] = []
    missing_patient: list[str] = []
    for it in items:
        rel = str(it.get("path") or "")
        pid = str(it.get("patient_id") or "").strip()
        if not pid:
            missing_patient.append(rel)
        f = base / rel
        if not f.is_file():
            # 清单可能相对影像根目录；找不到时再试清单自身目录
            alt = path.parent / rel
            if alt.is_file():
                f = alt
            else:
                missing_file.append(rel)
                continue
        if file_sha256(f) != it.get("sha256"):
            bad_sha.append(rel)

    dev = _load_dev(args)
    overlap = check_disjoint(patients(items), dev) if dev else []

    ok = not (bad_sha or missing_file or missing_patient or overlap)
    detail = {
        "count": len(items),
        "patient_count": len(patients(items)),
        "sha_mismatch": bad_sha[:5],
        "missing_file": missing_file[:5],
        "missing_patient_id": missing_patient[:5],
        "dev_overlap": overlap[:5],
    }
    if ok:
        print(f"[0] 校验通过：{path}（{len(items)} 条 / {detail['patient_count']} 位患者）")
    else:
        print(f"[1] 校验不通过：{json.dumps(detail, ensure_ascii=False)}")
    if args.report:
        rp = Path(args.report).expanduser()
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(
            json.dumps({"manifest": str(path), "ok": ok, **detail}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="独立验证集冻结/校验（注册级）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    fz = sub.add_parser("freeze", help="从清单 JSONL 生成冻结清单（含逐条 sha256）")
    fz.add_argument("--input", required=True, help="清单 JSONL（每行 {patient_id,path,label[,organ]}）")
    fz.add_argument("--out", required=True, help="输出冻结清单（建议 data/eval/t2a_independent_manifest.json）")
    fz.add_argument("--base-dir", default=None, help="影像根目录（默认取输入清单位置）")
    fz.add_argument("--dev-manifest", default=None, help="开发集/公开集清单（取其患者 ID 做交集断言）")
    fz.add_argument("--dev-patients", default=None, help="开发集患者名单（每行一个）")
    fz.add_argument(
        "--expect",
        default=None,
        help='期望分层比例 JSON（如 \'{"Malignant":0.35,"Benign":0.65}\'），用于确认切分没跑偏',
    )
    fz.add_argument("--tolerance", type=float, default=DEFAULT_RATIO_TOLERANCE)
    fz.set_defaults(func=_cmd_freeze)

    vf = sub.add_parser("verify", help="校验冻结清单（sha256 / 缺患者 ID / 与开发集交集）")
    vf.add_argument("--manifest", required=True)
    vf.add_argument("--base-dir", default=None)
    vf.add_argument("--dev-manifest", default=None)
    vf.add_argument("--dev-patients", default=None)
    vf.add_argument("--report", default=None, help="把结论写成 JSON（可选）")
    vf.set_defaults(func=_cmd_verify)

    args = ap.parse_args(argv)
    if getattr(args, "expect", None):
        try:
            args.expect = json.loads(args.expect)
        except json.JSONDecodeError as exc:
            print(f"[2] --expect 不是合法 JSON：{exc}")
            return 2
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
