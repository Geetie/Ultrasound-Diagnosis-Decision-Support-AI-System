"""T-GB-EDG-03 边缘端本地安全检查（标签白名单 + 坐标范围校验）。

为什么边缘端要再做一次：
- 上行链路可能中断，边缘端不能让"不该出现的字段"进入本地缓存/展示；
- 推理后处理可能算出越界框，前端直接渲染会画出错误位置；
- 与 SafetyGateway 的关系是"纵深防御"：SG 是权威防线（服务端），
  边缘端本地校验负责"不要制造脏数据 / 尽早暴露异常"。

规格（`开发计划.md` T-GB-EDG-03）：白名单字段过滤正确率 100% +
坐标范围校验通过。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from edge.protocols import NavLabel

# 白名单口径必须与以下 3 处保持**完全一致**（跨模块一致性由
# ``tests/unit/edg/test_whitelist_consistency.py`` 锁定）：
#   1. safety_gateway/degraded_output.py  : DEFAULT_WHITELIST_FIELDS
#   2. safety_gateway/sg_config.py        : SGSettings.safety_gateway_whitelist_fields
#   3. safety_gateway/checker/orchestrator.py : SafetyGatewayChecker.DEFAULT_WHITELIST_FIELDS
# 语义：只允许解剖/影像征象类字段上行，禁止分级结论类术语外泄。
# M1/M2/M3/M6/M9 新增项均为"影像采集属性/测量/征象"，不含分级词汇
# （如 TI-RADS/恶性/可疑），因此不突破该语义边界。
DEFAULT_ALLOWED_LABELS: Tuple[str, ...] = (
    "organ_name",
    "anatomical_location",
    "echogenicity",
    "boundary",
    "morphology",
    "vascularity",
    "calcification",
    "lesion_size_measurements",
    "observations",
    # M1 切面标准性评估
    "plane_assessment",
    # M6 图像质量评估
    "image_quality",
    # M2 B+ 级高危标记与建议动作（仅"需关注"，无分级结论）
    "risk_flag",
    "suggested_action",
    # M9 后方回声特征
    "posterior_acoustic",
    # M3 自动测量（结构化测量与派生尺寸）
    "measurement",
    "aspect_ratio",
    "distance_to_capsule",
    "distance_to_organ_edge",
    "distance_to_skin",
    "volume",
)

# 归一化坐标允许的数值范围（含端点）
DEFAULT_COORD_RANGE: Tuple[float, float] = (0.0, 1.0)


@dataclass
class LocalSafetyResult:
    """本地安全检查结果。"""

    passed: bool
    violations: List[str] = field(default_factory=list)
    # 通过白名单过滤后的数据（越界/未知字段被丢弃）
    filtered: Dict[str, Any] = field(default_factory=dict)
    # 坐标越界的标注（供调用方决定丢弃或裁剪）
    rejected_labels: List[NavLabel] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "violations": list(self.violations),
            "filtered_keys": sorted(self.filtered.keys()),
            "rejected_label_count": len(self.rejected_labels),
        }


class LocalSafetyChecker:
    """标签白名单 + 坐标范围校验。"""

    def __init__(
        self,
        allowed_labels: Iterable[str] = DEFAULT_ALLOWED_LABELS,
        coord_range: Tuple[float, float] = DEFAULT_COORD_RANGE,
        min_confidence: float = 0.0,
    ) -> None:
        if coord_range[0] >= coord_range[1]:
            raise ValueError("coord_range 下界必须小于上界")
        self._allowed = frozenset(allowed_labels)
        self._coord_min, self._coord_max = coord_range
        self._min_confidence = min_confidence

    @property
    def allowed_labels(self) -> frozenset:
        return self._allowed

    def filter_fields(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """按白名单过滤字段（未在白名单中的键直接丢弃）。"""
        return {k: v for k, v in payload.items() if k in self._allowed}

    def check_label(self, label: NavLabel) -> List[str]:
        """校验单个标注，返回违规原因（空列表表示通过）。"""
        violations: List[str] = []
        if label.label not in self._allowed:
            violations.append(f"label_not_whitelisted:{label.label}")

        if label.confidence < self._min_confidence:
            violations.append(f"confidence_below_min:{label.confidence}")

        x0, y0 = label.x, label.y
        x1, y1 = label.x + label.width, label.y + label.height
        for name, value in (("x", x0), ("y", y0), ("x2", x1), ("y2", y1)):
            if not (self._coord_min <= value <= self._coord_max):
                violations.append(f"coordinate_out_of_range:{name}={value}")

        if label.width <= 0 or label.height <= 0:
            violations.append("non_positive_box_size")

        return violations

    def filter_labels(self, labels: Sequence[NavLabel]) -> Tuple[List[NavLabel], List[str], List[NavLabel]]:
        """校验一批标注。

        Returns:
            (通过的标注, 违规原因列表, 被拒绝的标注)
        """
        accepted: List[NavLabel] = []
        rejected: List[NavLabel] = []
        violations: List[str] = []
        for label in labels:
            problems = self.check_label(label)
            if problems:
                violations.extend(problems)
                rejected.append(label)
            else:
                accepted.append(label)
        return accepted, violations, rejected

    def check(
        self,
        payload: Mapping[str, Any],
        labels: Sequence[NavLabel] = (),
    ) -> LocalSafetyResult:
        """一次性完成字段白名单过滤 + 坐标校验。

        行外字段不会导致整体失败（按设计"过滤"而非"拒绝"），
        但坐标/标签违规会导致 ``passed=False``。
        """
        filtered = self.filter_fields(payload)
        dropped = sorted(set(payload.keys()) - set(filtered.keys()))

        accepted, label_violations, rejected = self.filter_labels(labels)
        violations: List[str] = []
        if dropped:
            violations.append("fields_filtered:" + ",".join(dropped))
        violations.extend(label_violations)

        filtered = dict(filtered)
        if labels:
            filtered["labels"] = accepted

        # 字段被过滤属预期行为（不算失败）；标签/坐标违规才算失败
        passed = not label_violations
        return LocalSafetyResult(
            passed=passed,
            violations=violations,
            filtered=filtered,
            rejected_labels=rejected,
        )


__all__ = [
    "DEFAULT_ALLOWED_LABELS",
    "DEFAULT_COORD_RANGE",
    "LocalSafetyChecker",
    "LocalSafetyResult",
]
