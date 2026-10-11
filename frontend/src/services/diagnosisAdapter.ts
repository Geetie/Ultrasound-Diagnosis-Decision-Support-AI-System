/**
 * 诊断结果适配层：真机 API 响应 → 工作区结论区视图模型
 *
 * 【为什么需要这一层】
 * `DiagnosisConclusion.tsx` 是**旧设计系统**写的（Orbitron 字体、amber warning、
 * accent.success），按"旧前端设计系统整体废弃"的指令已不作为实现依据。
 * 它读的字段（`standard_conclusion` / `full_conclusion` / `deep_analysis` /
 * `target_organ` / `finding_summary`）在真机响应里**一个都不存在** ——
 * 实测 2026-10-02 req_20261002_da4fa542，`diagnosis` 下的键只有：
 *   organ · rollout · routing · sg_result · timings_ms · completed_at
 *   · fusion_result · review_reason
 * 所以这里从 `fusion_result` 抽真实字段，不去猜名字。
 *
 * 【契约 §0 P1 默认沉默 —— 本层的核心纪律】
 * 每一个视图字段都只在**后端真的给了数据**时才存在。拿不到就省略，
 * 由设计侧"不渲染"规则生效 —— **绝不编造临床映射**。
 * 反例（禁止）：看到 tier=TR2 就自行映射成"低风险 2%–5%"。
 * 后端没给恶性概率，界面上就不该有这一行。
 */

import type { DiagnosisResult } from '@/types/api';

/** 「看一眼」档的触发理由（契约 §1：仅命中触发条件时渲染） */
export type AttentionReason =
  | 'self_rag_failed' // Self-RAG 未通过
  | 'low_confidence' // 融合层终置信度低于阈值
  | 'needs_review' // 融合层显式要求复核
  | 'consistency_partial'; // 一致性降级

/** 恶性风险行 —— 仅当后端显式给出恶性概率时才有 */
export interface MalignancyRisk {
  /** 原始口径文本，例如 "约 2%–5%"。不给裸百分比（契约要求带区间与措辞）。 */
  range: string;
  /** 档位措辞，例如 "低"。 */
  level: string;
}

/** 分割旁路视图模型 —— 来自 result.nodule_segmentation（后端 2026-10-03 挂入）。 */
export interface SegmentationView {
  available: boolean;
  /** available=false 时的降级原因（原文透传，渲染层映射成中文） */
  reason?: string;
  /** 分割帧的像素尺寸 —— bbox 坐标系，也是 SVG overlay 的 viewBox */
  width?: number;
  height?: number;
  /** 病灶外接框 [x1, y1, x2, y2]（像素坐标）；无检出时为 null */
  bbox?: [number, number, number, number] | null;
  area_mm2?: number | null;
  area_px?: number | null;
  mean_prob?: number | null;
  /** 分割所属影像的 image_id（A.51 帧级归属：多影像栈据此只在对应影像上叠加）。 */
  imageId?: string;
  /** 择优帧号（0 起）与所在影像总帧数（A.51）。 */
  frameIndex?: number;
  totalFrames?: number;
  /** 择优策略：best_mean_prob（多候选择优）/ single_frame。 */
  selectionPolicy?: string;
  /** 实际评估的候选帧数（≤3）。 */
  candidatesEvaluated?: number;
}

/** 校验并收敛后端的旁路 dict —— 脏数据宁可当「不可用」也不渲染错误框。 */
function toSegmentationView(raw: unknown): SegmentationView | undefined {
  if (typeof raw !== 'object' || raw === null) return undefined;
  const d = raw as Record<string, unknown>;
  const bboxRaw = d.bbox;
  const bbox =
    Array.isArray(bboxRaw) &&
    bboxRaw.length === 4 &&
    bboxRaw.every((n) => typeof n === 'number' && Number.isFinite(n))
      ? ([bboxRaw[0], bboxRaw[1], bboxRaw[2], bboxRaw[3]] as [
          number,
          number,
          number,
          number,
        ])
      : null;
  const width = typeof d.width === 'number' ? d.width : undefined;
  const height = typeof d.height === 'number' ? d.height : undefined;
  return {
    available: d.available === true,
    reason: typeof d.reason === 'string' ? d.reason : undefined,
    width,
    height,
    bbox,
    area_mm2: typeof d.area_mm2 === 'number' ? d.area_mm2 : null,
    area_px: typeof d.area_px === 'number' ? d.area_px : null,
    mean_prob: typeof d.mean_prob === 'number' ? d.mean_prob : null,
    imageId: typeof d.image_id === 'string' ? d.image_id : undefined,
    frameIndex: typeof d.frame_index === 'number' ? d.frame_index : undefined,
    totalFrames: typeof d.total_frames === 'number' ? d.total_frames : undefined,
    selectionPolicy: typeof d.selection_policy === 'string' ? d.selection_policy : undefined,
    candidatesEvaluated:
      typeof d.candidates_evaluated === 'number' ? d.candidates_evaluated : undefined,
  };
}

/** 结论区一级视图模型（对应 04_判读态结论） */
export interface ConclusionView {
  /** 一句话结论 —— 直接来自 fusion_result.content，不二次改写 */
  headline: string;
  /** 器官 —— 来自 diagnosis.organ */
  organ?: string;
  /** 分级原文（如 TR2 / TI-RADS 3），后端给什么显示什么 */
  tier?: string;
  /** 证据条目 id（来自 evidence.knowledge_ids 与 content 里的证据引用） */
  evidenceIds: string[];
  /** 「看一眼」档 —— 多条理由时按此顺序取第一条，不并列渲染（P2 一次决策） */
  attention?: { reason: AttentionReason; text: string };
  /** 恶性风险 —— 后端未给风险区间时整个字段不存在 */
  malignancyRisk?: MalignancyRisk;
  /** 建议动作 —— 仅当后端 recommendations 里有可执行动作 */
  advice?: string;
  /** 安全网关是否显式要求转人工（与 requires_manual_review 可能矛盾，见下） */
  safetyBlocked?: { message: string; errorCode?: string };
  /** 终置信度 */
  finalConfidence?: number;
  /** 分割旁路（病灶 bbox + 面积）—— available=false 时影像区显示降级原因 */
  segmentation?: SegmentationView;
}

/** 融合层终置信度低于此值才触发「看一眼」。低于典型诊断阈值。 */
export const LOW_CONFIDENCE_THRESHOLD = 0.7;

/** 从 `fusion_result.content` 抽分级（TR2 / TI-RADS 3 之类），不解释、不改写。 */
function extractTier(content: string): string | undefined {
  // 实测样本："[thyroid] TR2 — 分级: TR2 总分: 2 置信度: 0.85 …"
  const m = content.match(/\b(TR\d|TI-RADS\s*\d)\b/i);
  return m ? m[1].replace(/\s+/g, ' ').toUpperCase() : undefined;
}

/** 从 content 里抽证据引用（形如 `chunk_121, chunk_124`）。 */
function extractInlineEvidence(content: string): string[] {
  const m = content.match(/证据:\s*([A-Za-z0-9_,\s]+)/);
  if (!m || !m[1]) return [];
  return m[1]
    .split(/[,，]\s*/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/** 从 content 里抽「理由」句，作为建议动作的来源之一。 */
function extractRationale(content: string): string | undefined {
  const m = content.match(/理由:\s*([^证]*?)(?:\s*证据:|$)/);
  const s = m?.[1]?.trim();
  return s || undefined;
}

/**
 * 致命判读：是否需要「人工复核」。
 *
 * 【实测发现的矛盾，必须在这里裁决】
 * 同一条真机响应里同时出现：
 *   requires_manual_review: false          ← 顶层字段
 *   sg_result.allowed: false, message: "安全校验服务不可用，请转为人工诊断"
 * 只看顶层会让确认动作区不出现，而安全网关明确要求转人工。
 * 契约 §4「不可削减」把签发前安全校验列为必须，因此**任一为真即视为需复核**。
 */
export function needsDoctorReview(result: DiagnosisResult | null): boolean {
  if (!result) return false;
  if (result.requires_manual_review) return true;
  if (result.diagnosis?.sg_result?.allowed === false) return true;
  if (result.hallucination_severity === 'severe') return true;
  return false;
}

/** 把真机响应映射成结论区视图模型。 */
export function toConclusionView(result: DiagnosisResult | null): ConclusionView | null {
  if (!result) return null;

  const diagnosis = result.diagnosis ?? {};
  const fusion = diagnosis.fusion_result;
  const content = fusion?.content?.trim() ?? '';
  if (!content) return null;

  // 「看一眼」档：按优先级取**第一条**，不并列（P2 一次决策）
  const attention = ((): ConclusionView['attention'] => {
    if (fusion?.self_rag_passed === false) {
      return { reason: 'self_rag_failed', text: 'Self-RAG 校验未通过 · 建议人工复核' };
    }
    if (result.consistency_flag === 'partial_consistent') {
      return { reason: 'consistency_partial', text: '双引擎结论部分不一致 · 建议人工复核' };
    }
    if (fusion?.needs_review) {
      return { reason: 'needs_review', text: '融合层标记需复核 · 请确认结论依据' };
    }
    if (
      typeof fusion?.final_confidence === 'number' &&
      fusion.final_confidence < LOW_CONFIDENCE_THRESHOLD
    ) {
      return {
        reason: 'low_confidence',
        text: `终置信度 ${(fusion.final_confidence * 100).toFixed(0)}% 低于 ${
          LOW_CONFIDENCE_THRESHOLD * 100
        }% 阈值`,
      };
    }
    return undefined;
  })();

  const evidenceIds = Array.from(
    new Set([
      ...extractInlineEvidence(content),
      ...(((result.evidence?.knowledge_ids as string[]) ?? []) as string[]),
    ])
  );

  return {
    headline: content,
    organ: diagnosis.organ,
    tier: extractTier(content),
    evidenceIds,
    attention,
    segmentation: toSegmentationView(result.nodule_segmentation),
    // ⚠️ 恶性风险**故意留空**：真机响应没有任何恶性概率字段。
    // 设计稿 04 的"低 · 约 2%–5%"是画布上的示意文案，不是后端产物。
    // 按 P1 省略而不是硬编码 —— 等后端真的给出区间口径再接。
    malignancyRisk: undefined,
    advice: extractRationale(content),
    safetyBlocked:
      diagnosis.sg_result?.allowed === false
        ? {
            message: diagnosis.sg_result.message,
            errorCode: diagnosis.sg_result.error_code,
          }
        : undefined,
    finalConfidence: fusion?.final_confidence,
  };
}
