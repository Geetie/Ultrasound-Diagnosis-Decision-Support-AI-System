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

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from edge.protocols import NavLabel

# 白名单口径必须与以下 3 处保持**完全一致**（跨模块一致性由
# ``tests/unit/edg/test_whitelist_consistency.py`` 锁定）：
#   1. safety_gateway/degraded_output.py  : DEFAULT_WHITELIST_FIELDS
#   2. safety_gateway/sg_config.py        : SGSettings.safety_gateway_whitelist_fields
#   3. safety_gateway/checker/orchestrator.py : SafetyGatewayChecker.DEFAULT_WHITELIST_FIELDS
# 语义：只允许解剖/影像征象类字段上行，禁止分级结论类术语外泄。
# M1/M2/M3/M6/M9 新增项均为"影像采集属性/测量/征象"，不含分级词汇
# （如 TI-RADS/恶性/可疑），因此不突破该语义边界。
DEFAULT_ALLOWED_LABELS: tuple[str, ...] = (
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
DEFAULT_COORD_RANGE: tuple[float, float] = (0.0, 1.0)

# ----------------------------------------------------------------------
# M2 快速路径高危标记（B+ 级）
# ----------------------------------------------------------------------
# B+ 级输出介于 B 级（导航）与 A 级（诊断结论）之间：只标记"需关注"并建议
# 启动标准评估，**不含**分级词汇或病种编码，因此不触发 SafetyGateway 的
# 分级结论过滤；但触发条件更严（置信度须超过器官级阈值 × RISK_CONFIDENCE_MULTIPLIER）。
RISK_FLAG_HIGH = "high"
SUGGESTED_ACTION_START_STANDARD_EVALUATION = "start_standard_evaluation"

# 高风险标签集合（B+ 触发源）。与 DEFAULT_ALLOWED_LABELS 分离：它们是
# "怀疑"级信号而非确认的征象，因此走更严的阈值校验路径。
DEFAULT_HIGH_RISK_LABELS: tuple[str, ...] = (
    "malignant_suspected",
    "anaplastic_carcinoma_suspected",
    "metastasis_suspected",
    "vascular_invasion_suspected",
)

# B+ 级置信度阈值相对器官级基础阈值的收紧系数（规格：× 1.05）
RISK_CONFIDENCE_MULTIPLIER = 1.05

# 各器官的导航级基础置信度阈值。真机调参由现场按模型标定覆盖；此处的值
# 只作为"未标定时的安全默认"，偏保守（宁可不标记，不要误标）。
DEFAULT_ORGAN_CONFIDENCE_THRESHOLDS: dict[str, float] = {
    "thyroid": 0.60,
    "liver": 0.60,
    "cardiac": 0.55,
}
DEFAULT_ORGAN_CONFIDENCE_THRESHOLD = 0.60


@dataclass
class LocalSafetyResult:
    """本地安全检查结果。"""

    passed: bool
    violations: list[str] = field(default_factory=list)
    # 通过白名单过滤后的数据（越界/未知字段被丢弃）
    filtered: dict[str, Any] = field(default_factory=dict)
    # 坐标越界的标注（供调用方决定丢弃或裁剪）
    rejected_labels: list[NavLabel] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
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
        coord_range: tuple[float, float] = DEFAULT_COORD_RANGE,
        min_confidence: float = 0.0,
        high_risk_labels: Iterable[str] = DEFAULT_HIGH_RISK_LABELS,
        organ_confidence_thresholds: Mapping[str, float] | None = None,
        risk_multiplier: float = RISK_CONFIDENCE_MULTIPLIER,
    ) -> None:
        if coord_range[0] >= coord_range[1]:
            raise ValueError("coord_range 下界必须小于上界")
        if risk_multiplier < 1.0:
            raise ValueError("risk_multiplier 必须 >= 1.0（B+ 级阈值只能更严）")
        self._allowed = frozenset(allowed_labels)
        self._high_risk = frozenset(high_risk_labels)
        self._organ_thresholds = dict(
            organ_confidence_thresholds or DEFAULT_ORGAN_CONFIDENCE_THRESHOLDS
        )
        self._risk_multiplier = risk_multiplier
        self._coord_min, self._coord_max = coord_range
        self._min_confidence = min_confidence

    @property
    def allowed_labels(self) -> frozenset:
        return self._allowed

    @property
    def high_risk_labels(self) -> frozenset:
        return self._high_risk

    # ---- M2 快速路径高危标记（B+ 级）----
    def is_high_risk(self, label: NavLabel) -> bool:
        """标签是否属于高风险集合（B+ 候选）。"""
        return label.label in self._high_risk

    def risk_confidence_threshold(self, organ: str | None = None) -> float:
        """B+ 级触发所需的置信度下限 = 器官级基础阈值 × 收紧系数（1.05）。"""
        base = self._organ_thresholds.get(
            (organ or "").strip().lower(), DEFAULT_ORGAN_CONFIDENCE_THRESHOLD
        )
        return base * self._risk_multiplier

    def mark_risk(self, label: NavLabel, organ: str | None = None) -> NavLabel:
        """对单个标注做 B+ 级判定，命中则返回带 risk_flag 的新标注。

        命中条件（规格）：标签属高风险集合，且置信度 > 器官级阈值 × 1.05。
        未命中时原样返回。``NavLabel`` 为 frozen dataclass，故用 ``replace`` 构造。
        """
        if not self.is_high_risk(label):
            return label
        if label.confidence <= self.risk_confidence_threshold(organ):
            return label
        return replace(
            label,
            risk_flag=RISK_FLAG_HIGH,
            suggested_action=SUGGESTED_ACTION_START_STANDARD_EVALUATION,
        )

    def mark_risk_batch(
        self, labels: Sequence[NavLabel], organ: str | None = None
    ) -> list[NavLabel]:
        """对一批标注做 B+ 级判定，返回的新列表与输入等长同序。"""
        return [self.mark_risk(label, organ=organ) for label in labels]

    def filter_fields(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """按白名单过滤字段（未在白名单中的键直接丢弃）。"""
        return {k: v for k, v in payload.items() if k in self._allowed}

    def check_label(self, label: NavLabel, organ: str | None = None) -> list[str]:
        """校验单个标注，返回违规原因（空列表表示通过）。

        标签合法性有两条路径：
        1. 命中普通白名单 → 通过（B 级导航标注）；
        2. 命中高风险集合 → 必须达到 B+ 级收紧阈值（器官级阈值 × 1.05），
           否则**拒绝转发**：疑癌类标签若低于阈值，既不足以升格为 B+ 级
           （避免误标引发不必要的深度诊断），也不应作为普通标注静默外泄。
        """
        violations: list[str] = []
        if label.label not in self._allowed:
            if label.label in self._high_risk:
                threshold = self.risk_confidence_threshold(organ)
                if label.confidence <= threshold:
                    violations.append(
                        f"risk_confidence_below_threshold:{label.confidence}"
                        f"<={round(threshold, 4)}"
                    )
            else:
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

    def filter_labels(
        self, labels: Sequence[NavLabel], organ: str | None = None
    ) -> tuple[list[NavLabel], list[str], list[NavLabel]]:
        """校验一批标注。

        Returns:
            (通过的标注, 违规原因列表, 被拒绝的标注)
        """
        accepted: list[NavLabel] = []
        rejected: list[NavLabel] = []
        violations: list[str] = []
        for label in labels:
            problems = self.check_label(label, organ=organ)
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
        organ: str | None = None,
    ) -> LocalSafetyResult:
        """一次性完成字段白名单过滤 + 坐标校验。

        行外字段不会导致整体失败（按设计"过滤"而非"拒绝"），
        但坐标/标签违规会导致 ``passed=False``。
        """
        filtered = self.filter_fields(payload)
        dropped = sorted(set(payload.keys()) - set(filtered.keys()))

        accepted, label_violations, rejected = self.filter_labels(labels, organ=organ)
        violations: list[str] = []
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
    "DEFAULT_HIGH_RISK_LABELS",
    "DEFAULT_ORGAN_CONFIDENCE_THRESHOLD",
    "DEFAULT_ORGAN_CONFIDENCE_THRESHOLDS",
    "RISK_CONFIDENCE_MULTIPLIER",
    "RISK_FLAG_HIGH",
    "SUGGESTED_ACTION_START_STANDARD_EVALUATION",
    "LocalSafetyChecker",
    "LocalSafetyResult",
]
