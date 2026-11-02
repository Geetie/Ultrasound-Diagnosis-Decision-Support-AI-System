"""A.129 / G37：处置建议规则可外部加载（YAML），非法文件整份拒绝并回退内置表。

缺口：规则写死在 Python 常量里，现场按本院流程改一条就要改代码发版；r14 rule-engine 仍 planned。
口径（本文件逐条钉住）：
1) 未配置路径 → 行为与内置表完全一致（不启用）；
2) 合法文件 → **整体替换**内置表（不是合并），并按 risk_levels / features_any 触发；
3) 非法文件（器官/紧急度/空 rules/坏 YAML）→ 整份拒绝、回退内置表、返回错误原因；
4) 文件不存在 → 回退并留原因。
"""

from __future__ import annotations

import pytest
import yaml

from app.config.settings import settings
from app.knowledge import clinical_action_rules as car
from app.knowledge.clinical_actions import actions_for

pytestmark = pytest.mark.track_rag


@pytest.fixture(autouse=True)
def _reset_settings(monkeypatch):
    monkeypatch.setattr(settings, "clinical_actions_rules_path", "")
    car._load_cached.cache_clear()
    yield
    car._load_cached.cache_clear()


def _write(tmp_path, payload) -> str:
    p = tmp_path / "rules.yaml"
    p.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return str(p)


def test_disabled_by_default_uses_builtin():
    assert car.load_rules() == ([], None)
    builtin = {a.action_type for a in actions_for("thyroid", [], "low")}
    assert "thyroid_function_test" in builtin


def test_valid_file_replaces_builtin(tmp_path, monkeypatch):
    path = _write(
        tmp_path,
        {
            "rules": [
                {
                    "organ": "thyroid",
                    "action_type": "local_mdt",
                    "urgency": "soon",
                    "risk_levels": ["high"],
                    "description": "本院多学科会诊",
                    "source_reference": "院内规程 2026",
                },
                {"organ": "thyroid", "action_type": "always_note", "description": "无条件提示"},
            ]
        },
    )
    monkeypatch.setattr(settings, "clinical_actions_rules_path", path)
    car._load_cached.cache_clear()

    high = actions_for("thyroid", [], "high")
    types = [a.action_type for a in high]
    assert types == ["local_mdt", "always_note"]  # 整体替换：内置表不再出现
    assert high[0].rule_id == "thyroid:local_mdt"
    assert high[0].source_reference == "院内规程 2026"

    low = [a.action_type for a in actions_for("thyroid", [], "low")]
    assert low == ["always_note"]  # risk_levels 不匹配 → 只剩无条件规则


def test_features_any_triggers(tmp_path, monkeypatch):
    path = _write(
        tmp_path,
        {"rules": [{"organ": "liver", "action_type": "mri", "features_any": ["nodule_gt_10mm"]}]},
    )
    monkeypatch.setattr(settings, "clinical_actions_rules_path", path)
    car._load_cached.cache_clear()
    assert [a.action_type for a in actions_for("liver", ["nodule_gt_10mm"], None)] == ["mri"]
    assert actions_for("liver", ["other"], None) == []


@pytest.mark.parametrize(
    "payload,needle",
    [
        ({"rules": [{"organ": "kidney", "action_type": "x"}]}, "不支持的组织"),
        ({"rules": [{"organ": "thyroid", "action_type": "x", "urgency": "whenever"}]}, "非法紧急度"),
        ({"rules": []}, "rules 为空"),
        ({"rules": [{"action_type": "x"}]}, "organ"),
    ],
)
def test_invalid_file_rejects_whole_table(tmp_path, monkeypatch, payload, needle):
    path = _write(tmp_path, payload)
    monkeypatch.setattr(settings, "clinical_actions_rules_path", path)
    car._load_cached.cache_clear()

    rules, err = car.load_rules()
    assert rules == [] and err, f"非法文件应整份拒绝（{needle}）"
    assert needle in err

    # 回退内置表：内置动作仍在（不是"没有建议"）
    assert "thyroid_function_test" in {a.action_type for a in actions_for("thyroid", [], "low")}


def test_missing_file_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "clinical_actions_rules_path", str(tmp_path / "nope.yaml"))
    car._load_cached.cache_clear()
    rules, err = car.load_rules()
    assert rules == [] and err.startswith("file_not_found:")
