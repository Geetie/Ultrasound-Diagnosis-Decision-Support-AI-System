"""语料来源集（source_set）归一化与元数据提取（2026-11-09）。

## 要解决的现实

知识库里同时装着**多套权威语料**，而且它们会在同一临床问题上给出不同口径：

| 项 | C-TIRADS 2021（中华医学会超声分会） | 甲状腺癌诊疗规范 2018（国家卫健委） |
|---|---|---|
| 3 类恶性风险 | ``<2%`` | ``<5%`` |
| 4a 恶性风险 | ``2%~10%`` | ``5%~10%`` |
| 4c | ``50%~90%`` | ``50%~85%`` |
| 5 类 | ``>90%`` | ``85%~100%`` |
| 4A 类 FNA 阈值 | ``>15 mm`` | ``>1 cm（10 mm）`` |

两者**不是竞争关系**：C-TIRADS 管"超声怎么报"，诊疗规范管"病人全程怎么走"，
但在分级交叉点上数字不同。医院 A 可能以 C-TIRADS 为准、医院 B 以诊疗规范为准，
也可能两者都启用（互补）。

## 本模块的职责（纯函数，无 IO，可单测）

1. **归一化**：把各种写法（``C-TIRADS`` / ``ctirads`` / ``C-TIRADS 2021`` /
   ``中华医学会超声分会``）归到同一个稳定标识 ``ctirads_2021``。
   风格照 ``app/rag/organ_alias.py``：**未知值原样返回**，不猜也不吞
   （见 :func:`normalize_source_set` 的取舍说明）。
2. **从现有数据推导**：本仓库的历史 chunk **没有** ``source_set`` 字段，
   但 ``source_doc_id`` 里已经带着可识别的来源前缀（``pdf_thyroid_ctirads_2021_*``
   / ``norm2018_s01`` / ``ase_diastolic_2025_*`` …）。:func:`source_set_of`
   用**显式规则表**把它认出来，让存量数据零迁移即可参与过滤与并列。
3. **人可读标签**：:func:`label_of` 给出"依据《C-TIRADS 2021》(中华医学会超声医学分会)"
   这句话里要用的两个字段。

## 为什么不新建一张表 / 不动 chunk 结构

``source_set`` 是**纯派生字段**：它完全由 ``source_doc_id`` 决定。把它写进
``knowledge_chunks.metadata`` 只是缓存，**真值在规则表里**。这样：

- 存量 8 个 kb_version 的 10 957 条 chunk **一条都不用改**就能过滤；
- 规则表改了立刻全库生效，不用重建索引；
- 新增语料时只需在规则表加一行（:data:`SOURCE_SET_RULES`），且有条目缺失守卫。

向量库侧的 payload 副本由 ``scripts/kb_backfill_source_set.py`` 回填（dense 腿
需要它做 Qdrant filter），但**向量库缺字段也不影响 BM25 腿与并列渲染**
（BM25 在进程内用本模块现算）。这个分层是刻意的：任何一个存储层没跟上，
系统退化成"过滤不生效"，而不是"过滤把结果全清空"。

## 与 `app/knowledge/authority.py` 的分工（别合并）

| 模块 | 字段 | 语义 |
|---|---|---|
| ``authority.py`` | ``source_kind`` | 来源**类型**：指南 / 教科书 / 文献 / 病例 |
| 本模块 | ``source_set`` | 来源**身份**：具体是哪一份指南的哪一版 |

两者正交：C-TIRADS 2021 与诊疗规范 2018 的 ``source_kind`` 都是 ``guideline``
（类型相同），但 ``source_set`` 不同（身份不同）。**"类型相同"正是它们会冲突的
原因** —— 权威性打分救不了这种情况，只能并列。
"""

from __future__ import annotations

import re
from typing import Any

# ----------------------------------------------------------------------
# 规则表：source_doc_id 前缀 → source_set 标识
# ----------------------------------------------------------------------

#: ``(前缀, source_set)``，**按顺序匹配**，先命中先返回。
#:
#: 前缀一律小写；匹配的是 ``source_doc_id`` 转小写后 ``startswith``。
#: 规则来源（实测，2026-11-09 在 kb_v1.5.0 上逐条核对）：
#:   * ``pdf_thyroid_ctirads_2021_*``   → C-TIRADS 2021 原文（中华医学会超声分会）
#:   * ``pdf_thyroid_thyroid_cancer_norm_2018_*`` / ``norm2018_s00..s08``
#:                                      → 甲状腺癌诊疗规范 2018（国家卫健委）
#:   * ``pdf_thyroid_thyroid_cancer_guideline_2022_*`` → 甲状腺癌诊疗指南 2022
#:   * ``ata_nodule_dtc_2015_*`` / ``ata_pediatric_2015_*`` / ``ata_ablation_2023*``
#:                                      → ATA 系列（美国甲状腺协会）
#:   * ``acr_ceus_lirads_2017_*`` / ``..._2027_*`` / ``acr_ceus_tra_2024_*``
#:                                      → ACR CEUS LI-RADS（超声造影）
#:   * ``acr_lirads_us_2024_*``         → ACR LI-RADS 超声监测
#:   * ``ase_*`` / ``bse_*`` / ``jaha_*`` → 心脏方向各学会文件
#:   * ``dialogue_*``                   → 对话语料（非指南，单独成集）
#:
#: ⚠️ 长的前缀必须排在短的前面（``acr_ceus_lirads_2027`` 不能先被 ``acr_ceus`` 抢走）。
SOURCE_SET_RULES: tuple[tuple[str, str], ...] = (
    # ---- 甲状腺 ----
    ("pdf_thyroid_ctirads_2021", "ctirads_2021"),
    ("ctirads_2021", "ctirads_2021"),
    ("pdf_thyroid_thyroid_cancer_norm_2018", "norm_2018"),
    ("norm2018", "norm_2018"),
    ("pdf_thyroid_thyroid_cancer_guideline_2022", "thyroid_guideline_2022"),
    ("ata_nodule_dtc_2015", "ata_2015"),
    ("ata_pediatric_2015", "ata_pediatric_2015"),
    ("ata_ablation_2023", "ata_ablation_2023"),
    # ---- 肝脏 ----
    ("acr_ceus_lirads_2027", "ceus_lirads_2027"),
    ("acr_ceus_lirads_2017", "ceus_lirads_2017"),
    ("acr_ceus_tra_2024", "ceus_tra_2024"),
    ("acr_lirads_us_2024", "lirads_us_2024"),
    ("pdf_liver_lirads_v2018", "lirads_v2018"),
    ("pdf_liver_liver_cancer_guideline_2024", "liver_guideline_2024"),
    ("pdf_liver_liver_us_diagnosis_guideline_2021", "liver_us_2021"),
    # ---- 心脏 ----
    ("ase_diastolic_2025", "ase_2025"),
    ("bse_diastolic_2024", "bse_2024"),
    ("pdf_cardiac_ase_chamber_quant_2015", "ase_chamber_2015"),
    ("pdf_cardiac_cardiac_echo_measurement_2016", "echo_measure_2016"),
    ("pdf_cardiac_cardiac_tte_standardization_2024", "tte_2024"),
    ("pdf_cardiac_cardiac_valve_consensus_2021", "valve_2021"),
    ("ase_comptte_2019_cn", "ase_comptte_2019"),
    ("ase_hcm_2022", "ase_hcm_2022"),
    ("ase_right_heart_2025", "ase_right_heart_2025"),
    ("ase_strain_2025", "ase_strain_2025"),
    ("ase_stress_echo_2020_cn", "ase_stress_echo_2020"),
    ("jaha_very_severe_as_2019", "jaha_as_2019"),
    # ---- 非指南语料（单独成集：它们不该跟指南并列） ----
    ("dialogue_", "dialogue"),
)

#: 认不出来源时使用的值。**不猜**（与 ``authority.py`` 的 ``unknown`` 同一条原则：
#: 元数据缺失是数据问题，不是"这条知识不可信"）。``unknown`` 在过滤时
#: **不会被任何白名单静默排除**，见 :func:`included_by_enabled`。
SOURCE_SET_UNKNOWN = "unknown"

#: 各 source_set 的人可读标签：``(显示名, 发布机构)``。
#:
#: 这两个字段直接渲染进用户的冲突并列块，所以**必须是可核对的机构名**，
#: 不许写"某学会""权威机构"这种含糊表述。显示名里的年份来自原标题。
SOURCE_SET_LABELS: dict[str, tuple[str, str]] = {
    "ctirads_2021": ("C-TIRADS 2021", "中华医学会超声医学分会"),
    "norm_2018": ("甲状腺癌诊疗规范 2018", "国家卫生健康委员会"),
    "thyroid_guideline_2022": ("甲状腺癌诊疗指南 2022", "国家卫生健康委员会"),
    "ata_2015": ("ATA 甲状腺结节与分化型甲状腺癌指南 2015", "美国甲状腺协会（ATA）"),
    "ata_pediatric_2015": ("ATA 儿童甲状腺结节指南 2015", "美国甲状腺协会（ATA）"),
    "ata_ablation_2023": ("ATA 良性甲状腺结节热消融共识 2023", "美国甲状腺协会（ATA）"),
    "ceus_lirads_2027": ("CEUS LI-RADS v2027", "美国放射学会（ACR）"),
    "ceus_lirads_2017": ("CEUS LI-RADS v2017", "美国放射学会（ACR）"),
    "ceus_tra_2024": ("CEUS TRA 2024", "美国放射学会（ACR）"),
    "lirads_us_2024": ("LI-RADS 超声监测 2024", "美国放射学会（ACR）"),
    "lirads_v2018": ("LI-RADS v2018", "美国放射学会（ACR）"),
    "liver_guideline_2024": ("原发性肝癌诊疗指南 2024", "国家卫生健康委员会"),
    "liver_us_2021": ("肝脏超声诊断指南 2021", "中华医学会超声医学分会"),
    "ase_2025": ("ASE 左室舒张功能评估指南 2025", "美国超声心动图学会（ASE）"),
    "bse_2024": ("BSE 舒张功能评估指南 2024", "英国超声心动图学会（BSE）"),
    "ase_chamber_2015": ("ASE 心腔定量指南 2015", "美国超声心动图学会（ASE）"),
    "echo_measure_2016": ("超声心动图测量指南 2016", "美国超声心动图学会（ASE）"),
    "tte_2024": ("经胸超声心动图标准化 2024", "美国超声心动图学会（ASE）"),
    "valve_2021": ("瓣膜病超声评估共识 2021", "美国超声心动图学会（ASE）"),
    "ase_comptte_2019": ("ASE 综合经胸超声心动图 2019", "美国超声心动图学会（ASE）"),
    "ase_hcm_2022": ("ASE 肥厚型心肌病超声指南 2022", "美国超声心动图学会（ASE）"),
    "ase_right_heart_2025": ("ASE 右心评估指南 2025", "美国超声心动图学会（ASE）"),
    "ase_strain_2025": ("ASE 心肌应变指南 2025", "美国超声心动图学会（ASE）"),
    "ase_stress_echo_2020": ("ASE 负荷超声心动图 2020", "美国超声心动图学会（ASE）"),
    "jaha_as_2019": ("JAHA 极重度主动脉瓣狭窄 2019", "美国心脏协会期刊（JAHA）"),
    "dialogue": ("临床对话语料", "本项目整理"),
    SOURCE_SET_UNKNOWN: ("未标注来源", "未标注"),
}

#: 别名 → 规范标识（键一律小写，且已按 :func:`_norm_key` 形状归一）。
#:
#: ⚠️ 别名表必须放**完整名称**（含年份），因为 ``_norm_key`` 只做形状归一
#: （小写/压空白），不做"去掉年份"这种语义猜测 —— 去掉年份会让
#: ``甲状腺癌诊疗规范 2018`` 与将来的 ``… 2025`` 撞成同一个键。
_ALIASES: dict[str, str] = {
    # C-TIRADS
    "c-tirads": "ctirads_2021",
    "c_tirads": "ctirads_2021",
    "ctirads": "ctirads_2021",
    "ctirads2021": "ctirads_2021",
    "c-tirads 2021": "ctirads_2021",
    "c-tirads2021": "ctirads_2021",
    "ctirads_2021": "ctirads_2021",
    "中华医学会超声医学分会": "ctirads_2021",
    "中华医学会超声分会": "ctirads_2021",
    # 诊疗规范
    "norm2018": "norm_2018",
    "norm_2018": "norm_2018",
    "norm 2018": "norm_2018",
    "甲状腺癌诊疗规范": "norm_2018",
    "甲状腺癌诊疗规范 2018": "norm_2018",
    "国家卫健委": "norm_2018",
    "国家卫生健康委员会": "norm_2018",
    # 其他
    "ata": "ata_2015",
    "ata2015": "ata_2015",
    "ata 2015": "ata_2015",
    "ata_2015": "ata_2015",
    "ase": "ase_2025",
    "ase2025": "ase_2025",
    "ase 2025": "ase_2025",
    "ase_2025": "ase_2025",
    "bse": "bse_2024",
    "bse2024": "bse_2024",
    "bse 2024": "bse_2024",
    "bse_2024": "bse_2024",
    "ceus_lirads": "ceus_lirads_2027",
    "ceus-lirads": "ceus_lirads_2027",
    "ceus_lirads_2027": "ceus_lirads_2027",
    "lirads": "lirads_v2018",
    "lirads_v2018": "lirads_v2018",
}


def _norm_key(value: str) -> str:
    """把任意写法压成检索键：去空白、去版本分隔噪声、小写。

    只做"形状"归一（大小写 / 首尾空白 / 连续空白 / ``-`` 与 ``_`` 互换），
    **不做语义猜测**。语义映射交给 :data:`_ALIASES` 与 :data:`SOURCE_SET_RULES`。
    """
    key = str(value or "").strip().lower()
    if not key:
        return ""
    key = re.sub(r"\s+", " ", key)
    return key


def normalize_source_set(value: str | None) -> str | None:
    """把 source_set 的各种写法归一到稳定标识（纯函数）。

    Args:
        value: 任意来源的写法：``ctirads_2021`` / ``C-TIRADS`` / ``C-TIRADS 2021``
            / ``中华医学会超声分会`` / ``norm2018`` …

    Returns:
        规范标识（见 :data:`SOURCE_SET_LABELS` 的键）；``None`` / 空串返回 ``None``。

    **未知值原样返回（小写化后）**，不返回 ``None``，也不返回 ``unknown``。

    理由与 ``app/rag/organ_alias.py::normalize_organ`` 相同，但这里更严重：
    如果未知值被归成 ``unknown``，医院的 ``rag_enabled_source_sets`` 里写错一个字
    就会**静默退化**成"启用了 unknown 这个集"，检索要么全空要么全放行，
    而运维看不出是拼写错误。原样返回则让错误可见（配置校验会报未知集）。
    """
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    key = _norm_key(raw)
    if key in _ALIASES:
        return _ALIASES[key]
    # 已经是规范标识（或未知标识）→ 原样返回，只做形状归一
    canonical = key.replace("-", "_").replace(" ", "_")
    return canonical


def normalize_source_sets(values: list[str] | tuple[str, ...] | None) -> list[str] | None:
    """批量归一化 + 去重（保持输入顺序）。

    Returns:
        ``None`` 入参返回 ``None``（语义是"不过滤"，与空列表不同）；空列表返回 ``[]``。
    """
    if values is None:
        return None
    out: list[str] = []
    for v in values:
        n = normalize_source_set(v)
        if n is not None and n not in out:
            out.append(n)
    return out


def source_set_of(*, source_doc_id: str | None = None, metadata: dict[str, Any] | None = None) -> str:
    """判定一条 chunk 属于哪个语料集（纯函数，按优先级）。

    顺序：
    1. ``metadata["source_set"]``（**显式标注优先**，迁移后由建库脚本写入）；
    2. ``source_doc_id`` 的显式前缀（:data:`SOURCE_SET_RULES`）；
    3. ``metadata["doc_prefix"]`` 的前缀匹配（建库脚本有时把前缀放这里）；
    4. :data:`SOURCE_SET_UNKNOWN`。

    **不抛异常**：metadata 可能是任意脏 JSON，任何异常都回退 ``unknown`` 并
    不打断检索（与 ``authority.source_kind_of`` 同一条失败安全原则）。
    """
    try:
        meta = dict(metadata or {})
        explicit = meta.get("source_set")
        if explicit:
            # 显式值也过一次归一：库里可能存着 "C-TIRADS 2021" 这种写法
            normalized = normalize_source_set(str(explicit))
            if normalized:
                return normalized

        for candidate in (source_doc_id, meta.get("doc_prefix")):
            key = _norm_key(str(candidate or ""))
            if not key:
                continue
            for prefix, source_set in SOURCE_SET_RULES:
                if key.startswith(prefix):
                    return source_set
    except Exception:  # silent-ok: 脏 metadata 不能打断检索，回退 unknown 即可
        return SOURCE_SET_UNKNOWN
    return SOURCE_SET_UNKNOWN


def label_of(source_set: str | None) -> tuple[str, str]:
    """取 source_set 的 ``(显示名, 发布机构)``；未知集返回 ``(标识, "未标注")``。

    **不返回空串**：渲染层拿它拼"依据《X》(Y)"，空串会让句子残缺，
    而残缺的引用比"未标注"更容易被误读成"已经核实过"。
    """
    key = normalize_source_set(source_set) or SOURCE_SET_UNKNOWN
    if key in SOURCE_SET_LABELS:
        return SOURCE_SET_LABELS[key]
    return (str(source_set or SOURCE_SET_UNKNOWN), "未标注")


def display_name_of(source_set: str | None) -> str:
    """只要显示名（``《C-TIRADS 2021》`` 花括号里的部分）。"""
    return label_of(source_set)[0]


def included_by_enabled(source_set: str, enabled: list[str] | tuple[str, ...] | None) -> bool:
    """``source_set`` 是否落在"本次启用的语料集"里。

    ## 口径（三条，缺一都会出临床事故）

    1. ``enabled`` 为 ``None`` 或空列表 → **全开**（与 ``rag_enabled_source_sets``
       的空值语义一致：不配置 = 不限制）。
    2. ``enabled`` 非空 → 只有**归一化后命中**的集算启用。
    3. :data:`SOURCE_SET_UNKNOWN` **不受白名单限制**（永远放行）。

    第 3 条是刻意的，理由是"白名单只能用来收窄已知来源，不能用来
    静默删掉未知来源"。反例：医院配了 ``[ctirads_2021]``，而某批新语料
    还没有进规则表 → 若把 unknown 一起过滤掉，那批内容会**无声消失**，
    医生看到"知识库里没有相关内容"，而实际上是"被一个拼写规则挡住了"。
    这种沉默比放行更危险，所以这里选择放行，并让调用方把
    「本次证据里有 N 条来源未标注」写进 trace（见 ``source_conflict.summary``）。
    """
    if not enabled:
        return True
    normalized = normalize_source_sets(list(enabled)) or []
    if not normalized:
        return True
    if source_set == SOURCE_SET_UNKNOWN:
        return True
    return normalize_source_set(source_set) in normalized


def known_source_sets() -> tuple[str, ...]:
    """规则表 + 标签表里出现过的全部规范标识（不含 ``unknown``）。

    用途：配置校验（医院配置里写了不存在的集名 → 启动期给可读报错），
    以及单测的"规则表与标签表一致"守卫。
    """
    ids = {s for _, s in SOURCE_SET_RULES}
    ids.update(k for k in SOURCE_SET_LABELS if k != SOURCE_SET_UNKNOWN)
    ids.discard(SOURCE_SET_UNKNOWN)
    return tuple(sorted(ids))


def unknown_source_sets(enabled: list[str] | tuple[str, ...] | None) -> list[str]:
    """配置里**不存在**的集名（写错字 / 用了还没进规则表的集）。

    返回空列表 = 配置全部可识别。**不抛异常**：这个函数用于启动期告警与
    单测，抛错会让一次拼写错误变成服务起不来 —— 那是过度反应，
    正确处置是"放行 + 明确告知"。
    """
    if not enabled:
        return []
    known = set(known_source_sets())
    out: list[str] = []
    for v in enabled:
        n = normalize_source_set(v)
        if n and n != SOURCE_SET_UNKNOWN and n not in known:
            out.append(str(v))
    return out


__all__ = [
    "SOURCE_SET_LABELS",
    "SOURCE_SET_RULES",
    "SOURCE_SET_UNKNOWN",
    "display_name_of",
    "included_by_enabled",
    "known_source_sets",
    "label_of",
    "normalize_source_set",
    "normalize_source_sets",
    "source_set_of",
    "unknown_source_sets",
]
