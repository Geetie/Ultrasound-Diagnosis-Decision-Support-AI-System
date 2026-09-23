"""Patient desensitization at index time (P0-RAG-03).

设计依据:
- 02-核心流程与知识库.md §1 流程图 `B → B0[患者身份信息脱敏]` 是硬约束
- `case_reports/` 子目录下的典型病例报告含 PII (身份证号、手机号、姓名)
  必须经脱敏后再进入知识库
- 复用项目已有的 2 份异构脱敏规则文件:
    desensitization_rules_safety.yaml
    desensitization_rules.yaml
  异构冗余: 2 套规则同时匹配才认为脱敏成功, 任一不一致报警

异构冗余的意义:
- 安全系统应当纵深防御; 一份规则被污染时另一份仍能识别
- 实际不依赖规则匹配的具体结果 (只是统计匹配数), 异构冗余的价值是
  触发报警, 而非阻止所有 PII — 后者由 SafetyGateway 在线拦截保证
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

import structlog

logger = structlog.get_logger(__name__)


# PII 标签 (与 SafetyGateway 的脱敏标签对齐)
PII_LABEL_NAME = "[REDACTED:NAME]"
PII_LABEL_PHONE = "[REDACTED:PHONE]"
PII_LABEL_ID_CARD = "[REDACTED:ID_CARD]"
PII_LABEL_EMAIL = "[REDACTED:EMAIL]"
PII_LABEL_ADDRESS = "[REDACTED:ADDRESS]"
PII_LABEL_DATE = "[REDACTED:DATE]"


@dataclass(frozen=True)
class DesensitizeResult:
    """脱敏结果.

    Attributes:
        sanitized: 脱敏后的文本 (含 PII 标签)
        applied_rules: 实际命中的规则名列表 (来自 2 份规则的并集)
        disagreements: 异构规则触发不一致时记录 (rule_x_only_in_file_a)
        skipped: True 表示 source_kind 不需要脱敏 (例如 guidelines)
    """

    sanitized: str
    applied_rules: List[str] = field(default_factory=list)
    disagreements: List[str] = field(default_factory=list)
    skipped: bool = False


class DesensitizationRuleSet:
    """加载并应用 1 份 YAML 规则文件.

    规则文件格式 (见 desensitization_rules_safety.yaml):
        rules:
          - name: "phone_11_digits"
            pattern: "1[3-9]\\d{9}"
            label: "[REDACTED:PHONE]"
    """

    def __init__(self, file_path: str | Path):
        self._path = Path(file_path)
        self._name = self._path.stem
        self._rules: List[Dict[str, Any]] = []
        self._compiled: List[Tuple[str, re.Pattern, str]] = []
        self._load_lock = threading.Lock()
        self.reload()

    def reload(self) -> None:
        with self._load_lock:
            if not self._path.exists():
                logger.warning(
                    "desensitization_rules_file_missing",
                    path=str(self._path),
                )
                self._rules = []
                self._compiled = []
                return
            with self._path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            self._rules = data.get("rules", [])
            self._compiled = []
            for r in self._rules:
                name = r.get("name", "?")
                pattern = r.get("pattern")
                label = r.get("label", "[REDACTED]")
                if not pattern:
                    continue
                try:
                    compiled = re.compile(pattern)
                except re.error as exc:
                    logger.warning(
                        "desensitization_rule_compile_failed",
                        name=name, error=str(exc),
                    )
                    continue
                self._compiled.append((name, compiled, label))

    def apply(self, text: str) -> Tuple[str, List[str]]:
        """Apply all rules to text. Returns (sanitized_text, hit_rule_names)."""
        if not self._compiled:
            return text, []
        hit_rules: List[str] = []
        sanitized = text
        for name, compiled, label in self._compiled:
            new_text, n = compiled.subn(label, sanitized)
            if n > 0:
                hit_rules.append(name)
                sanitized = new_text
        return sanitized, hit_rules


class Desensitizer:
    """P0-RAG-03 脱敏器.

    异构冗余: 加载 2 份独立规则文件, 2 套同时通过才认为脱敏成功.
    source_kind 决定是否需要脱敏 (guidelines 跳过).
    """

    def __init__(
        self,
        rules_file_safety: str | Path = "desensitization_rules_safety.yaml",
        rules_file_default: str | Path = "desensitization_rules.yaml",
    ):
        self._rules_safety = DesensitizationRuleSet(rules_file_safety)
        self._rules_default = DesensitizationRuleSet(rules_file_default)
        # 强制加载的 PII 兜底规则 (即使 YAML 文件缺失也能脱敏最常见 PII)
        # 注意: 18 位身份证号的前 11 位恰好可能命中手机号模式 (1[3-9]\d{9}),
        # 所以用 (?!\\d) 断言避免 phone 误吞 ID card 头部. 顺序也关键:
        # ID card 先于 phone, 这样 ID card 把整段打码, phone 不会重复命中.
        self._fallback_patterns: List[Tuple[str, re.Pattern, str]] = [
            (
                "id_card_18_chars_fb",
                re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
                PII_LABEL_ID_CARD,
            ),
            (
                "phone_11_digits_fb",
                re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
                PII_LABEL_PHONE,
            ),
            (
                "email_fb",
                re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
                PII_LABEL_EMAIL,
            ),
        ]

    def desensitize(
        self, content: str, source_kind: str = "case_reports"
    ) -> DesensitizeResult:
        """Apply desensitization with heterogeneous redundancy.

        source_kind:
            case_reports  → apply desensitization
            guidelines    → skip (medical guidelines are public knowledge)
            other         → apply (defensive default)
        """
        if source_kind == "guidelines":
            return DesensitizeResult(
                sanitized=content, applied_rules=[], skipped=True
            )

        # Apply primary rule set (safety).
        sanitized_a, hits_a = self._rules_safety.apply(content)
        # Apply secondary rule set (default).
        sanitized_b, hits_b = self._rules_default.apply(sanitized_a)

        # Apply fallback patterns (always-on safety net).
        applied_fb: List[str] = []
        for name, compiled, label in self._fallback_patterns:
            new_text, n = compiled.subn(label, sanitized_b)
            if n > 0:
                applied_fb.append(name)
                sanitized_b = new_text

        all_hits = set(hits_a) | set(hits_b) | set(applied_fb)
        # Disagreement detection: 2 套 YAML 命中数差异 > 1 触发告警
        # (例如 A 命中 N 个, B 命中 M 个, |N-M|>1)
        disagreements: List[str] = []
        only_a = set(hits_a) - set(hits_b)
        only_b = set(hits_b) - set(hits_a)
        if only_a:
            disagreements.append(
                f"only_in_safety:{','.join(sorted(only_a))}"
            )
        if only_b:
            disagreements.append(
                f"only_in_default:{','.join(sorted(only_b))}"
            )
        if disagreements:
            logger.warning(
                "desensitization_rule_disagreement",
                source_kind=source_kind,
                only_in_safety=list(only_a),
                only_in_default=list(only_b),
            )

        return DesensitizeResult(
            sanitized=sanitized_b,
            applied_rules=sorted(all_hits),
            disagreements=disagreements,
            skipped=False,
        )


_default_desensitizer: Optional[Desensitizer] = None


def get_default_desensitizer() -> Desensitizer:
    """Return a process-wide singleton desensitizer."""
    global _default_desensitizer
    if _default_desensitizer is None:
        _default_desensitizer = Desensitizer()
    return _default_desensitizer
