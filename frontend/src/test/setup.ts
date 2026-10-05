import '@testing-library/jest-dom/vitest';
import { afterEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';
import { Modal } from 'antd';

// 每个用例后卸载组件，避免 antd Portal 残留污染下一个用例。
// 先 flush 一次宏任务：antd message/Modal 会在 scheduler 里排后续工作，
// 若在 jsdom 环境拆除后才执行，会抛出 "window is not defined" 的未处理异常。
afterEach(async () => {
  // Modal.confirm 这类**命令式** Modal 不在 React 树里，cleanup() 拆不到它们 ——
  // 残留的弹窗会让下一个用例的 findByText 撞上 "Found multiple elements"。
  // （ReportPage 的签发知情确认就是这么写的，4 个用例全军覆没才发现。）
  Modal.destroyAll();
  cleanup();
  await new Promise((resolve) => setTimeout(resolve, 0));
});

// jsdom 未实现 matchMedia：antd 响应式与暗室主题都依赖它
if (typeof window !== 'undefined' && !window.matchMedia) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}

// jsdom 未实现 ResizeObserver（antd 的部分组件使用）
if (typeof globalThis.ResizeObserver === 'undefined') {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
}

// jsdom 未实现 scrollTo，Breadcrumb/Modal 会调用
if (typeof window !== 'undefined' && !window.scrollTo) {
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
}
