/**
 * 诊断结果页的展示常量与纯函数。
 *
 * 单独成文件的原因：组件文件只导出组件才能保持 React Fast Refresh 生效
 * （``react-refresh/only-export-components``）。把常量混在组件文件里会让
 * 热更新退化为整页刷新，开发体验与 CI lint 都会受影响。
 */
import { tokens } from '@/styles/tokens';

/** M5 恶性风险特征码 → 中文（与后端 risk_rules 的 feature 名对齐） */
export const MALIGNANCY_FEATURE_LABELS: Record<string, string> = {
  micro: '微钙化',
  irregular: '形态不规则',
  'ill-defined': '边界不清',
  hypoechoic: '低回声',
  mixed: '混合/丰富血流',
  lobulated: '分叶状',
  internal: '内部血流',
  aspect_ratio_taller_than_wide: '纵横比>1',
  posterior_attenuation: '后方回声衰减',
  clear: '边界清晰',
  oval: '椭圆形/形态规则',
  anechoic: '无回声（囊性）',
  posterior_shadowing: '后方声影',
  posterior_enhancement: '后方回声增强',
};

/**
 * M3 尺寸来源 → 展示文案。
 *
 * ``estimated=true`` 表示该尺寸来自默认像素间距的**估算**，界面必须提示：
 * 把估算值当真实尺寸会让医生基于看似精确的错误数字做判断。
 */
export const PIXEL_SPACING_SOURCE_LABELS: Record<string, { label: string; estimated: boolean }> = {
  dicom: { label: 'DICOM 像素间距', estimated: false },
  default: { label: '估算（缺 PixelSpacing）', estimated: true },
  text: { label: '报告文本', estimated: false },
};

/** M4 鉴别诊断可能性分档（规格：>0.7 绿 / 0.4-0.7 黄 / <0.4 红） */
export const DIFFERENTIAL_HIGH_THRESHOLD = 0.7;
export const DIFFERENTIAL_MEDIUM_THRESHOLD = 0.4;

export function confidenceColor(confidence: number | null | undefined): string {
  if (confidence === null || confidence === undefined) return tokens.color.text.muted;
  if (confidence > DIFFERENTIAL_HIGH_THRESHOLD) return tokens.color.accent.success;
  if (confidence >= DIFFERENTIAL_MEDIUM_THRESHOLD) return tokens.color.accent.warning;
  return tokens.color.accent.danger;
}
