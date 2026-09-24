import { useEffect, useMemo, useState } from 'react';
import { tokens } from '@/styles/tokens';

/**
 * 触摸屏适配（T-GB-FE-04）。
 *
 * 判定依据按可靠性排序：
 * 1. `matchMedia('(pointer: coarse)')` —— 主指针为粗指针（手指/触控笔）；
 * 2. `navigator.maxTouchPoints > 0` —— 具备触摸能力（触屏笔记本也命中）；
 * 3. `'ontouchstart' in window` —— 老设备兜底。
 *
 * 命中后放大热区与字号、加大间距，并在 <body> 上挂 `touch-mode` 类，
 * 供全局 CSS 统一调整（避免在每个组件里散落条件样式）。
 * 暗室模式下（`dark-room` 类）由 CSS 进一步降低亮度并保持对比。
 */
export interface TouchAdaptation {
  isTouch: boolean;
  /** 最小可点热区（px） */
  hitTarget: number;
  /** 图标尺寸（px） */
  iconSize: number;
  /** 字号缩放系数（1 = 不缩放） */
  fontScale: number;
  /** 间距缩放系数（1 = 不缩放） */
  gapScale: number;
  /** 悬浮元素离屏幕边缘的留白（px） */
  edgeOffset: number;
}

const DESKTOP_ADAPTATION: TouchAdaptation = {
  isTouch: false,
  hitTarget: 32,
  iconSize: 16,
  fontScale: 1,
  gapScale: 1,
  edgeOffset: 16,
};

const TOUCH_ADAPTATION: TouchAdaptation = {
  isTouch: true,
  hitTarget: tokens.touch.hitTarget,
  iconSize: tokens.touch.iconSize,
  fontScale: tokens.touch.fontScale,
  gapScale: tokens.touch.gapScale,
  edgeOffset: tokens.touch.edgeOffset,
};

/**
 * @param win 要检测的 window；**不传**表示使用全局 window，传 `null` 表示
 *   无 window（SSR）。两者语义不同，故不能用默认参数合并。
 */
export function detectTouch(win?: Window | null): boolean {
  const target = win === undefined ? (typeof window === 'undefined' ? null : window) : win;
  if (!target) return false;

  if (typeof target.matchMedia === 'function') {
    // 现代浏览器以指针精度与触点数为准；这两个信号最可靠，
    // 而 `ontouchstart` 在桌面 Chrome / jsdom 里也可能存在，容易误判。
    if (target.matchMedia('(pointer: coarse)').matches) return true;
    return (target.navigator?.maxTouchPoints ?? 0) > 0;
  }

  // 无 matchMedia 的老环境才退回触摸事件特征
  return 'ontouchstart' in target || (target.navigator?.maxTouchPoints ?? 0) > 0;
}

export function useTouchAdaptation(): TouchAdaptation {
  const [isTouch, setIsTouch] = useState<boolean>(() => detectTouch());

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const query = window.matchMedia?.('(pointer: coarse)');
    if (!query) return;

    const onChange = () => setIsTouch(detectTouch());
    // 外接鼠标/键盘时（如触屏笔记本切换到鼠标）需要跟随变化
    query.addEventListener?.('change', onChange);
    return () => query.removeEventListener?.('change', onChange);
  }, []);

  useEffect(() => {
    if (typeof document === 'undefined') return;
    document.body.classList.toggle('touch-mode', isTouch);
    return () => document.body.classList.remove('touch-mode');
  }, [isTouch]);

  return useMemo(() => (isTouch ? TOUCH_ADAPTATION : DESKTOP_ADAPTATION), [isTouch]);
}
