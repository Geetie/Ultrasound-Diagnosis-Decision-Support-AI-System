"""管线级**可读降级文案**（医生能看到的话，不是技术串）。

**为什么要有这个文件**：A.73 取证发现 358 条"空结论"里，87 条永远停在
`findings={"status": "processing"}`、18 条只写了 `{"error": ...}`（无正文）——
医生打开报告看到的是**空白**，这比"说一句实话"糟得多。

规矩（与 `app/agents/specialist/readable.py` 同源）：
* 面向医生的话里**不放**异常类名以外的技术细节、不放请求 ID、不放原始报错；
* 必须明确"没做成什么"+"下一步该怎么办"；
* 任何"没完成"的终态都要有一段可读正文（`findings.fusion_result.content`），
  这样 `_conclusion_of()` 与前端都不会拿到空串。
"""

from __future__ import annotations

#: 统一的人工兜底建议（与专科层措辞保持一致，避免两套说法）
_HANDOFF = "建议由医师复核后重新发起判读"


def pipeline_failed_text(error_type: str) -> str:
    """管线未捕获异常 → 医生可见的一句话。"""
    return f"本次判读未完成（{error_type or '未知故障'}），系统已记录该故障，{_HANDOFF}。"


def stale_processing_text(minutes: int) -> str:
    """请求长时间停在"处理中"（进程崩溃/更新丢失）→ 医生可见的一句话。"""
    return (
        f"本次判读未能在预期时间内完成（已等待超过 {minutes} 分钟），"
        f"系统已将其标记为失败，{_HANDOFF}。"
    )


def no_conclusion_text(reason: str) -> str:
    """终态写了但正文为空 → 兜底正文（避免空白报告）。"""
    return f"本次判读未产出结论（{reason}），{_HANDOFF}。"
