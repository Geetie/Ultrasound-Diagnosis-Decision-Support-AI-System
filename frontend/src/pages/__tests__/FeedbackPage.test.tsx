import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const postMock = vi.fn();

vi.mock('@/services/api', () => ({
  default: { post: (...args: unknown[]) => postMock(...args) },
}));

// 语音听写组件在 jsdom 下无法真实录音：这里替身只负责把文本回调出来，
// 被测的是页面自身的"字段选择 + 文本合并 + 仍可编辑"逻辑。
vi.mock('@/components/voice/VoiceDictationButton', () => ({
  default: ({ onTranscript, label }: { onTranscript: (text: string) => void; label?: string }) => (
    <button type="button" onClick={() => onTranscript('结节边界不清')}>
      {label ?? '语音填写'}
    </button>
  ),
}));

import FeedbackPage from '../FeedbackPage';

describe('FeedbackPage 语音输入（T-GB-FE-05）', () => {
  beforeEach(() => {
    postMock.mockReset();
    postMock.mockResolvedValue({ data: { ok: true } });
  });

  it('语音转写落到所选字段且文本可继续编辑', async () => {
    const user = userEvent.setup();
    render(<FeedbackPage />);

    // 默认目标字段为"原因"
    await user.click(screen.getByRole('button', { name: /开始口述/ }));

    const reasonInput = screen.getByPlaceholderText('说明修正原因') as HTMLTextAreaElement;
    await waitFor(() => expect(reasonInput).toHaveValue('结节边界不清'));

    // 转写后仍可编辑
    await user.type(reasonInput, '，建议随访');
    expect(reasonInput).toHaveValue('结节边界不清，建议随访');
  });

  it('追加第二次转写时以句号连接，避免前后文粘连', async () => {
    const user = userEvent.setup();
    render(<FeedbackPage />);

    const dictation = screen.getByRole('button', { name: /开始口述/ });
    await user.click(dictation);
    await user.click(dictation);

    const reasonInput = screen.getByPlaceholderText('说明修正原因') as HTMLTextAreaElement;
    await waitFor(() =>
      expect(reasonInput.value).toBe('结节边界不清。结节边界不清')
    );
  });

  it('提交反馈时带上 L2/L3 级别与内容', async () => {
    const user = userEvent.setup();
    render(<FeedbackPage />);

    await user.type(screen.getByPlaceholderText('输入相关字段名称'), 'boundary');
    await user.type(screen.getByPlaceholderText('AI输出的原始内容'), '边界清晰');
    await user.type(screen.getByPlaceholderText('修正后的正确内容'), '边界不清');
    await user.type(screen.getByPlaceholderText('说明修正原因'), '与图像不符');
    await user.click(screen.getByRole('button', { name: /提交反馈/ }));

    await waitFor(() => expect(postMock).toHaveBeenCalledTimes(1));
    const [url, payload] = postMock.mock.calls[0];
    expect(url).toBe('/feedback');
    expect(payload).toMatchObject({
      feedback_level: 'L2',
      feedback_type: 'correction',
      content: expect.objectContaining({ field: 'boundary', reason: '与图像不符' }),
    });
  });

  it('渲染语音填写区块（医生可先选字段再口述）', () => {
    render(<FeedbackPage />);
    expect(screen.getByTestId('voice-fill-section')).toBeInTheDocument();
    expect(screen.getByLabelText('语音填写目标字段')).toBeInTheDocument();
  });
});
