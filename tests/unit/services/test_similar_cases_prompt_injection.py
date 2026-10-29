"""01-1 方案(a)：similar_cases 特征文本注入 prompt 的守卫。

判据（用户拍板"NO_GAIN 用例不再是总数不变"）：
* 特征文本真的到达 user_query（= 到达 specialist prompt 的"# 用户查询"段，
  ``_build_prompt`` 原样渲染），而不是只落在 findings 载荷里；
* deidentified（跨病人）作用域**绝不**进 prompt（A.82 合规边界）；
* 无特征文本时不注入空段。
"""

from __future__ import annotations

from app.services import diagnosis_pipeline as dp
from app.services.similar_cases import CASE_DISCLAIMER


def _similar_cases(feature_text: str) -> dict:
    return {
        "status": "ok",
        "scope": "self",
        "feature_text": feature_text,
        "feature_text_source": "prior_findings",
        "cases": [{"image_id": "img_1", "score": 0.9}],
    }


class TestAppendSimilarCasesNote:
    def test_self_scope_feature_text_is_appended(self):
        out = dp._append_similar_cases_note("请判读", _similar_cases("低回声，边界清"))
        assert "低回声，边界清" in out
        assert "【本人历史影像判读特征" in out

    def test_deidentified_scope_never_enters_prompt(self):
        d = _similar_cases("跨病人内容")
        d["scope"] = "deidentified"
        assert dp._append_similar_cases_note("请判读", d) == "请判读"

    def test_truncated_to_200_chars(self):
        out = dp._append_similar_cases_note("q", _similar_cases("x" * 500))
        assert len(out) == len("q") + len("\n【本人历史影像判读特征（背景参考，非诊断依据）】") + 200

    def test_none_or_bad_shape_returns_original(self):
        assert dp._append_similar_cases_note("q", None) == "q"
        assert dp._append_similar_cases_note("q", {"status": "unavailable"}) == "q"
        assert dp._append_similar_cases_note("q", {"scope": "self"}) == "q"


class TestPriorFindingsFeatureText:
    async def test_no_patient_or_organ_returns_empty(self):
        assert await dp._prior_findings_feature_text(
            patient_id=None, organ="thyroid", exclude_request_id="r1"
        ) == ""
        assert await dp._prior_findings_feature_text(
            patient_id="p1", organ=None, exclude_request_id="r1"
        ) == ""

    async def test_db_failure_returns_empty_not_raise(self, monkeypatch):
        class _Boom:
            async def __aenter__(self):
                raise RuntimeError("db down")

            async def __aexit__(self, *a):
                return False

        monkeypatch.setattr(dp, "AsyncSessionLocal", lambda: _Boom())
        assert (
            await dp._prior_findings_feature_text(
                patient_id="p1", organ="thyroid", exclude_request_id="r1"
            )
            == ""
        )


class TestDisclaimerAlignedWithPromptFact:
    def test_disclaimer_no_longer_denies_prompt_participation(self):
        """特征文本现在会进 prompt —— 免责声明不得再说"不参与本次结论生成"。"""
        assert "不参与本次结论生成" not in CASE_DISCLAIMER
