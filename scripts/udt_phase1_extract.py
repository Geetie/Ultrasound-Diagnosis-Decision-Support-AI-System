"""UDT Phase 1 三器官术语草案抽取：从 kb_src_v13 分器官文档（对话+指南）
挖掘中文超声术语（词素匹配 + 频次），产出 phase1 草案词汇表 JSON。

产出: data/udt/phase1_draft_v1.json  {organ: {term: freq}}
这是"审定材料包"的底稿——频次排序供勾选复核。
"""
from __future__ import annotations

import json
import logging
import re
import sys
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)

SRC = Path("/mnt/ee16/home/student03/uai/src")
KB = SRC / "data" / "kb_src_v13"
OUT = SRC / "data" / "udt" / "phase1_draft_v1.json"

MORPHEMES = (
    "回声|结节|肿块|占位|钙化|血流|瓣膜|反流|狭窄|积液|囊肿|结石|增大|萎缩|"
    "肥厚|梗死|纤维化|脂肪肝|血管瘤|转移|淋巴|甲状腺|肝脏|胆|胰腺|"
    "乳头状|滤泡|髓样|未分化|甲功|促甲状腺|心包|心肌|心室|心房|主动脉|"
    "肺动脉|二尖瓣|三尖瓣|射血分数|室间隔|左室|右室|肝右叶|肝左叶|"
    "右叶|左叶|峡部|包膜|边界|形态|纵横比|后方衰减|彗星尾|"
    "肝硬化|门静脉|胆管|胆囊|息肉|腹水"
)

TERM_RE = re.compile(rf"[\u4e00-\u9fff]{{0,4}}(?:{MORPHEMES})[\u4e00-\u9fff]{{0,4}}")
STOP_PREFIX = ("的", "了", "在", "是", "和", "与", "或", "及", "有", "无明显",
               "可见", "考虑", "建议", "提示", "患者", "检查", "超声", "报告")
# 对话语料噪声词素（寒暄/口语/出处残留）
NOISE = ("其实", "总是", "医生", "杂志", "并不可", "得了", "咨询", "回复",
         "问答", "不少", "网友", "很多", "一些", "表示", "认为", "发现是",
         "生说", "淋巴结核", "时间", "问题", "情况", "症状", "治疗方法的",
         "医院", "专家")
MIN_FREQ = 3
MAX_LEN = 12


def extract_organ(organ: str) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for p in (KB / organ).glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("extract_organ: 未预期异常，按原逻辑降级", exc_info=True)
            continue
        text = d.get("content", "") or d.get("text", "")
        for raw in TERM_RE.findall(text):
            t = raw.strip()
            if not 2 <= len(t) <= MAX_LEN:
                continue
            if t.startswith(STOP_PREFIX):
                continue
            if any(n in t for n in NOISE):
                continue
            counter[t] += 1
    return {t: f for t, f in counter.most_common() if f >= MIN_FREQ}


def main() -> int:
    result = {}
    for organ in ("thyroid", "liver", "cardiac"):
        terms = extract_organ(organ)
        result[organ] = terms
        print(f"[udt-draft] {organ}: {len(terms)} terms (freq>={MIN_FREQ})")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print("[udt-draft] ->", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
