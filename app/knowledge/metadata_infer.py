"""Source metadata inference for knowledge-base chunks.

为什么单独一个模块（2026-11-09）
--------------------------------
改造前的 chunk 只有 ``organ`` + ``section_path``，**没有任何来源属性**：
检索到一块内容时，下游无法回答"这是谁说的、哪一年的、是不是中华医学会的
指南"。这在临床场景是硬伤——同一问题 ACR 与中华医学会的推荐可以不同，
没有权威标记就无法做冲突检测与来源加权。

本模块只做**从文件名/正文推断**的确定性映射（无 LLM、无网络），
推断不出的字段留空而不是猜（``None`` / ``""``），便于事后审计。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import structlog

logger = structlog.get_logger("app.knowledge.metadata_infer")


@dataclass(frozen=True)
class SourceMetadata:
    """单个 source doc 的来源属性。"""
    source_set: str = ""
    source_authority: str = ""
    publish_year: int | None = None


#: 文件名前缀 → (source_set, authority)。顺序敏感，先匹配者胜。
#: 权威值取自各文件正文首页（实测，见 reports/RAG阶段1_分块前沿研究_20261109.md）。
_SOURCE_RULES: tuple[tuple[str, str, str], ...] = (
    # 中华医学会 / 国家卫健委体系
    ("ctirads_2021", "ctirads_2021", "中华医学会超声医学分会"),
    ("thyroid_cancer_guideline_2022", "thyroid_ca_2022", "国家卫生健康委员会"),
    ("thyroid_cancer_norm_2018", "norm_2018", "国家卫生健康委员会"),
    ("cardiac_echo_measurement_2016", "cardiac_ref_2016", "中华医学会超声医学分会"),
    ("cardiac_tte_standardization_2024", "cardiac_tte_2024", "中华医学会超声医学分会"),
    ("cardiac_valve_consensus_2021", "cardiac_valve_2021", "中华医学会心血管病学分会"),
    ("liver_cancer_guideline_2024", "liver_ca_2024", "国家卫生健康委员会"),
    ("liver_us_diagnosis_guideline_2021", "liver_us_2021", "中华医学会超声医学分会"),
    ("ksth_rfa_2017", "ksth_rfa_2017", "韩国甲状腺放射学会"),
    # ACR（美国放射学会）
    ("acr_ceus_lirads_2017_core", "acr_ceus_lirads_2017", "ACR"),
    ("acr_ceus_lirads_2027_core", "acr_ceus_lirads_2027", "ACR"),
    ("acr_ceus_tra_2024_core", "acr_ceus_tra_2024", "ACR"),
    ("acr_lirads_us_surveillance_2024_core", "acr_lirads_us_2024", "ACR"),
    # ASE（美国超声心动图学会）/ BSE / ATA
    ("ase_comprehensive_tte_2019_cn", "ase_tte_2019", "ASE"),
    ("ase_diastolic_2025", "ase_diastolic_2025", "ASE"),
    ("ase_hcm_2022", "ase_hcm_2022", "ASE"),
    ("ase_right_heart_2025", "ase_right_heart_2025", "ASE"),
    ("ase_strain_2025", "ase_strain_2025", "ASE"),
    ("ase_stress_echo_2020_cn", "ase_stress_2020", "ASE"),
    ("bse_diastolic_2024", "bse_diastolic_2024", "BSE"),
    ("jaha_very_severe_as_2019", "jaha_as_2019", "AHA"),
    ("ata_ablation_benign_nodules_2023", "ata_ablation_2023", "ATA"),
    ("ata_ablation_benign_2023", "ata_ablation_2023", "ATA"),
    ("ata_nodule_dtc_2015", "ata_dtc_2015", "ATA"),
    ("ata_pediatric_2015", "ata_pediatric_2015", "ATA"),
)

#: 文件名里显式的 4 位年份（20xx）
_YEAR_RE = re.compile(r"(20[0-9]{2})")


def infer_source_metadata(source_doc_id: str) -> SourceMetadata:
    """从 source_doc_id（= 文件名去扩展名，常带 ``pdf_<organ>_`` 前缀）推断属性。

    推断顺序：先按前缀规则表精确匹配，再退化为"取文件名里的年份"。
    匹配不上时 source_set 回落为去前缀的文件名主体，authority 留空。
    """
    raw = (source_doc_id or "").strip()
    if not raw:
        return SourceMetadata()

    # 去掉 builder 加的 pdf_<organ>_ 前缀
    stem = re.sub(r"^pdf_(?:thyroid|liver|cardiac|heart|X|x)_", "", raw)
    # 去掉尾部序号（_0001 / _0001#0000）——注意不能误删年份（_2021）
    stem = re.split(r"[#]", stem)[0]
    stem = re.sub(r"_(?:0\d{3})$", "", stem)

    for prefix, sset, authority in _SOURCE_RULES:
        if stem.startswith(prefix):
            year = _infer_year(stem, prefix)
            return SourceMetadata(
                source_set=sset, source_authority=authority, publish_year=year
            )

    year_m = _YEAR_RE.search(stem)
    year = int(year_m.group(1)) if year_m else None
    logger.debug(
        "source_metadata_unmatched", source_doc_id=source_doc_id, stem=stem
    )
    return SourceMetadata(source_set=stem, source_authority="", publish_year=year)


def _infer_year(stem: str, prefix: str) -> int | None:
    """年份优先取规则前缀里的年份，其次取文件名里的第一个 20xx。"""
    m = _YEAR_RE.search(prefix)
    if m:
        return int(m.group(1))
    m = _YEAR_RE.search(stem)
    return int(m.group(1)) if m else None
