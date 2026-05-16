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
} as const;

export type DesignTokens = typeof tokens;
