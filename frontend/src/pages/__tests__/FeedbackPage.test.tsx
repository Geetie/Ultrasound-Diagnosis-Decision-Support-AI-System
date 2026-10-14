import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const postMock = vi.fn();
// A.62 批 3 补：本页在 A.61 起还会 `get('/eval-set/stats')` 拉评测集进度。
// 只 mock post、不 mock get → `apiClient.get` 是 undefined，组件一挂载就抛错（整文件 4 条全红）。
// 教训：**改一个页面就要把该页面的所有测试文件都跑一遍**，只跑新增的那一个会漏掉这种"新依赖打穿老用例"。
const getMock = vi.fn();

vi.mock('@/services/api', () => ({
  default: {
    post: (...args: unknown[]) => postMock(...args),
    get: (...args: unknown[]) => getMock(...args),
  },
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
import { useSessionStore } from '@/stores/sessionStore';

describe('FeedbackPage 语音输入（T-GB-FE-05）', () => {
  beforeEach(() => {
    postMock.mockReset();
    postMock.mockResolvedValue({ data: { ok: true } });
    getMock.mockReset();
    // 进度区块对本文件是"背景信息"：让它走"尚未采集"分支即可（专测在 FeedbackPageEvalProgress.test.tsx）
    getMock.mockResolvedValue({ data: { available: false, reason: '尚未采集（测试替身）' } });
    // 会话状态每个用例各自决定（默认无会话）
    useSessionStore.setState({ sessionId: null });
  });

  it('没有会话时不发请求，并给可读提示（不让医生拿到 404 错误码）', async () => {
    const user = userEvent.setup();
    render(<FeedbackPage />);

    await user.type(screen.getByPlaceholderText('输入相关字段名称'), 'boundary');
    await user.type(screen.getByPlaceholderText('AI输出的原始内容'), '边界清晰');
    await user.type(screen.getByPlaceholderText('修正后的正确内容'), '边界不清');
    await user.type(screen.getByPlaceholderText('说明修正原因'), '与图像不符');
    await user.click(screen.getByRole('button', { name: /提交反馈/ }));

    // 关键：**不发请求**（发出去只会拿到 404「判读请求不存在」，医生看不懂）
    await waitFor(() => expect(screen.getByText(/未关联会话/)).toBeInTheDocument());
    expect(postMock).not.toHaveBeenCalled();
    // 让 antd message 的调度先落地再收尾：否则 React 会在测试环境销毁后继续调度
    // → `ReferenceError: window is not defined`（unhandled error 会把 vitest 退出码变成 1，
    //   看起来像用例挂了，其实是环境时序噪声）
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
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
    // A.62 批 3：反馈必须挂在会话上（无会话不发请求）→ 先放一个会话进 store
    useSessionStore.setState({ sessionId: 'sess_test_1' });
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
    // A.62 批 3 契约：**不许自造判读 id** —— 留空表示"按本会话关联最近一次判读"
    // （服务端要求 request_id 必须是真实判读 id；自造 `fb_…` 会让医生提交必得 404）
    expect(payload.request_id).toBe('');
    expect(payload.request_id).not.toMatch(/^fb_/);
    expect(payload.session_id).toBe('sess_test_1');
  });

  it('渲染语音填写区块（医生可先选字段再口述）', () => {
    render(<FeedbackPage />);
    expect(screen.getByTestId('voice-fill-section')).toBeInTheDocument();
    expect(screen.getByLabelText('语音填写目标字段')).toBeInTheDocument();
  });
});
