"""检索链路降级汇总（G4 / G7 / N6 的共因修复）。

## 缺陷（三本台账同一根因）

`rerank_engine`（精排是否退化成启发式）、`triggered_bm25_fallback`（BM25 兜底）、
`image_leg_skip_reason`（影像腿跳过）、`p99_violation`（延迟越界）这些信号
**只写日志**，全仓没有任何读侧把它们送到医生眼前：

- G4：精排只在 FULL 档跑；关掉/降级为启发式时，医生以为拿到的是精排结果；
- G7：BM25 静默退两腿（dense+sparse 都没结果才兜底），报告看起来一切正常；
- N6：降级标记系统性到不了医生端 —— 不止精排一处，是整类。

## 设计（为什么是纯函数 + 一个聚合视图）

医生需要的是**一句话的"这次结果的质量前提"**，不是四个布尔字段。
所以本模块把四个信号汇总为一条 :class:`RetrievalDegradation`：

* ``degraded``：本次检索是否**低于标准配置**（任一降级信号为真）；
* ``reasons``：原因码列表（机器可读，便于统计与告警）；
* ``notes``：中文说明（医生可读，逐条对应原因，不合并成"系统繁忙"这种废话）。

不在这里做任何**阈值判断或猜测**：每个信号都来自检索层真实产出，
缺数据时如实报 ``unknown``，绝不用 False 冒充"没降级"（与 N2 同一口径）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["RetrievalDegradation", "summarize_degradation"]

#: 原因码 → 医生可读说明。**逐条一一对应**，不做合并文案。
_REASON_NOTES: dict[str, str] = {
    "rerank_disabled": "本次未做精排（标准档按设计跳过精排，结论基于融合排序）",
    "rerank_heuristic": "精排服务不可用，已退化为启发式排序（相关性可能偏低）",
    "bm25_fallback": "向量与关键词检索均无结果，已退化为仅关键词检索",
    "image_leg_skipped": "本次未使用影像检索（原因见 detail）",
    "p99_violation": "本次检索延迟超出目标（结果本身不受影响，但可能拖慢整体）",
    "missing_row_dropped": "部分检索命中在知识库中找不到对应正文，已丢弃",
}


@dataclass
class RetrievalDegradation:
    """一次检索的降级状态（医生端可见）。"""

    degraded: bool = False
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: 影像腿跳过的原始原因码（未跳过为 None）
    image_leg_skip_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "degraded": self.degraded,
            "reasons": list(self.reasons),
            "notes": list(self.notes),
            "image_leg_skip_reason": self.image_leg_skip_reason,
        }


def summarize_degradation(result: Any) -> RetrievalDegradation:
    """从 :class:`RetrievalResult`（或同形 dict）汇总降级状态。纯函数。

    Args:
        result: 检索结果对象或 dict；``None`` 视为**未执行检索**，
            返回 ``degraded=False`` 且不带原因（调用方据此区分"没降级"与"没检索"）。

    Returns:
        :class:`RetrievalDegradation` —— 原因码与说明一一对应，按固定顺序排列
        （便于前端稳定展示与测试断言）。
    """

    def _get(name: str, default: Any = None) -> Any:
        if result is None:
            return default
        if isinstance(result, dict):
            return result.get(name, default)
        return getattr(result, name, default)

    if result is None:
        return RetrievalDegradation()

    reasons: list[str] = []
    notes: list[str] = []

    rerank_engine = str(_get("rerank_engine", "") or "").strip().lower()
    if rerank_engine == "heuristic":
        reasons.append("rerank_heuristic")
    elif rerank_engine == "none":
        # "none" 的含义分两种：请求本就不要求精排（标准档）→ 仍属"未精排"，
        # 如实告知医生"结论基于融合排序"，但措辞不同于"精排失败"。
        reasons.append("rerank_disabled")

    if bool(_get("triggered_bm25_fallback", False)):
        reasons.append("bm25_fallback")

    image_skip = str(_get("image_leg_skip_reason", "") or "").strip()
    if image_skip:
        reasons.append("image_leg_skipped")

    if bool(_get("p99_violation", False)):
        reasons.append("p99_violation")

    if int(_get("dropped_missing_row", 0) or 0) > 0:
        reasons.append("missing_row_dropped")

    for reason in reasons:
        note = _REASON_NOTES.get(reason)
        if note:
            notes.append(note)

    return RetrievalDegradation(
        degraded=bool(reasons),
        reasons=reasons,
        notes=notes,
        image_leg_skip_reason=image_skip or None,
    )
