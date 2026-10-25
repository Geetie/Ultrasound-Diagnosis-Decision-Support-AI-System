import os
import re
import threading
from typing import Any

import structlog

from safety_gateway.desensitization_rules import (
    DesensitizationRule,
    get_default_person_name_set,
    get_default_rules,
    init_person_name_pattern,
    load_patch_rules,
    load_person_name_set_from_file,
    load_rules_from_yaml,
    validate_heterogeneous_redundancy,
    validate_rules,
)
from safety_gateway.sg_config import sg_settings as settings

logger = structlog.get_logger()


class SafetyGatewayDesensitizationEngine:
    """SafetyGateway脱敏引擎

    线程安全说明：
    - _rules_lock (threading.Lock) 保护 _rules 和 _compiled_patterns 的读写
    - desensitize()/check_sensitive_data() 使用快照模式：在锁内复制引用，锁外使用快照
    - reload_rules() 在锁内原子替换 _rules 和 _compiled_patterns
    - asyncio.to_thread(reload_rules) 在独立线程中执行，与主线程通过 _rules_lock 协调

    拆分说明（FIX-P2-01）：
    - 规则定义/加载/验证 → desensitization_rules.py
    - 本模块保留引擎核心：脱敏执行、热更新编排
    """

    def __init__(self, rules_path: str | None = None, patch_path: str | None = None):
        self._rules: list[DesensitizationRule] = []
        self._compiled_patterns: dict[str, re.Pattern] = {}
        self._person_name_pattern: re.Pattern | None = None
        self._person_name_set: set[str] = set()
        self._rules_lock = threading.Lock()
        self._patch_loaded: bool = False
        self._reload_lock = threading.Lock()

        if rules_path and os.path.exists(rules_path):
            self.load_rules(rules_path)
        else:
            self._rules = get_default_rules()
            self._compile_patterns()

        if patch_path:
            self._apply_patch_rules(patch_path)
        elif hasattr(settings, 'desensitization_rules_safety_patch_path'):
            patch = settings.desensitization_rules_safety_patch_path
            if patch and os.path.exists(patch):
                self._apply_patch_rules(patch)

        self._init_person_name_matcher()

    def _init_person_name_matcher(self) -> None:
        self._person_name_pattern = init_person_name_pattern()

        loaded_set = load_person_name_set_from_file()
        if loaded_set is not None:
            self._person_name_set = loaded_set
        else:
            self._person_name_set = get_default_person_name_set()

    def _apply_patch_rules(self, patch_path: str) -> None:
        patch_rules = load_patch_rules(patch_path)
        if not patch_rules:
            self._patch_loaded = False
            return

        existing_names = {r.name for r in self._rules}
        for rule in patch_rules:
            if rule.name not in existing_names:
                self._rules.append(rule)

        self._compile_patterns()
        self._patch_loaded = True

    def load_rules(self, rules_path: str) -> None:
        loaded_rules = load_rules_from_yaml(rules_path)
        if loaded_rules is not None:
            self._rules = loaded_rules
            self._compile_patterns()
        else:
            self._rules = get_default_rules()
            self._compile_patterns()

    def _compile_patterns(self) -> None:
        self._compiled_patterns = {}
        for rule in self._rules:
            if rule.enabled:
                try:
                    self._compiled_patterns[rule.name] = re.compile(rule.pattern)
                except re.error as e:
                    logger.warning(
                        "sg_desensitization_rule_compile_failed",
                        rule_name=rule.name,
                        pattern=rule.pattern,
                        error=str(e),
                    )

    def check_person_name(self, text: str) -> list[str]:
        detected = []
        if self._person_name_pattern:
            for full_match in self._person_name_pattern.finditer(text):
                matched_text = full_match.group(0)
                for name_combo in self._person_name_set:
                    if matched_text == name_combo:
                        detected.append("sg_person_name_dict")
                        break
        return detected

    def desensitize(self, text: str | None) -> str | None:
        if text is None:
            return None
        if not text:
            return text

        result: str = text

        with self._rules_lock:
            sorted_rules: list[DesensitizationRule] = sorted(
                [r for r in self._rules if r.enabled],
                key=lambda x: x.priority,
            )
            patterns_snapshot = dict(self._compiled_patterns)
            person_name_pattern = self._person_name_pattern

        for rule in sorted_rules:
            pattern: re.Pattern | None = patterns_snapshot.get(rule.name)
            if pattern:
                result = pattern.sub(rule.replacement, result)

        if person_name_pattern:
            result = self._desensitize_person_names(result, person_name_pattern)

        return result

    def _desensitize_person_names(self, text: str, pattern: re.Pattern) -> str:
        person_name_set = self._person_name_set
        matches = list(pattern.finditer(text))
        if not matches:
            return text
        result_parts = []
        last_end = 0
        for match in matches:
            matched_text = match.group(0)
            name_part = match.group(1) if match.lastindex and match.lastindex >= 1 else matched_text
            result_parts.append(text[last_end:match.start()])
            is_in_dict = name_part in person_name_set or matched_text in person_name_set
            if is_in_dict:
                result_parts.append("[REDACTED:NAME]")
            else:
                result_parts.append(matched_text)
            last_end = match.end()
        result_parts.append(text[last_end:])
        return "".join(result_parts)

    MAX_RECURSION_DEPTH = 20

    def desensitize_dict(self, data: dict[str, Any], depth: int = 0) -> dict[str, Any]:
        if depth >= self.MAX_RECURSION_DEPTH:
            logger.error("sg_desensitize_dict_depth_exceeded", depth=depth)
            raise ValueError(f"Desensitization recursion depth exceeded (max={self.MAX_RECURSION_DEPTH}). Data at depth {depth} was not desensitized.")
        result = {}
        for key, value in data.items():
            if isinstance(value, str):
                result[key] = self.desensitize(value)
            elif isinstance(value, dict):
                result[key] = self.desensitize_dict(value, depth=depth + 1)
            elif isinstance(value, list):
                result[key] = self._desensitize_list(value, depth=depth + 1)
            elif isinstance(value, tuple):
                result[key] = tuple(
                    self.desensitize(item) if isinstance(item, str)
                    else self.desensitize_dict(item, depth=depth + 1) if isinstance(item, dict)
                    else self._desensitize_list(item, depth=depth + 1) if isinstance(item, list)
                    else item
                    for item in value
                )
            elif isinstance(value, set):
                result[key] = {
                    self.desensitize(item) if isinstance(item, str)
                    else item
                    for item in value
                }
            else:
                result[key] = value
        return result

    def _desensitize_list(self, items: list[Any], depth: int = 0) -> list[Any]:
        if depth >= self.MAX_RECURSION_DEPTH:
            logger.error("sg_desensitize_list_depth_exceeded", depth=depth)
            raise ValueError(f"Desensitization recursion depth exceeded (max={self.MAX_RECURSION_DEPTH}). Data at depth {depth} was not desensitized.")
        processed = []
        for item in items:
            if isinstance(item, str):
                processed.append(self.desensitize(item))
            elif isinstance(item, dict):
                processed.append(self.desensitize_dict(item, depth=depth + 1))
            elif isinstance(item, list):
                processed.append(self._desensitize_list(item, depth=depth + 1))
            else:
                processed.append(item)
        return processed

    def check_sensitive_data(self, text: str) -> list[str]:
        detected_types = []

        with self._rules_lock:
            sorted_rules: list[DesensitizationRule] = sorted(
                [r for r in self._rules if r.enabled],
                key=lambda x: x.priority,
            )
            patterns_snapshot = dict(self._compiled_patterns)

        for rule in sorted_rules:
            pattern: re.Pattern | None = patterns_snapshot.get(rule.name)
            if pattern and pattern.search(text):
                detected_types.append(rule.name)

        person_name_hits = self.check_person_name(text)
        detected_types.extend(person_name_hits)

        return list(set(detected_types))

    def explain_sensitive_data(self, text: str) -> list[dict[str, Any]]:
        """**与 `check_sensitive_data` 同一判据**的定位版：给出规则名 + 偏移 + 长度。

        ## 为什么需要它（2026-10-25 真机排查耗时约 40 分钟）

        ``check_sensitive_data`` 只回规则名（如 ``sg_patient_name``），于是 SG
        日志里只有"检测到 sg_patient_name" —— **不知道是哪一段文本命中的**。
        真机现象是：同一段结论**重放**判 pass、走完整链路判 fail，两端都看不到
        命中位置，只能盲猜（我先猜了"旧进程旧规则""载荷形状""并发"，全错）。

        ## 为什么不直接记原文

        **PHI 不得进日志**（AGENTS.md 硬规则 10）。所以只给
        ``start`` / ``end`` / ``length``：配合调用方的"字段路径 + 该字段长度"
        就能在库里按偏移取那几字复核，而不落任何患者信息到日志。

        Returns:
            ``[{"rule": str, "start": int, "end": int, "length": int}, ...]``；
            未命中返回空列表。person-name 词典命中记 ``sg_person_name_dict``。
        """
        out: list[dict[str, Any]] = []

        with self._rules_lock:
            sorted_rules: list[DesensitizationRule] = sorted(
                [r for r in self._rules if r.enabled],
                key=lambda x: x.priority,
            )
            patterns_snapshot = dict(self._compiled_patterns)

        for rule in sorted_rules:
            pattern: re.Pattern | None = patterns_snapshot.get(rule.name)
            if not pattern:
                continue
            m = pattern.search(text)
            if m:
                out.append({
                    "rule": rule.name,
                    "start": int(m.start()),
                    "end": int(m.end()),
                    "length": int(m.end() - m.start()),
                })

        if self._person_name_pattern:
            for full_match in self._person_name_pattern.finditer(text):
                if full_match.group(0) in self._person_name_set:
                    out.append({
                        "rule": "sg_person_name_dict",
                        "start": int(full_match.start()),
                        "end": int(full_match.end()),
                        "length": int(full_match.end() - full_match.start()),
                    })
                    break

        return out

    def get_rules(self) -> list[DesensitizationRule]:
        return self._rules

    def _validate_rules(self, rules: list[DesensitizationRule]) -> list[str]:
        return validate_rules(rules)

    def _get_default_rules(self) -> list[DesensitizationRule]:
        return get_default_rules()

    def _get_default_person_name_set(self) -> set[str]:
        return get_default_person_name_set()

    def validate_heterogeneous_redundancy(self, main_rules_path: str | None = None) -> list[dict[str, str]]:
        sg_rules = self._rules if self._rules else get_default_rules()
        return validate_heterogeneous_redundancy(sg_rules, main_rules_path)

    def reload_rules(self) -> bool:
        if not self._reload_lock.acquire(blocking=False):
            logger.warning("sg_desensitization_reload_already_in_progress")
            return False
        try:
            return self._do_reload_rules()
        finally:
            self._reload_lock.release()

    def _do_reload_rules(self) -> bool:
        rules_path = settings.desensitization_rules_safety_path
        patch_path = settings.desensitization_rules_safety_patch_path

        with self._rules_lock:
            old_rules = list(self._rules)
            old_patterns = dict(self._compiled_patterns)

        try:
            new_rules = []
            patch_rules_to_add = []

            if rules_path and os.path.exists(rules_path):
                loaded_rules = load_rules_from_yaml(rules_path)
                if loaded_rules is None:
                    return False
                validation_errors = validate_rules(loaded_rules)
                if validation_errors:
                    logger.error(
                        "sg_desensitization_rules_validation_failed",
                        errors=validation_errors,
                    )
                    return False
                new_rules.extend(loaded_rules)
            else:
                new_rules = get_default_rules()

            existing_patch_rules = [r for r in old_rules if getattr(r, 'is_patch', False)]

            if patch_path and os.path.exists(patch_path):
                patch_rules = load_patch_rules(patch_path)
                if patch_rules:
                    validation_errors = validate_rules(patch_rules)
                    if validation_errors:
                        logger.warning(
                            "sg_desensitization_patch_rules_validation_failed_keeping_existing",
                            errors=validation_errors,
                        )
                        patch_rules_to_add = existing_patch_rules
                    else:
                        patch_rules_to_add = patch_rules
                else:
                    patch_rules_to_add = existing_patch_rules
            else:
                patch_rules_to_add = existing_patch_rules

            existing_names = {r.name for r in new_rules}
            for rule in patch_rules_to_add:
                if rule.name not in existing_names:
                    new_rules.append(rule)

            compiled_patterns = {}
            for rule in new_rules:
                if rule.enabled:
                    try:
                        compiled_patterns[rule.name] = re.compile(rule.pattern)
                    except re.error as e:
                        logger.error(
                            "sg_desensitization_rule_compile_failed_during_reload",
                            rule_name=rule.name,
                            pattern=rule.pattern,
                            error=str(e),
                        )
                        with self._rules_lock:
                            self._rules = old_rules
                            self._compiled_patterns = old_patterns
                        return False

            with self._rules_lock:
                self._rules = new_rules
                self._compiled_patterns = compiled_patterns
            logger.info(
                "sg_desensitization_rules_reloaded",
                rule_count=len(new_rules),
                pattern_count=len(compiled_patterns),
                patch_rule_count=len([r for r in new_rules if getattr(r, 'is_patch', False)]),
            )
            return True

        except Exception as e:
            logger.error("sg_desensitization_rules_reload_failed", error=str(e))
            with self._rules_lock:
                self._rules = old_rules
                self._compiled_patterns = old_patterns
            return False


from safety_gateway.registry import sg_registry


def get_safety_gateway_desensitization_engine() -> SafetyGatewayDesensitizationEngine:
    if not sg_registry.is_registered("desensitization_engine"):
        rules_path = settings.desensitization_rules_safety_path
        patch_path = settings.desensitization_rules_safety_patch_path
        sg_registry.register(
            "desensitization_engine",
            lambda: SafetyGatewayDesensitizationEngine(rules_path=rules_path, patch_path=patch_path),
        )
    return sg_registry.get("desensitization_engine")


def _reset_desensitization_engine() -> None:
    sg_registry.reset("desensitization_engine")


sg_desensitization_engine = get_safety_gateway_desensitization_engine()
