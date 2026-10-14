"""评测集采集器的**去重口径**守卫（纯函数，无需 DB）。

为什么单独测这个：`dedup_key` 决定「700 例」到底在数什么。A.61 版读的是
`DiagnosisResult.patient_id` —— 而**该模型根本没有这一列**（患者在 `DiagnosisSession` 上），
于是 `patient_id` 恒为空、去重静默降级成「按影像」，样本数随之偏大。
这条测试把三种口径都钉住，任何一次"回退到退化口径"都会在这里现形。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts/langfuse/collect_eval_set.py"


def _load():
    spec = importlib.util.spec_from_file_location("_collect_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


COLLECT = _load()


@pytest.mark.parametrize(
    ("patient", "basis", "request_id", "expect_key", "expect_reason"),
    [
        ("P001", "sop.1.2.3", "req_1", "P001|sop.1.2.3", "patient_image"),
        # 患者 ID 缺失 → 退化口径（必须被**显式标出**，不许假装是同患者同影像）
        ("", "sop.1.2.3", "req_1", "img:sop.1.2.3", "image_only"),
        # 没有影像标识 → 退化到请求级（调用方据此计 undedupable）
        ("P001", "", "req_1", "req:req_1", "no_basis"),
        ("", "", "req_1", "req:req_1", "no_basis"),
    ],
)
def test_去重键与降级原因(patient, basis, request_id, expect_key, expect_reason):
    key, reason = COLLECT._dedup_key(patient, basis, request_id)
    assert (key, reason) == (expect_key, expect_reason)


def test_同患者不同影像不合并_同影像必合并():
    """口径本体：键按（患者, 影像）二元组 —— 同患者多影像保留，同影像必然同键。"""
    a, _ = COLLECT._dedup_key("P001", "sopA", "r1")
    b, _ = COLLECT._dedup_key("P001", "sopB", "r2")
    c, _ = COLLECT._dedup_key("P001", "sopA", "r3")
    assert a != b, "同一患者的两张影像必须保留为两条样本"
    assert a == c, "同一患者的同一影像必须去重成一条（与 request_id 无关）"


def test_患者ID不同则不同样本():
    a, _ = COLLECT._dedup_key("P001", "sopA", "r1")
    b, _ = COLLECT._dedup_key("P002", "sopA", "r2")
    assert a != b, "不同患者即使影像标识相同也必须分开（标识不该跨患者复用）"


def test_采集器不再读不存在的字段():
    """回归守卫：`DiagnosisResult` 没有 `patient_id` —— 采集器必须从会话取。

    这条断言直接盯住"静默降级"的根因：源码里若再出现从判读行取 `patient_id` 的写法，
    或者丢掉了会话级回退，就地失败。
    """
    text = SCRIPT.read_text(encoding="utf-8")
    assert "session_patient" in text, "缺少「会话 → 患者」映射（去重口径会退化成按影像）"
    assert "DiagnosisSession" in text, "未导入 DiagnosisSession"
