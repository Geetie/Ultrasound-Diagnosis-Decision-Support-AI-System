"""A.139 / G48：脱敏 NER 判据的**离线实测**（此前"无模型无实测"）。

G48 的原话是"NER 判据无模型无实测"。模型确实没有，但"实测"可以离线做：
用**合成集**（不是真实 PHI）量出当前规则在各类 PII 上的召回/误杀，把
"哪些类可靠、哪些类靠不住"变成数字，而不是一句"用的是 NER"。

本测试同时是**门禁**与**台账**：
- 手机号 / 身份证 / 邮箱 / 带称谓或"患者："前缀的姓名 → 断言高召回；
- 医学词（甲状腺、低回声、TI-RADS…）→ 断言零误杀（误杀会让结论不可读）；
- **无前缀的陌生姓名** → 只测不设门禁，数字写进日志（这是已知缺口，等 NER 模型）。

合成集里的姓名取自公共姓氏 + 规则表内的姓名集合，**不含任何真实患者信息**。
"""

from __future__ import annotations

import logging

import pytest

from safety_gateway.desensitization import SafetyGatewayDesensitizationEngine as SafetyGatewayDesensitizer
from safety_gateway.desensitization_rules import load_person_name_set_from_file

pytestmark = pytest.mark.track_sg

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("desens_eval")

REDACTED = "[REDACTED"


@pytest.fixture(scope="module")
def desens() -> SafetyGatewayDesensitizer:
    return SafetyGatewayDesensitizer()


def _hit(desens: SafetyGatewayDesensitizer, text: str) -> bool:
    return REDACTED in (desens.desensitize(text) or "")


def test_structured_pii_recall_is_complete(desens):
    cases = {
        "phone": "联系电话 13800138000 请回电",
        "id_card": "身份证 110101199003071234 已核对",
        "email": "邮箱 zhangsan@example.com 可联系",
    }
    missed = [k for k, v in cases.items() if not _hit(desens, v)]
    logger.info("desens_eval structured recall: %s/%s", len(cases) - len(missed), len(cases))
    assert missed == [], f"结构化 PII 未命中：{missed}"


def test_prefixed_names_are_redacted(desens):
    names = sorted(load_person_name_set_from_file() or set())
    assert names, "姓名集合未加载，测试前提不成立"
    # 用带称谓的形式（规则表用前后缀定位），抽 20 个样本足够暴露回归
    sample = [n for n in names if n.endswith(("先生", "女士", "主任"))][:20]
    missed = [n for n in sample if not _hit(desens, f"患者：{n}，今日复诊")]
    logger.info("desens_eval prefixed-name recall: %s/%s", len(sample) - len(missed), len(sample))
    assert len(missed) <= 1, f"带前缀姓名召回不足：{missed}"


def test_medical_terms_are_never_redacted(desens):
    terms = [
        "甲状腺右叶低回声结节，边界清，形态规则",
        "TI-RADS 4a，建议超声引导下细针穿刺",
        "肝内见 12×10 mm 高回声灶，后方回声增强",
        "LVEF 62%，左室舒张功能减低",
        "淋巴结皮髓质分界清",
    ]
    false_positives = [t for t in terms if REDACTED in (desens.desensitize(t) or "")]
    logger.info("desens_eval medical false positives: %s/%s", len(false_positives), len(terms))
    assert false_positives == [], f"医学词被误脱敏（会毁掉结论可读性）：{false_positives}"


def test_unprefixed_stranger_names_are_redacted(desens):
    """无称谓/无前缀的陌生姓名（裸姓名 + 就诊动作动词）必须脱敏。

    **G48 已闭合**（2026-11-03）：此前 0/4，原因是三条姓名规则都要求
    前缀/称谓/分隔符，`王小明来我院就诊` 这种临床常见写法完全漏掉。
    现用"姓氏词典 + 就诊动作动词"锚点覆盖（不引 NER 模型：
    可选中文 NER 的许可都不适合本项目 —— ckiplab 系 GPL-3.0、
    uer/CLUENER 系未声明许可）。

    门禁从此收紧为"必须全中"：这是 PII 红线，不是可选项。
    """
    strangers = ["王小明", "陈嘉禾", "周文博", "欧阳思远"]
    missed = [n for n in strangers if not _hit(desens, f"{n}来我院就诊")]
    logger.info(
        "desens_eval unprefixed stranger recall: %s/%s",
        len(strangers) - len(missed), len(strangers),
    )
    assert missed == [], f"无前缀姓名未脱敏（PII 泄漏）：{missed}"
