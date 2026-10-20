"""模型输出守卫：拦截「复述提示词示例」这类脏输出（A.70 / R17）。

**为什么需要**：A.70 真机核查发现，评测集里 **98.7%（155/169）的"结论"其实是提示词示例的照抄** ——
`app/agents/prompts/{thyroid,cardiac,liver}.md` 的「输出」段放了完整示例答案，小模型（1.5B/7B）
在长中文 prompt 下把示例当成要复述的内容，于是输出里出现：

    证据: chunk_a1b2, chunk_c3d4 … 患者PATIENT_001: 该结节为良性
    # 输出 分级: TR2 …（同一段重复 4 次，每次分级不同）

`chunk_a1b2` / `chunk_c3d4` / `PATIENT_001` 都是**模板占位符**（真实证据 ID 形如
`kb_v1.3.0:pdf_..._0003#0000`）。这类输出一旦入库，医生会看到模板文字、评测会被污染。

**本模块只做"识别"**（纯函数、零依赖）：命中即由调用方**拒收并降级**（转人工复核），
不改 prompt、不改任何阈值 —— 修 prompt 属于另一个决策（A.70.1 的方案 A/C）。
"""

from __future__ import annotations

import re

#: 模板里的假 chunk ID（`chunk_a1b2` 形态；真 ID 一定带 `#数字` 后缀或 `kb_v...:` 前缀）
_PLACEHOLDER_CHUNK = re.compile(r"chunk_[a-z0-9]{4}\b")

#: 模板里的假病人 ID
_PLACEHOLDER_PATIENT = re.compile(r"PATIENT_\d+")

#: 模板的「输出」段标题（正常结论不会出现这个 markdown 标题）
_OUTPUT_HEADER = re.compile(r"#\s*输出")

#: 命中几次「# 输出」才算复述（正常结论里出现一次都罕见；两次以上必是照抄）
_OUTPUT_HEADER_MIN = 2


def detect_prompt_echo(text: str) -> str | None:
    """判断模型输出是否在复述提示词示例。纯函数。

    Returns:
        命中时返回**原因码**（用于日志与 trace），未命中返回 ``None``：

        * ``placeholder_chunk_id`` —— 出现 `chunk_xxxx` 这类占位证据 ID；
        * ``placeholder_patient_id`` —— 出现 `PATIENT_001` 这类占位病人；
        * ``repeated_output_header`` —— `# 输出` 出现 ≥2 次（同一段被反复复述）。
    """
    t = text or ""
    if _PLACEHOLDER_CHUNK.search(t):
        return "placeholder_chunk_id"
    if _PLACEHOLDER_PATIENT.search(t):
        return "placeholder_patient_id"
    if len(_OUTPUT_HEADER.findall(t)) >= _OUTPUT_HEADER_MIN:
        return "repeated_output_header"
    return None


def has_real_evidence_id(text: str, evidence_ids: list[str]) -> bool:
    """输出里是否引用了**本次真实证据**的 ID（用于"引用可信度"诊断，不作拦截条件）。

    只做精确子串匹配：真实 ID 形如 `kb_v1.3.0:pdf_x_0001#0000`，被模型原样引用才算。
    """
    return any(ev and ev in (text or "") for ev in evidence_ids or [])
