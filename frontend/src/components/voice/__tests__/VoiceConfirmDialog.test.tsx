import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

const confirmVoiceCommand = vi.fn();

vi.mock('@/services/voiceService', () => ({
  confirmVoiceCommand: (...args: unknown[]) => confirmVoiceCommand(...args),
}));

import VoiceConfirmDialog from '../VoiceConfirmDialog';
import { parseVoiceCommand } from '@/services/voiceCommand';

describe('VoiceConfirmDialog（T-GB-FE-02）', () => {
  afterEach(() => {
    confirmVoiceCommand.mockReset();
  });

  it('展示解析出的指令与识别文本', () => {
    render(
      <VoiceConfirmDialog
        open
        command={parseVoiceCommand('开始甲状腺诊断')}
        sessionId="sess_1"
        onClose={vi.fn()}
      />
    );

    expect(screen.getByText('发起诊断')).toBeInTheDocument();
    expect(screen.getByDisplayValue('开始甲状腺诊断')).toBeInTheDocument();
  });

  it('参数缺失时禁用执行并提示补全（不猜测即执行）', () => {
    render(
      <VoiceConfirmDialog
        open
        command={parseVoiceCommand('开始诊断')}
        sessionId="sess_1"
        onClose={vi.fn()}
      />
    );

    expect(screen.getByText('指令信息不完整')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /确认执行/ })).toBeDisabled();
  });

  it('参数完整时确认执行并上报 confirmed=true', async () => {
    const user = userEvent.setup();
    confirmVoiceCommand.mockResolvedValue({
      session_id: 'sess_1',
      executed: true,
      action_taken: 'diagnosis_initiated',
      request_id: 'req_1',
    });
    const onExecuted = vi.fn();
    const onClose = vi.fn();

    render(
      <VoiceConfirmDialog
        open
        command={parseVoiceCommand('开始甲状腺诊断')}
        sessionId="sess_1"
        onClose={onClose}
        onExecuted={onExecuted}
      />
    );

    await user.click(screen.getByRole('button', { name: /确认执行/ }));

    await waitFor(() => expect(confirmVoiceCommand).toHaveBeenCalledTimes(1));
    const payload = confirmVoiceCommand.mock.calls[0][0];
    expect(payload).toMatchObject({
      sessionId: 'sess_1',
      commandType: 'diagnosis_trigger',
      organ: 'thyroid',
      confirmed: true,
    });
    expect(onExecuted).toHaveBeenCalledWith(
      expect.objectContaining({ request_id: 'req_1' })
    );
    expect(onClose).toHaveBeenCalled();
  });

  it('取消指令会上报 confirmed=false（安全审计需要知道医生拒绝了什么）', async () => {
    const user = userEvent.setup();
    confirmVoiceCommand.mockResolvedValue({
      session_id: 'sess_1',
      executed: false,
      action_taken: 'rejected_by_user',
    });
    const onClose = vi.fn();

    render(
      <VoiceConfirmDialog
        open
        command={parseVoiceCommand('开始甲状腺诊断')}
        sessionId="sess_1"
        onClose={onClose}
      />
    );

    await user.click(screen.getByRole('button', { name: /取消指令/ }));

    await waitFor(() => expect(confirmVoiceCommand).toHaveBeenCalledTimes(1));
    expect(confirmVoiceCommand.mock.calls[0][0]).toMatchObject({ confirmed: false });
    expect(onClose).toHaveBeenCalled();
  });

  it('无活跃会话时禁止执行', () => {
    render(
      <VoiceConfirmDialog
        open
        command={parseVoiceCommand('开始甲状腺诊断')}
        sessionId={null}
        onClose={vi.fn()}
      />
    );

    expect(screen.getByText(/当前没有活跃检查会话/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /确认执行/ })).toBeDisabled();
  });

  it('档位切换可改档位后执行', async () => {
    const user = userEvent.setup();
    confirmVoiceCommand.mockResolvedValue({ session_id: 'sess_1', executed: true });

    render(
      <VoiceConfirmDialog
        open
        command={parseVoiceCommand('改成实时模式')}
        sessionId="sess_1"
        onClose={vi.fn()}
      />
    );

    await user.click(screen.getByLabelText('全量模式'));
    await user.click(screen.getByRole('button', { name: /确认执行/ }));

    await waitFor(() => expect(confirmVoiceCommand).toHaveBeenCalledTimes(1));
    expect(confirmVoiceCommand.mock.calls[0][0]).toMatchObject({
      commandType: 'grade_change',
      gradeTarget: 'full',
      confirmed: true,
    });
  });

  it('提交失败时保留弹层（医生可重试）', async () => {
    const user = userEvent.setup();
    confirmVoiceCommand.mockRejectedValue(new Error('network'));
    const onClose = vi.fn();

    render(
      <VoiceConfirmDialog
        open
        command={parseVoiceCommand('开始甲状腺诊断')}
        sessionId="sess_1"
        onClose={onClose}
      />
    );

    await user.click(screen.getByRole('button', { name: /确认执行/ }));

    await waitFor(() => expect(confirmVoiceCommand).toHaveBeenCalled());
    expect(onClose).not.toHaveBeenCalled();
  });
});
