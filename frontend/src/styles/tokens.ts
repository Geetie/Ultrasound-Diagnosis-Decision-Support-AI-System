export const tokens = {
  color: {
    bg: {
      primary: '#0a0e1a',
      secondary: '#111827',
      elevated: '#1a2035',
      hover: '#1e2745',
    },
    accent: {
      primary: '#00d4ff',
      primaryHover: '#33dfff',
      primaryAlpha20: '#00d4ff20',
      primaryAlpha40: '#00d4ff40',
      secondary: '#7c3aed',
      success: '#10b981',
      warning: '#f59e0b',
      danger: '#ef4444',
      info: '#3b82f6',
    },
    text: {
      primary: '#e2e8f0',
      secondary: '#94a3b8',
      muted: '#64748b',
      inverse: '#0f172a',
    },
    border: {
      default: '#1e293b',
      accent: 'rgba(0, 212, 255, 0.2)',
      warning: 'rgba(245, 158, 11, 0.5)',
    },
    darkroom: {
      amber: '#f59e0b',
      amberBorder: 'rgba(245, 158, 11, 0.6)',
      amberBg: 'rgba(245, 158, 11, 0.1)',
      // 暗室模式下进一步压低背景亮度、提高文字对比（T-GB-FE-04）
      bgDeep: '#05070f',
      textHigh: '#f1f5f9',
      accentDim: '#0aa8cc',
    },
    voice: {
      idle: '#00d4ff',
      recording: '#ef4444',
      processing: '#f59e0b',
      glow: 'rgba(0, 212, 255, 0.35)',
      glowRecording: 'rgba(239, 68, 68, 0.45)',
    },
  },
  font: {
    display: "'Orbitron', sans-serif",
    body: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    mono: "'JetBrains Mono', 'Fira Code', monospace",
  },
  radius: {
    sm: 6,
    md: 10,
    lg: 16,
    xl: 24,
  },
  spacing: {
    xs: 4,
    sm: 8,
    md: 16,
    lg: 24,
    xl: 32,
  },
  // 触摸屏适配（T-GB-FE-04）：诊断工作站多为鼠标，但床旁触摸屏需要
  // 更大的热区与字号。数值按"最小可点区域 48px"（人机工程常用下限）设定。
  touch: {
    hitTarget: 48,
    hitTargetLarge: 64,
    iconSize: 22,
    iconSizeLarge: 28,
    fontScale: 1.15,
    gapScale: 1.5,
    // 触摸设备上悬浮按钮离边缘的留白（避免与系统手势冲突）
    edgeOffset: 24,
    edgeOffsetLarge: 32,
  },
} as const;

export type DesignTokens = typeof tokens;
