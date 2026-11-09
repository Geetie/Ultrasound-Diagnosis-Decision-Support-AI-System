"""source_set 归一化与派生的守卫（2026-11-09）。

## 为什么这些用例值得存在

``source_set`` 是"医院能按权威语料过滤"和"多来源冲突并列"两项能力的**唯一**
数据基础。它写错不会报错，只会让：

* 医院配了 ``ctirads_2021`` → 过滤命中 0 行 → 医生看到"知识库没有相关内容"
  （而语料明明在库里）；
* 或者更糟：过滤**没生效** → 医生以为只看本院权威语料，实际看了全部。

所以这里钉四类不变量：归一化、派生、白名单语义、规则表与标签表的一致性。
"""

from __future__ import annotations

import pytest

from app.knowledge.source_set import (
    SOURCE_SET_LABELS,
    SOURCE_SET_RULES,
    SOURCE_SET_UNKNOWN,
    display_name_of,
    included_by_enabled,
    known_source_sets,
    label_of,
    normalize_source_set,
    normalize_source_sets,
    source_set_of,
    unknown_source_sets,
)


class TestNormalize:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("ctirads_2021", "ctirads_2021"),
            ("C-TIRADS", "ctirads_2021"),
            ("C-TIRADS 2021", "ctirads_2021"),
            ("c_tirads", "ctirads_2021"),
            ("中华医学会超声医学分会", "ctirads_2021"),
            ("中华医学会超声分会", "ctirads_2021"),
            ("norm2018", "norm_2018"),
            ("甲状腺癌诊疗规范 2018", "norm_2018"),
            ("国家卫健委", "norm_2018"),
            ("ASE 2025", "ase_2025"),
            ("BSE", "bse_2024"),
            ("CEUS-LIRADS", "ceus_lirads_2027"),
            ("ata", "ata_2015"),
        ],
    )
    def test_alias_and_case_forms_collapse_to_one_id(self, raw, expected):
        assert normalize_source_set(raw) == expected

    def test_none_and_blank_become_none(self):
        assert normalize_source_set(None) is None
        assert normalize_source_set("") is None
        assert normalize_source_set("   ") is None

    def test_unknown_value_is_returned_verbatim_not_swallowed(self):
        """未知值**原样返回**（小写化），不返回 None、不返回 unknown。

        取舍见 `normalize_source_set` 的 docstring：归成 unknown 会让
        "医院把集名拼错一个字"变成静默放开/静默收紧，运维看不出是拼写错误。
        """
        assert normalize_source_set("my_custom_set") == "my_custom_set"
        assert normalize_source_set("My-Custom Set") == "my_custom_set"

    def test_batch_normalization_dedupes_keeping_order(self):
        out = normalize_source_sets(["C-TIRADS", "norm2018", "ctirads_2021"])
        assert out == ["ctirads_2021", "norm_2018"]

    def test_batch_none_vs_empty_are_distinguishable(self):
        """``None``（不过滤）与 ``[]``（过滤成空）语义不同，不能混。"""
        assert normalize_source_sets(None) is None
        assert normalize_source_sets([]) == []


class TestDeriveFromChunk:
    @pytest.mark.parametrize(
        ("doc_id", "expected"),
        [
            # 甲状腺：两套冲突语料
            ("pdf_thyroid_ctirads_2021_0012", "ctirads_2021"),
            ("pdf_thyroid_thyroid_cancer_norm_2018_0007", "norm_2018"),
            ("norm2018_s01", "norm_2018"),
            ("pdf_thyroid_thyroid_cancer_guideline_2022_0001", "thyroid_guideline_2022"),
            ("ata_nodule_dtc_2015_0043", "ata_2015"),
            ("ata_pediatric_2015_0032", "ata_pediatric_2015"),
            ("ata_ablation_2023_0023", "ata_ablation_2023"),
            # 肝脏：CT/MRI 与 CEUS 是两套不同判据
            ("acr_ceus_lirads_2027_0005", "ceus_lirads_2027"),
            ("acr_ceus_lirads_2017_0021", "ceus_lirads_2017"),
            ("pdf_liver_lirads_v2018_0001", "lirads_v2018"),
            ("acr_lirads_us_2024_0016", "lirads_us_2024"),
            # 心脏：ASE 与 BSE 切点可能不同
            ("ase_diastolic_2025_0014", "ase_2025"),
            ("bse_diastolic_2024_0121", "bse_2024"),
            # 非指南语料单独成集
            ("dialogue_内科_2627", "dialogue"),
        ],
    )
    def test_real_doc_ids_resolve(self, doc_id, expected):
        """这些 doc_id 全部取自 kb_v1.5.0 的实测数据（不是编的）。"""
        assert source_set_of(source_doc_id=doc_id) == expected

    def test_longest_prefix_wins(self):
        """``acr_ceus_lirads_2027`` 不能被更短的 ``acr_ceus_*`` 抢走。"""
        assert source_set_of(source_doc_id="acr_ceus_lirads_2027_0005") == "ceus_lirads_2027"
        assert source_set_of(source_doc_id="acr_ceus_tra_2024_0003") == "ceus_tra_2024"

    def test_explicit_metadata_beats_prefix(self):
        """显式标注优先于前缀推断（迁移后 metadata 是权威）。"""
        assert (
            source_set_of(
                source_doc_id="pdf_thyroid_ctirads_2021_0012",
                metadata={"source_set": "norm_2018"},
            )
            == "norm_2018"
        )

    def test_metadata_value_is_itself_normalized(self):
        assert (
            source_set_of(source_doc_id="x", metadata={"source_set": "C-TIRADS 2021"})
            == "ctirads_2021"
        )

    def test_unknown_when_nothing_matches(self):
        assert source_set_of(source_doc_id="mystery_doc_0001") == SOURCE_SET_UNKNOWN
        assert source_set_of(source_doc_id="") == SOURCE_SET_UNKNOWN
        assert source_set_of() == SOURCE_SET_UNKNOWN

    def test_dirty_metadata_does_not_raise(self):
        """脏 metadata 不许打断检索（失败安全，与 authority.source_kind_of 同口径）。"""

        class Exploding(dict):
            def get(self, *a, **k):
                raise RuntimeError("dirty metadata")

        # 显式传入一个会在 .get 上炸的 dict 子类
        assert source_set_of(source_doc_id="x", metadata=Exploding()) == SOURCE_SET_UNKNOWN


class TestEnabledWhitelist:
    def test_none_and_empty_mean_all_enabled(self):
        assert included_by_enabled("ctirads_2021", None) is True
        assert included_by_enabled("ctirads_2021", []) is True

    def test_non_empty_is_a_whitelist(self):
        assert included_by_enabled("ctirads_2021", ["ctirads_2021"]) is True
        assert included_by_enabled("norm_2018", ["ctirads_2021"]) is False

    def test_whitelist_values_are_normalized(self):
        """医院配置里写 ``C-TIRADS`` 与写 ``ctirads_2021`` 必须等价。"""
        assert included_by_enabled("ctirads_2021", ["C-TIRADS"]) is True

    def test_unknown_source_is_never_filtered_out(self):
        """**最关键的一条**：白名单只能收窄已知来源，不能静默删掉未知来源。

        反例（若这里改成 False 就会真实发生）：医院配 ``[ctirads_2021]``，
        而某批新语料还没进规则表 → 那批内容无声消失，医生看到
        "知识库里没有相关内容"，实际是被一个拼写/规则缺口挡住了。
        沉默比放行更危险，所以 unknown 永远放行。
        """
        assert included_by_enabled(SOURCE_SET_UNKNOWN, ["ctirads_2021"]) is True

    def test_whitelist_of_only_unknown_does_not_empty_the_kb(self):
        assert included_by_enabled("ctirads_2021", [SOURCE_SET_UNKNOWN]) is False
        assert included_by_enabled(SOURCE_SET_UNKNOWN, [SOURCE_SET_UNKNOWN]) is True


class TestLabelsAndRegistry:
    def test_every_rule_target_has_a_label(self):
        """规则表产出的每个标识都必须有可读标签（否则渲染出「未标注」）。"""
        missing = sorted({s for _, s in SOURCE_SET_RULES} - set(SOURCE_SET_LABELS))
        assert missing == [], f"这些 source_set 没有登记标签：{missing}"

    def test_every_label_is_used_by_a_rule(self):
        """反向：标签表里不许有从规则表拿不到的孤儿条目（除了 unknown）。"""
        produced = {s for _, s in SOURCE_SET_RULES}
        orphans = sorted(set(SOURCE_SET_LABELS) - produced - {SOURCE_SET_UNKNOWN})
        assert orphans == [], f"这些标签永远取不到（没有规则产出它）：{orphans}"

    def test_labels_carry_a_real_organization(self):
        """标签里的机构名必须可核对：不许写"权威机构""某学会"这种。"""
        vague = {"未标注", "未知", "权威机构", "某学会", ""}
        for ss, (name, org) in SOURCE_SET_LABELS.items():
            assert name.strip(), f"{ss} 显示名为空"
            assert org.strip(), f"{ss} 机构名为空"
            if ss != SOURCE_SET_UNKNOWN:
                assert org not in vague, f"{ss} 的机构名过于含糊：{org!r}"

    def test_unknown_label_is_explicit(self):
        assert label_of(SOURCE_SET_UNKNOWN) == ("未标注来源", "未标注")
        assert display_name_of("mystery_set") == "mystery_set"
        assert label_of("mystery_set")[1] == "未标注"

    def test_known_ids_are_stable_and_sorted(self):
        ids = known_source_sets()
        assert list(ids) == sorted(ids)
        assert SOURCE_SET_UNKNOWN not in ids
        assert "ctirads_2021" in ids and "norm_2018" in ids


class TestUnknownConfigDetection:
    def test_typo_is_reported(self):
        """医院把集名拼错 → 必须能被报出来（启动期告警的唯一依据）。"""
        assert unknown_source_sets(["ctirads_2021", "ctirads_20211"]) == ["ctirads_20211"]

    def test_known_values_are_not_reported(self):
        assert unknown_source_sets(["C-TIRADS", "norm2018"]) == []
        assert unknown_source_sets(None) == []
        assert unknown_source_sets([]) == []

    def test_typo_does_not_raise(self):
        """拼错一个字不该让服务起不来（过度反应），只报告。"""
        bad = unknown_source_sets(["!!!", "ctirads_20211"])
        assert set(bad) == {"!!!", "ctirads_20211"}
        # 关键：函数**返回**而不抛 —— 调用方（启动期 lifespan）据返回值告警。
        assert isinstance(bad, list)


class TestCrossConventionBridge:
    """与 `app/knowledge/metadata_infer.py` 的命名互通（2026-11-09）。

    ## 为什么这组用例必须存在

    同日另有一路实现在**建库时**把 `metadata["source_set"]` 写进 chunk，
    用的是**不带年份后缀**的标识（`ase_diastolic_2025` / `ata_dtc_2015` …）。
    两套并存会产生最坏的一类不一致：**医院配的集名与库里存的值对不上**，
    白名单把本院语料自己挡掉，医生看到"知识库没有相关内容" ——
    而这既没有报错，也没有日志。
    """

    @pytest.mark.parametrize(
        ("other_scheme", "canonical"),
        [
            ("ase_diastolic_2025", "ase_2025"),
            ("bse_diastolic_2024", "bse_2024"),
            ("ata_dtc_2015", "ata_2015"),
            ("thyroid_ca_2022", "thyroid_guideline_2022"),
            ("ase_tte_2019", "ase_comptte_2019"),
            ("acr_ceus_lirads_2027", "ceus_lirads_2027"),
            ("acr_ceus_lirads_2017", "ceus_lirads_2017"),
            ("liver_ca_2024", "liver_guideline_2024"),
            ("cardiac_ref_2016", "echo_measure_2016"),
            ("cardiac_tte_2024", "tte_2024"),
        ],
    )
    def test_other_scheme_normalizes_to_canonical(self, other_scheme, canonical):
        assert normalize_source_set(other_scheme) == canonical

    def test_config_written_in_either_scheme_matches_stored_chunk(self):
        """无论医院配哪一套写法、库里存哪一套，`included_by_enabled` 都必须判 True。"""
        for stored, configured in (
            ("ase_diastolic_2025", "ase_2025"),   # 库存另一套，配置用本套
            ("ase_2025", "ase_diastolic_2025"),   # 库存本套，配置用另一套
        ):
            assert included_by_enabled(stored, [configured]) is True, (
                f"库存 {stored} / 配置 {configured} 判成了不启用 —— 会把本院语料自己挡掉"
            )

    def test_other_scheme_ids_are_known_not_typos(self):
        """另一套标识不许被启动期告警当成拼写错误报出来。"""
        assert unknown_source_sets(["ase_diastolic_2025", "ata_dtc_2015"]) == []
