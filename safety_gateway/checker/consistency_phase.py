from typing import Any, ClassVar

import structlog

from safety_gateway.checker.phase_base import CheckContext, CheckPhase
from safety_gateway.sg_config import sg_settings as settings
from safety_gateway.validation_result import CheckResult, ConsistencyLevel, DegradedReason

logger = structlog.get_logger()


class ConsistencyPhase(CheckPhase):

    async def check(self, context: CheckContext) -> CheckResult | None:
        return await self.check_with_params(context)

    async def check_with_params(
        self,
        patient_id: str,
        output_data: dict[str, Any],
        context: CheckContext,
        elapsed_ms: float,
        conclusion_suppressed: bool = False,
    ) -> CheckResult | None:
        fusion_output = output_data.get("fusion_output", {})
        specialist_conclusions = output_data.get("specialist_conclusions", [])

        if conclusion_suppressed:
            fusion_output_for_check = {k: v for k, v in fusion_output.items() if k != "diagnostic_conclusion"}
        else:
            fusion_output_for_check = fusion_output

        consistency_result = await self.check_consistency(fusion_output_for_check, specialist_conclusions)

        consistency_level = consistency_result.get("consistency_level", ConsistencyLevel.CONSISTENT)
        conflicts = consistency_result.get("conflicts", [])

        if consistency_level == ConsistencyLevel.CONTRADICTORY:
            logger.warning(
                "sg_consistency_contradictory",
                patient_id=patient_id,
                conflict_count=len(conflicts),
            )
            return CheckResult.degraded(
                consent_status=context.consent_status,
                message=f"一致性校验发现矛盾，需人工核验：{consistency_result['message']}",
                degraded_reason=DegradedReason.CONSISTENCY_CONTRADICTORY,
                elapsed_ms=elapsed_ms,
                consent_check_timeout=context.is_timeout,
            )

        if consistency_level == ConsistencyLevel.PARTIAL_CONSISTENT:
            logger.warning(
                "sg_consistency_partial",
                patient_id=patient_id,
                conflict_count=len(conflicts),
            )
            return CheckResult.degraded(
                consent_status=context.consent_status,
                message=f"一致性校验部分一致，附加差异说明：{consistency_result['message']}",
                degraded_reason=DegradedReason.CONSISTENCY_PARTIAL,
                elapsed_ms=elapsed_ms,
                consent_check_timeout=context.is_timeout,
            )

        if consistency_level == ConsistencyLevel.EVIDENCE_INSUFFICIENT:
            logger.warning(
                "sg_consistency_evidence_insufficient",
                patient_id=patient_id,
            )
            return CheckResult.degraded(
                consent_status=context.consent_status,
                message=f"依据不足，无法验证一致性，需人工复核：{consistency_result['message']}",
                degraded_reason=DegradedReason.CONSISTENCY_PARTIAL,
                elapsed_ms=elapsed_ms,
                consent_check_timeout=context.is_timeout,
                requires_manual_review=True,
            )

        return None

    async def check_consistency(
        self,
        fusion_output: dict[str, Any],
        specialist_conclusions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        conflicts = []

        if len(specialist_conclusions) == 0:
            self_check_conflicts = self._check_fusion_self_consistency(fusion_output)
            if self_check_conflicts:
                return {
                    "consistent": False,
                    "consistency_level": ConsistencyLevel.CONTRADICTORY,
                    "conflicts": self_check_conflicts,
                    "message": f"融合层输出自洽性检查发现{len(self_check_conflicts)}处矛盾",
                }
            return {
                "consistent": True,
                "consistency_level": ConsistencyLevel.CONSISTENT,
                "conflicts": [],
                "message": "无专科结论，融合层输出自洽性检查通过",
            }

        conclusions_by_organ: dict[str, list[dict[str, Any]]] = {}
        for conclusion in specialist_conclusions:
            organ = conclusion.get("organ", "unknown")
            if organ not in conclusions_by_organ:
                conclusions_by_organ[organ] = []
            conclusions_by_organ[organ].append(conclusion)

        fusion_diagnosis = fusion_output.get("diagnostic_conclusion", {})
        fusion_grade = fusion_diagnosis.get("grade", "")
        fusion_finding = fusion_diagnosis.get("finding", "")
        fusion_confidence = fusion_diagnosis.get("confidence")

        for organ, conclusions in conclusions_by_organ.items():
            for conclusion in conclusions:
                spec_grade = conclusion.get("grade", "")
                spec_finding = conclusion.get("finding", "")
                spec_confidence = conclusion.get("confidence")

                if spec_grade and fusion_grade and spec_grade != fusion_grade:
                    conflicts.append({
                        "type": "grade_mismatch",
                        "organ": organ,
                        "specialist_grade": spec_grade,
                        "fusion_grade": fusion_grade,
                        "description": f"{organ}专科分级({spec_grade})与融合层分级({fusion_grade})不一致",
                    })

                if spec_finding and fusion_finding and spec_finding != fusion_finding:
                    conflicts.append({
                        "type": "finding_mismatch",
                        "organ": organ,
                        "specialist_finding": spec_finding,
                        "fusion_finding": fusion_finding,
                        "description": f"{organ}专科发现与融合层发现不一致",
                    })

                if spec_confidence is not None and fusion_confidence is not None:
                    confidence_diff = abs(spec_confidence - fusion_confidence)
                    if confidence_diff > settings.sg_consistency_confidence_divergence_threshold:
                        conflicts.append({
                            "type": "confidence_divergence",
                            "organ": organ,
                            "specialist_confidence": spec_confidence,
                            "fusion_confidence": fusion_confidence,
                            "divergence": round(confidence_diff, 3),
                            "description": f"{organ}专科置信度({spec_confidence})与融合层置信度({fusion_confidence})差异过大({confidence_diff:.3f})",
                        })

                conclusion_text = conclusion.get("conclusion", "")
                evidence_text = conclusion.get("evidence", "")
                if conclusion_text and evidence_text:
                    if self._check_conclusion_evidence_contradiction(conclusion_text, evidence_text):
                        conflicts.append({
                            "type": "conclusion_evidence_contradiction",
                            "organ": organ,
                            "description": f"{organ}结论与依据存在矛盾，需人工复核",
                            "requires_manual_review": True,
                        })

        consistency_level = self._classify_consistency(conflicts, specialist_conclusions)
        return {
            "consistent": consistency_level == ConsistencyLevel.CONSISTENT,
            "consistency_level": consistency_level,
            "conflicts": conflicts,
            "message": "一致性校验通过" if consistency_level == ConsistencyLevel.CONSISTENT else f"检测到{len(conflicts)}处不一致",
        }

    def _classify_consistency(self, conflicts: list[dict[str, Any]], specialist_conclusions: list[dict[str, Any]] | None = None) -> str:
        """按设计文档01-架构设计§3.5实现4态一致性判定

        判定优先级: evidence_insufficient > contradictory > partial_consistent > consistent
        - evidence_insufficient: 有专科结论但缺少关键字段(grade/finding/confidence)
        - contradictory: 结论-依据矛盾 或 同一器官分级与发现均不一致
        - partial_consistent: 仅分级不一致 或 仅发现不一致 或 置信度差异过大
        - consistent: 无冲突
        """
        from safety_gateway.validation_result import ConsistencyLevel

        if specialist_conclusions:
            for conclusion in specialist_conclusions:
                has_grade = bool(conclusion.get("grade"))
                has_finding = bool(conclusion.get("finding"))
                has_confidence = conclusion.get("confidence") is not None
                if not has_grade and not has_finding and not has_confidence:
                    return ConsistencyLevel.EVIDENCE_INSUFFICIENT

        if not conflicts:
            return ConsistencyLevel.CONSISTENT

        has_contradiction = any(
            c.get("type") == "conclusion_evidence_contradiction"
            or c.get("type") == "fusion_self_contradiction"
            for c in conflicts
        )
        if has_contradiction:
            return ConsistencyLevel.CONTRADICTORY

        organ_conflict_counts: dict[str, int] = {}
        for c in conflicts:
            organ = c.get("organ", "unknown")
            organ_conflict_counts[organ] = organ_conflict_counts.get(organ, 0) + 1

        if any(count >= 2 for count in organ_conflict_counts.values()):
            return ConsistencyLevel.CONTRADICTORY

        return ConsistencyLevel.PARTIAL_CONSISTENT

    CONTRADICTION_PAIRS: ClassVar[list[tuple[str, str]]] = [
        ("良性", "恶性"),
        ("正常", "异常"),
        ("阴性", "阳性"),
        ("囊性", "实性"),
        ("弥漫性", "局灶性"),
        ("高回声", "低回声"),
        ("高回声", "无回声"),
    ]

    NEGATION_PREFIXES: ClassVar[tuple[str, ...]] = (
        "非", "无", "未", "不", "未见", "不伴", "除外", "不考虑", "未提示", "排除",
        "否认", "无证据", "不支持", "暂不考虑",
    )

    def _is_negated(self, text: str, word: str) -> bool:
        word_pos = text.find(word)
        if word_pos == -1:
            return False
        prefix_window = text[max(0, word_pos - 6):word_pos]
        return any(prefix in prefix_window for prefix in self.NEGATION_PREFIXES)

    def _has_contradiction_in_sentences(self, c_sent: str, e_sent: str) -> bool:
        for pos_word, neg_word in self.CONTRADICTION_PAIRS:
            if pos_word in c_sent and neg_word in e_sent:
                if not self._is_negated(e_sent, neg_word) and not self._is_negated(c_sent, pos_word):
                    logger.info("sg_conclusion_evidence_contradiction_detected", conclusion=c_sent, evidence=e_sent, pos_word=pos_word, neg_word=neg_word)
                    return True
            if neg_word in c_sent and pos_word in e_sent:
                if not self._is_negated(e_sent, pos_word) and not self._is_negated(c_sent, neg_word):
                    logger.info("sg_conclusion_evidence_contradiction_detected", conclusion=c_sent, evidence=e_sent, pos_word=neg_word, neg_word=pos_word)
                    return True
        return False

    def _split_sentences(self, text: str) -> list[str]:
        import re
        sentences = re.split(r'[。？！\n]', text)
        return [s.strip() for s in sentences if s.strip()]

    def _check_conclusion_evidence_contradiction(self, conclusion: str, evidence: str) -> bool:
        conclusion_sentences = self._split_sentences(conclusion)
        evidence_sentences = self._split_sentences(evidence)
        for c_sent in conclusion_sentences:
            for e_sent in evidence_sentences:
                if self._has_contradiction_in_sentences(c_sent, e_sent):
                    return True
        return False

    def _check_fusion_self_consistency(self, fusion_output: dict[str, Any]) -> list[dict[str, Any]]:
        conflicts = []
        diagnostic_conclusion = fusion_output.get("diagnostic_conclusion", {})
        conclusion_text = diagnostic_conclusion.get("conclusion", "")
        evidence_text = diagnostic_conclusion.get("evidence", "")
        if conclusion_text and evidence_text:
            if self._check_conclusion_evidence_contradiction(conclusion_text, evidence_text):
                conflicts.append({
                    "type": "fusion_self_contradiction",
                    "description": "融合层诊断结论与依据存在矛盾，需人工复核",
                    "requires_manual_review": True,
                })
        return conflicts
