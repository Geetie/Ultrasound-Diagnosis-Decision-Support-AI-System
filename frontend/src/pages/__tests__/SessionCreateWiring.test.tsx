import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import SessionCreatePage from '../SessionCreatePage';
import { createSession } from '@/services/sessionService';
import { createFhirPatient, getFhirPatient, listPatients } from '@/services/fhirService';
import type { SessionInfo } from '@/types/api';

vi.mock('@/services/sessionService', () => ({ createSession: vi.fn() }));
vi.mock('@/services/fhirService', () => ({
  getFhirPatient: vi.fn(),
  createFhirPatient: vi.fn(),
  listPatients: vi.fn(),
}));

const mockCreate = vi.mocked(createSession);
const mockGetPatient = vi.mocked(getFhirPatient);
const mockCreatePatient = vi.mocked(createFhirPatient);
const mockListPatients = vi.mocked(listPatients);

/** axios 形状的错误 —— getApiErrorCode / getApiErrorMessage 只认 response.data */
function httpError(status: number, body: Record<string, unknown>) {
  return { response: { status, data: body } };
}

function makeSession(sessionId: string): SessionInfo {
  return {
    session_id: sessionId,
    patient_id: 'P1',
    device_id: 'dev-01',
    status: 'pending',
    target_organs: ['thyroid'],
    organ_statuses: {},
    knowledge_base_version: 'kb_v1.0.0',
    created_at: '2026-10-08T10:00:00Z',
    updated_at: '2026-10-08T10:00:00Z',
    expired_at: null,
    consent_status_at_creation: 'granted',
  };
}

/** 记录路由落点：断言「开始检查」后真的带着 session_id 进了工作区 */
function LocationProbe({ seen }: { seen: string[] }) {
  const loc = useLocation();
  seen.push(loc.pathname + loc.search);
  return null;
}

function renderPage() {
  const seen: string[] = [];
  render(
    <MemoryRouter initialEntries={['/session/new']}>
      <Routes>
        <Route path="/session/new" element={<SessionCreatePage />} />
        <Route path="/" element={<LocationProbe seen={seen} />} />
      </Routes>
    </MemoryRouter>,
  );
  return seen;
}

const PATIENT_OK = {
  resourceType: 'Patient' as const,
  id: 'P1',
  name: [{ text: 'P1' }],
  active: true,
};

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  // A.51 患者候选：默认空结果（多数用例不关心候选）。**必须给默认值** ——
  // 页面在输入 ≥2 字符后会防抖检索，mock 返回 undefined 会在 .then 处炸。
  mockListPatients.mockResolvedValue({ items: [], total: 0, offset: 0, limit: 6 });
});

describe('SessionCreatePage · 接线（2026-10-08）', () => {
  it('校验通过 + 填设备 → 开始检查 → 带 session_id 进入工作区，并记住设备号', async () => {
    const user = userEvent.setup();
    const seen = renderPage();
    mockGetPatient.mockResolvedValue(PATIENT_OK);
    mockCreate.mockResolvedValue(makeSession('sess_new_1'));

    await user.type(screen.getByLabelText('患者编号'), 'P1');
    await user.click(screen.getByRole('button', { name: '校验患者' }));
    expect(await screen.findByText(/已确认：P1/)).toBeInTheDocument();

    // 患者确认了但设备没填 —— 不允许提交（缺任一都不该凭空编一个 id）
    expect(screen.getByRole('button', { name: '开始检查' })).toBeDisabled();
    await user.type(screen.getByLabelText('设备编号'), 'dev-01');
    await user.click(screen.getByRole('button', { name: '开始检查' }));

    await waitFor(() =>
      expect(seen.some((s) => s.startsWith('/?session_id=sess_new_1'))).toBe(true),
    );
    // navigate('/') 丢 session_id 的旧缺陷 —— 这里钉死必须带
    expect(mockCreate).toHaveBeenCalledWith({
      patient_id: 'P1',
      device_id: 'dev-01',
      target_organs: ['thyroid'],
    });
    expect(localStorage.getItem('uai_last_device_id')).toBe('dev-01');
  });

  it('患者未签署知情同意（E1008）→ 给出找质控/科室负责人的指引，且无法提交', async () => {
    const user = userEvent.setup();
    renderPage();
    // consent 端点医生角色是 403，本来就不可能由医生补签 —— 指引必须如实
    mockGetPatient.mockRejectedValue(httpError(403, { error_code: 'E1008' }));

    await user.type(screen.getByLabelText('患者编号'), 'P1');
    await user.click(screen.getByRole('button', { name: '校验患者' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('知情同意');
    expect(screen.getByRole('button', { name: '开始检查' })).toBeDisabled();
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it('患者不存在（E1002）→ 出现建档按钮，建档成功后要求重新校验而不是假装可用', async () => {
    const user = userEvent.setup();
    renderPage();
    mockGetPatient.mockRejectedValue(
      httpError(404, { error_code: 'E1002', error_message: 'Patient not found: P404' }),
    );

    await user.type(screen.getByLabelText('患者编号'), 'P404');
    await user.click(screen.getByRole('button', { name: '校验患者' }));

    const createBtn = await screen.findByRole('button', { name: '为 P404 建档' });
    mockCreatePatient.mockResolvedValue({ ...PATIENT_OK, id: 'P404', name: [] });
    await user.click(createBtn);

    // 建档 ≠ 同意：明确要求重新校验，不能直接把状态置成已确认
    expect(await screen.findByText(/建档完成/)).toBeInTheDocument();
    expect(screen.queryByText(/已确认：/)).toBeNull();
    expect(screen.getByRole('button', { name: '开始检查' })).toBeDisabled();
  });

  it('校验通过但创建时同意已撤回（E1010 竞态）→ 状态退回同意指引，不跳转', async () => {
    const user = userEvent.setup();
    const seen = renderPage();
    mockGetPatient.mockResolvedValue(PATIENT_OK);
    mockCreate.mockRejectedValue(httpError(403, { error_code: 'E1010' }));

    await user.type(screen.getByLabelText('患者编号'), 'P1');
    await user.click(screen.getByRole('button', { name: '校验患者' }));
    await screen.findByText(/已确认：P1/);
    await user.type(screen.getByLabelText('设备编号'), 'dev-01');
    await user.click(screen.getByRole('button', { name: '开始检查' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('撤回');
    expect(seen).toEqual([]);
  });

  it('回填上次成功使用的设备号（重复检查时设备往往就是这一台）', async () => {
    localStorage.setItem('uai_last_device_id', 'dev-last');
    renderPage();
    expect(screen.getByLabelText('设备编号')).toHaveValue('dev-last');
  });
});

describe('SessionCreatePage · 患者候选（A.51 新增检索端点）', () => {
  it('输入 ≥2 字符 → 展示候选；点候选即校验（手工路径不受影响）', async () => {
    const user = userEvent.setup();
    renderPage();
    mockListPatients.mockResolvedValue({
      items: [
        {
          patient_id: 'P-1001',
          name: '张**',
          gender: 'female',
          consent_status: 'granted',
          last_session_at: '2026-10-10T08:00:00Z',
        },
      ],
      total: 1,
      offset: 0,
      limit: 6,
    });
    mockGetPatient.mockResolvedValue({
      resourceType: 'Patient',
      id: 'P-1001',
      name: [{ text: '张**' }],
      active: true,
    });

    await user.type(screen.getByLabelText('患者编号'), 'P-1');
    const suggestion = await screen.findByRole('button', { name: /张\*\*/ });
    expect(suggestion).toBeInTheDocument();

    await user.click(suggestion);

    expect(await screen.findByText(/已确认：张\*\*/)).toBeInTheDocument();
    expect(mockGetPatient).toHaveBeenCalledWith('P-1001');
    // 点选后候选收起（避免遮住确认信息）
    expect(screen.queryByRole('button', { name: /张\*\*/ })).toBeNull();
  });

  it('检索失败静默兜底：不弹错误，手工录入 + 校验仍可用', async () => {
    const user = userEvent.setup();
    renderPage();
    mockListPatients.mockRejectedValue(new Error('boom'));
    mockGetPatient.mockResolvedValue(PATIENT_OK);

    await user.type(screen.getByLabelText('患者编号'), 'P9');
    await user.click(screen.getByRole('button', { name: '校验患者' }));

    expect(await screen.findByText(/已确认：P1/)).toBeInTheDocument();
    expect(screen.queryByText(/boom/)).toBeNull();
  });
});
