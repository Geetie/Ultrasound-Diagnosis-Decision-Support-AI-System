import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import WorkspacePage from '../WorkspacePage';

/**
 * 工作区的**导航出口**与**影像装载插槽**。
 *
 * 2026-10-05 真浏览器走查实测 `/ui/` 的 `btn=[]`：工作区是全站唯一不套 AppLayout
 * 的页面（通栏 900 骨架放不下侧栏），于是登录后医生被困在这里，10 个导航项一个都点不到。
 * 出口放在常驻条内 —— 条高 40 不变，900 骨架不受影响。
 */

describe('WorkspacePage · 导航出口', () => {
  it('给了 navItems 就渲染常驻条右侧的导航', () => {
    render(
      <WorkspacePage
        navItems={[
          { key: 'worklist', label: '工作列表', onClick: vi.fn() },
          { key: 'settings', label: '设置', onClick: vi.fn() },
        ]}
      />
    );

    const nav = screen.getByRole('navigation', { name: '页面导航' });
    expect(nav).toBeInTheDocument();
    expect(screen.getByText('工作列表')).toBeInTheDocument();
    expect(screen.getByText('设置')).toBeInTheDocument();
  });

  it('点导航项真的触发对应回调（不是装饰）', () => {
    const onWorklist = vi.fn();
    render(
      <WorkspacePage navItems={[{ key: 'worklist', label: '工作列表', onClick: onWorklist }]} />
    );

    screen.getByText('工作列表').click();
    expect(onWorklist).toHaveBeenCalled();
  });

  it('不传 navItems 就不渲染导航（默认沉默）', () => {
    render(<WorkspacePage />);
    expect(screen.queryByRole('navigation', { name: '页面导航' })).toBeNull();
  });
});

describe('WorkspacePage · 影像装载插槽', () => {
  it('扫查态把 uploadSlot 渲染进结论区', () => {
    render(
      <WorkspacePage
        state="scanning"
        uploadSlot={<input type="file" aria-label="选择 DICOM 影像文件" />}
      />
    );

    expect(screen.getByLabelText('选择 DICOM 影像文件')).toBeInTheDocument();
    expect(screen.getByText('等待装载影像')).toBeInTheDocument();
  });

  it('判读态不再渲染装载入口（结论出来了就不该再让人换图）', () => {
    render(
      <WorkspacePage
        state="conclusion"
        uploadSlot={<input type="file" aria-label="选择 DICOM 影像文件" />}
      />
    );

    expect(screen.queryByLabelText('选择 DICOM 影像文件')).toBeNull();
  });

  it('不传 uploadSlot 时扫查态仍是可用界面，不留空洞', () => {
    render(<WorkspacePage state="scanning" />);
    expect(screen.getByText('等待装载影像')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
