"""P0-2 约束解码：grading schema / GBNF / guided 抽取守卫。"""
import pytest

from app.inference.grading_schema import (
    GRADING_ENUMS,
    extract_guided_grading,
    grading_gbnf,
    grading_json_schema,
)


class TestGradingSchema:
    def test_三器官枚举与生产分级体系对齐(self):
        """A.95 更新：原断言钉的是**漏项**的旧枚举，必须改。

        旧断言（错误，已作废）：
            thyroid == ["2","3","4a","4b","5"]   ← 缺 TR1
            liver   == ["1","2","3","4","5"]     ← 缺 LR-M / LR-TIV

        真机后果（A.95 Q29 重测发现）：
        * TR1（正常）被枚举排除 → 正常病例被迫报 TR2 → **假阳性**；
        * LR-M（可能恶性但非 HCC 特异）与 LR-TIV（**癌栓**）无法输出 →
          模型只能塞进 LR-4/LR-5 → **临床语义降级**（癌栓被报成普通病灶）。

        补全枚举后常规档一致率 75.18% → 79.56%（+4.4pp）。
        新断言直接对**生产常量**，不再抄一份必然漂移的硬编码。
        """
        from app.agents.specialist.liver import LI_RADS_LEVELS

        # thyroid: TR1~TR5 全覆盖（4a/4b 是 TR4 细分）
        assert GRADING_ENUMS["thyroid"] == ["1", "2", "3", "4a", "4b", "5"]
        assert "1" in GRADING_ENUMS["thyroid"], "缺 TR1 = 正常病例被迫报 TR2"

        # liver: 与 LI_RADS_LEVELS 严格一致（含 LR-M / LR-TIV）
        assert GRADING_ENUMS["liver"] == [
            x.removeprefix("LR-") for x in LI_RADS_LEVELS
        ], "liver 枚举必须与 LI_RADS_LEVELS 一致（缺 M/TIV 会让癌栓无法输出）"

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
