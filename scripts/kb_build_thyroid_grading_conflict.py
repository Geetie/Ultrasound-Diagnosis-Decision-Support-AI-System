"""把「甲状腺分级两套体系冲突」显式建成一份知识文档（2026-11-09）。

## 为什么要建这份

知识库里同时存在两套甲状腺分级体系，**风险区间与 FNA 阈值不一致**：

| 项 | C-TIRADS 2021 | 甲状腺癌诊疗规范 2018 |
|---|---|---|
| 3 类恶性风险 | **<2%** | **<5%** |
| 4a 恶性风险 | **2%~10%** | **5%~10%** |
| 4b | 10%~50% | 10%~50% |
| 4c | 50%~90% | 50%~85% |
| 5 类 | **>90%** | **85%~100%** |
| 分类方法 | **计数法**（积几分） | **征象数法**（几种恶性征象） |
| 4A 类 FNA 阈值 | **>15 mm** | **>1 cm（10 mm）** |

两份都是权威中文指南，**没有哪一份"错"**，是两套体系。

## 不显式并存的后果

- 模型检索到 A 就报 A，检索到 B 就报 B → **同一问题两次回答给出不同穿刺建议**
- 医生看到的答案取决于哪份 chunk 排第一 —— 这是**不可接受的随机性**

## 做法

建一份「对照表」文档，让模型一次看到两套口径与**它们的适用场景**：
- C-TIRADS 2021：中华医学会超声医学分会，**计数法**，面向超声科日常报告
- 诊疗规范 2018：国家卫健委，**征象数法**，面向甲状腺癌诊疗全流程

**这两段原文逐字取自各自 PDF**，不做改写。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

#: 两段原文的锚点（逐字校验，防编造）
CTIRADS_ANCHORS = ("表1 基于计数法的", "4 A 低度可疑恶性", "2~1 0")
NORM_ANCHORS = ("4a", "具有 1 种恶性征象", "5%~10%")


def extract(pdf: Path) -> str:
    from pypdf import PdfReader

    return "\n".join((pg.extract_text() or "") for pg in PdfReader(str(pdf)).pages)


def norm(s: str) -> str:
    s = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", s)
    return re.sub(r"\s{2,}", " ", s).strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ctirads", default=str(Path.home() / "uai/data/corpus/raw/ctirads_2021.pdf"))
    ap.add_argument("--norm2018", default=str(Path.home() / "uai/data/corpus/raw/thyroid_cancer_norm_2018.pdf"))
    ap.add_argument("--out", default=str(Path.home() / "uai/data/kb_docs/thyroid_grading_conflict.jsonl"))
    args = ap.parse_args(argv)

    ct = extract(Path(args.ctirads).expanduser())
    nm = extract(Path(args.norm2018).expanduser())

    # 校验锚点
    missing = [a for a in CTIRADS_ANCHORS if a not in ct]
    if missing:
        raise SystemExit(f"C-TIRADS 锚点缺失 {missing} —— 拒绝产出")
    missing = [a for a in NORM_ANCHORS if a not in nm]
    if missing:
        raise SystemExit(f"诊疗规范 锚点缺失 {missing} —— 拒绝产出")

    i = ct.find("表1 基于计数法的")
    ct_seg = norm(ct[i : i + 400])
    j = nm.find("4a")
    # 往前退到表头
    j = max(0, nm.rfind("分类", 0, j) if nm.rfind("分类", 0, j) > 0 else j)
    nm_seg = norm(nm[j : j + 500])

    doc = {
        "source_doc_id": "thyroid_grading_two_system_conflict",
        "title": "甲状腺分级两套体系对照：C-TIRADS 2021 vs 甲状腺癌诊疗规范 2018",
        "source_kind": "cross_reference",
        "section_path": "甲状腺 / 分级体系对照",
        "organ": "thyroid",
        "content": (
            "# 甲状腺分级：两套权威体系并存，风险区间与穿刺阈值不一致\n\n"
            "中国有两套并行的甲状腺结节危险分层体系，**都是权威来源，但口径不同**。\n"
            "回答分级相关问题时，应先说明用的是哪一套。\n\n"
            "## 对照表\n\n"
            "| 项 | C-TIRADS 2021（中华医学会超声分会） | 甲状腺癌诊疗规范 2018（国家卫健委） |\n"
            "|---|---|---|\n"
            "| 分类方法 | **计数法**：可疑恶性征象计数得分 | **征象数法**：按恶性征象种数 |\n"
            "| 3 类恶性风险 | **<2%** | **<5%** |\n"
            "| 4a 类恶性风险 | **2%~10%** | **5%~10%** |\n"
            "| 4b 类恶性风险 | 10%~50% | 10%~50% |\n"
            "| 4c 类恶性风险 | 50%~90% | 50%~85% |\n"
            "| 5 类恶性风险 | **>90%** | **85%~100%** |\n"
            "| 4A 类 FNA 阈值 | **>15 mm**（紧邻被膜/多灶 >10 mm） | **>1 cm（10 mm）** |\n"
            "| 适用场景 | 超声科日常报告与分级 | 甲状腺癌诊疗全流程（筛查到随访） |\n\n"
            "## 计数法的计分规则（C-TIRADS）\n\n"
            "可疑恶性特征（各计 1 分）：实性、微钙化、极低回声、边缘模糊、"
            "边缘不规则或甲状腺外侵犯、垂直位。\n"
            "彗星尾伪像为良性特征，总分减 1。\n\n"
            "## 原文：C-TIRADS 2021 表 1\n\n"
            f"{ct_seg}\n\n"
            "## 原文：甲状腺癌诊疗规范 2018 表 1\n\n"
            f"{nm_seg}"
        ),
        "license_note": "两份均为中国公开发布的临床指南/规范；仅内部检索使用",
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"已写 → {out}（{len(doc['content'])} 字符）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
