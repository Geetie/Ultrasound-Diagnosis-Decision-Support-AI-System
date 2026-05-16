"""
融合层脱敏模块 - 三层脱敏的第二层
提供LLM输出后的额外脱敏校验，防止推理过程中意外生成的身份信息

设计决策：融合层复用标准化层引擎是有意的"纵深防御"策略。
- 第一遍：标准化层规则捕获已知模式（身份证、手机号等）
- 第二遍：融合层特有模式捕获 LLM 生成场景（模拟患者、上下文泄露等）
两层使用不同规则集，复用引擎框架而非规则集，确保异构冗余。
"""
import re
import structlog
from typing import Dict, List, Optional, Any
from pydantic import BaseModel

from app.security.desensitization import desensitization_engine

logger = structlog.get_logger()


class FusionDesensitizationResult(BaseModel):
    """融合层脱敏结果"""
    desensitized_text: Optional[str]
    sensitive_data_detected: List[str]
    modified: bool


class FusionDesensitizationEngine:
    """融合层脱敏引擎 - 第二道防线"""
    
    def __init__(self):
        # 融合层特有的额外敏感模式（除了标准化层已有的）
        self._additional_sensitive_patterns = {
            "llm_generated_patient": r'(?:生成的患者|模拟患者|示例患者|测试患者)\s*[:：]\s*([\u4e00-\u9fa5]{2,4})',
            "patient_context_leak": r'(?:患者的|病人的)\s*(?:姓名|身份证|电话|手机号|住址|地址)[是为：:]\s*([^\n，,。.]{2,20})',
            "hidden_identifier": r'(?:ID|id|编号|病案号|病历号)\s*[:：]\s*([A-Za-z0-9\-_]{6,20})',
            "llm_generated_id_card": r'(?<!\d)([1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx])(?!\d)',
            # 融合层电话脱敏使用全掩码(******)，比标准化层的部分掩码(****5678)更激进。
            # 这是有意设计：融合层作为安全兜底，应采用更保守的策略，
            # 确保标准化层遗漏的电话号码被完全遮蔽。
            "llm_generated_phone": r'(?<!\d)(1[3-9]\d{9})(?!\d)',
        }
        self._replacement_rules = {
            "llm_generated_patient": r'PATIENT_***',
            "patient_context_leak": r'***',
            "hidden_identifier": r'ID_***',
            "llm_generated_id_card": r'********************',
            "llm_generated_phone": r'******',
        }
        self._compiled_additional_patterns = {
            name: re.compile(pattern) 
            for name, pattern in self._additional_sensitive_patterns.items()
        }
    
    def check_additional_sensitive(self, text: str) -> List[str]:
        """检查融合层特有的敏感数据模式"""
        detected = []
        for name, pattern in self._compiled_additional_patterns.items():
            if pattern.search(text):
                detected.append(name)
        return detected
    
    def desensitize_llm_output(self, text: Optional[str]) -> FusionDesensitizationResult:
        """
        对LLM输出进行脱敏 - 第二层防线
        
        首先使用标准化层的脱敏，然后额外检查融合层特有的模式
        """
        if text is None:
            return FusionDesensitizationResult(
                desensitized_text=None,
                sensitive_data_detected=[],
                modified=False
            )
        
        original_text = text
        
        # 第一步：应用标准化层脱敏
        desensitized = desensitization_engine.desensitize(original_text)
        
        # 第二步：额外检查融合层特有模式
        additional_detected = self.check_additional_sensitive(desensitized)
        
        # 第三步：对检测到的额外敏感模式进行处理
        final_desensitized = desensitized
        fusion_modified = False
        for name, pattern in self._compiled_additional_patterns.items():
            if name in additional_detected:
                replacement = self._replacement_rules.get(name, '***')
                new_text = pattern.sub(replacement, final_desensitized)
                if new_text != final_desensitized:
                    fusion_modified = True
                    final_desensitized = new_text

        modified = original_text != final_desensitized

        all_detected = []
        if modified and desensitized != original_text:
            all_detected.append("standard_desensitization_applied")
        if fusion_modified:
            all_detected.append("fusion_desensitization_applied")
        all_detected.extend(additional_detected)
        
        if modified:
            logger.info(
                "fusion_desensitization_applied",
                detected_types=all_detected,
                modified_chars=len(original_text) - len(final_desensitized)
            )
        
        return FusionDesensitizationResult(
            desensitized_text=final_desensitized,
            sensitive_data_detected=all_detected,
            modified=modified
        )
    
    MAX_RECURSION_DEPTH = 20

    def _desensitize_list(self, items: list, _depth: int = 0) -> list:
        if _depth >= self.MAX_RECURSION_DEPTH:
            raise ValueError(f"Desensitization recursion depth exceeded (max={self.MAX_RECURSION_DEPTH}). Data at depth {_depth} was not desensitized.")
        return [
            self.desensitize_llm_output(item).desensitized_text if isinstance(item, str) else
            self.desensitize_dict(item, _depth + 1) if isinstance(item, dict) else
            self._desensitize_list(item, _depth + 1) if isinstance(item, list) else
            item
            for item in items
        ]

    def desensitize_dict(self, data: Dict[str, Any], _depth: int = 0) -> Dict[str, Any]:
        if _depth >= self.MAX_RECURSION_DEPTH:
            logger.error("fusion_desensitize_dict_recursion_depth_exceeded", depth=_depth)
            raise ValueError(f"Desensitization recursion depth exceeded (max={self.MAX_RECURSION_DEPTH}). Data at depth {_depth} was not desensitized.")
        result = {}
        
        for key, value in data.items():
            if isinstance(value, str):
                desensitize_result = self.desensitize_llm_output(value)
                result[key] = desensitize_result.desensitized_text
            elif isinstance(value, dict):
                result[key] = self.desensitize_dict(value, _depth + 1)
            elif isinstance(value, list):
                result[key] = self._desensitize_list(value, _depth + 1)
            elif isinstance(value, tuple):
                result[key] = tuple(
                    self.desensitize_llm_output(item).desensitized_text
                    if isinstance(item, str)
                    else self.desensitize_dict(item, _depth + 1) if isinstance(item, dict)
                    else self._desensitize_list(item, _depth + 1) if isinstance(item, list)
                    else item
                    for item in value
                )
            elif isinstance(value, set):
                result[key] = {
                    self.desensitize_llm_output(item).desensitized_text
                    if isinstance(item, str)
                    else item
                    for item in value
                }
            else:
                result[key] = value
        
        return result


def get_fusion_desensitization_engine() -> FusionDesensitizationEngine:
    """获取融合层脱敏引擎实例"""
    return FusionDesensitizationEngine()


fusion_desensitization_engine = get_fusion_desensitization_engine()
