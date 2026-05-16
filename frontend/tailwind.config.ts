import { tokens } from './src/styles/tokens';

/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        'bg-primary': tokens.color.bg.primary,
        'bg-secondary': tokens.color.bg.secondary,
        'bg-elevated': tokens.color.bg.elevated,
        'bg-hover': tokens.color.bg.hover,
        'accent-primary': tokens.color.accent.primary,
        'accent-secondary': tokens.color.accent.secondary,
        'accent-success': tokens.color.accent.success,
        'accent-warning': tokens.color.accent.warning,
        'accent-danger': tokens.color.accent.danger,
        'accent-info': tokens.color.accent.info,
        'text-primary': tokens.color.text.primary,
        'text-secondary': tokens.color.text.secondary,
        'text-muted': tokens.color.text.muted,
        'text-inverse': tokens.color.text.inverse,
        'border-default': tokens.color.border.default,
        'border-accent': tokens.color.border.accent,
        'border-warning': tokens.color.border.warning,
      },
      fontFamily: {
        display: ['Orbitron', 'sans-serif'],
        body: ['Inter', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
      spacing: {
        xs: `${tokens.spacing.xs}px`,
        sm: `${tokens.spacing.sm}px`,
        md: `${tokens.spacing.md}px`,
        lg: `${tokens.spacing.lg}px`,
        xl: `${tokens.spacing.xl}px`,
      },
      borderRadius: {
        sm: `${tokens.radius.sm}px`,
        md: `${tokens.radius.md}px`,
        lg: `${tokens.radius.lg}px`,
        xl: `${tokens.radius.xl}px`,
      },
      transitionDuration: {
        fast: '150ms',
        normal: '300ms',
        slow: '500ms',
      },
      transitionTimingFunction: {
        default: 'cubic-bezier(0.4, 0, 0.2, 1)',
        spring: 'cubic-bezier(0.34, 1.56, 0.64, 1)',
      },
      animation: {
        'pulse-glow': 'pulse-glow 2s ease-in-out infinite',
        'blink-warning': 'blink-warning 1s ease-in-out infinite',
        'slide-in-left': 'slide-in-left 300ms cubic-bezier(0.4, 0, 0.2, 1)',
        'slide-in-right': 'slide-in-right 300ms cubic-bezier(0.4, 0, 0.2, 1)',
        'fade-in': 'fade-in 500ms cubic-bezier(0.4, 0, 0.2, 1)',
        'flash-red': 'flash-red 500ms ease-out',
        'annotation-pending-blink': 'annotation-pending-blink 1.5s ease-in-out infinite',
      },
      keyframes: {
        'pulse-glow': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.5' },
        },
        'blink-warning': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.3' },
        },
        'slide-in-left': {
          '0%': { transform: 'translateX(-100%)', opacity: '0' },
          '100%': { transform: 'translateX(0)', opacity: '1' },
        },
        'slide-in-right': {
          '0%': { transform: 'translateX(100%)', opacity: '0' },
          '100%': { transform: 'translateX(0)', opacity: '1' },
        },
        'fade-in': {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        'flash-red': {
          '0%': { backgroundColor: 'rgba(239, 68, 68, 0.5)' },
          '100%': { backgroundColor: 'transparent' },
        },
        'annotation-pending-blink': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.4' },
        },
      },
    },
  },
  plugins: [],
};
