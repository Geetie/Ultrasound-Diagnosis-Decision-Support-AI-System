import re
import yaml
import os
import structlog
from typing import Dict, List, Optional, Any
from pydantic import BaseModel
from app.config.settings import settings

logger = structlog.get_logger()

DESENSITIZATION_RULES_PATH = os.environ.get(
    "DESENSITIZATION_RULES_PATH",
    "desensitization_rules.yaml"
)

class DesensitizationRule(BaseModel):
    name: str
    pattern: str
    replacement: str
    description: Optional[str] = None
    enabled: bool = True
    priority: int = 10

class DesensitizationRules(BaseModel):
    rules: List[DesensitizationRule]

class DesensitizationEngine:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DesensitizationEngine, cls).__new__(cls)
            cls._instance._rules = []
            cls._instance._compiled_patterns = {}
        return cls._instance
    
    def load_rules(self, rules_path: str = None):
        if rules_path is None:
            rules_path = DESENSITIZATION_RULES_PATH
        
        if not os.path.exists(rules_path):
            self._rules = self._get_default_rules()
            self._save_rules(rules_path)
        else:
            with open(rules_path, 'r', encoding='utf-8') as f:
                rules_data = yaml.safe_load(f)
                self._rules = [DesensitizationRule(**rule) for rule in rules_data.get('rules', [])]
        
        self._compile_patterns()
    
    def _get_default_rules(self) -> List[DesensitizationRule]:
        return [
            DesensitizationRule(
                name="id_card",
                pattern=r'(?<![*\d])\d{17}[\dXx](?![*\d])',
                replacement='********************',
                description="身份证号脱敏",
                priority=1
            ),
            DesensitizationRule(
                name="phone",
                pattern=r'(?<![*\d])(1[3-9]\d)(\d{4})(\d{4})(?![*\d])',
                replacement='******\\3',
                description="手机号脱敏，保留后4位",
                priority=2
            ),
            DesensitizationRule(
                name="patient_name",
                pattern=r'((?:患者姓名：|患者姓名|患者：|患者:|患者姓名:|患者|病人)\s*)([\u4e00-\u9fa5]{2,4})',
                replacement='\\1PATIENT_\\2',
                description="患者姓名脱敏（处理带前缀的姓名）",
                priority=3
            ),
            DesensitizationRule(
                name="patient_name_with_title",
                pattern=r'([\u4e00-\u9fa5]{2,4})(女士|先生|同志|医生)',
                replacement='PATIENT_\\1\\2',
                description="患者姓名脱敏（处理带称谓的姓名）",
                priority=4
            ),
            DesensitizationRule(
                name="patient_name_simple",
                pattern=r'^([\u4e00-\u9fa5]{2,4})(?=，|,|$)',
                replacement='PATIENT_\\1',
                description="患者姓名脱敏（处理独立的2-4字姓名）",
                priority=5
            ),
            DesensitizationRule(
                name="birth_date",
                pattern=r'(\d{4})-(\d{2})-(\d{2})',
                replacement='\\1-**-**',
                description="出生日期脱敏，仅保留年份",
                priority=5
            ),
            DesensitizationRule(
                name="address",
                pattern=r'([\u4e00-\u9fa5]{2,8}?市)([\u4e00-\u9fa5][^，,]*)',
                replacement='\\1***',
                description="住址脱敏，仅保留到市级",
                priority=6
            ),
            DesensitizationRule(
                name="medical_record",
                pattern=r'(?<![A-Za-z\d])MR\d{8,12}(?![A-Za-z\d])',
                replacement='MR********',
                description="病历号脱敏",
                priority=7
            ),
            DesensitizationRule(
                name="email",
                pattern=r'[a-zA-Z0-9_.-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]+',
                replacement='EMAIL_***',
                description="邮箱地址脱敏",
                priority=8
            ),
            DesensitizationRule(
                name="study_instance_uid",
                pattern=r'^\d+\.\d+\.\d+\.\d+\.\d+\.\d+\.\d+(\.\d+)*$',
                replacement='STUDY_INSTANCE_UID',
                description="DICOM Study Instance UID脱敏",
                priority=9
            ),
            DesensitizationRule(
                name="ip_address",
                pattern=r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}',
                replacement='***.***.***.***',
                description="IP地址脱敏",
                priority=10
            ),
            DesensitizationRule(
                name="hospital_id",
                pattern=r'^HOSP_[A-Za-z0-9]{8,12}$',
                replacement='HOSPITAL_***',
                description="医院ID脱敏",
                priority=11,
                enabled=False
            )
        ]
    
    def _save_rules(self, rules_path: str):
        rules_data = {"rules": [rule.model_dump() for rule in self._rules]}
        directory = os.path.dirname(rules_path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory)
        with open(rules_path, 'w', encoding='utf-8') as f:
            yaml.dump(rules_data, f, default_flow_style=False, allow_unicode=True)
    
    def _compile_patterns(self):
        self._compiled_patterns = {}
        for rule in self._rules:
            if rule.enabled:
                try:
                    self._compiled_patterns[rule.name] = re.compile(rule.pattern)
                except re.error as e:
                    logger.warning(
                        "desensitization_rule_compile_failed",
                        rule_name=rule.name,
                        pattern=rule.pattern,
                        error=str(e)
                    )
    
    def desensitize(self, text: Optional[str]) -> Optional[str]:
        if text is None:
            return None
        if not text:
            return text

        result: str = text

        sorted_rules: List[DesensitizationRule] = sorted(
            [r for r in self._rules if r.enabled],
            key=lambda x: x.priority
        )

        for rule in sorted_rules:
            pattern: Optional[re.Pattern] = self._compiled_patterns.get(rule.name)
            if pattern:
                result = pattern.sub(rule.replacement, result)

        return result
    
    def desensitize_dict(self, data: Dict[str, Any]) -> Dict[str, Any]:
        result = {}
        for key, value in data.items():
            if isinstance(value, str):
                result[key] = self.desensitize(value)
            elif isinstance(value, dict):
                result[key] = self.desensitize_dict(value)
            elif isinstance(value, list):
                result[key] = [
                    self.desensitize(item) if isinstance(item, str) else
                    self.desensitize_dict(item) if isinstance(item, dict) else
                    item
                    for item in value
                ]
            else:
                result[key] = value
        return result
    
    def get_rules(self) -> List[DesensitizationRule]:
        return self._rules
    
    def add_rule(self, rule: DesensitizationRule):
        self._rules.append(rule)
        self._compile_patterns()
    
    def update_rule(self, rule_name: str, updated_rule: DesensitizationRule):
        for i, rule in enumerate(self._rules):
            if rule.name == rule_name:
                self._rules[i] = updated_rule
                break
        self._compile_patterns()
    
    def remove_rule(self, rule_name: str):
        self._rules = [r for r in self._rules if r.name != rule_name]
        self._compile_patterns()

def get_desensitization_engine() -> DesensitizationEngine:
    engine = DesensitizationEngine()
    engine.load_rules()
    return engine

desensitization_engine = get_desensitization_engine()