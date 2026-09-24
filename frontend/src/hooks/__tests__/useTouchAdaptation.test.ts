import { renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { detectTouch, useTouchAdaptation } from '../useTouchAdaptation';

function fakeWindow(options: {
  coarse?: boolean;
  maxTouchPoints?: number;
  touchEvent?: boolean;
  withMatchMedia?: boolean;
}): Window {
  const base = {
    navigator: { maxTouchPoints: options.maxTouchPoints ?? 0 },
    ...(options.touchEvent ? { ontouchstart: null } : {}),
  };
  if (options.withMatchMedia === false) {
    return base as unknown as Window;
  }
  return {
    ...base,
    matchMedia: vi.fn().mockReturnValue({
      matches: Boolean(options.coarse),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }),
  } as unknown as Window;
}

describe('detectTouch', () => {
  it('粗指针判定为触摸设备', () => {
    expect(detectTouch(fakeWindow({ coarse: true }))).toBe(true);
  });

  it('maxTouchPoints > 0 判定为触摸设备（触屏笔记本）', () => {
    expect(detectTouch(fakeWindow({ maxTouchPoints: 10 }))).toBe(true);
  });

  it('无 matchMedia 的老设备退回 ontouchstart 特征', () => {
    expect(detectTouch(fakeWindow({ touchEvent: true, withMatchMedia: false }))).toBe(true);
  });

  it('纯鼠标桌面为 false（ontouchstart 存在也不误判）', () => {
    expect(detectTouch(fakeWindow({ touchEvent: true }))).toBe(false);
  });

  it('无 window（SSR）返回 false', () => {
    expect(detectTouch(null)).toBe(false);
  });
});

describe('useTouchAdaptation', () => {
  afterEach(() => {
    document.body.className = '';
    vi.restoreAllMocks();
  });

  it('桌面环境不放大', () => {
    vi.spyOn(window, 'matchMedia').mockReturnValue({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    } as unknown as MediaQueryList);

    const { result } = renderHook(() => useTouchAdaptation());
    expect(result.current.isTouch).toBe(false);
    expect(result.current.fontScale).toBe(1);
    expect(result.current.hitTarget).toBe(32);
  });

  it('触摸环境放大热区与字号，并在 body 上挂 touch-mode', () => {
    vi.spyOn(window, 'matchMedia').mockReturnValue({
      matches: true,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    } as unknown as MediaQueryList);

    const { result, unmount } = renderHook(() => useTouchAdaptation());

    expect(result.current.isTouch).toBe(true);
    expect(result.current.hitTarget).toBeGreaterThanOrEqual(48);
    expect(result.current.fontScale).toBeGreaterThan(1);
    expect(document.body.classList.contains('touch-mode')).toBe(true);

    unmount();
    expect(document.body.classList.contains('touch-mode')).toBe(false);
  });
});
