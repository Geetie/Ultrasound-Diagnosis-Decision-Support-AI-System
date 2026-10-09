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

  it('空栈：什么都不渲染（默认沉默），不 enable 元素', () => {
    render(<WorkspaceDicomLayer imageIds={[]} />);
    expect(cs.enable).not.toHaveBeenCalled();
    expect(screen.queryByText('影像加载中…')).toBeNull();
  });
});
