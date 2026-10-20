"""A.70.5 守卫：历史脏记录清点必须**只读**、**用生产守卫**、**分母正确**。

三条纪律：
1. 只发 SELECT（清理历史数据要拍板，脚本不许偷偷改库）；
2. 判定用生产守卫 `app.inference.output_guard.detect_prompt_echo`，不另写一套规则；
3. 脏记录占比的分母是**真结论**（降级样本没有结论，不该进分母）——否则"降级多"会把脏率压低。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SRC = Path(__file__).resolve().parents[3] / "scripts" / "audit" / "p47_dirty_record_inventory.py"


def _load():
    spec = importlib.util.spec_from_file_location("_p47_under_test", _SRC)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_summarize_separates_degraded_dirty_clean():
    p47 = _load()
    items = [
        {"organ": "thyroid", "month": "2026-10", "reason": "placeholder_chunk_id", "degraded": False},
        {"organ": "thyroid", "month": "2026-10", "reason": "placeholder_patient_id", "degraded": False},
        {"organ": "cardiac", "month": "2026-10", "reason": None, "degraded": False},
        {"organ": "cardiac", "month": "2026-10", "reason": None, "degraded": True},
    ]
    out = p47.summarize(items)
    assert out["total"] == 4
    assert out["degraded"] == 1
    assert out["dirty"] == 2
    assert out["clean"] == 1
    # 分母 = 总数 - 降级 = 3
    assert out["dirty_share"] == round(2 / 3, 4)
    assert out["clean_share"] == round(1 / 3, 4)
    assert out["reasons"] == {"placeholder_chunk_id": 1, "placeholder_patient_id": 1}
    assert out["by_organ"]["thyroid"]["dirty_rate"] == 1.0


def test_summarize_all_degraded_has_no_share():
    """全是降级样本时占比为 None（不写 0，也不写 1）。"""
    p47 = _load()
    out = p47.summarize([{"organ": "x", "month": "2026-10", "reason": None, "degraded": True}])
    assert out["dirty"] == 0
    assert out["dirty_share"] is None
    assert out["clean_share"] is None


def test_degraded_breakdown_is_reported_separately():
    """降级要能按时间/器官看（42% 的请求没结论，比回显更影响可用性）。"""
    p47 = _load()
    items = [
        {"organ": "thyroid", "month": "2026-09", "reason": None, "degraded": True},
        {"organ": "thyroid", "month": "2026-09", "reason": None, "degraded": True},
        {"organ": "cardiac", "month": "2026-10", "reason": None, "degraded": True},
        {"organ": "thyroid", "month": "2026-10", "reason": "placeholder_chunk_id", "degraded": False},
    ]
    out = p47.summarize(items)
    assert out["degraded_by_month"] == {"2026-09": 2, "2026-10": 1}
    assert out["degraded_by_organ"] == {"thyroid": 2, "cardiac": 1}
    # 降级不进"按月份脏率"的分母（真结论才进）→ 2026-09 只有降级，故整月不出现
    assert "2026-09" not in out["by_month"]
    assert out["by_month"]["2026-10"]["total"] == 1


def test_script_is_readonly_and_reuses_production_guard():
    src = _SRC.read_text(encoding="utf-8")
    assert "from app.inference.output_guard import detect_prompt_echo" in src
    assert "from collect_eval_set import _conclusion_of" in src
    upper = src.upper()
    assert "UPDATE " not in upper and "DELETE FROM" not in upper, "清点脚本不许改库"
    assert "chunk_a1b2" not in src and "PATIENT_001" not in src


def test_fingerprint_does_not_leak_text():
    p47 = _load()
    text = "患者PATIENT_001 甲状腺结节"
    fp = p47.fingerprint(text)
    assert len(fp) == 16
    assert "PATIENT" not in fp and "甲状腺" not in fp
