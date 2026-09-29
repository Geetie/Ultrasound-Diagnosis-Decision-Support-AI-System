"""P0-2 约束解码：grading schema / GBNF / guided 抽取守卫。"""
import pytest

from app.inference.grading_schema import (
    GRADING_ENUMS,
    extract_guided_grading,
    grading_gbnf,
    grading_json_schema,
)


class TestGradingSchema:
    def test_三器官枚举与expected形态对齐(self):
        assert GRADING_ENUMS["thyroid"] == ["2", "3", "4a", "4b", "5"]
        assert GRADING_ENUMS["liver"] == ["1", "2", "3", "4", "5"]
        assert GRADING_ENUMS["cardiac"] == ["normal", "mild", "moderate", "severe"]

    def test_json_schema结构(self):
        schema = grading_json_schema("thyroid")
        assert schema["type"] == "object"
        assert schema["required"] == ["grading"]
        assert schema["properties"]["grading"]["enum"] == GRADING_ENUMS["thyroid"]

    def test_未知器官返回None(self):
        assert grading_json_schema("breast") is None
        assert grading_gbnf("breast") is None

    def test_gbnf含全部枚举分支(self):
        gbnf = grading_gbnf("liver")
        for v in GRADING_ENUMS["liver"]:
            assert f'"{v}"' in gbnf
        assert gbnf.startswith("root ::=")


class TestExtractGuidedGrading:
    def test_纯json(self):
        assert extract_guided_grading('{"grading": "4a"}') == "4a"

    def test_带fence与前后缀(self):
        assert extract_guided_grading('结论如下\n```json\n{"grading": "5"}\n```\n以上') == "5"

    def test_损坏json回退None(self):
        assert extract_guided_grading('{"grading": "4a"') is None

    def test_无json返回None(self):
        assert extract_guided_grading("分级: TI-RADS 4a") is None

    @pytest.mark.parametrize("organ", ["thyroid", "liver", "cardiac"])
    def test_各器官schema与gbnf可互相印证(self, organ):
        schema = grading_json_schema(organ)
        gbnf = grading_gbnf(organ)
        enum = GRADING_ENUMS[organ]
        # 每个 schema 枚举值都应出现在 GBNF 分支里
        for v in enum:
            assert f'"{v}"' in gbnf
        assert schema["properties"]["grading"]["enum"] == enum
