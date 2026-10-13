"""结论正文的**医生可读**文案合成（A.59）。

背景（真机取证）
----------------
p28 的结论区断言实测到医生可见正文是::

    [cardiac] LVEF=unknown, severity=normal — …

—— 器官代码用英文方括号、键值对是英文（`LVEF=` / `severity=`）、枚举值也是英文
（`unknown` / `normal`）；降级分支（零 RAG / LLM 不可用 / 解析失败）更直接把::

    [thyroid] evidence_insufficient: no relevant knowledge retrieved; manual review required

当**结论正文**。同一形态在 A.56 已在**分级**链路修过一次（英文技术串移出医生可见正文），
但器官代码、心脏的 LVEF 键值与三处降级串仍在（A.58.10① 当初误记为「安全门」问题，
A.59 更正为**专科正文**问题 —— 安全门那句「检测到禁忌条件：检测到4条禁忌」本来就是中文）。

口径（三条，改这里前先读）
--------------------------
1. **正文是给医生看的**：中文、可读、**不含**英文键值对与器官代码；机器要的值走
   结构化字段与 ``reasoning_trace``（那里保留英文标识便于审计与对账，**不要**搬过来）。
2. **可读 ≠ 改写**：只做「代码 → 中文名」「英文枚举 → 中文」的**等价映射**；
   分级简写（``TR3`` / ``LR-5``）是临床标准表达，**原样保留**。
3. **必须仍可被下游解析**：``app/fusion/consistency.py`` 会从正文抽 LVEF 数值
   （正则 ``(?:LVEF|EF|射血分数)\\s*[:=]?\\s*(\\d{1,3})``）→ 数值形态固定写成
   「左室射血分数（LVEF）62%」（``LVEF`` 后接**空格**再接数字，正则可命中）；
   分级标记 ``TR3`` 等保持原样（``_extract_grade_label`` 依赖）。

降级文案统一以「已转人工判读」收尾（与 A.56 的分级降级、A.58 的索要转人工同一口径），
调用方**必须**同时置 ``needs_review=True`` —— 降级不许静默（A.58 的零静默纪律）。
"""
from __future__ import annotations

from app.agents.specialist.solicitation import HANDOFF_MARKER
from app.models.schemas import Organ, organ_cn

#: 转人工后的行动建议（三处降级共用；含 ``HANDOFF_MARKER``，供
#: :func:`app.agents.specialist.solicitation.is_system_handoff_text` 识别为系统文案）
_HANDOFF_ADVICE = "已转人工判读：请结合图像与病史自行判断。"

#: 严重程度英文枚举 → 中文。
#:
#: 两套词表都要覆盖（心脏专科现网用 ASE 标签，`grading_definitions` 的 canonical
#: 用短名 —— `cardiac._CANONICAL_TO_CARDIAC_LABEL` 已把短名归一到 ASE 标签）：
#:   * 现网标签：``normal`` / ``mildly_reduced`` / ``moderately_reduced`` / ``severely_reduced``
#:   * canonical 短名：``normal`` / ``mild`` / ``moderate`` / ``severe``
#: 未登记的取值**原样返回**（暴露映射缺项，而不是把英文偷偷漏给医生 —— 守卫会判红）。
SEVERITY_CN: dict[str, str] = {
    "normal": "正常",
    "mildly_reduced": "轻度降低",
    "moderately_reduced": "中度降低",
    "severely_reduced": "重度降低",
    "mild": "轻度降低",
    "moderate": "中度降低",
    "severe": "重度降低",
    "unknown": "未判定",
}


def severity_cn(severity: str | None) -> str:
    """严重程度枚举 → 中文；未登记值原样返回（暴露映射缺项，而不是掩盖）。"""
    if not severity:
        return SEVERITY_CN["unknown"]
    return SEVERITY_CN.get(severity, severity)


def lvef_phrase(value: float | None) -> str:
    """左室射血分数的人话表达。

    数值形态刻意写成 ``LVEF 62%``（**空格**分隔）：``consistency._extract_lvef`` 的
    正则在 ``LVEF`` 后允许 ``[:=]?`` 与空白，但**不允许**紧贴中文括号 ——
    「（LVEF）62%」这种写法会让下游抽不到数值。
    """
    if value is None:
        return "左室射血分数（LVEF）未测值"
    return f"左室射血分数（LVEF）{value:g}%"


def graded_content(organ: Organ, grade: str, reason: str | None) -> str:
    """甲状腺 / 肝脏的结论正文：``部位 · 分级 — 理由``（分级简写原样保留）。"""
    head = f"{organ_cn(organ)} · {grade}"
    return f"{head} — {reason}" if reason else head


def cardiac_content(lvef_value: float | None, severity: str | None, reason: str | None) -> str:
    """心脏的结论正文：``心脏 · 左室射血分数（LVEF）62% · 严重程度：中度 — 理由``。"""
    head = f"{organ_cn(Organ.CARDIAC)} · {lvef_phrase(lvef_value)} · 严重程度：{severity_cn(severity)}"
    return f"{head} — {reason}" if reason else head


def cardiac_lvef_unparseable_text() -> str:
    """心脏「拿不到 LVEF / 收缩功能分级」→ 可读降级 + **可行动的下一步**。

    A.62 批 1：原先文案只说「已转人工判读：请结合图像与病史自行评估」，医生看不出
    **该补什么**（真机实测：近端 30 条样本里 10 条降级，其中 9 条是心脏，正文全为
    「LVEF: 无法计算 分级: 无法判断」）。这里补两条可执行路径：已有测量值 → 补填后重发；
    没有测量 → 人工判读并自行评估。

    写法刻意与 :func:`lvef_phrase` 对齐（``LVEF 55%``）：A.59 踩过「（LVEF）55%」这种
    紧贴中文括号的写法会让下游 ``consistency._extract_lvef`` 抽不到数值。

    ⚠️ 「让医生补填测量值」这一操作指引属**待 00 号确认**的措辞（见 16 号附录 A.62）；
    文案是单一事实源，改措辞只动这一处。
    """
    return (
        "心脏：本次未取得可判定的左室射血分数（LVEF）。如已有测量结果，"
        "请在临床信息中补填（写法如「LVEF 55%」）后重新发起判读；如无测量结果，"
        f"{_HANDOFF_ADVICE}"
    )


def evidence_insufficient_text(organ: Organ | str) -> str:
    """检索为空 → 可读降级（技术细节留在 ``reasoning_trace``）。"""
    return f"{organ_cn(organ)}：本次未检索到与检查部位相关的知识条目，{_HANDOFF_ADVICE}"


def llm_unavailable_text(organ: Organ | str, exc_type: str) -> str:
    """LLM 调用异常 → 可读降级（异常类名进括号，便于工程侧对账，不落原始报错）。"""
    return f"{organ_cn(organ)}：判读服务暂不可用（{exc_type}），{_HANDOFF_ADVICE}"


def parse_failed_text(organ: Organ | str, exc_type: str) -> str:
    """解析异常 → 可读降级。"""
    return f"{organ_cn(organ)}：判读结果无法解析（{exc_type}），{_HANDOFF_ADVICE}"


def specialist_unavailable_text(organ: Organ | str) -> str:
    """调度层拿不到专科实现 / 专科抛错 → 可读降级。"""
    return f"{organ_cn(organ)}：专科判读不可用，{_HANDOFF_ADVICE}"


def pipeline_unavailable_text() -> str:
    """管线级兜底（dispatch 失败）。

    刻意**不带**原始 ``reason``：该字段来源不确定（可能含内部标识与英文异常串），
    医生可见正文只给"未能启动"这一事实，原始原因留在 ``reasoning_trace``。
    """
    return f"判读流程未能启动，{_HANDOFF_ADVICE}"


__all__ = [
    "HANDOFF_MARKER",
    "SEVERITY_CN",
    "cardiac_content",
    "cardiac_lvef_unparseable_text",
    "evidence_insufficient_text",
    "graded_content",
    "llm_unavailable_text",
    "lvef_phrase",
    "parse_failed_text",
    "pipeline_unavailable_text",
    "severity_cn",
    "specialist_unavailable_text",
]
