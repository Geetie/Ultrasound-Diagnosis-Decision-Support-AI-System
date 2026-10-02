import type { ImageQuality, PlaneAssessment, RiskFlag, SuggestedAction } from './api';

export interface NavigationAnnotation {
  id: string;
  label: string;
  confidence: number;
  threshold: number;
  bbox: [number, number, number, number];
  /** M2 B+ 级高危标记：仅"需关注"，不含分级结论；命中时自动升级深度分析 */
  risk_flag?: RiskFlag | null;
  suggested_action?: SuggestedAction | null;
}

export type AnnotationValidationStatus =
  | 'verified'
  | 'pending'
  | 'timeout'
  | 'revoked';

export interface AnnotationWithStatus {
  annotation: NavigationAnnotation;
  status: AnnotationValidationStatus;
  frameId: string;
  timestamp: number;
  revokeReason?: string;
}

export interface AnnotationStyleConfig {
  borderColor: string;
  borderStyle: 'solid' | 'dashed';
  borderWidth: number;
  fillOpacity: number;
  labelBgColor: string;
  labelTextColor: string;
  dashArray?: string;
  animation?: string;
}

export const ANNOTATION_STYLES: Record<AnnotationValidationStatus, AnnotationStyleConfig> = {
  verified: {
    borderColor: '#10b981',
    borderStyle: 'solid',
    borderWidth: 2,
    fillOpacity: 0.15,
    labelBgColor: '#10b981',
    labelTextColor: '#ffffff',
  },
  pending: {
    borderColor: '#f59e0b',
    borderStyle: 'dashed',
    borderWidth: 2,
    fillOpacity: 0.4,
    labelBgColor: '#f59e0b',
    labelTextColor: '#0f172a',
    dashArray: '8 4',
    animation: 'annotation-pending-blink 1.5s ease-in-out infinite',
  },
  timeout: {
    borderColor: '#ef4444',
    borderStyle: 'solid',
    borderWidth: 2,
    fillOpacity: 0,
    labelBgColor: '#ef4444',
    labelTextColor: '#ffffff',
  },
  revoked: {
    borderColor: '#ef4444',
    borderStyle: 'solid',
    borderWidth: 2,
    fillOpacity: 0,
    labelBgColor: '#ef4444',
    labelTextColor: '#ffffff',
    animation: 'flash-red 500ms ease-out',
  },
};

// M2 快速路径高危标记（B+ 级）的视觉样式：
// 橙色（#f97316）+ 脉冲呼吸，与校验态的琥珀色闪烁（pending）、
// 普通标注的蓝色实线形成明确区分。仅标记"需关注"，不含诊断结论。
export const HIGH_RISK_ANNOTATION_STYLE: AnnotationStyleConfig = {
  borderColor: '#f97316',
  borderStyle: 'solid',
  borderWidth: 2,
  fillOpacity: 0.18,
  labelBgColor: '#f97316',
  labelTextColor: '#0b0f14',
  animation: 'annotation-high-risk-pulse 1.6s ease-in-out infinite',
};

/** B+ 级标注旁的提示文案（自动升级已发起，无需医生手动操作） */
export const HIGH_RISK_ACTION_TEXT = '正在启动深度分析…';

/** M1 切面类型 → 中文 */
export const PLANE_TYPE_LABELS: Record<string, string> = {
  transverse: '横切面',
  longitudinal: '纵切面',
  non_standard: '非标准切面',
};

/** M1 切面问题码 → 中文（后端 issue 码为英文枚举） */
export const PLANE_ISSUE_LABELS: Record<string, string> = {
  image_too_dark: '图像过暗',
  image_too_bright: '图像过亮',
  low_contrast: '对比度不足',
  plane_below_standard: '切面标准化不足',
};

/** M6 图像质量分项问题码 → 中文 */
export const IMAGE_QUALITY_ISSUE_LABELS: Record<string, string> = {
  image_too_dark: '图像过暗',
  image_too_bright: '图像过亮',
  severe_blur: '严重模糊',
  saturation_clipping: '灰阶饱和',
  low_contrast: '对比度不足',
};

/** 切面状态（M1）：前端三态展示口径 */
export type PlaneStatusLevel = 'standard' | 'warning' | 'unknown';

/** 图像质量提示级别（M6）：≥0.7 无提示 / 0.5-0.7 警示 / <0.5 严重 */
export type ImageQualityLevel = 'ok' | 'warning' | 'severe';

export const IMAGE_QUALITY_OK_THRESHOLD = 0.7;
export const IMAGE_QUALITY_WARN_THRESHOLD = 0.5;

export function resolvePlaneStatusLevel(
  assessment: PlaneAssessment | null | undefined
): PlaneStatusLevel {
  if (!assessment) return 'unknown';
  return assessment.is_standard ? 'standard' : 'warning';
}

export function resolveImageQualityLevel(
  quality: ImageQuality | null | undefined
): ImageQualityLevel {
  if (!quality) return 'ok';
  if (quality.overall_score < IMAGE_QUALITY_WARN_THRESHOLD) return 'severe';
  if (quality.overall_score < IMAGE_QUALITY_OK_THRESHOLD) return 'warning';
  return 'ok';
}

export interface NavigationState {
  currentFrameAnnotations: AnnotationWithStatus[];
  lowConfidenceFrameIds: Set<string>;
  isNavigationActive: boolean;
  organSwitching: boolean;
  currentOrgan: string | null;
  /** M1 帧级切面评估（随导航消息下发，后端缺省为 null） */
  currentFramePlaneAssessment: PlaneAssessment | null;
  /** M6 帧级图像质量（随导航消息下发，后端缺省为 null） */
  currentFrameImageQuality: ImageQuality | null;
}
