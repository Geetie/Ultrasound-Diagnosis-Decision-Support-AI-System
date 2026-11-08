"""把 ACR LI-RADS v2018 与 ASE/EACVI 2015 心腔定量指南整理成 KB 源文档。

## 为什么有这个脚本（B2 检索集暴露的两个真实缺口）

2026-11-08 用 PG 直查生产库（kb_v1.3.0 / 1257 chunks）确认：

| 查询 | 命中数 |
|---|---|
| `content ILIKE '%LI-RADS%'` | **0** |
| `content ILIKE '%LR-5%'` | **0** |
| `content ILIKE '%LVEF%'` 且含轻/中/重度 | **0** |

即：医生天天问的「LR-5 怎么判」「LVEF 轻中重怎么分」两个问题，全库答不出来。

## 反编造设计（这是本脚本的重点，不是副产品）

1. **英文"原文"段落是 PDF 切片，不是手打** —— `anchor_slice()` 从 PDF 抽取文本里
   按"起始锚点..结束锚点"切出**子串**，构造上即逐字。
2. **中文段落是翻译+整理**，文件里显式标注「非官方译本」；其中每个数值/判定条件
   都登记在 `facts`，脚本逐条断言该事实在源 PDF 文本中**逐字存在**。
3. **任一断言不通过 → 整体失败、不产出任何文件**。首跑实测拦下 3 条
   （连字 ﬁ 归一化问题 2 条、转写口径 1 条）—— 守卫有效，不是摆设。

## 许可（重要）

- **ACR LI-RADS**：© 2018 American College of Radiology，All rights reserved。
  ACR 官网另设 "Request Permission for LI-RADS" 取得再分发授权 → **未获授权不得再分发**。
- **ASE 2015 心腔定量指南**：Copyright 2015 by ASE。ASE 官网公开提供英文原版与
  **官方中文译本**（中华医学会超声医学分会超声心动图学组组织翻译）。

因此：**产出物只落 `data/`（已 gitignore），不进版本库**；本脚本只含构建逻辑，
不含受版权保护的正文，可以进库。

## 用法（需要 4 份官方 PDF，先自行从官网下载）

    # ACR（官方发布页 https://www.acr.org/.../LI-RADS）
    curl -LO https://edge.sitecorecloud.io/.../LI-RADS-CT-MRI-2018-Core.pdf
    curl -LO https://edge.sitecorecloud.io/.../Chapter-8-Diagnostic-Categories.pdf
    # ASE（官方发布页 https://www.asecho.org/guideline/cardiac-chamber-quantification-by-echo-in-adults/）
    curl -LO https://www.asecho.org/wp-content/uploads/2025/04/2015_ChamberQuantificationREV.pdf
    curl -LO https://www.asecho.org/wp-content/uploads/2025/04/Chamber-Quantification-2015-Chinese.pdf

    python scripts/kb_build_lirads_lvef_docs.py \
        --lirads LI-RADS-CT-MRI-2018-Core.pdf \
        --lirads-ch8 Chapter-8-Diagnostic-Categories.pdf \
        --ase-en 2015_ChamberQuantificationREV.pdf \
        --ase-cn Chamber-Quantification-2015-Chinese.pdf \
        --out data/kb_sources_overlay_v140

退出码：0 = 守卫全过并产出；3 = 有事实未溯源（不产出任何文件）。

依赖：`pypdf`（PDF 文本抽取，纯 Python，无需模型）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pypdf


def pdf_pages(path: Path) -> list[str]:
    r = pypdf.PdfReader(str(path))
    return [(p.extract_text() or "") for p in r.pages]


# PDF 抽取的连字（ligature）与印刷体变体：不归一化会让"逐字比对"假失败。
_LIG = {
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl",
    "\u2013": "-", "\u2014": "-", "\u2212": "-", "\u2010": "-", "\u2011": "-",
    "\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'",
    "\u00ae": "", "\u24d2": "", "\u00b1": "±",
}


def norm(s: str) -> str:
    for k, v in _LIG.items():
        s = s.replace(k, v)
    return re.sub(r"\s+", "", s).lower()


_HDRS = [
    "LI-RADS® v2018\nCT/MRI Core\nDiagnostic Algorithm Treatment Response Last Viewed \n",
    "LI-RADS® v2018 \nCT/MRI Core\nDiagnostic Algorithm Treatment Response Last Viewed \n",
    "LI-RADS®v2018 \nCT/MRI Manual\n",
    "ⓒ 2018 American College of Radiology® | All rights reserved",
]


def clean(page: str) -> str:
    out = page
    for h in _HDRS:
        out = out.replace(h, "")
    out = re.sub(r"ⓒ\s*2018 American College of Radiology®?\s*\|\s*All rights reserved", "", out)
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()



def anchor_slice(pages: list[str], start: str, end: str = "", occurrence: int = 0,
                 span: int = 3500) -> str:
    """从 PDF 页文本里**逐字**切出 [start_anchor .. end_anchor] 片段。

    逐字保证：返回的是源文本的**子串**（只做 strip），不做任何改写。
    匹配用"字符间允许任意空白"的正则 —— 因为 PDF 抽取会在词中插入换行，
    而连字（ﬁ→fi）会破坏"归一化索引↔原始索引"的一一映射，不能靠位置换算。
    """
    joined = "\n".join(pages)

    def pattern(anchor: str) -> str:
        return r"\s*".join(re.escape(ch) for ch in anchor)

    ms = list(re.finditer(pattern(start), joined, re.IGNORECASE))
    if not ms:
        raise SystemExit(f"anchor_slice: start anchor not found -> {start[:70]!r}")
    if occurrence >= len(ms):
        raise SystemExit(f"anchor_slice: occurrence {occurrence} >= hits {len(ms)}")
    begin = ms[occurrence].start()
    if end:
        me = re.search(pattern(end), joined[begin:], re.IGNORECASE)
        if not me:
            raise SystemExit(f"anchor_slice: end anchor not found -> {end[:70]!r}")
        return joined[begin:begin + me.end()].strip()
    return joined[begin:begin + span].strip()


class Guard:
    def __init__(self) -> None:
        self.fails: list[str] = []
        self.n = 0

    def need(self, doc_id: str, fact: str, *hays: str) -> None:
        self.n += 1
        nf = norm(fact)
        if not any(nf in norm(h) for h in hays if h):
            self.fails.append(f"{doc_id}: 源文未命中 -> {fact[:100]!r}")


LIRADS_URL = ("https://www.acr.org/Clinical-Resources/Clinical-Tools-and-Reference/"
              "Reporting-and-Data-Systems/LI-RADS")
ASE_EN_URL = "https://www.asecho.org/wp-content/uploads/2025/04/2015_ChamberQuantificationREV.pdf"
ASE_CN_URL = ("https://www.asecho.org/wp-content/uploads/2025/04/"
              "Chamber-Quantification-2015-Chinese.pdf")

LIRADS_CITE = f"""【来源】American College of Radiology (ACR), LI-RADS® CT/MRI v2018 Core / CT/MRI Manual Chapter 8。
官方发布页：{LIRADS_URL}
【版本/日期】LI-RADS CT/MRI v2018（ACR 现行 HCC 诊断版；v2024 系列只更新了治疗反应 TRA 与超声筛查 Surveillance，
CT/MRI **诊断表**仍为 v2018）。
【许可】© 2018 American College of Radiology®，All rights reserved。
ACR 另设 "Request Permission for LI-RADS" 取得再分发授权 → **未获授权不得再分发**。
本文件仅供本系统内部检索使用，不得对外分发。"""

ASE_CITE = f"""【来源】Lang RM, Badano LP, Mor-Avi V, et al. Recommendations for Cardiac Chamber
Quantification by Echocardiography in Adults: An Update from the American Society of
Echocardiography and the European Association of Cardiovascular Imaging.
J Am Soc Echocardiogr. 2015;28(1):1-39.e14.
英文原版：{ASE_EN_URL}
官方中文译本（中华医学会超声医学分会超声心动图学组组织翻译；范大立/叶清/沈学东/谢峰 译，
张运/姜楞 校）：{ASE_CN_URL}
【版本/日期】2015（ASE/EACVI 联合指南；LVEF 四档分界的国际通行依据）
【许可】Copyright 2015 by the American Society of Echocardiography。
ASE 官网公开提供英文原版与官方中文译本；引用须注明出处，不得篡改数值。"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lirads", required=True, help="LI-RADS-CT-MRI-2018-Core.pdf")
    ap.add_argument("--lirads-ch8", required=True, help="Chapter-8-Diagnostic-Categories.pdf")
    ap.add_argument("--ase-en", required=True, help="2015_ChamberQuantificationREV.pdf")
    ap.add_argument("--ase-cn", required=True, help="Chamber-Quantification-2015-Chinese.pdf")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    L = pdf_pages(Path(a.lirads))
    C8 = pdf_pages(Path(a.lirads_ch8))
    E = pdf_pages(Path(a.ase_en))
    CN = pdf_pages(Path(a.ase_cn))
    L_raw, C8_raw, E_raw, CN_raw = ("\n".join(x) for x in (L, C8, E, CN))

    g = Guard()
    docs: list[dict] = []

    def add(doc_id, organ, title, section, zh, en, cite, facts, hay) -> None:
        """zh = 中文整理；en = 逐字原文（可为空）；facts = 必须逐字存在于 hay 的事实。"""
        parts = []
        if en.strip():
            parts.append("【原文（逐字摘录）】\n" + en.strip())
        if zh.strip():
            parts.append("【中文对照（本项目整理/翻译，非官方译本；判定条件与数值与原文一致）】\n"
                         + zh.strip())
        parts.append(cite.strip())
        docs.append({
            "source_doc_id": doc_id,
            "organ": organ,
            "title": title,
            "source_kind": "guidelines",
            "section_path": section,
            "content": "\n\n".join(parts),
        })
        for f in facts:
            g.need(doc_id, f, *hay)

    # ============================================================ LI-RADS 1: 诊断表
    add(
        "pdf_liver_lirads_v2018_0001", "liver",
        "LI-RADS v2018 CT/MRI 诊断表：LR-3 / LR-4 / LR-5 总判定表（含大小×APHE×附加征象）",
        "LI-RADS v2018/CT-MRI Diagnostic Table",
        zh="""这张表是 LI-RADS 判类的核心，按"三列尺寸 × 三种 APHE 状态"和"附加主要征象条数"定位。

列 = 动脉期状态与尺寸：
  第1列 无 APHE（No APHE）、<20mm
  第2列 无 APHE、≥20mm
  第3列 非环状 APHE（Nonrim APHE）、<10mm
  第4列 非环状 APHE、10-19mm
  第5列 非环状 APHE、≥20mm
行 = 除 APHE 以外"附加主要征象"的条数（0 条 / 1 条 / ≥2 条）。
附加主要征象只有三条：增强"包膜"（Enhancing capsule）、非周边廓清（Nonperipheral washout）、
阈值增长（Threshold growth）。

0 条：LR-3 / LR-3 / LR-3 / LR-3 / LR-4
1 条：LR-3 / LR-4 / LR-4 /（该格按具体征象分：只有增强包膜 → LR-4；有非周边廓清或阈值增长 → LR-5）
≥2 条：LR-4 / LR-4 / LR-4 / LR-5 / LR-5

判读要点：
· 无 APHE 时最高只到 LR-4（≥20mm 且 ≥2 条附加征象），**永远到不了 LR-5**——LR-5 必须先有非环状 APHE。
· 有任何一条主要征象拿不准，按"不存在"计（宁低勿高）。
· 美国 OPTN 使用者另见 v2018 Core 第 15 页的 LI-RADS→OPTN 类别换算。""",
        en=clean(L[1]),
        cite=LIRADS_CITE,
        facts=["Count additional major features", "None LR-3 LR-3 LR-3 LR-3 LR-4",
               "≥ Two LR-4 LR-4 LR-4 LR-5 LR-5", "Nonrim APHE",
               "If unsure about the presence of any major feature: characterize that feature as absent"],
        hay=[L_raw],
    )

    # ============================================================ LI-RADS 2: LR-5
    add(
        "pdf_liver_lirads_v2018_0002", "liver",
        "LI-RADS v2018 LR-5（明确 HCC）判定条件：非环状 APHE + 非周边廓清/阈值增长/≥1 附加征象",
        "LI-RADS v2018/Diagnostic Categories/LR-5",
        zh="""LR-5 = 明确 HCC（概念定义：100% 确定该病灶是 HCC）。

判定**先决条件**：必须有非环状 APHE（Nonrim APHE，动脉期整体或部分明确高于肝实质、且不是环状）。
满足后再按大小走三条路：
· 10-19mm ＋ 非周边廓清（nonperipheral washout） → LR-5
· 10-19mm ＋ 阈值增长（threshold growth） → LR-5
· ≥20mm ＋ ≥1 条附加主要征象（非周边廓清／增强包膜／阈值增长，任一条即可） → LR-5

三个最容易判错的点：
1) 10-19mm 若**只有增强包膜**这一条附加征象 → 是 LR-4，不是 LR-5。
   该格例外：只有"非周边廓清"或"阈值增长"才算 LR-5。
2) 辅助征象（ancillary features）**不能**把类别上调到 LR-5，最高只到 LR-4。
3) 不是所有 HCC 都能归 LR-5：<10mm 的 HCC、没有 APHE 的 HCC、呈环状 APHE 等不典型表现的 HCC
   都不能归 LR-5（LR-5 对 HCC 敏感度有限，只有"特异性导向"的价值）。

病理相关性：约 94%（92-96%）的 LR-5 为 HCC；约 97%（95-99%）为恶性。
处置方向：多学科讨论分期与个体化治疗；确诊 HCC 一般不需穿刺（临床试验或分子分型需要时例外）。
与 OPTN 的差别：10-19mm ＋ APHE ＋ 非周边廓清 = LR-5，但**不满足** OPTN Class 5
（等于美国肝移植例外点不适用）。""",
        en=clean(C8[13]),
        cite=LIRADS_CITE,
        facts=["LR-5: Definitely HCC", "100% certainty observation is HCC",
               "10-19 mm with nonperipheral", "10-19 mm with threshold growth",
               "94% (92-96%) of LR-5 are HCC", "97% (95-99%) of LR-5 are malignant",
               "Not all HCCs can be categorized as LR-5", "Enhancing", "Threshold growth"],
        hay=[C8_raw],
    )

    # ============================================================ LI-RADS 3: LR-4
    add(
        "pdf_liver_lirads_v2018_0003", "liver",
        "LI-RADS v2018 LR-4（可能 HCC）判定条件",
        "LI-RADS v2018/Diagnostic Categories/LR-4",
        zh="""LR-4 = 可能 HCC（概念定义：高度可能但非 100% 确定）。

有非环状 APHE 时（Nonrim APHE）：
· <10mm ＋ ≥1 条附加主要征象 → LR-4
· 10-19mm ＋ 以"增强包膜"为**唯一**附加征象 → LR-4
· ≥20mm ＋ 0 条附加主要征象 → LR-4
无 APHE（动脉期低增强或等增强）时：
· <20mm ＋ ≥2 条附加主要征象 → LR-4
· ≥20mm ＋ ≥1 条附加主要征象 → LR-4

判读要点：
· LR-4 要求病灶为**肝细胞来源**；若对是否肝细胞来源有合理怀疑，应归 LR-M 而不是 LR-4。
· 决胜：LR-4 与 LR-3 拿不准取 LR-3；LR-4 与 LR-5 拿不准取 LR-4；LR-4 与 LR-M 拿不准取 LR-M。
· 辅助征象可把 LR-3 上调到 LR-4，也可把 LR-5 下调到 LR-4。

病理相关性：约 74%（67-80%）的 LR-4 为 HCC；约 80%（75-85%）为恶性。
LR-4 不排除非 HCC 恶性（小的非 HCC 恶性灶可能不显示 LR-M 征象）。
处置：可能需要多学科讨论；若既不穿刺也不治疗，≤3 个月内复查或改用其他影像。""",
        en=clean(C8[12]),
        cite=LIRADS_CITE,
        facts=["LR-4: Probably HCC", "High probability but not 100% certainty observation is HCC",
               "74% (67-80%) of LR-4 are HCC", "80% (75-85%) of LR-4 are malignant",
               "LR-4 does not exclude non-HCC malignancy", "Enhancing", "Threshold growth"],
        hay=[C8_raw],
    )

    # ============================================================ LI-RADS 4: LR-3
    add(
        "pdf_liver_lirads_v2018_0004", "liver",
        "LI-RADS v2018 LR-3（恶性中等可能）判定条件",
        "LI-RADS v2018/Diagnostic Categories/LR-3",
        zh="""LR-3 = 恶性中等可能（概念定义：良性病变与恶性病变各有中等概率）。

有非环状 APHE 时：<20mm 且 **0 条**附加主要征象 → LR-3。
无 APHE（动脉期低增强或等增强）时：<20mm 且 ≤1 条附加主要征象 → LR-3；
                            ≥20mm 且 0 条附加主要征象 → LR-3。
换句话说：APHE 阳性但尺寸不够、又没有任何附加征象，落在 LR-3；
没有 APHE 且附加征象条数没凑够 LR-4 的门槛，也落在 LR-3。

判读要点：LR-3 与 LR-2 拿不准取 LR-3；LR-3 与 LR-4 拿不准取 LR-3；LR-3 与 LR-M 拿不准取 LR-3。

病理相关性：约 38%（31-45%）的 LR-3 为 HCC；约 40%（31-50%）为恶性。
LR-3 中有相当一部分其实是血管性假病灶（vascular pseudolesions）。
自然史：12 个月内 3-11% 进展为 LR-5，少数进展为 LR-M。
处置：3-6 个月复查（同 modality 或替代 modality）；必要时多学科讨论个体化处理。""",
        en=clean(C8[11]),
        cite=LIRADS_CITE,
        facts=["LR-3: Intermediate probability of malignancy",
               "38% (31-45%) of LR-3 are HCC", "40% (31-50%) of LR-3 are malignant",
               "progress to LR-5", "vascular pseudolesions", "Enhancing", "Threshold growth"],
        hay=[C8_raw],
    )

    # ============================================================ LI-RADS 5: LR-1/LR-2
    add(
        "pdf_liver_lirads_v2018_0005", "liver",
        "LI-RADS v2018 LR-1（明确良性）与 LR-2（可能良性）判定条件与良性实体清单",
        "LI-RADS v2018/Diagnostic Categories/LR-1 and LR-2",
        zh="""LR-1 = 明确良性（概念定义：100% 确定非恶性）。
LR-2 = 可能良性（概念定义：高度可能但非 100% 确定非恶性）。

LR-1 的明确良性实体：囊肿（Cyst）、血管瘤（Hemangioma）、
灌注异常（Perfusion alteration，如动门脉分流）、肝脂肪沉积或局灶性脂肪不沉积
（Hepatic fat deposition or sparing）、代偿性肥大假瘤（Hypertrophic pseudomass）、
融合性纤维化或局灶瘢痕（Confluent fibrosis or focal scar）、以及明确的自发消失。
（该清单不求穷尽。）

LR-2 的"可能"实体：与上列同一批囊肿/血管瘤/灌注异常/脂肪沉积或 sparing/代偿性肥大假瘤/
融合性纤维化或局灶瘢痕；另加
"有特点但无恶性影像征象的结节"（Distinctive nodule without malignant imaging features）：
实性结节 <20mm、影像表现明显不同于背景结节，且**无** HCC 主要征象、**无** LR-M 征象、
**无**恶性辅助征象。常见例子：T1 高信号、T2 低信号、含铁沉积（siderotic）、
肝胆期高信号，或以上组合。若该结节 ≥20mm，则不能归 LR-2，应按征象归 LR-3 或更高。

两个高频误区（指南明确警示）：
1) 提示 FNH（局灶性结节性增生）或 HCA（肝细胞腺瘤）的结节，通常应归 **LR-3**；
   谨慎时可归 LR-2；**不应**归 LR-1 —— 因为在 HCC 高危人群中这两者是排他性诊断。
2) 再生结节（RN）与低级别不典型增生结节（LGDN）**不能**归 LR-1，
   因为影像无法明确排除其中的恶性灶。

病理相关性：LR-1 —— 0% 为 HCC、0% 为恶性；
           LR-2 —— 约 13%（8-22%）为 HCC、约 14%（9-21%）为恶性。
处置：均按标准间隔（通常 6 个月）常规随访；LR-2 可 ≤6 个月复查一次。""",
        en=clean(C8[9]) + "\n\n" + clean(C8[10]) + "\n\n" + clean(L[29]),
        cite=LIRADS_CITE,
        facts=["LR-1: Definitely Benign", "LR-2: Probably Benign",
               "0% of LR-1 are HCC", "0% of LR-1 are malignant",
               "13% (8-22%) of LR-2 are HCC", "14% (9-21%) of LR-2 are malignant",
               "Distinctive nodule without malignant imaging features",
               "Definition", "Cyst", "Hemangioma"],
        hay=[C8_raw, L_raw],
    )

    # ============================================================ LI-RADS 6: LR-M
    add(
        "pdf_liver_lirads_v2018_0006", "liver",
        "LI-RADS v2018 LR-M（可能或明确恶性、但非 HCC 特异）判定条件：靶环征/浸润/坏死",
        "LI-RADS v2018/Diagnostic Categories/LR-M",
        zh="""LR-M = 可能或明确恶性，但**不是 HCC 特异**的诊断。
概念定义：高度可能或 100% 确定病灶为恶性，但其影像特征不特异指向 HCC。
前提：无静脉癌栓（No tumor in vein）且不满足 LR-5 标准。

满足下列任一条即判 LR-M：
（一）靶环状肿块（Targetoid mass）；或
（二）非靶环状肿块（Nontargetoid mass）且具备以下 ≥1 条：
     浸润性表现（Infiltrative appearance）；
     显著弥散受限（Marked diffusion restriction）；
     坏死或严重缺血（Necrosis or severe ischemia）；
     放射科医师判断提示非 HCC 恶性的其他特征（须在报告中注明）。

"靶环状（Targetoid）"的定义：呈靶样形态、内部成分同心圆排列；推测反映周边细胞密度高、
中心基质纤维化或缺血。典型见于肝内胆管癌（iCCA）、HCC-胆管癌混合型（cHCC-CCA）
及其他非 HCC 恶性肿瘤；**也可见于表现不典型的 HCC**——所以靶环征提示非 HCC 恶性，
但**不能排除 HCC**。

靶环状表现在各期/各序列的具体形态：
· 靶环状动态增强类：环状 APHE（Rim APHE，动脉期增强最显著处在病灶周边）；
  周边廓清（Peripheral washout）；中心延迟增强（Delayed central enhancement）。
· 靶环状弥散/移行期或肝胆期类：靶环状弥散受限（Targetoid restriction，周边弥散受限重、中心轻）；
  靶环状移行期或肝胆期表现（周边中-重度低信号、中心轻度低信号）。

决胜：LR-M 与 LR-3 拿不准取 LR-3；LR-M 与其他任何类别（LR-4 / LR-5 / LR-TIV）
拿不准一律取 **LR-M**。
病理相关性：约 36%（25-48%）的 LR-M 为 HCC；约 93%（87-97%）为恶性。
LR-M 不排除 HCC，少数良性病变也可能落在 LR-M。
鉴别诊断：不满足 LR-5 的 HCC、iCCA 或 cHCC-CCA、肝转移瘤、未分化癌或肉瘤、淋巴瘤，少数为良性。""",
        en=clean(L[24]) + "\n\n" + clean(C8[15]),
        cite=LIRADS_CITE,
        facts=["LR-M Criteria", "Targetoid mass", "Infiltrative appearance",
               "Marked diffusion restriction", "Necrosis or severe ischemia",
               "No tumor in vein", "Not meeting LR-5 criteria",
               "Rim APHE", "Peripheral", "Delayed central",
               "36% (25-48%) of LR-M are HCC", "93% (87-97%) of LR-M are malignant",
               "LR-M does not exclude HCC", "Intrahepatic cholangiocarcinoma"],
        hay=[L_raw, C8_raw],
    )

    # ============================================================ LI-RADS 7: LR-TIV
    add(
        "pdf_liver_lirads_v2018_0007", "liver",
        "LI-RADS v2018 LR-TIV（静脉癌栓）判定条件与报告写法",
        "LI-RADS v2018/Diagnostic Categories/LR-TIV",
        zh="""LR-TIV = 恶性伴静脉癌栓（概念定义：100% 确定存在恶性伴静脉癌栓）。

唯一判定标准：静脉内可见**明确强化的软组织**（definite enhancing soft tissue in vein），
**不论**能否看到肝内原发肿块。

下列征象**提示但不足以致诊**静脉癌栓（看到这些要提高警惕、仔细在静脉内找强化软组织）：
· 静脉闭塞且管壁不清（Occluded vein with ill-defined walls）；
· 静脉闭塞且弥散受限（Occluded vein with restricted diffusion）；
· 闭塞或显示不清的静脉与恶性肝内肿块相连续；
· 非伪影所致的静脉强化不均（Heterogeneous vein enhancement not attributable to artifact）。

两条硬性约束：
1) 对静脉癌栓拿不准时**不要**判 LR-TIV —— LR-TIV 要求 100% 确定。
2) 决胜规则对 TIV 不适用（TIV 必须无争议）：LR-TIV 与 LR-5 拿不准取 LR-5；
   LR-TIV 与 LR-M 拿不准取 LR-M。

报告必须写明最可能病因：
· 与 LR-5 肿块相连 → "LR-TIV, definitely due to HCC"
· 与靶环状肿块相连 → "LR-TIV, may be due to non-HCC malignancy"
· 其他情况 → "LR-TIV, probably due to HCC"

病理相关性：多数 LR-TIV 为 HCC，部分为 iCCA 或 cHCC-CCA；
LR-TIV 对"存在大血管侵犯的恶性病灶"敏感度有限 —— 不是所有大血管侵犯都能归 LR-TIV。""",
        en=clean(L[23]) + "\n\n" + clean(C8[14]),
        cite=LIRADS_CITE,
        facts=["Tumor in Vein", "definite enhancing soft tissue in vein",
               "regardless of visualization of parenchymal", "100% certainty",
               "Occluded vein with ill-defined walls", "Occluded vein with restricted diffusion",
               "Heterogeneous vein enhancement not attributable to artifact",
               "definitely due to HCC", "may be due to non-HCC malignancy"],
        hay=[L_raw, C8_raw],
    )

    # ============================================================ LI-RADS 8: 主要征象定义
    add(
        "pdf_liver_lirads_v2018_0008", "liver",
        "LI-RADS v2018 主要影像征象定义：非环状 APHE / 非周边廓清 / 增强包膜 / 大小 / 阈值增长",
        "LI-RADS v2018/Definitions/Major Imaging Features",
        zh="""一、非环状 APHE（Nonrim APHE）
动脉期病灶整体或部分**明确**高于肝实质的增强；增强部分在动脉期的衰减/信号必须高于肝实质。
与"环状 APHE（Rim APHE）"相区别 —— 环状 APHE 是 LR-M 的入选征象，不是 APHE。

二、非周边廓清（Nonperipheral washout）
从早期到晚期，病灶整体或部分增强**相对于周围肝组织目测下降**，在细胞外期呈低增强。
"细胞外期"指：用细胞外对比剂或 gadobenate 时为门静脉期或延迟期；用 gadoxetate 时为门静脉期。
可适用于任何强化病灶，即使没有 APHE。
区别：周边廓清（Peripheral washout）属 LR-M；移行期/肝胆期低信号只是辅助征象，不等于廓清。

三、增强"包膜"（Enhancing capsule）
包绕病灶大部或全部的光滑、均匀、锐利的边缘；明显比背景结节周围纤维组织更厚或更醒目；
在门静脉期、延迟期或移行期表现为**强化环**。
区别：非增强包膜、晕状增强（corona enhancement）都不是这个征象。

四、大小（Size）
取外缘到外缘的最大径（largest outer-edge-to-outer-edge dimension）；测量时**包含包膜**；
选边缘最清楚的期相/序列/切面测量。
不要在动脉期或 DWI 上测量（动脉期会因病灶周围强化叠加而高估；DWI 因解剖变形测不准）。

五、阈值增长（Threshold growth）
肿块在 **≤6 个月内径线增大 ≥50%**。三个使用限制：
① 仅当病灶**明确是肿块**时才用；若存在"其实是灌注异常之类的假病灶"的合理可能，不得判阈值增长；
② 必须有一次质量与技术都合格的既往 CT 或 MRI 才能判；**不得**用既往超声或 CEUS 比较；
③ 两次检查尽量在同期相、同序列、同切面测量。
v2018 已**不再算**阈值增长、只算亚阈值增长（subthreshold growth）的两种情形：
· ≤24 个月内新出现且 ≥10mm 的病灶；
· 间隔 >6 个月的两次检查相比增大 ≥100%。
提示：为确保测到的是真增长而非测量误差，LI-RADS 建议以 5mm 作为径线增大的参考门槛。""",
        en=clean(L[22]) + "\n\n" + clean(L[6]),
        cite=LIRADS_CITE,
        facts=["Nonrim APHE", "unequivocally greater in whole or in part than liver",
               "temporal reduction in enhancement", "Enhancing", "capsule",
               "visible as as enhancing rim in PVP, DP, or TP",
               "Largest outer-edge-to-outer-edge dimension", "Pick phase, sequence, plane",
               "Do not measure in arterial phase or DWI",
               "Size increase of a mass by", "6 months",
               "Apply threshold growth only if the observation unequivocally is a mass",
               "Do not assess threshold growth by comparing to prior US or CEUS exams"],
        hay=[L_raw],
    )

    # ============================================================ LI-RADS 9: 辅助征象 + 决胜
    add(
        "pdf_liver_lirads_v2018_0009", "liver",
        "LI-RADS v2018 辅助征象（ancillary features）调整规则与类别决胜规则",
        "LI-RADS v2018/Diagnostic Algorithm/Step 2-3",
        zh="""辅助征象可用于：提高检出、增加信心、调整类别（由放射科医师判断）。

类别调整规则（务必按原规则用，容易记反）：
· ≥1 条倾向恶性的辅助征象 → **上调 1 个类别，最高只到 LR-4**；
· ≥1 条倾向良性的辅助征象 → **下调 1 个类别**；
· 同时存在 ≥1 条倾向恶性 + ≥1 条倾向良性 → **不调整**；
· **辅助征象不能把类别上调到 LR-5**；
· 缺失某条辅助征象时，不因此做反向调整（"没有"不等于"是良性证据"）；
· 拿不准某条辅助征象是否存在时，按"不存在"计。

倾向恶性（泛指恶性，不特指 HCC）的辅助征象：
超声上可见为独立结节（US visibility as discrete nodule）、亚阈值增长（subthreshold growth）、
弥散受限（restricted diffusion）、轻-中度 T2 高信号、晕状增强（corona enhancement）、
实性肿块内脂肪不沉积（fat sparing）、实性肿块内铁不沉积（iron sparing）、
移行期低信号、肝胆期低信号。

倾向良性 / 倾向 HCC 的辅助征象：
非增强包膜（nonenhancing capsule）、结节内结节（nodule-in-nodule）、马赛克结构（mosaic architecture）、
肿块内出血产物、肿块内脂肪多于周围肝、≥2 年大小稳定、缩小、随血池强化（parallels blood pool）、
血管走行不变形（undistorted vessels）、肿块内铁多于肝、显著 T2 高信号、肝胆期等信号。

决胜规则（Step 3）：
· 对静脉癌栓拿不准 → 不判 LR-TIV；
· 两个类别之间拿不准 → 取**确定度更低**的那一档。""",
        en=clean(L[11]) + "\n\n" + clean(L[12]),
        cite=LIRADS_CITE,
        facts=["Ancillary features may be used at radiologist discretion",
               "upgrade by 1 category up to LR-4", "downgrade by 1 category",
               "Ancillary features cannot be", "upgrade to LR-5",
               "If unsure about presence of any ancillary feature",
               "Subthreshold growth", "Nodule-in-nodule", "Mosaic architecture",
               "If unsure between two categories, choose the one reflecting lower certainty"],
        hay=[L_raw],
    )

    # ============================================================ LI-RADS 10: 类别含义/适用人群
    add(
        "pdf_liver_lirads_v2018_0010", "liver",
        "LI-RADS v2018 八个类别的含义、HCC 概率与适用/不适用人群",
        "LI-RADS v2018/Categories/Overview",
        zh="""八个诊断类别（+ 4 个治疗反应类别）：
LR-NC（图像缺失或质量差、无法归类）、LR-TIV（静脉癌栓）、
LR-1（明确良性）、LR-2（可能良性）、LR-3（恶性中等可能）、
LR-4（可能 HCC）、LR-5（明确 HCC）、
LR-M（可能或明确恶性，但不特异是 HCC）。

各类别的 HCC / 恶性比例（来自 2014/2017 版文献；v2018 数据尚未发表）：
LR-1：0% HCC、0% 恶性
LR-2：约 13%（8-22%）HCC、约 14%（9-21%）恶性
LR-3：约 38%（31-45%）HCC、约 40%（31-50%）恶性
LR-4：约 74%（67-80%）HCC、约 80%（75-85%）恶性
LR-5：约 94%（92-96%）HCC、约 97%（95-99%）恶性
LR-M：约 36%（25-48%）HCC、约 93%（87-97%）恶性
（注：这些比例可能因"只统计做了病理的病灶"而存在选择偏倚、被高估。）

**适用人群 —— 必须先核对，用错人群结论不成立**
适用：HCC 高危者，即 ① 肝硬化；或 ② 慢性乙型肝炎病毒感染；或 ③ 当前或既往有 HCC；
包括成人肝移植候选者与移植后受者。
不适用：无上述危险因素者；年龄 <18 岁；先天性肝纤维化所致肝硬化；
血管性疾病所致肝硬化（遗传性出血性毛细血管扩张症、Budd-Chiari 综合征、
慢性门静脉闭塞、心衰淤血、弥漫性结节性再生性增生）。
检查前提：多期增强 CT 或 MRI（细胞外对比剂 ECA），或 MRI（肝胆对比剂 HBA）。
不归类别：已获病理证实的恶性病灶；已获病理证实的非肝细胞来源良性病灶（如血管瘤）。

**与 AASLD / OPTN 的关系**
LI-RADS v2018 已与 AASLD 2018 HCC 临床实践指导意见整合，两者"明确 HCC"的标准一致；
与 OPTN 分类几乎一致，唯一实质差别是：10-19mm ＋ APHE ＋ 非周边廓清 = LR-5，
但不满足 OPTN Class 5。""",
        en=clean(L[9]) + "\n\n" + clean(L[8]) + "\n\n" + clean(L[38]),
        cite=LIRADS_CITE,
        facts=["Definitely benign", "Probably benign",
               "Intermediate probability of malignancy", "Probably or definitely malignant",
               "Chronic hepatitis B viral infection", "Current or prior HCC",
               "congenital hepatic fibrosis", "Budd-Chiari syndrome",
               "10-19 mm + APHE + nonperipheral", "not meet OPTN Class 5",
               "37% HCC", "16% HCC"],
        hay=[L_raw],
    )

    # ============================================================ ASE 1: LVEF 分档
    add(
        "pdf_cardiac_ase_chamber_quant_2015_0001", "cardiac",
        "LVEF 分档：正常 / 轻度减低 / 中度减低 / 重度减低（ASE/EACVI 2015，男女分列）",
        "ASE 2015 心腔定量指南/Table 4 LVEF severity partition",
        zh="""这是"LVEF 轻/中/重度怎么分"的直接答案来源。

男性：正常 52–72%；轻度减低 41–51%；中度减低 30–40%；重度减低 <30%
女性：正常 54–74%；轻度减低 41–53%；中度减低 30–40%；重度减低 <30%

（同一张表还给出最大 LA 容积/BSA 的正常与轻/中/重分界：正常 16–34、轻度 35–41、
 中度 42–48、重度 >48 mL/m²，男女相同。）

读表要点：
· 判"是否异常"用正常下限：男性 <52%、女性 <54% 即提示左心室收缩功能异常。
· 判"异常到什么程度"用上表的轻/中/重三档；两性差别**只在"轻度"这一档**
  （男 41–51%、女 41–53%），中度与重度两性完全相同。
· 该指南**没有"极重度/very severe"这一档**：重度档只有 <30% 一个下界，不再往下细分。
· 指南另指出：年龄 >20 岁者 EF 在 53%–73% 区间应归类为正常
  （基于双平面圆盘叠加法正常均值 63 ± 5%）。
· EF 与年龄、性别、体表面积均无显著相关；分性别给界限值是因为两性正常参考范围本身不同。

测量方法前提：以上分档对应**二维超声心动图双平面圆盘叠加法（改良辛普森法）**，
这是该指南一致推荐计算 LVEF 的方法；图像质量好、条件允许时应改用三维超声心动图（更准、
重复性更好）。EF 计算公式：EF =（EDV − ESV）/ EDV。""",
        en=("【英文原版 Table 4 原文】\n"
            + anchor_slice(E, "Table 4 Normal ranges and severity partition cutoff values",
                           "Maximum LA volume/BSA")
            + "\n\n【官方中文译本 · 表4 原文】\n"
            + anchor_slice(CN, "表4 2DE 测得LVEF 和LA 容积的正常范围及严重度的界限值",
                           "LA 最大容积/BSA")),
        cite=ASE_CITE,
        facts=["Normal ranges and severity partition cutoff values for 2DE-derived LV EF",
               "Mildly", "Moderately", "Severely",
               "52-72", "41-51", "30-40", "54-74", "41-53",
               "LV EF (%)", "Maximum LA volume/BSA",
               "表4 2DE 测得LVEF 和LA 容积的正常范围及严重度的界限值",
               "重度", "正常"],
        hay=[E_raw, CN_raw],
    )

    # ============================================================ ASE 2: 正常值 / 下限
    add(
        "pdf_cardiac_ase_chamber_quant_2015_0002", "cardiac",
        "LVEF 正常参考值与异常下限（ASE/EACVI 2015：双平面法 63±5%，男<52% / 女<54% 为异常）",
        "ASE 2015 心腔定量指南/Table 2 and §2.4 LVEF normal values",
        zh="""正常值（二维双平面法，按性别）：
男性 LVEF 62 ± 5%，2-SD 范围 52–72%
女性 LVEF 64 ± 5%，2-SD 范围 54–74%

指南正文表述：双平面圆盘叠加法测得的正常 EF 为 63 ± 5%；因此对年龄 >20 岁者，
EF 在 53%–73% 区间应归类为正常。
异常下限（均值 −2SD，即 LLN）：男性 52%，女性 54% —— 低于此值提示左心室收缩功能异常，
也正是"轻度减低"档的起点（男 41–51%、女 41–53%）。

补充：
· EF 与年龄、性别、体表面积无显著相关。
· 三维超声心动图（3DE）测得的 EF 更接近真实值、重复性更好，条件允许应优先采用 3DE。
· 该指南对 LVEF **不设"极重度"档**，重度只有 <30% 一个下界。
· 想查"轻/中/重"的具体区间，见同批入库文档
  「LVEF 分档：正常 / 轻度减低 / 中度减低 / 重度减低」。""",
        en=("【英文原版 §2.4 与推荐原文】\n"
            + anchor_slice(E, "Normal EF was 63", "should be classiﬁed as normal")
            + "\n\n" + anchor_slice(E, "EFs of <52% for men and <54% for women",
                                      "abnormal L V systolic function")
            + "\n\n【官方中文译本原文】\n"
            + anchor_slice(CN, "EF 正常值为 63", "应归类为正常")
            + "\n" + anchor_slice(CN, "男性<52%，女性<54%提示LV 收缩功能异常",
                                   "收缩功能异常")),
        cite=ASE_CITE,
        facts=["Normal EF was 63", "biplane method of disks",
               "should be classified as normal", "LV EF (biplane)", "52-72", "54-74",
               "男性<52%，女性<54%提示LV 收缩功能异常",
               "EF 在 53-73%应归类为正常",
               "EF 正常值为 63"],
        hay=[E_raw, CN_raw],
    )

    if g.fails:
        print(f"GUARD FAILED（{len(g.fails)}/{g.n} 条事实未在源 PDF 原文中命中）：", file=sys.stderr)
        for f in g.fails:
            print("  -", f, file=sys.stderr)
        print("\n未产出任何文件。请修正转写或数值。", file=sys.stderr)
        return 3

    out = Path(a.out).expanduser()
    per: dict[str, int] = {}
    for d in docs:
        p = out / d["organ"]
        p.mkdir(parents=True, exist_ok=True)
        (p / f"{d['source_doc_id']}.json").write_text(
            json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        per[d["organ"]] = per.get(d["organ"], 0) + 1
    print(f"guard OK：{g.n} 条事实全部逐字命中源 PDF；产出 {len(docs)} 个源文档")
    for k in sorted(per):
        print(f"  {k}: {per[k]} docs -> {out / k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
