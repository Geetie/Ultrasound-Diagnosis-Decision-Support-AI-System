import type { ThemeConfig } from 'antd';
import { tokens } from './tokens';

const darkMedicalTheme: ThemeConfig = {
  token: {
    colorPrimary: tokens.color.accent.primary,
    colorBgBase: tokens.color.bg.primary,
    colorBgContainer: tokens.color.bg.secondary,
    colorBgElevated: tokens.color.bg.elevated,
    colorBgLayout: tokens.color.bg.primary,
    colorBorder: tokens.color.border.default,
    colorBorderSecondary: tokens.color.border.default,
    colorText: tokens.color.text.primary,
    colorTextSecondary: tokens.color.text.secondary,
    colorTextTertiary: tokens.color.text.muted,
    colorSuccess: tokens.color.accent.success,
    colorWarning: tokens.color.accent.warning,
    colorError: tokens.color.accent.danger,
    colorInfo: tokens.color.accent.info,
    colorLink: tokens.color.accent.primary,
    colorLinkHover: tokens.color.accent.primaryHover,
    borderRadius: tokens.radius.md,
    borderRadiusSM: tokens.radius.sm,
    borderRadiusLG: tokens.radius.lg,
    fontFamily: tokens.font.body,
    fontFamilyCode: tokens.font.mono,
    fontSize: 14,
    controlHeight: 36,
    controlHeightSM: 28,
    controlHeightLG: 44,
  },
  components: {
    Layout: {
      siderBg: tokens.color.bg.primary,
      headerBg: tokens.color.bg.secondary,
      bodyBg: tokens.color.bg.primary,
    },
    Menu: {
      darkItemBg: tokens.color.bg.primary,
      darkItemSelectedBg: tokens.color.bg.hover,
      darkItemHoverBg: tokens.color.bg.elevated,
      darkItemColor: tokens.color.text.secondary,
      darkItemSelectedColor: tokens.color.accent.primary,
      darkItemHoverColor: tokens.color.text.primary,
    },
    Button: {
      primaryShadow: `0 0 12px ${tokens.color.accent.primaryAlpha40}`,
    },
    Input: {
      colorBgContainer: tokens.color.bg.elevated,
      colorBorder: tokens.color.border.default,
      activeBorderColor: tokens.color.accent.primary,
      hoverBorderColor: `${tokens.color.accent.primary}80`,
    },
    Card: {
      colorBgContainer: tokens.color.bg.secondary,
      colorBorderSecondary: tokens.color.border.default,
    },
    Table: {
      colorBgContainer: tokens.color.bg.secondary,
      headerBg: tokens.color.bg.elevated,
      rowHoverBg: tokens.color.bg.hover,
    },
    Modal: {
      contentBg: tokens.color.bg.elevated,
      headerBg: tokens.color.bg.elevated,
    },
    Tooltip: {
      colorBgSpotlight: tokens.color.bg.elevated,
    },
    Tag: {
      defaultBg: tokens.color.bg.hover,
    },
  },
};

export default darkMedicalTheme;
