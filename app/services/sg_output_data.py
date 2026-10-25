"""发给安全网关的 ``output_data`` —— **唯一构造点**。

## 为什么必须只有一处（2026-09-29 真机暴露）

项目里有**两个**地方会调用 ``safety_gateway_client.check_output``：

* 写路径：`app/services/diagnosis_pipeline.py::_safe_safety_check`（诊断产出时）；
* 读路径：`app/api/v1/diagnosis.py::get_diagnosis_result`（结果被查询时**再校验一次**
  输出安全，这是原设计：结果可能被再次展示）。

两边各自拼 payload，于是必然漂移 —— 真机上表现得很典型：

* 写路径发 `{"organ","findings","recommendation"}`，读路径发的是 worker 落库的
  **整包 findings**（`organ/fusion_result/sg_result/timings_ms`）——形状完全不同；
* **两边都没带患者条件**，而安全网关的禁忌判定在"拿不到条件数据"时按设计判**命中**
  （`safety_gateway/contraindication.py::_match_condition` 末尾 `return True`）。

结果：同一份诊断在"写"处 pass、在"读"处被判"检测到 4 条禁忌"，
`requires_manual_review` 恒为真，系统永远给不出自动结论。

> 判据：**跨进程/跨模块的同一种载荷，只允许有一个构造函数**。
"""
from __future__ import annotations

from typing import Any

__all__ = ["build_sg_output_data", "extract_findings_text"]


def extract_findings_text(fusion_result: Any) -> str:
    """从融合结果抽取 findings 文本（**对象与 dict 两种形态都要认**）。

    写路径拿到的是对象（``FusionResult.final_response.content``），而读路径拿到的是
    落库后的 dict —— 同一个抽取语义，不能因为形态不同就各写一份。

    ## A.88 修掉一个"读路径恒空"的真缺陷（2026-10-25 真机量化）

    本函数原先只认两种 dict 形状：``{"final_response": {"content": ...}}`` 与
    ``{"final_response": "<文本>"}``。但**生产落库的形状是扁平的** ——
    ``_fusion_result_to_dict`` 把正文写成 ``fusion_result["content"]``（见
    ``app/services/diagnosis_pipeline.py:1857-1859``），没有 ``final_response`` 这一层。

    真机核对（服务器 PG，``diagnosis_results`` 最近 20 条带 fusion_result 的行）：
    **20/20 是扁平 shape**（键 = ``content/completed_at/crag_*/final_confidence/
    needs_review/review_reason/self_rag_passed/total_latency_ms``），
    **0/20 含 final_response**。于是：

    * 读路径 ``GET /result/{id}`` 再校验一次 SG 时，喂进去的是**空 findings**
      （``app/api/v1/diagnosis.py:700``）→ 禁忌/脱敏判定在"无正文"上跑；
    * 患者历史对比的结论摘要恒为空（``app/api/v1/patients.py:174``）；
    * 盘上/库里有正文，但任何"按落库读"的消费方都读不到 —— 典型的
      "写的人一套形状、读的人另一套形状"。

    为什么测试没抓到：用例里的样本形状是**手写的** ``{"final_response": {...}}``
    （``tests/unit/services/test_sg_output_data.py:86``），与真实落库形状不同。

    本次按"落库形状优先、历史/模型形状兼容"补齐：扁平 ``content`` 先取，
    再退回 ``final_response`` 的两种形状。**不改写入侧**（写入侧没有错，是读取侧
    认错了形状），因此对既有历史行**立即生效**、不需要数据迁移。
    """
    if fusion_result is None:
        return ""

    if isinstance(fusion_result, dict):
        # ① 生产真实形状：扁平 content（_fusion_result_to_dict 的产物）
        flat = fusion_result.get("content")
        if isinstance(flat, str) and flat:
            return flat
        # ② 兼容 FusionResult.model_dump() / 历史样本形状
        final = fusion_result.get("final_response")
        if isinstance(final, dict):
            return str(final.get("content") or "")
        if isinstance(final, str):
            return final
        return ""

    final = getattr(fusion_result, "final_response", None)
    if final is None:
        return ""
    return getattr(final, "content", "") or ""


def build_sg_output_data(
    *,
    organ: str,
    findings: str | None,
    recommendation: str | None = None,
    patient_context: Any = None,
) -> dict[str, Any]:
    """构造 ``check_output(output_data=...)`` 的载荷。

    Args:
        organ: 目标器官。
        findings: 观察描述文本（由 :func:`extract_findings_text` 抽取）。
        recommendation: 建议文本；省略时按 findings 截断（与写路径一致，避免两边形状不同）。
        patient_context: 请求携带的患者上下文（含 ``conditions``）。
            **方向性关键**：
              * ``conditions`` 是 list（**含空列表**）→ 如实转发为
                ``diagnosis_data.patient_conditions``，安全网关正常评估（空列表 = 已确认无条件）；
              * 缺失或为 ``None``（未提供）→ **不添加该键**，安全网关维持"缺数据即保守判命中"。

            注意不要用 ``"conditions" in patient_context`` 判断：``model_dump()`` 会把
            未提供的可选字段输出成 ``None``，那样会把"未提供"误当成"已确认无条件"（fail-open）。
    """
    data: dict[str, Any] = {"organ": organ}
    if findings:
        data["findings"] = findings
    if recommendation is None and findings:
        recommendation = findings[:500]
    if recommendation:
        data["recommendation"] = recommendation

    conditions = patient_context.get("conditions") if isinstance(patient_context, dict) else None
    if isinstance(conditions, list):
        data["diagnosis_data"] = {"patient_conditions": list(conditions)}

    return data
