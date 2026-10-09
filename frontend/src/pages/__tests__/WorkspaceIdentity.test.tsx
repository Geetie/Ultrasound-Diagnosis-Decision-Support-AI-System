import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import WorkspacePage from '../WorkspacePage';

/**
 * A.36 P0：常驻条身份必须数据驱动。
 * 写死「张** · 女 · 52 岁 · 门诊」时换任何患者都显示同一人 —— 患者身份错显
 * 是最高级别临床风险。缺失数据时如实显示不可用，不猜、不占位假人。
 */
describe('WorkspacePage · 身份数据驱动', () => {
  it('会话 A 显示 A 的脱敏身份', () => {
    render(<WorkspacePage identity="张** · 女 · 52 岁" />);
    expect(screen.getByText('张** · 女 · 52 岁')).toBeInTheDocument();
    expect(screen.queryByText('患者信息不可用')).toBeNull();
  });

  it('切换会话身份随之切换（不残留上一位患者）', () => {
    const { unmount } = render(<WorkspacePage identity="李** · 男 · 41 岁" />);
    expect(screen.getByText('李** · 男 · 41 岁')).toBeInTheDocument();
    unmount();
    render(<WorkspacePage identity="王** · 男 · 58 岁" />);
    expect(screen.getByText('王** · 男 · 58 岁')).toBeInTheDocument();
    expect(screen.queryByText(/李\*\*/)).toBeNull();
  });

  it('无身份数据时如实显示不可用（不占位假人）', () => {
    render(<WorkspacePage />);
    expect(screen.getByText('患者信息不可用')).toBeInTheDocument();
  });
});
