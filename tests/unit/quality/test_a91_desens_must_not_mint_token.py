"""A.91 守卫：**脱敏引擎自己不许制造 `PATIENT_00N`**（这是假数据的源头）。

## 这一条是本轮最关键也最反直觉的发现

修复顺序真实发生过：
1. A.90 手工清洗历史 47 条 `PATIENT_` → 库内归零；
2. A.91 在 `specialist/base.py` 4c 步接上"落库前清洗"；
3. 真机跑新判读 → **`PATIENT_` 又出现了**；
4. 以为是 CRAG 绕过，于是给 `fusion/crag.py` 也加上清洗；
5. 真机再跑 → **还是出现**；
6. 加三层探针（CRAG 入口 / CRAG 返回 / 落库前）→ 三个探针全都
   `has_token=False`，但**落库后是 True**；
7. 于是怀疑 L2 融合层脱敏 → 直接调 `fusion_desensitization_engine`
   实测：
   ```
   输入：… 证据: 无 患者： 请结合图像与病史进行人工判读。
   输出：… 证据: 无 患者： PATIENT_001像与病史进行人工判读。
   ```

**根因**：`desensitization_rules.yaml` 的 `patient_name` 规则第二段是裸的
`([\u4e00-\u9fa5]{2,4})` —— 把"患者："后面**任意 2-4 个汉字**当姓名，
于是把「请结合图」当成患者姓名，脱敏成 `PATIENT_001`。

也就是说：**这个标记不是模型抄的、也不是历史脏数据，是脱敏引擎自己生产的。**
A.90 的清洗是治标，A.77 的"清洗而非拒收"也只处理了症状。

## 为什么这条特别值得写成守卫

因为它满足"最危险缺陷"的全部特征：
* 输出**看起来像真实脱敏**（`PATIENT_001` 是合法的 token 格式）；
* 发生在**安全机制内部**（脱敏层），没人会怀疑安全层在制造数据；
* 每一条判读都会中招（只要结论里有"患者："+ 动词短语）；
* 表面症状（安全网关降级）与真因（引擎误报）隔了三层调用。

## 本守卫钉什么

1. `patient_name` 规则**不许**再是"任意 2-4 汉字"；
2. 谓词开头（请/需/继续/建议/目前…）不得被当姓名；
3. 姓氏白名单内的真姓名**仍然要被脱敏**（不能为了修误报而废掉功能）；
4. 规则必须能被 `_make_name_replacer` 用 —— **恰好 2 个捕获组**且 group(2)=姓名
   （这是硬约束：replacer 里写死 `match.group(2)`）；
5. 端到端：句子过引擎后**不得产生 `PATIENT_`**。
"""
from __future__ import annotations

import re

import pytest
import yaml

from app.security.desensitization import DESENSITIZATION_RULES_PATH, desensitization_engine

pytestmark = [pytest.mark.track_sec]

#: 真机实际出现的两种形态（来自 PG 里落库的结论正文）
LIVE_SENTENCE = (
    "甲状腺 · TR4 — 分级: 无法判断 置信度: 0.0 "
    "理由: 当前信息不足以进行 TI-RADS 分级。 证据: 无 "
    "患者： 请结合图像与病史进行人工判读。"
)


def _patient_name_rule() -> dict:
    with open(DESENSITIZATION_RULES_PATH, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    hits = [r for r in data["rules"] if r.get("name") == "patient_name"]
    assert len(hits) == 1, f"patient_name 规则数 = {len(hits)}"
    return hits[0]


class TestRuleShape:
    def test_pattern_is_not_bare_hanzi_range(self):
        """反证腿：把 pattern 改回 `(...)([\\u4e00-\\u9fa5]{2,4})` 即红。

        这是本缺陷的**唯一根因**：裸范围等于"任意 2-4 汉字都算姓名"。

        判据：姓名捕获组 `<name>` **前面不得紧邻 `)` 或字符串开头**
        （即不得是"前缀组结束 → 立刻抓任意汉字"），必须中间夹着
        lookahead（`(?=...)`）或负向断言（`(?!...)`）。
        """
        pat = _patient_name_rule()["pattern"]
        # 规范化空白后，找姓名捕获组的位置
        m = re.search(r"\(\[\\u4e00-\\u9fa5\]\{2,4\}\)", pat)
        assert m is not None, "pattern 里应有姓名捕获组 ([\\u4e00-\\u9fa5]{2,4})"
        before = pat[: m.start()].rstrip()
        assert before.endswith(")"), "结构异常（姓名组前应是前缀组）"
        # 前缀组结束后必须紧跟 lookahead/负向断言，而不是直接接姓名组
        tail = pat[len(before): m.start()]
        assert tail.strip() == "", (
            "姓名捕获组紧跟前缀组，中间没有姓氏/谓词约束 —— "
            "这会把「患者：请结合图」的「请结合图」当姓名脱敏成 PATIENT_00N"
        )

    def test_has_surname_lookahead(self):
        pat = _patient_name_rule()["pattern"]
        assert "(?=[" in pat, "缺少姓氏白名单 lookahead"
        assert "赵" in pat and "陈" in pat, "姓氏白名单里没有常见姓氏"

    def test_has_predicate_exclusion(self):
        pat = _patient_name_rule()["pattern"]
        assert "(?!" in pat, "缺少谓词排除断言"
        for w in ("请", "继续", "建议", "目前"):
            assert w in pat, f"谓词排除里缺 {w}"

    def test_exactly_two_capture_groups_with_name_in_group2(self):
        """硬约束：`_make_name_replacer` 写死 `match.group(2)` 取姓名。

        反证：把姓氏 lookahead 写成 `(?:...)` 捕获组 → 组数变 3 → 本用例红，
        且生产会 IndexError / 脱敏错位（本轮实际踩到）。
        """
        compiled = re.compile(_patient_name_rule()["pattern"])
        assert compiled.groups == 2, (
            f"捕获组 = {compiled.groups}，必须恰好 2（group(1)=前缀, group(2)=姓名）"
        )
        m = compiled.search("患者：张建国，男")
        assert m is not None and m.group(2) == "张建国"


class TestBehavior:
    @pytest.mark.parametrize(
        "text",
        [
            LIVE_SENTENCE,
            "患者：请提供更多信息",
            "患者：目前情况稳定",
            "患者：建议定期复查",
            "患者：继续观察即可",
            "患者：需进一步完善检查",
        ],
    )
    def test_sentence_is_not_treated_as_name(self, text):
        """句子/谓词不得被当姓名（反证：还原旧 pattern 后这些用例全红）。"""
        out = desensitization_engine.desensitize(text)
        assert "PATIENT_" not in out, (
            f"脱敏引擎把句子当姓名，制造了 PATIENT_ 标记：{out}"
        )
        assert out == text, f"不该改动的正文被改了：{out}"

    @pytest.mark.parametrize(
        "text,name",
        [
            ("患者：张建国，男，45岁", "张建国"),
            ("患者：李秀英 女 62岁", "李秀英"),
            ("患者：王小明，建议复查", "王小明"),
            ("病人：陈志强", "陈志强"),
        ],
    )
    def test_real_names_still_masked(self, text, name):
        """**功能不能被修没**：姓氏白名单内的真姓名仍要脱敏。"""
        out = desensitization_engine.desensitize(text)
        assert name not in out, f"真姓名 {name} 没被脱敏：{out}"

    def test_live_sentence_end_to_end(self):
        """真机原句端到端：过引擎后无 PATIENT_、且其余内容不变。"""
        out = desensitization_engine.desensitize(LIVE_SENTENCE)
        assert "PATIENT_" not in out
        assert "TI-RADS" in out, "正文其余部分被误改"
        assert "请结合图像与病史进行人工判读" in out


class TestTheWholeChainCannotReintroduceToken:
    """整条链路的契约：**任何环节都不许生产 `PATIENT_00N` 并写进结论**。"""

    def test_output_guard_cleans_if_anyone_produces_it(self):
        """兜底：即使别的环节产出了，落库前的清洗也要能去掉。

        （`specialist/base.py` 4c 与 `output_guard.clean_patient_token_leak`
        已接线；这里钉函数契约。）
        """
        from app.inference.output_guard import clean_patient_token_leak

        dirty = "患者： PATIENT_001像与病史进行人工判读。"
        cleaned, hits = clean_patient_token_leak(dirty)
        assert "PATIENT_" not in cleaned
        assert hits >= 1


class TestFieldLabelIsNotAName:
    """`患者解释:` / `患者沟通:` 这类**字段标签**绝不能被吃成 `患者PATIENT_00N:`。

    ## 这是本缺陷最后一个、也是最常见的漏网形态

    给 `patient_name` 加姓氏白名单后，真机**仍然** 5/5 出现 `PATIENT_`。
    靠 L2 前后探针才抓到：

        入：… 患者解释: 请结合图像与病史进行人工判读。
        出：… 患者PATIENT_001: 请结合图像与病史进行人工判读。

    即 `患者` + `解释` 被当成姓名 —— 而 `患者解释` 是 prompt 里的**固定字段名**，
    **几乎每条结论都有**，所以这个漏网比之前修的那些更致命。

    教训：谓词排除（请/需/继续…）只覆盖了"动词"，没覆盖"名词标签"。
    真实中文里紧跟 `患者` 的名词一样多：解释/沟通/说明/情况/信息/病史/症状…
    """

    @pytest.mark.parametrize(
        "label",
        ["解释", "沟通", "说明", "情况", "信息", "病史", "症状", "家属", "本人", "自述"],
    )
    def test_label_not_masked(self, label):
        text = f"… 证据: 无 患者{label}: 请结合图像与病史进行人工判读。"
        out = desensitization_engine.desensitize(text)
        assert "PATIENT_" not in out, (
            f"「患者{label}:」被当成姓名脱敏成了 {out[-60:]!r}"
        )

    def test_live_field_label_exact(self):
        """真机原句（L2 探针抓到的真实输入）。"""
        text = (
            "甲状腺 · TR4 — 分级: 无法判断 置信度: 0.0 理由: 信息不足。 证据: 无 "
            "鉴别诊断: 1. — 2. — 3. — 患者解释: 请结合图像与病史进行人工判读，"
            "以获得更准确的诊断建议。"
        )
        out = desensitization_engine.desensitize(text)
        assert "PATIENT_" not in out
        assert "患者解释" in out, "字段标签被改坏了"
