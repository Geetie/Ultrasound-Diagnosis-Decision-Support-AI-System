import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import WorkspaceImageRegion, { segBelongsToLiveFrame } from '../WorkspaceImageRegion';
import type { SegmentationView } from '@/services/diagnosisAdapter';

/**
 * 影像区：分割旁路标注（bbox + 面积）与设计稿示意框的显示规则。
 *
 * 背景（2026-10-08 走查）：后端 2026-10-03 就在诊断结果里挂了 nodule_segmentation
 * （bbox/面积/概率/降级原因），前端从未渲染 —— 影像区里那两个「结节A/B」是设计稿
 * 写死的示意框，叠在真实影像上等于伪造标注。
 */

const SEG_OK: SegmentationView = {
  available: true,
  width: 640,
  height: 480,
  bbox: [100, 80, 300, 240],
  area_mm2: 123.4,
  area_px: 8200,
  mean_prob: 0.87,
};

describe('WorkspaceImageRegion · A.51 帧级归属门控', () => {
  const MULTI: SegmentationView = {
    ...SEG_OK,
    imageId: 'img_multi',
    frameIndex: 4,
    totalFrames: 9,
    selectionPolicy: 'best_mean_prob',
  };

  it('多帧影像：只有择优帧号匹配时才归属', () => {
    expect(segBelongsToLiveFrame(MULTI, { index: 4, imageId: 'img_multi' })).toBe(true);
    expect(segBelongsToLiveFrame(MULTI, { index: 3, imageId: 'img_multi' })).toBe(false);
  });

  it('多影像栈：image_id 不匹配即不归属（哪怕帧号相同）', () => {
    expect(segBelongsToLiveFrame({ ...MULTI, totalFrames: 1 }, { index: 0, imageId: 'img_other' })).toBe(false);
  });

  it('单帧影像：帧号一致即归属', () => {
    expect(
      segBelongsToLiveFrame(
        { ...SEG_OK, imageId: 'img_single', frameIndex: 0, totalFrames: 1 },
        { index: 0, imageId: 'img_single' },
      ),
    ).toBe(true);
  });

  it('available=false 与首帧前（liveFrame=null）语义', () => {
    expect(segBelongsToLiveFrame({ available: false }, { index: 0, imageId: 'x' })).toBe(false);
    expect(segBelongsToLiveFrame(MULTI, null)).toBe(true);
  });

  it('A.52 分期②：多帧且分割不在当前帧 → 出「跳到该帧」按钮（一键直达）', () => {
    render(
      <WorkspaceImageRegion
        dicomImageIds={['dicomfile:0?frame=1', 'dicomfile:0?frame=2']}
        dicomFrameOwners={['img_multi', 'img_multi']}
        segmentation={MULTI}
      />,
    );
    const jump = screen.getByRole('button', { name: /跳到第 \d+ 帧/ });
    expect(jump).toBeInTheDocument();
    expect(jump.getAttribute('data-region')).toBe('segmentation-jump');
  });

  it('A.52 分期②：单帧分割（totalFrames=1）不出直达按钮（没有可跳的帧）', () => {
    render(
      <WorkspaceImageRegion
        dicomImageIds={['dicomfile:0?frame=1', 'dicomfile:0?frame=2']}
        dicomFrameOwners={['img_single', 'img_single']}
        segmentation={{ ...SEG_OK, imageId: 'img_single', frameIndex: 0, totalFrames: 1 }}
      />,
    );
    expect(screen.queryByRole('button', { name: /跳到第 \d+ 帧/ })).toBeNull();
  });
});

describe('WorkspaceImageRegion · 分割标注', () => {
  it('available=true 时渲染 SVG bbox 与面积标签', () => {
    render(<WorkspaceImageRegion dicomImageId="dicomfile:0" segmentation={SEG_OK} />);
    const svg = screen.getByLabelText('病灶分割标注');
    expect(svg).toBeInTheDocument();
    expect(svg.getAttribute('viewBox')).toBe('0 0 640 480');
    expect(screen.getByText(/病灶 123\.4 mm² · 87%/)).toBeInTheDocument();
  });

  it('无 PixelSpacing 时用像素面积，不猜 mm²（后端契约：宁缺不猜）', () => {
    render(
      <WorkspaceImageRegion
        dicomImageId="dicomfile:0"
        segmentation={{ ...SEG_OK, area_mm2: null }}
      />,
    );
    expect(screen.getByText(/病灶 8200 px²/)).toBeInTheDocument();
  });

  it('available=false 时显示降级原因 —— 绝不显示成"未检出结节"（后端契约）', () => {
    render(
      <WorkspaceImageRegion
        dicomImageId="dicomfile:0"
        segmentation={{ available: false, reason: 'not_enabled' }}
      />,
    );
    expect(screen.getByText(/未做分割：分割旁路未启用/)).toBeInTheDocument();
    expect(screen.queryByLabelText('病灶分割标注')).toBeNull();
  });

  it('未知原因原文透传，不静默吞掉', () => {
    render(
      <WorkspaceImageRegion
        dicomImageId="dicomfile:0"
        segmentation={{ available: false, reason: 'weird_case' }}
      />,
    );
    expect(screen.getByText(/未做分割：weird_case/)).toBeInTheDocument();
  });

  it('无真实影像时不渲染分割层（标注跟着影像走，不悬浮在占位上）', () => {
    render(<WorkspaceImageRegion segmentation={SEG_OK} />);
    expect(screen.queryByLabelText('病灶分割标注')).toBeNull();
  });

  it('真实影像显示时隐藏设计稿示意框（结节A/B 与 18.4×12.1mm 是写死的）', () => {
    render(<WorkspaceImageRegion dicomImageId="dicomfile:0" />);
    expect(screen.queryByText('结节 A · 未校验')).toBeNull();
    expect(screen.queryByText('结节 B · 已校验')).toBeNull();
    expect(screen.queryByText('18.4 × 12.1 mm')).toBeNull();
  });

  it('设计预览态（无真实影像）保留示意框 —— 设计契约仍按画布还原', () => {
    render(<WorkspaceImageRegion />);
    expect(screen.getByText('结节 A · 未校验')).toBeInTheDocument();
    expect(screen.getByText('结节 B · 已校验')).toBeInTheDocument();
    expect(screen.getByText('18.4 × 12.1 mm')).toBeInTheDocument();
  });
});

describe('WorkspaceImageRegion · A.56 器官错配引导', () => {
  /**
   * 本系统按**声明的部位**出结论。装心脏图却声明甲状腺时，会得到「结节 TI-RADS 分级 +
   * 病灶面积」压在心脏图上 = 错误器官语义（比不输出更危险）。护栏有二：
   *   ① 建会话页能声明 cardiac（ORGAN_PRESETS，PagesShell 测试钉住）；
   *   ② 分割不可用（心脏无结节模型）时给出可执行的纠正路径，而不是让医生对着
   *      「未做分割：分割旁路未启用」自己猜。
   */
  it('分割不可用 + 有真实影像 → 出现「改选部位」引导', () => {
    render(
      <WorkspaceImageRegion
        dicomImageId="dicomfile:0"
        segmentation={{ available: false, reason: 'not_enabled' }}
      />,
    );
    expect(screen.getByText(/并非当前声明的检查部位/)).toBeInTheDocument();
  });

  it('分割可用时不出现该引导（不给正常流程加噪声）', () => {
    render(<WorkspaceImageRegion dicomImageId="dicomfile:0" segmentation={SEG_OK} />);
    expect(screen.queryByText(/并非当前声明的检查部位/)).toBeNull();
  });

  it('无真实影像（设计预览态）不出现该引导 —— 引导必须挂在真实影像上', () => {
    render(<WorkspaceImageRegion segmentation={{ available: false, reason: 'not_enabled' }} />);
    expect(screen.queryByText(/并非当前声明的检查部位/)).toBeNull();
  });
});