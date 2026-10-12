"""A.52 版本溯源守卫：报告/判读结果的「模型版本」必须是**可核验的真值**。

背景：此前 `model_version` 全线写 Phase 0A 占位常量 `phase0a_mock_model_version`
（= `"thyroid_v1.0.0"`，凭空写的甲状腺版本号）——报告里「知识库版本」已修真为
`kb_v1.3.0`，而「模型版本」仍是占位，等于把编造值当溯源信息交付。

定案（按**溯源可核验**原则）：
- `model_version` = **诊断引擎版本**（`settings.diagnosis_engine_version`，如 `diag_v1.0.0`）
  —— 参与「模型↔知识库」兼容矩阵的地方只认这个（矩阵要求可解析 `vN.M`）；
- `llm_model` = **实际产出结论的生成模型标识**（取 vLLM 配置的模型名 + revision）
  —— 答"哪个模型"，可对线上核对；
- 两者都不得回落到 Phase 0A 占位常量。
"""
from __future__ import annotations

import pathlib
import re

from app.config.settings import settings

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

# 医生可见面：这些文件里**不允许**再出现 Phase 0A 模型版本占位
NO_PLACEHOLDER_FILES = [
    "app/api/v1/diagnosis.py",
    "app/services/report_service.py",
    "app/services/diagnosis_pipeline.py",
]


def test_resolved_model_version_is_engine_version_not_mock():
    assert settings.resolved_model_version == settings.diagnosis_engine_version
    assert settings.resolved_model_version != settings.phase0a_mock_model_version
    # 兼容矩阵要求可解析出 vN.M（否则会静默判"不兼容"）
    from app.knowledge.version import parse_model_version

    assert parse_model_version(settings.resolved_model_version) is not None


def test_resolved_llm_model_follows_vllm_config():
    base = settings.llm_model_version or settings.vllm_default_model or "unknown"
    expected = (
        f"{base}@{settings.llm_model_revision}" if settings.llm_model_revision else base
    )
    assert settings.resolved_llm_model == expected


def test_engine_and_kb_versions_are_compatible():
    """引擎版本与生产知识库版本必须在兼容矩阵内（启动期同款判据）。"""
    from app.config.validator import validate_version_compatibility

    assert validate_version_compatibility() == []


def test_no_phase0a_model_placeholder_in_doctor_facing_paths():
    offenders = []
    for rel in NO_PLACEHOLDER_FILES:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if "phase0a_mock_model_version" in line and not line.strip().startswith("#"):
                offenders.append(f"{rel}:{i}: {line.strip()[:100]}")
    assert offenders == [], "医生可见面出现 Phase 0A 模型版本占位：\n" + "\n".join(offenders)


def test_report_payload_carries_real_versions():
    """报告构建载荷里的版本字段取自 settings 真值，且含生成模型标识。"""
    src = (REPO_ROOT / "app/services/report_service.py").read_text(encoding="utf-8")
    assert '"model_version": settings.resolved_model_version' in src
    assert '"llm_model": settings.resolved_llm_model' in src
    # 报告模板必须把生成模型写出来（医生/审计要能读到"哪个模型"）
    assert re.search(r"生成模型：\{\{llm_model\}\}", src), "报告模板缺「生成模型」行"
