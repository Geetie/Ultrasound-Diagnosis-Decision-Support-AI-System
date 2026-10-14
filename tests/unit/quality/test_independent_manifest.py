"""独立验证集冻结/校验流程守卫（A.62 批 3）。

为什么测它：注册级「独立验证」最容易被做假的地方不是模型，而是**数据**——
换一批更好看的、拿有重叠的当独立集、缺患者 ID 却照跑。
这条测试把三件事钉死：缺患者 ID 报错、与开发集交集必须为空、sha256 冻结（改了能查出来）。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts/audit/p34_freeze_independent_manifest.py"


def _load():
    spec = importlib.util.spec_from_file_location("_p34_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


P34 = _load()


def _write_imgs(tmp: Path, n: int, prefix: str = "img") -> list[dict]:
    items = []
    for i in range(n):
        p = tmp / f"{prefix}{i}.bin"
        p.write_bytes(f"content-{prefix}-{i}".encode())
        items.append({"patient_id": f"PT{i:03d}", "path": p.name, "label": "Malignant" if i % 2 else "Benign"})
    return items


def _write_jsonl(p: Path, items: list[dict]) -> Path:
    p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in items), encoding="utf-8")
    return p


# ---------------------------------------------------------------- 纯函数

def test_sha256_稳定且对内容敏感(tmp_path):
    a = tmp_path / "a.bin"
    a.write_bytes(b"same")
    b = tmp_path / "b.bin"
    b.write_bytes(b"same")
    c = tmp_path / "c.bin"
    c.write_bytes(b"different")
    assert P34.file_sha256(a) == P34.file_sha256(b)
    assert P34.file_sha256(a) != P34.file_sha256(c)


def test_缺患者ID时拒绝_不许把不知道是谁的数据当独立集(tmp_path):
    items = _write_imgs(tmp_path, 2)
    items[1]["patient_id"] = ""
    with pytest.raises(ValueError, match="缺 patient_id"):
        P34.validate_items(items, tmp_path)


def test_缺标签或影像缺失都报错(tmp_path):
    items = _write_imgs(tmp_path, 1)
    items[0]["label"] = ""
    with pytest.raises(ValueError, match="缺 label"):
        P34.validate_items(items, tmp_path)

    items = _write_imgs(tmp_path, 1)
    items[0]["path"] = "not-there.bin"
    with pytest.raises(ValueError, match="影像不存在"):
        P34.validate_items(items, tmp_path)


def test_空清单不算独立集(tmp_path):
    with pytest.raises(ValueError, match="清单为空"):
        P34.validate_items([], tmp_path)


def test_比例与交集工具(tmp_path):
    items = [{"patient_id": "A", "label": "Malignant"}, {"patient_id": "B", "label": "Benign"}]
    assert P34.ratios(items) == {"Benign": 0.5, "Malignant": 0.5}
    assert P34.patients(items) == {"A", "B"}
    assert P34.check_disjoint({"A", "C"}, {"C", "D"}) == ["C"]
    assert P34.check_disjoint({"A"}, {"B"}) == []
    assert P34.ratio_deviation({"Malignant": 0.5}, {"Malignant": 0.35}) == pytest.approx(0.15)


def test_read_dev_patients_支持名单与清单两种格式(tmp_path):
    txt = tmp_path / "dev.txt"
    txt.write_text("PT001\nPT002\n\n", encoding="utf-8")
    assert P34.read_dev_patients(txt) == {"PT001", "PT002"}

    man = tmp_path / "dev.json"
    man.write_text(
        json.dumps({"items": [{"patient_id": "PT003"}, {"path": "x"}]}), encoding="utf-8"
    )
    assert P34.read_dev_patients(man) == {"PT003"}


# ---------------------------------------------------------------- CLI 端到端

def test_freeze_通过并落清单(tmp_path):
    items = _write_imgs(tmp_path, 4)
    inp = _write_jsonl(tmp_path / "labels.jsonl", items)
    out = tmp_path / "manifest.json"

    code = P34.main(["freeze", "--input", str(inp), "--out", str(out), "--base-dir", str(tmp_path)])
    assert code == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["scope"] == P34.SCOPE
    assert data["count"] == 4 and data["patient_count"] == 4
    assert all(it["sha256"] for it in data["items"])


def test_freeze_与开发集有交集就拒绝(tmp_path):
    items = _write_imgs(tmp_path, 3)
    inp = _write_jsonl(tmp_path / "labels.jsonl", items)
    dev = tmp_path / "dev.txt"
    dev.write_text("PT000\n", encoding="utf-8")  # 与独立集重合

    code = P34.main(
        [
            "freeze", "--input", str(inp), "--out", str(tmp_path / "m.json"),
            "--base-dir", str(tmp_path), "--dev-patients", str(dev),
        ]
    )
    assert code == 1, "与开发集患者重叠必须判红（自己考自己）"
    assert not (tmp_path / "m.json").exists()


def test_freeze_缺患者ID判红(tmp_path):
    items = _write_imgs(tmp_path, 2)
    items[0]["patient_id"] = ""
    inp = _write_jsonl(tmp_path / "labels.jsonl", items)
    code = P34.main(
        ["freeze", "--input", str(inp), "--out", str(tmp_path / "m.json"), "--base-dir", str(tmp_path)]
    )
    assert code == 1


def test_verify_通过_篡改内容则判红(tmp_path):
    items = _write_imgs(tmp_path, 3)
    inp = _write_jsonl(tmp_path / "labels.jsonl", items)
    out = tmp_path / "manifest.json"
    assert P34.main(["freeze", "--input", str(inp), "--out", str(out), "--base-dir", str(tmp_path)]) == 0

    # ① 原样校验 → 通过
    assert P34.main(["verify", "--manifest", str(out), "--base-dir", str(tmp_path)]) == 0

    # ② 换掉一个影像（换集刷分的典型形态）→ 必须判红
    (tmp_path / "img1.bin").write_bytes(b"tampered")
    assert P34.main(["verify", "--manifest", str(out), "--base-dir", str(tmp_path)]) == 1


def test_verify_清单不存在返回2(tmp_path):
    assert P34.main(["verify", "--manifest", str(tmp_path / "nope.json")]) == 2
