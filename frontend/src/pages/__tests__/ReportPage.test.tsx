import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const getMock = vi.fn();
const postMock = vi.fn();
const patchMock = vi.fn();

vi.mock('@/services/api', () => ({
  default: {
    get: (...args: unknown[]) => getMock(...args),
    post: (...args: unknown[]) => postMock(...args),
    patch: (...args: unknown[]) => patchMock(...args),
  },
}));

import ReportPage from '../ReportPage';

const draftReport = {
  report_id: 'rep_1',
  request_id: 'req_1',
  session_id: 'sess_1',
  patient_id: 'P-1',
  organ: 'thyroid',
  status: 'draft',
  template_version: 'v1',
  content_digest: 'digest-1',
  signature_present: false,
  signature_verified: false,
  signing_key_name: null,
  signed_at: null,
  signed_by: null,
  pacs_adapter: null,
  pacs_status: null,
  pacs_message: null,
  pacs_attempts: null,
  pushed_at: null,
  created_at: '2026-09-25T10:00:00Z',
  content: { organ: 'thyroid' },
  rendered: '超声所见：甲状腺右叶结节 12mm\n诊断意见：TI-RADS 4a',
  completeness: {
    organ: 'thyroid',
    template: 'thyroid_standard',
    required: ['findings', 'measurement_summary', 'conclusion'],
    missing: ['measurement_summary'],
    passed: false,
  },
};

function renderPage(entry = '/report?request_id=req_1') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <ReportPage />
    </MemoryRouter>
  );
}

describe('ReportPage（M8 报告查看与签发）', () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
    getMock.mockResolvedValue({ data: draftReport });
    postMock.mockResolvedValue({ data: draftReport });
  });

  it('带上 request_id 进入时自动取报告并渲染正文', async () => {
    renderPage();

    await waitFor(() =>
      expect(screen.getByTestId('report-preview')).toHaveTextContent('TI-RADS 4a')
    );
    expect(getMock).toHaveBeenCalledWith('/report/by-request/req_1');
    expect(screen.getByTestId('report-status')).toHaveTextContent('草稿');
  });

  it('完整性不通过时列出缺失段落（用中文名，便于医生定位）', async () => {
    renderPage();

    const panel = await screen.findByTestId('completeness-panel');
    expect(panel).toHaveTextContent('存在缺失段落');
    expect(panel).toHaveTextContent('测量摘要');
  });

  it('未签发时不可推送，并说明原因', async () => {
    renderPage();
    await screen.findByTestId('report-status');

    expect(screen.getByRole('button', { name: /推送 PACS\/HIS/ })).toBeDisabled();
    expect(screen.getByText(/未签发不可推送/)).toBeInTheDocument();
  });

  it('签发后状态变为已签发且推送按钮解禁（完整性未过 → 先确认再签）', async () => {
    const user = userEvent.setup();
    postMock.mockResolvedValueOnce({
      data: { ...draftReport, status: 'signed', signature_present: true, signed_by: 'doc_1' },
    });
    renderPage();
    await screen.findByTestId('report-status');

    await user.click(screen.getByRole('button', { name: /签发报告/ }));
    // 完整性未过（draftReport.missing = ['measurement_summary']）→ 必须先过知情确认
    await screen.findByTestId('sign-confirm-ok');
    await user.click(screen.getByTestId('sign-confirm-ok'));

    await waitFor(() =>
      expect(screen.getByTestId('report-status')).toHaveTextContent('已签发')
    );
    expect(postMock).toHaveBeenCalledWith('/report/rep_1/sign', {});
    expect(screen.getByRole('button', { name: /推送 PACS\/HIS/ })).toBeEnabled();
  });

  it('完整性未过时点签发先弹缺失清单，取消则不发请求', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId('report-status');

    await user.click(screen.getByRole('button', { name: /签发报告/ }));
    // 缺失项要**列出来**，而不是一句"有缺失" —— 医生得知道具体缺什么才能决策。
    // 用 within(dialog) 限定：页面本身的完整性面板也有一份「缺失：」，不限定会撞重复。
    expect(await screen.findByTestId('sign-confirm-cancel')).toBeInTheDocument();
    expect(within(screen.getByRole('dialog')).getByText(/缺失：/)).toHaveTextContent('测量摘要');
    await user.click(screen.getByTestId('sign-confirm-cancel'));

    expect(postMock).not.toHaveBeenCalled();
  });

  it('完整性通过时点签发直接签，不再多弹一层', async () => {
    const user = userEvent.setup();
    getMock.mockResolvedValueOnce({
      data: {
        ...draftReport,
        completeness: {
          organ: 'thyroid',
          template: 'thyroid_standard',
          required: ['findings', 'conclusion'],
          missing: [],
          passed: true,
        },
      },
    });
    postMock.mockResolvedValueOnce({
      data: { ...draftReport, status: 'signed', signature_present: true },
    });
    renderPage();
    await screen.findByTestId('report-status');

    await user.click(screen.getByRole('button', { name: /签发报告/ }));

    await waitFor(() =>
      expect(screen.getByTestId('report-status')).toHaveTextContent('已签发')
    );
    // 没有缺失就不该打扰医生 —— 弹窗只在真正有缺失时出现
    expect(screen.queryByTestId('sign-confirm-ok')).toBeNull();
  });

  it('缺「超声所见」（阻断段）→ 签发禁用并给补录面板，而不是弹确认', async () => {
    const user = userEvent.setup();
    getMock.mockResolvedValueOnce({
      data: {
        ...draftReport,
        completeness: {
          organ: 'thyroid',
          template: 'thyroid_standard',
          required: ['findings', 'conclusion'],
          missing: ['findings'],
          blocking: ['findings'],
          passed: false,
        },
      },
    });
    renderPage();
    await screen.findByTestId('report-status');

    // 阻断段缺失：签发按钮禁用（服务端 E1030 也会拒），不能靠医生点一次才明白
    expect(screen.getByRole('button', { name: /签发报告/ })).toBeDisabled();
    // 出路就在同页：补录面板出现，并标注这一段是必需
    expect(screen.getByTestId('supplement-panel')).toBeInTheDocument();
    expect(screen.getByText(/（必需，补录后才能签发）/)).toBeInTheDocument();
    expect(screen.getByTestId('supplement-input-findings')).toBeInTheDocument();
  });

  it('补录保存 → 调 PATCH → 完整性恢复 → 签发解锁', async () => {
    const user = userEvent.setup();
    // 第一次 GET：缺 findings + measurement_summary
    getMock.mockResolvedValueOnce({
      data: {
        ...draftReport,
        completeness: {
          organ: 'thyroid',
          template: 'thyroid_standard',
          required: ['findings', 'measurement_summary'],
          missing: ['findings', 'measurement_summary'],
          blocking: ['findings'],
          passed: false,
        },
      },
    });
    renderPage();
    await screen.findByTestId('report-status');

    await user.type(screen.getByTestId('supplement-input-findings'), '甲状腺右叶低回声结节');
    await user.type(screen.getByTestId('supplement-input-measurement_summary'), '长径 12mm');
    patchMock.mockResolvedValueOnce({
      data: {
        ...draftReport,
        rendered: '超声所见：甲状腺右叶低回声结节',
        completeness: {
          organ: 'thyroid',
          template: 'thyroid_standard',
          required: ['findings', 'measurement_summary'],
          missing: [],
          blocking: [],
          passed: true,
        },
      },
    });
    await user.click(screen.getByTestId('supplement-save'));

    await waitFor(() =>
      expect(patchMock).toHaveBeenCalledWith('/report/rep_1', {
        sections: { findings: '甲状腺右叶低回声结节', measurement_summary: '长径 12mm' },
      })
    );
    await waitFor(() => expect(screen.queryByTestId('supplement-panel')).toBeNull());
    expect(screen.getByRole('button', { name: /签发报告/ })).toBeEnabled();
  });

  it('签名校验未通过时明确报出"正文可能已被改动"', async () => {
    const user = userEvent.setup();
    postMock.mockResolvedValueOnce({
      data: { report_id: 'rep_1', signature_present: true, signature_verified: false },
    });
    renderPage();
    await screen.findByTestId('report-status');

    await user.click(screen.getByRole('button', { name: /校验签名/ }));

    await waitFor(() =>
      expect(screen.getByTestId('verify-note')).toHaveTextContent('签名校验未通过')
    );
  });

  it('没有 request_id 时提示先填 ID，且不发起请求', async () => {
    const user = userEvent.setup();
    renderPage('/report');
    getMock.mockClear();

    await user.click(screen.getByRole('button', { name: /生成草稿/ }));

    expect(postMock).not.toHaveBeenCalled();
    expect(getMock).not.toHaveBeenCalled();
  });
});
