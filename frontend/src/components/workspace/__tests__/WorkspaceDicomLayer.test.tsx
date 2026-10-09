import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * 多帧分期①（A.38）：帧指示器与浏览工具胶囊的行为。
 *
 * cornerstone 必须 mock：它在 import 期摸 WebGL/canvas，jsdom 必崩
 * （见本目录其他套件与 hooks/__tests__/useDicomUpload.test.ts 的同款处理）。
 * 动态 import 与静态 import 一样会被 vi.mock 拦截。
 */
const cs = vi.hoisted(() => ({
  initCornerstone: vi.fn(async () => undefined),
  enable: vi.fn(),
  disable: vi.fn(),
  loadAndCacheImage: vi.fn(async () => ({ minPixelValue: 0, maxPixelValue: 255 })),
  displayImage: vi.fn(),
  resize: vi.fn(),
  scrollStack: vi.fn(),
  activateTool: vi.fn(),
  deactivateTool: vi.fn(),
  onMeasureCompleted: vi.fn(() => () => {}),
}));

vi.mock('@/components/dicom/cornerstone', () => cs);

import WorkspaceDicomLayer from '../WorkspaceDicomLayer';

describe('WorkspaceDicomLayer · 多帧分期①', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('多帧：setStack 收整栈 imageIds，渲染帧指示器「1/3」与翻帧按钮', async () => {
    render(<WorkspaceDicomLayer imageIds={['dicomfile:0', 'dicomfile:1', 'dicomfile:2']} />);
    await waitFor(() => expect(cs.displayImage).toHaveBeenCalled());
    // 整栈进 setStack（顺序=上传顺序），不是只有最后一张
    expect(cs.displayImage).toHaveBeenCalledWith(
      expect.anything(),
      expect.arrayContaining([
        expect.objectContaining({ imageId: 'dicomfile:0' }),
        expect.objectContaining({ imageId: 'dicomfile:2' }),
      ]),
    );
    const indicator = screen.getByText('1/3');
    expect(indicator).toBeInTheDocument();
    expect(screen.getByLabelText('上一帧')).toBeInTheDocument();
    expect(screen.getByLabelText('下一帧')).toBeInTheDocument();
  });

  it('单帧：不渲染帧指示器（默认沉默 —— 单帧没有可翻的东西），工具组照常', async () => {
    render(<WorkspaceDicomLayer imageIds={['dicomfile:0']} />);
    await waitFor(() => expect(cs.displayImage).toHaveBeenCalled());
    expect(screen.queryByText('1/1')).toBeNull();
    expect(screen.queryByLabelText('上一帧')).toBeNull();
    expect(screen.getByRole('toolbar', { name: '影像浏览工具' })).toBeInTheDocument();
  });

  it('点「下一帧」调 scrollStack(+1)——拖动/滚轮之外给医生确定性入口', async () => {
    render(<WorkspaceDicomLayer imageIds={['dicomfile:0', 'dicomfile:1']} />);
    await waitFor(() => expect(cs.displayImage).toHaveBeenCalled());
    fireEvent.click(screen.getByLabelText('下一帧'));
    await waitFor(() => expect(cs.scrollStack).toHaveBeenCalledWith(expect.anything(), 1));
    fireEvent.click(screen.getByLabelText('上一帧'));
    await waitFor(() => expect(cs.scrollStack).toHaveBeenCalledWith(expect.anything(), -1));
  });

  it('工具互斥单选：激活「缩放」时禁用其余两个（与 useDicomViewer 同语义）', async () => {
    render(<WorkspaceDicomLayer imageIds={['dicomfile:0']} />);
    await waitFor(() => expect(cs.displayImage).toHaveBeenCalled());
    // 初始激活窗宽窗位
    await waitFor(() => expect(cs.activateTool).toHaveBeenCalledWith('WwwcTool'));
    cs.activateTool.mockClear();
    cs.deactivateTool.mockClear();
    fireEvent.click(screen.getByTitle('缩放'));
    await waitFor(() => expect(cs.activateTool).toHaveBeenCalledWith('ZoomTool'));
    expect(cs.deactivateTool).toHaveBeenCalledWith('WwwcTool');
    expect(cs.deactivateTool).toHaveBeenCalledWith('PanTool');
  });

  it('测量工具（A.40）：胶囊组含「长度/椭圆」，点「长度」激活 Length 并禁用其余', async () => {
    render(<WorkspaceDicomLayer imageIds={['dicomfile:0']} />);
    await waitFor(() => expect(cs.displayImage).toHaveBeenCalled());
    await waitFor(() => expect(cs.activateTool).toHaveBeenCalledWith('WwwcTool'));
    cs.activateTool.mockClear();
    cs.deactivateTool.mockClear();
    fireEvent.click(screen.getByTitle('长度'));
    await waitFor(() => expect(cs.activateTool).toHaveBeenCalledWith('Length'));
    expect(cs.deactivateTool).toHaveBeenCalledWith('WwwcTool');
    expect(cs.deactivateTool).toHaveBeenCalledWith('ZoomTool');
    expect(cs.deactivateTool).toHaveBeenCalledWith('PanTool');
    // 椭圆按钮在（激活互斥由同一 effect 覆盖，不重复断言）
    expect(screen.getByTitle('椭圆')).toBeInTheDocument();
  });

  it('测量读数：画完长度显示「长度 12.3 mm」，换栈清空（默认沉默）', async () => {
    type MeasureHandler = (m: { tool: string; value: number; unit: string }) => void;
    const handlers: MeasureHandler[] = [];
    // hoisted mock 的签名是 () => cancel，这里收 handler 需要断言
    (
      cs.onMeasureCompleted as unknown as {
        mockImplementation: (fn: (h: MeasureHandler) => () => undefined) => void;
      }
    ).mockImplementation((h) => {
      handlers.push(h);
      return () => undefined;
    });
    const { rerender } = render(<WorkspaceDicomLayer imageIds={['dicomfile:0']} />);
    await waitFor(() => expect(screen.getByRole('toolbar', { name: '影像浏览工具' })).toBeInTheDocument());
    await waitFor(() => expect(handlers.length).toBeGreaterThan(0));
    // 初始无读数（默认沉默）—— 注意 /长度\d/ 而非 /长度/：工具按钮文案就叫「长度」
    expect(screen.queryByText(/长度 \d/)).toBeNull();

    handlers[handlers.length - 1]({ tool: 'Length', value: 12.34, unit: 'mm' });
    // act 外触发 setState，更新异步调度 —— 用 waitFor 等刷新
    await waitFor(() => expect(screen.getByText('长度 12.3 mm')).toBeInTheDocument());

    // 换影像：旧读数不属于新影像，立即清空
    rerender(<WorkspaceDicomLayer imageIds={['dicomfile:9']} />);
    await waitFor(() => expect(screen.queryByText(/长度 12\.3/)).toBeNull());
  });

  it('空栈：什么都不渲染（默认沉默），不 enable 元素', () => {
    render(<WorkspaceDicomLayer imageIds={[]} />);
    expect(cs.enable).not.toHaveBeenCalled();
    expect(screen.queryByText('影像加载中…')).toBeNull();
  });
});
