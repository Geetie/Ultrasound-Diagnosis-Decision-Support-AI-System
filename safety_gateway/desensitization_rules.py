import os
import re

import structlog
import yaml

from safety_gateway.sg_config import sg_settings as settings
from safety_gateway.sg_rule_model import SGDesensitizationRule as DesensitizationRule

logger = structlog.get_logger()


MEDICAL_PERSON_NAME_SUFFIXES = [
    "医生", "主任", "教授", "护士", "技师", "院长",
    "患者", "先生", "女士", "同志", "家属",
]

MEDICAL_PERSON_PREFIX_EXCLUSIONS = "科部室组"


def get_default_rules() -> list[DesensitizationRule]:
    yaml_path = getattr(settings, 'desensitization_rules_safety_path', None)
    if yaml_path and os.path.exists(yaml_path):
        try:
            with open(yaml_path, encoding="utf-8") as f:
                rules_data = yaml.safe_load(f)
            rules = [DesensitizationRule(**r) for r in rules_data.get("rules", [])]
            if rules:
                logger.info("sg_desensitization_default_rules_from_yaml", path=yaml_path, count=len(rules))
                return rules
        except Exception as e:
            logger.error("sg_desensitization_default_yaml_load_failed", error=str(e), path=yaml_path)

    return _get_hardcoded_default_rules()


def _get_hardcoded_default_rules() -> list[DesensitizationRule]:
    return [
        DesensitizationRule(
            name="sg_id_card",
            pattern=r"[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]",
            replacement="[REDACTED:ID_CARD]",
            description="身份证号脱敏（异构差异：精确日期格式校验，主规则源使用宽松匹配）",
            priority=1,
        ),
        DesensitizationRule(
            name="sg_phone",
            pattern=r"(?<!\d)(1[3-9]\d{9})(?!\d)",
            replacement="[REDACTED:PHONE]",
            description="手机号脱敏（异构差异：整体匹配+号段校验，主规则源使用分组替换模式）",
            priority=2,
        ),
        DesensitizationRule(
            name="sg_patient_name",
            pattern=r"(?:患者[：:]\s*)[\u4e00-\u9fa5]{2,4}|[\u4e00-\u9fa5]{2,4}(?=女士|先生|同志|医生|主任|教授|护士|技师)",
            replacement="[REDACTED:NAME]",
            description="患者姓名脱敏（异构差异：基于前后缀定位，主规则源使用NER模型）",
            priority=3,
        ),
        DesensitizationRule(
            name="sg_patient_name_standalone",
            pattern=r"^(?:患者|病人|被检者)[：:]\s*[\u4e00-\u9fa5]{2,4}$",
            replacement="患者：[REDACTED:NAME]",
            description="独立患者姓名脱敏（需患者/病人/被检者前缀）",
            priority=4,
        ),
        DesensitizationRule(
            name="sg_birth_date",
            pattern=r"(?:出生(?:日期|年月日)?|生日|生辰)\s*[：:]?\s*\d{4}\s*[-/年]\s*\d{1,2}\s*[-/月]\s*\d{1,2}\s*日?",
            replacement="[REDACTED:BIRTH_DATE]",
            description="出生日期脱敏（异构差异：允许空格分隔，主规则源使用严格日期格式）",
            priority=5,
        ),
        DesensitizationRule(
            name="sg_address",
            pattern=r"(?:住址|地址|家庭住址|现住址)\s*[：:]?\s*[\u4e00-\u9fa5]{2,6}(?:省|市|区|县|镇)[\u4e00-\u9fa5]*\d*号?",
            replacement="[REDACTED:ADDRESS]",
            description="住址脱敏（异构差异：基于行政区划关键词，主规则源使用通用地址正则）",
            priority=6,
        ),
        DesensitizationRule(
            name="sg_medical_record_number",
            pattern=r"(?:病历号|病案号|MR号|住院号)\s*[：:]?\s*[A-Za-z]{0,3}[\d\-_]{4,20}",
            replacement="[REDACTED:MEDICAL_RECORD]",
            description="病历号脱敏（异构差异：允许字母前缀+数字，主规则源使用纯数字匹配）",
            priority=7,
        ),
        DesensitizationRule(
            name="sg_dicom_uid",
            pattern=r"(?:StudyInstanceUID|SeriesInstanceUID|SOPInstanceUID)\s*[：:]?\s*\d[\d.]{10,64}",
            replacement="[REDACTED:DICOM_UID]",
            description="DICOM StudyInstanceUID脱敏",
            priority=8,
        ),
        DesensitizationRule(
            name="sg_email",
            pattern=r"[\w\u4e00-\u9fa5][\w.\u4e00-\u9fa5%-]*@[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}(?:\.[a-zA-Z]{2,})*",
            replacement="[REDACTED:EMAIL]",
            description="邮箱脱敏（异构差异：支持中文用户名和多标签域名，主规则源使用标准RFC格式）",
            priority=4,
        ),
        DesensitizationRule(
            name="sg_ip_address",
            pattern=r"(?:IP地址|IP[:：]|服务器IP[:：])\s*\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}",
            replacement="[REDACTED:IP_ADDRESS]",
            description="IP地址脱敏（异构差异：语义前缀+显式段数校验，主规则源使用纯IP正则）",
            priority=9,
        ),
    ]


def get_default_person_name_set() -> set[str]:
    surnames = "张李王刘陈赵黄周吴徐孙马朱胡林郭何高罗"
    titles = ["医生", "主任", "教授", "副主任医师", "主治医师", "护士长", "院长", "科长"]
    return {f"{s}{t}" for s in surnames for t in titles}


def init_person_name_pattern() -> re.Pattern:
    suffix_pattern = "|".join(re.escape(s) for s in MEDICAL_PERSON_NAME_SUFFIXES)
    return re.compile(
        rf"(?<![{MEDICAL_PERSON_PREFIX_EXCLUSIONS}])([\u4e00-\u9fa5]{{1,4}})(?:{suffix_pattern})"
    )


def load_person_name_set_from_file() -> set[str] | None:
    names_path = getattr(settings, 'medical_person_names_path', None)
    if not names_path or not os.path.exists(names_path):
        return None
    try:
        with open(names_path, encoding="utf-8") as f:
            names_data = yaml.safe_load(f)
        person_name_set: set[str] = set()
        surnames = names_data.get("surnames", [])
        suffixes = names_data.get("suffixes", MEDICAL_PERSON_NAME_SUFFIXES)
        common_full_names = names_data.get("common_full_names", [])
        for surname in surnames:
            for suffix in suffixes:
                person_name_set.add(f"{surname}{suffix}")
        for full_name in common_full_names:
            person_name_set.add(full_name)
        logger.info(
            "sg_person_name_dict_loaded_from_file",
            path=names_path,
            combination_count=len(person_name_set),
        )
        return person_name_set
    except Exception as e:
        logger.error("sg_person_name_dict_load_failed", error=str(e))
        return None


def load_patch_rules(patch_path: str) -> list[DesensitizationRule]:
    try:
        with open(patch_path, encoding="utf-8") as f:
            patch_data = yaml.safe_load(f)

        patch_rules = []
        for rule_data in patch_data.get("rules", []):
            rule = DesensitizationRule(**rule_data, is_patch=True)
            patch_rules.append(rule)

        logger.info(
            "sg_desensitization_patch_rules_loaded",
            path=patch_path,
            patch_count=len(patch_rules),
        )
        return patch_rules
    except Exception as e:
        logger.error("sg_desensitization_patch_rules_load_failed", error=str(e), path=patch_path)
        return []


def load_rules_from_yaml(rules_path: str) -> list[DesensitizationRule] | None:
    try:
        with open(rules_path, encoding="utf-8") as f:
            rules_data = yaml.safe_load(f)
            loaded_rules = [
                DesensitizationRule(**rule) for rule in rules_data.get("rules", [])
            ]
        logger.info("sg_desensitization_rules_loaded", path=rules_path, count=len(loaded_rules))
        return loaded_rules
    except Exception as e:
        logger.error("sg_desensitization_rules_load_failed", error=str(e), path=rules_path)
        return None


def validate_rules(rules: list[DesensitizationRule]) -> list[str]:
    errors = []
    rule_names = set()

    for rule in rules:
        if not rule.name:
            errors.append("Rule has empty name")
            continue

        if rule.name in rule_names:
            errors.append(f"Duplicate rule name: {rule.name}")
        rule_names.add(rule.name)

        if not rule.pattern:
            errors.append(f"Rule '{rule.name}' has empty pattern")
            continue

        try:
            re.compile(rule.pattern)
        except re.error as e:
            errors.append(f"Rule '{rule.name}' has invalid regex pattern: {e!s}")

        if rule.priority < 0 or rule.priority > 100:
            errors.append(f"Rule '{rule.name}' has invalid priority: {rule.priority} (must be 0-100)")

    return errors


_SG_TO_MAIN_DOMAIN_MAP: dict[str, str] = {
    "sg_id_card": "id_card",
    "sg_phone": "phone",
    "sg_patient_name": "patient_name",
    "sg_patient_name_standalone": "patient_name",
    "sg_patient_name_simple": "patient_name",
    "sg_birth_date": "birth_date",
    "sg_address": "address",
    "sg_medical_record_number": "medical_record_number",
    "sg_dicom_uid": "dicom_uid",
    "sg_email": "email",
    "sg_ip_address": "ip_address",
}


def validate_heterogeneous_redundancy(
    sg_rules: list[DesensitizationRule],
    main_rules_path: str | None = None,
) -> list[dict[str, str]]:
    identical_rules: list[dict[str, str]] = []
    sg_patterns: dict[str, str] = {}
    for r in sg_rules:
        domain = _SG_TO_MAIN_DOMAIN_MAP.get(r.name, r.name)
        sg_patterns[domain] = r.pattern
    if main_rules_path and os.path.exists(main_rules_path):
        try:
            with open(main_rules_path, encoding="utf-8") as f:
                main_data = yaml.safe_load(f)
            main_rules = main_data.get("rules", [])
            for main_rule in main_rules:
                main_name = main_rule.get("name", "")
                main_pattern = main_rule.get("pattern", "")
                if main_name in sg_patterns and sg_patterns[main_name] == main_pattern:
                    identical_rules.append({
                        "sg_domain": main_name,
                        "main_rule": main_name,
                        "pattern": main_pattern,
                    })
        except Exception as e:
            logger.error("sg_desensitization_heterogeneous_validation_failed", error=str(e))
    return identical_rules
