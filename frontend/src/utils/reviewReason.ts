/**
 * 转人工原因的人话化（A.122 / G24）。
 *
 * 背景：后端 `result.review_reason` 一直在，但**工作区没有任何读者** ——
 * 医生只看到一个"需复核"标记，不知道为什么。原因码是自由串（后端多处写入），
 * 因此映射必须**带兜底分支**：未知码原样露出（附提示），绝不静默成"未知"或猜一个。
 *
 * 码表来源（后端 `app/` 内实际写入值，grep 得到）：
 * safety_gateway_unavailable / consistency_failed / conflict_detected /
 * self_rag_failed / specialist_flagged / dispatcher_failed / fusion_layer_failed /
 * fusion_validate_budget_exceeded / no_specialist_responded / consent_withdrawn /
 * consent_withdrawn_cancelled，以及本轮新增的 hallucination_severe /
 * grading_floor_raised / ai_safety_circuit_breaker_<state>。
 */

export const REVIEW_REASON_LABELS: Record<string, string> = {
  safety_gateway_unavailable: '安全网关不可用，需人工复核',
  consistency_failed: '一致性校验未通过',
  conflict_detected: '多专科结论存在冲突',
  self_rag_failed: 'Self-RAG 自检未通过',
  specialist_flagged: '专科模型标记需复核',
  dispatcher_failed: '专科调度失败，未产出结论',
  fusion_layer_failed: '融合层失败，结论不可用',
  fusion_validate_budget_exceeded: '校验超时降级（预算耗尽）',
  no_specialist_responded: '无专科响应',
  consent_withdrawn: '患者已撤回同意',
  consent_withdrawn_cancelled: '患者撤回同意，判读已取消',
  hallucination_severe: '检出严重幻觉，结论需人工确认',
  grading_floor_raised: '分级低于下限规则，需人工确认',
};

const CIRCUIT_BREAKER_PREFIX = 'ai_safety_circuit_breaker_';

/** 原因码 → 人话。空值返回 undefined（= 不渲染）；未知码原样露出（不猜）。 */
export function reviewReasonLabel(code?: string | null): string | undefined {
  const raw = (code ?? '').trim();
  if (!raw) return undefined;
  if (REVIEW_REASON_LABELS[raw]) return REVIEW_REASON_LABELS[raw];
  if (raw.startsWith(CIRCUIT_BREAKER_PREFIX)) {
    const state = raw.slice(CIRCUIT_BREAKER_PREFIX.length) || 'unknown';
    return `AI 安全熔断（${state}），结论需人工确认`;
  }
  // 未知码：不猜语义，但必须让医生看到"有个原因码"，便于报修与追溯
  return `需人工复核（原因码：${raw}）`;
}
