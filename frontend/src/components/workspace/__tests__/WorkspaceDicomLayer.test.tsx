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
  restoreAnnotations: vi.fn(() => 0),
  clearAnnotations: vi.fn(),
  getCurrentImageId: vi.fn(() => 'dicomfile:0'),
  getStackIndex: vi.fn(() => 0),
}));

vi.mock('@/components/dicom/cornerstone', () => cs);

const annSvc = vi.hoisted(() => ({
  fetchAnnotations: vi.fn(async (): Promise<Record<string, unknown>[]> => []),
  saveAnnotations: vi.fn(async (): Promise<number> => 0),
}));

vi.mock('@/services/annotationsService', () => annSvc);

import WorkspaceDicomLayer from '../WorkspaceDicomLayer';

describe('WorkspaceDicomLayer · 多帧分期①', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    annSvc.fetchAnnotations.mockResolvedValue([]);
    annSvc.saveAnnotations.mockResolvedValue(0);
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

  it('标注存档（A.40）：有 archiveKey 时测量完成即 PUT，挂载时 GET 恢复渲染', async () => {
    type MeasureHandler = (m: {
      tool: string;
      value: number;
      unit: string;
      annotation?: Record<string, unknown>;
    }) => void;
    const handlers: MeasureHandler[] = [];
    (
      cs.onMeasureCompleted as unknown as {
        mockImplementation: (fn: (h: MeasureHandler) => () => undefined) => void;
      }
    ).mockImplementation((h) => {
      handlers.push(h);
      return () => undefined;
    });
    const { rerender } = render(
      <WorkspaceDicomLayer imageIds={['dicomfile:0']} archiveKey="img_20261009_ab12cd34" />,
    );
    await waitFor(() => expect(handlers.length).toBeGreaterThan(0));
    // 无存档 → GET 返回空，不恢复
    await waitFor(() => expect(annSvc.fetchAnnotations).toHaveBeenCalledWith('img_20261009_ab12cd34'));
    expect(cs.restoreAnnotations).not.toHaveBeenCalled();

    handlers[handlers.length - 1]({
      tool: 'Length',
      value: 9.9,
      unit: 'mm',
      annotation: { toolName: 'Length', data: {} },
    });
    await waitFor(() =>
      expect(annSvc.saveAnnotations).toHaveBeenCalledWith('img_20261009_ab12cd34', [
        expect.objectContaining({ toolName: 'Length' }),
      ]),
    );

    // 换到带存档注释的影像：GET 返回注释 → 恢复渲染
    annSvc.fetchAnnotations.mockResolvedValue([
      { toolName: 'Length', data: { length: 5 }, uuid: 'a1' },
    ]);
    rerender(
      <WorkspaceDicomLayer imageIds={['dicomfile:1']} archiveKey="img_20261009_ab12cd34" />,
    );
    await waitFor(() =>
      expect(cs.restoreAnnotations).toHaveBeenCalledWith(
        expect.anything(),
        [expect.objectContaining({ toolName: 'Length' })],
      ),
    );
  });

  it('无 archiveKey（预览场景）不做任何标注网络请求', async () => {
    render(<WorkspaceDicomLayer imageIds={['dicomfile:0']} />);
    await waitFor(() => expect(cs.displayImage).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 50));
    expect(annSvc.fetchAnnotations).not.toHaveBeenCalled();
    expect(annSvc.saveAnnotations).not.toHaveBeenCalled();
  });

  it('空栈：什么都不渲染（默认沉默），不 enable 元素', () => {
    render(<WorkspaceDicomLayer imageIds={[]} />);
    expect(cs.enable).not.toHaveBeenCalled();
    expect(screen.queryByText('影像加载中…')).toBeNull();
  });

  it('帧级标注归属（A.45 分期③，E1 修正锚点）：按 referencedImageId 匹配当前 imageId', async () => {
    // 存档：img0 有一条（带 referencedImageId）、img1 一条、旧格式一条（无
    // referencedImageId，frame_index=1 兜底）
    annSvc.fetchAnnotations.mockResolvedValue([
      { toolName: 'Length', data: {}, uuid: 'a-img0', metadata: { referencedImageId: 'dicomfile:0' } },
      { toolName: 'Length', data: {}, uuid: 'a-img1', metadata: { referencedImageId: 'dicomfile:1' } },
      { toolName: 'Length', data: {}, uuid: 'a-legacy', frame_index: 1 },
    ]);
    const { rerender } = render(
      <WorkspaceDicomLayer
        imageIds={['dicomfile:0', 'dicomfile:1']}
        archiveKey="img_20261009_ab12cd34"
      />,
    );
    // 当前 imageId=dicomfile:0：只恢复 img0 的注释（img1 与 legacy 兜底帧1 不恢复）
    await waitFor(() => expect(cs.restoreAnnotations).toHaveBeenCalled());
    const restoreCalls = (cs.restoreAnnotations as unknown as {
      mock: { calls: Array<[HTMLElement, Array<{ uuid?: string }>]> };
    }).mock.calls;
    const firstCall = restoreCalls[0][1];
    expect(firstCall.map((a) => a.uuid)).toEqual(['a-img0']);
    expect(cs.clearAnnotations).toHaveBeenCalled();

    // 翻到帧 1（currentImageId 变为 dicomfile:1）：清场后恢复 img1 + legacy 兜底
    rerender(
      <WorkspaceDicomLayer
        imageIds={['dicomfile:0', 'dicomfile:1']}
        archiveKey="img_20261009_ab12cd34"
      />,
    );
    cs.scrollStack.mockImplementation(() => 1);
    (cs.getCurrentImageId as unknown as { mockImplementation: (f: () => string) => void })
      .mockImplementation(() => 'dicomfile:1');
    fireEvent.keyDown(window, { key: 'ArrowRight' });
    await waitFor(() => {
      const calls = (cs.restoreAnnotations as unknown as {
        mock: { calls: Array<[HTMLElement, Array<{ uuid?: string }>]> };
      }).mock.calls;
      const last = calls[calls.length - 1][1];
      expect(last.map((a) => a.uuid).sort()).toEqual(['a-img1', 'a-legacy']);
    });
  });

  it('A.52：cornerstone 异步加载失败 → 医生可见「影像无法渲染」（不再静默黑屏）', async () => {
    render(<WorkspaceDicomLayer imageIds={['dicomfile:0']} />);
    await waitFor(() => expect(cs.displayImage).toHaveBeenCalled());
    // 真机实测报文：多帧每帧字节数为奇 → 总长/帧数 不整除
    const evt = new Event('unhandledrejection') as Event & { reason?: unknown };
    evt.reason = new Error('Invalid frame length: 1494928 for 7 frames');
    window.dispatchEvent(evt);
    const banner = await screen.findByText(/影像无法渲染/);
    expect(banner).toBeInTheDocument();
    expect(banner.getAttribute('data-region')).toBe('dicom-load-error');
  });

  it('A.52：与本组件无关的异步错误不误报', async () => {
    render(<WorkspaceDicomLayer imageIds={['dicomfile:0']} />);
    await waitFor(() => expect(cs.displayImage).toHaveBeenCalled());
    const evt = new Event('unhandledrejection') as Event & { reason?: unknown };
    evt.reason = new Error('some unrelated network glitch');
    window.dispatchEvent(evt);
    await waitFor(() => expect(screen.queryByText(/影像无法渲染/)).toBeNull());
  });

  it('A.52：坏 DICOM 的「no pixel data」错误也要出横幅（医生不能只看到黑屏）', async () => {
    render(<WorkspaceDicomLayer imageIds={['dicomfile:0']} />);
    await waitFor(() => expect(cs.displayImage).toHaveBeenCalled());
    const evt = new Event('error') as ErrorEvent;
    Object.defineProperty(evt, 'message', {
      value: 'loadImageFromNaturalizedMetadata: no pixel data in NATURALIZED for imageId dicomfile:0',
    });
    window.dispatchEvent(evt);
    expect(await screen.findByText(/影像无法渲染/)).toBeInTheDocument();
  });

  it('A.52 分期②：多帧出 cine 播放/速度控件；播放后帧在推进，暂停即停', async () => {
    render(<WorkspaceDicomLayer imageIds={['dicomfile:0', 'dicomfile:1', 'dicomfile:2']} />);
    await waitFor(() => expect(cs.displayImage).toHaveBeenCalled());
    const play = screen.getByLabelText('播放');
    expect(screen.getByText('8fps')).toBeInTheDocument();

    fireEvent.click(play);
    expect(screen.getByLabelText('暂停')).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 220)); // 8fps → 约 1~2 次推进
    const callsWhilePlaying = cs.scrollStack.mock.calls.length;
    expect(callsWhilePlaying).toBeGreaterThan(0);

    fireEvent.click(screen.getByLabelText('暂停'));
    await new Promise((r) => setTimeout(r, 220));
    expect(cs.scrollStack.mock.calls.length).toBe(callsWhilePlaying);
  });

  it('A.52 分期②：末帧回绕到首帧（cine 语义，不停在末帧）', async () => {
    cs.getStackIndex.mockReturnValue(2); // 3 帧栈的末帧
    render(<WorkspaceDicomLayer imageIds={['dicomfile:0', 'dicomfile:1', 'dicomfile:2']} />);
    await waitFor(() => expect(cs.displayImage).toHaveBeenCalled());
    fireEvent.click(screen.getByLabelText('播放'));
    await new Promise((r) => setTimeout(r, 200));
    expect(cs.scrollStack).toHaveBeenCalledWith(expect.anything(), -2); // 回绕 delta
  });

  it('A.52 分期②：单帧不出 cine 控件（没有可播的东西）', async () => {
    render(<WorkspaceDicomLayer imageIds={['dicomfile:0']} />);
    await waitFor(() => expect(cs.displayImage).toHaveBeenCalled());
    expect(screen.queryByLabelText('播放')).toBeNull();
  });

  it('A.52 分期②：播放中强制切回窗宽窗位（测量工具停用，避免归属错帧）', async () => {
    render(<WorkspaceDicomLayer imageIds={['dicomfile:0', 'dicomfile:1']} />);
    await waitFor(() => expect(cs.displayImage).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('button', { name: '长度' }));
    await waitFor(() => expect(cs.activateTool).toHaveBeenCalled());
    cs.activateTool.mockClear();
    fireEvent.click(screen.getByLabelText('播放'));
    await waitFor(() => expect(cs.activateTool).toHaveBeenCalled());
  });

  it('A.52 分期②：空格切换播放/暂停', async () => {
    render(<WorkspaceDicomLayer imageIds={['dicomfile:0', 'dicomfile:1']} />);
    await waitFor(() => expect(cs.displayImage).toHaveBeenCalled());
    fireEvent.keyDown(window, { code: 'Space', key: ' ' });
    expect(await screen.findByLabelText('暂停')).toBeInTheDocument();
    fireEvent.keyDown(window, { code: 'Space', key: ' ' });
    expect(await screen.findByLabelText('播放')).toBeInTheDocument();
  });

  it('A.52 分期②：关键帧直达（targetFrame）按 delta 滚到目标帧', async () => {
    const { rerender } = render(
      <WorkspaceDicomLayer imageIds={['dicomfile:0', 'dicomfile:1', 'dicomfile:2', 'dicomfile:3']} />,
    );
    await waitFor(() => expect(cs.displayImage).toHaveBeenCalled());
    cs.scrollStack.mockClear();
    cs.getStackIndex.mockReturnValue(0);
    rerender(
      <WorkspaceDicomLayer
        imageIds={['dicomfile:0', 'dicomfile:1', 'dicomfile:2', 'dicomfile:3']}
        targetFrame={{ index: 2, nonce: 1 }}
      />,
    );
    await waitFor(() => expect(cs.scrollStack).toHaveBeenCalledWith(expect.anything(), 2));
  });

  it('A.52：帧归属上报的是**后端** image_id（不是 cornerstone 句柄）', async () => {
    const onFrameChange = vi.fn();
    render(
      <WorkspaceDicomLayer
        imageIds={['dicomfile:0?frame=1']}
        frameOwners={['img_backend_7']}
        onFrameChange={onFrameChange}
      />,
    );
    await waitFor(() => expect(onFrameChange).toHaveBeenCalled());
    expect(onFrameChange).toHaveBeenCalledWith(0, 'img_backend_7');
  });
});
