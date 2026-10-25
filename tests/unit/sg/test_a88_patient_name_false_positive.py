"""A.88 守卫：`sg_patient_name` 不得把**普通医疗用语**判成患者姓名。

## 真机缺陷（2026-10-25，误报率约 50%）

`desensitization_rules_safety.yaml` 的姓名规则第二分支原为：

    [\u4e00-\u9fa5]{2,4}(?=女士|先生|同志|医生|主任|教授|护士|技师)

即"称谓前 2~4 个汉字就是人名"。实际运行里这条把**动词+角色**一起吃掉了：

| 真实结论片段 | 原正则捕获 | 后果 |
|---|---|---|
| `建议咨询主治医生` | `咨询主治` | 判 `sg_patient_name` → 整条结论降级**抑制输出** |
| `由值班医生处理` | `由值班` | 同上 |
| `请外科医生会诊` | `时请外科` | 同上 |
| `安抚: PATIENT_010医生建议` | `班`（单字！） | 同上 |

临床上"请咨询主治医生"是**最正常不过的**一句建议，所以这条规则让**约一半**判读
走进了"检测到敏感数据 → 降级输出模式"（`conclusion_suppressed=True`），
表现为 `sg.passed=False` 而 `requires_manual_review=False` —— 医生看到的是一份
被抑制的结论，且**没有任何提示说是规则误报**。

排查成本也值得记：SG 日志当时只写"检测到 sg_patient_name"，不写命中位置，
于是先后猜过"旧进程旧规则""载荷形状差异""并发"三次全错，最后靠新增的
`sg_sensitive_hit_located`（规则 + 字段路径 + 偏移 + 长度）才定位。

## 修法（两条门禁，都要）

1. **姓氏门禁**：称谓前那个字必须来自常见汉语姓氏表（`姓氏 + 0~3 字`）；
2. **词界门禁**：姓氏字符**前面不能还是汉字**（否则「值班医生」的「班」会被当姓氏）；
3. **角色复合词排除**：姓氏后紧跟 `科院部处室诊护药技专内外儿妇产床检影病`
   一律不算（`全科医生` 的「全」是姓氏但不构成人名）。

## 这份守卫钉什么

* **不得误报**（反证腿：把规则改回 `[\u4e00-\u9fa5]{2,4}(?=医生)` 即红）；
* **仍要能报真人名**（`张三医生` / `患者：张三` / `王主任` / `李大夫`）——
  只修误报而让真阳性消失，是更坏的修法；
* **偏移信息可用**（`explain_sensitive_data` 返回的 start/end 指向真正命中的那几字）。
"""
from __future__ import annotations

import pytest

from safety_gateway.desensitization import get_safety_gateway_desensitization_engine

pytestmark = [pytest.mark.track_sg]


@pytest.fixture(scope="module")
def engine():
    return get_safety_gateway_desensitization_engine()


class TestMedicalPhrasesAreNotPatientNames:
    """反证腿：把规则改回 `[\\u4e00-\\u9fa5]{2,4}(?=医生)`，本类全红。"""

    @pytest.mark.parametrize(
        "text",
        [
            "建议咨询主治医生",
            "由值班医生处理",
            "请外科医生会诊",
            "建议与临床医生沟通",
            "转诊至专科医生",
            "必要时请内科医生",
            "建议咨询上级医生",
            "全科医生",
            "请主任审核",
            "安抚: PATIENT_010医生建议进行详细检查",
            "PATIENT_004医生建议决定后续步骤",
        ],
    )
    def test_common_phrase_not_flagged(self, engine, text):
        assert "sg_patient_name" not in engine.check_sensitive_data(text), (
            f"{text!r} 被误判为人名 —— 这会让整条结论降级抑制输出"
        )


class TestRealNamesStillDetected:
    """只修误报而丢真阳性是更坏的修法，所以这条必须一起钉。"""

    @pytest.mark.parametrize(
        "text",
        [
            "张三医生建议手术",
            "患者：张三",
            "患者:李四",
            "王主任查房记录",
            "张教授会诊",
            "李大夫",
        ],
    )
    def test_real_name_flagged(self, engine, text):
        assert "sg_patient_name" in engine.check_sensitive_data(text)


class TestHitIsLocatable:
    """命中必须能定位到**偏移**（且不落原文）—— 这是本次排查最缺的东西。"""

    def test_locations_point_at_the_match(self, engine):
        """命中必须能定位到**偏移**，且偏移取出的正是那个人名。"""
        hit_text = "甲状腺结节，张三医生建议手术。"
        locs = engine.explain_sensitive_data(hit_text)

        assert locs, "真人名必须能被定位"
        first = locs[0]
        assert first["rule"] == "sg_patient_name"
        assert hit_text[first["start"]:first["end"]] == "张三"
        assert first["length"] == 2

    def test_no_location_when_clean(self, engine):
        assert engine.explain_sensitive_data("甲状腺结节，建议复查。") == []

    def test_explain_matches_check(self, engine):
        """两个函数必须是**同一判据**：否则日志里"命中了"但"定位到 0 条"会自相矛盾。"""
        samples = [
            "张三医生建议手术",
            "建议咨询主治医生",
            "身份证号：110101199001011234",
            "普通文本无敏感信息",
        ]
        for s in samples:
            has = bool(engine.check_sensitive_data(s))
            locs = bool(engine.explain_sensitive_data(s))
            assert has == locs, f"{s!r}: check={has} explain={locs}"


class TestSurnameGateIsDeliberate:
    """姓氏表本身是这套门禁的核心，钉住它不空、不重复、含高频姓。"""

    def test_rule_pattern_has_surname_and_boundary_guards(self, engine):
        rule = next(r for r in engine.get_rules() if r.name == "sg_patient_name")
        pat = rule.pattern

        # ① 姓氏门禁：字符类 + 词界断言
        assert "赵钱孙李" in pat, "姓氏表缺失/被删 → 误报会立刻回来"
        assert "(?<![" in pat, "缺少词界断言 → 「值班医生」的「班」会被当姓氏"
        # ② 角色复合词排除
        assert "科" in pat and "院" in pat, "缺少科室/角色复合词排除"
        # ③ 强锚点分支保留
        assert "患者[：:]" in pat
