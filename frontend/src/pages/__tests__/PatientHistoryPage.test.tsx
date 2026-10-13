import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const fetchHistory = vi.fn();
const fetchDicom = vi.fn();

vi.mock('@/services/patientHistoryService', () => ({
  fetchPatientHistory: (...args: unknown[]) => fetchHistory(...args),
}));

vi.mock('@/services/imagingService', () => ({
  fetchDicomForDisplay: (...args: unknown[]) => fetchDicom(...args),
}));

// DICOM 层单测在别处覆盖（cornerstone 初始化）；这里只验证"并排位置有没有给它喂帧"
vi.mock('@/components/workspace/WorkspaceDicomLayer', () => ({
  default: ({ imageIds }: { imageIds: string[] }) => (
    <div data-testid="dicom-layer">{imageIds.join(',')}</div>
  ),
}));

import PatientHistoryPage, { areaDeltaLabel } from '../PatientHistoryPage';

function item(over: Record<string, unknown> = {}) {
  return {
    session_id: 's1',
    created_at: '2026-09-01T09:00:00Z',
    status: 'completed',
    target_organs: ['thyroid'],
    organ: 'thyroid',
    knowledge_base_version: 'kb_v1.3.0',
    request_id: 'req_1',
    confidence: 0.7,
    conclusion: '结论一',
    report_id: null,
    report_status: null,
    report_signed: false,
    signed_at: null,
    image_count: 1,
    first_image_id: 'img_1',
    segmentation: { available: true, area_mm2: 100 },
    ...over,
  };
}

function resp(over: Record<string, unknown> = {}) {
  return {
    patient_id: 'P-1',
    patient_exists: true,
    total: 1,
    organs: ['thyroid'],
    comparable_organs: [],
    items: [item()],
    ...over,
  };
}

function renderPage(url = '/history?patient_id=P-1') {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <PatientHistoryPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  fetchHistory.mockReset();
  fetchDicom.mockReset();
  fetchDicom.mockResolvedValue('wadouri:server-copy');
});

describe('PatientHistoryPage', () => {
  it('缺少患者标识 → 出输入框引导（不请求、不白屏）', async () => {
    renderPage('/history');
    expect(screen.getByText(/历史检查对比/)).toBeInTheDocument();
    expect(document.querySelector('[data-region="history-missing-patient"]')).not.toBeNull();
    expect(fetchHistory).not.toHaveBeenCalled();
  });

  it('患者不存在 → 明示「系统里没有这个患者」+ 建档引导', async () => {
    fetchHistory.mockResolvedValue(resp({ patient_exists: false, total: 0, items: [] }));
    renderPage();
    expect(await screen.findByText(/系统里没有这个患者/)).toBeInTheDocument();
    expect(document.querySelector('[data-region="history-not-exist"]')).not.toBeNull();
  });

  it('有患者但无检查 → 空态提示（不显示对比列）', async () => {
    fetchHistory.mockResolvedValue(resp({ total: 0, items: [], organs: [] }));
    renderPage();
    expect(await screen.findByText(/该患者暂无检查记录/)).toBeInTheDocument();
    expect(document.querySelector('[data-region="history-root"]')).not.toBeNull();
  });

  it('只有一次检查 → 明确说不可对比，只展示单次', async () => {
    fetchHistory.mockResolvedValue(resp());
    renderPage();
    expect(await screen.findByText(/只有一次检查，暂无可对比对象/)).toBeInTheDocument();
    expect(document.querySelector('[data-region="history-single"]')).not.toBeNull();
  });

  it('两次同器官 → 默认选中最近两次并给出面积变化', async () => {
    fetchHistory.mockResolvedValue(
      resp({
        total: 2,
        comparable_organs: ['thyroid'],
        items: [
          item({ session_id: 's2', created_at: '2026-10-01T09:00:00Z', segmentation: { available: true, area_mm2: 150 } }),
          item({ session_id: 's1', created_at: '2026-09-01T09:00:00Z', segmentation: { available: true, area_mm2: 100 } }),
        ],
      }),
    );
    renderPage();
    await waitFor(() =>
      expect(document.querySelector('[data-region="history-delta-area"]')).not.toBeNull(),
    );
    // +50% 且带两侧绝对值（100 → 150）
    expect(screen.getByText(/\+50\.0%/)).toBeInTheDocument();
    expect(screen.getByText(/100\.0 → 150\.0 mm²/)).toBeInTheDocument();
    // 并排两列都拿到了帧（服务端副本）
    await waitFor(() => expect(screen.getAllByTestId('dicom-layer').length).toBe(2));
  });

  it('A.57：默认配对优先「最近一次检查所在器官」，不是 comparable_organs 第一个', async () => {
    // 真机 p26 实测场景：最新两次是心脏（本轮刚做的），更早还有成对的甲状腺；
    // comparable_organs 按器官名排序把 thyroid 排在前面 → 旧逻辑默认并排了**旧甲状腺对**，
    // 医生看不到刚做完的两次对比（也与「默认选中最近两次同器官」的意图不符）。
    const cardiacNew = item({
      session_id: 'c-new', organ: 'cardiac', target_organs: ['cardiac'],
      created_at: '2026-10-13T05:42:26Z', segmentation: { available: true, area_mm2: 2936.8 },
    });
    const cardiacOld = item({
      session_id: 'c-old', organ: 'cardiac', target_organs: ['cardiac'],
      created_at: '2026-10-13T05:42:12Z', segmentation: { available: true, area_mm2: 2003.3 },
    });
    const thyroidNew = item({
      session_id: 't-new', created_at: '2026-10-13T04:22:47Z',
      segmentation: { available: true, area_mm2: 3205.8 },
    });
    const thyroidOld = item({
      session_id: 't-old', created_at: '2026-10-13T04:22:40Z',
      segmentation: { available: true, area_mm2: 3205.8 },
    });
    fetchHistory.mockResolvedValue(
      resp({
        total: 4,
        organs: ['cardiac', 'thyroid'],
        comparable_organs: ['thyroid', 'cardiac'], // 服务端顺序：thyroid 在前
        items: [cardiacNew, cardiacOld, thyroidNew, thyroidOld],
      }),
    );
    renderPage();
    await waitFor(() =>
      expect(document.querySelector('[data-region="history-delta-area"]')).not.toBeNull(),
    );
    // 配对的是**心脏**那两次（2003.3 → 2936.8 ≈ +46.6%）
    expect(screen.getByText(/2003\.3 → 2936\.8 mm²/)).toBeInTheDocument();
    expect(screen.getByText(/\+46\.6%/)).toBeInTheDocument();
    // 绝不是旧甲状腺对（3205.8 → 3205.8 = 0%）
    expect(screen.queryByText(/3205\.8 → 3205\.8 mm²/)).toBeNull();
  });

  it('两次部位不同 → 明确提示不做直接对比', async () => {
    fetchHistory.mockResolvedValue(
      resp({
        total: 2,
        organs: ['thyroid', 'cardiac'],
        comparable_organs: [],
        items: [
          item({ session_id: 's2', organ: 'cardiac', target_organs: ['cardiac'] }),
          item({ session_id: 's1', organ: 'thyroid' }),
        ],
      }),
    );
    renderPage();
    await waitFor(() =>
      expect(document.querySelector('[data-region="history-organ-mismatch"]')).not.toBeNull(),
    );
    const alert = document.querySelector('[data-region="history-organ-mismatch"]') as HTMLElement;
    expect(alert.textContent).toMatch(/两次检查部位不同/);
  });

  it('报告状态可见（已签发/草稿），未关联报告也说清楚', async () => {
    fetchHistory.mockResolvedValue(
      resp({
        total: 2,
        comparable_organs: ['thyroid'],
        items: [
          item({ session_id: 's2', report_id: 'rep_2', report_status: 'signed', report_signed: true }),
          item({ session_id: 's1' }),
        ],
      }),
    );
    renderPage();
    expect(await screen.findByText('报告已签发')).toBeInTheDocument();
    expect(screen.getByText('尚无报告')).toBeInTheDocument();
  });

  it('一侧无分割 → 面积变化说明原因而不是给假数字', async () => {
    fetchHistory.mockResolvedValue(
      resp({
        total: 2,
        comparable_organs: ['thyroid'],
        items: [
          item({ session_id: 's2', segmentation: { available: true, area_mm2: 150 } }),
          item({
            session_id: 's1',
            segmentation: { available: false, reason: 'bypass_disabled' },
          }),
        ],
      }),
    );
    renderPage();
    expect(await screen.findByText(/上次未做分割（bypass_disabled）/)).toBeInTheDocument();
  });

  it('areaDeltaLabel：面积 0 时不算变化率（避免除零得到 inf%）', () => {
    const left = item({ segmentation: { available: true, area_mm2: 0 } });
    const right = item({ segmentation: { available: true, area_mm2: 10 } });
    expect(areaDeltaLabel(left as never, right as never).text).toMatch(/无法算变化率/);
  });

  it('A.54 真机回归：跨器官**不给**测值变化（避免制造伪"病情变化"）', async () => {
    fetchHistory.mockResolvedValue(
      resp({
        total: 2,
        organs: ['thyroid', 'cardiac'],
        comparable_organs: [],
        items: [
          item({ session_id: 's2', organ: 'cardiac', segmentation: { available: true, area_mm2: 2003.3 } }),
          item({ session_id: 's1', organ: 'thyroid', segmentation: { available: true, area_mm2: 2608.4 } }),
        ],
      }),
    );
    renderPage();
    await waitFor(() =>
      expect(document.querySelector('[data-region="history-delta-suppressed"]')).not.toBeNull(),
    );
    expect(document.querySelector('[data-region="history-delta-area"]')).toBeNull();
    expect(screen.getByText(/不给出测值变化/)).toBeInTheDocument();
  });

  it('A.54 真机回归：影像容器必须有尺寸（DICOM 层是 absolute inset:0，无高度会塌成 0×0）', async () => {
    fetchHistory.mockResolvedValue(resp({ total: 1 }));
    renderPage();
    const wrap = await waitFor(() => {
      const el = document.querySelector('[data-region="history-image"]') as HTMLElement | null;
      expect(el).not.toBeNull();
      return el!;
    });
    expect(wrap.style.position).toBe('relative');
    expect(Number.parseInt(wrap.style.height, 10)).toBeGreaterThan(100);
  });

  it('A.54 真机回归：取字节失败要说明原因（不把"失败"混成"加载中"）', async () => {
    fetchDicom.mockRejectedValue(new Error('boom'));
    fetchHistory.mockResolvedValue(resp({ total: 1 }));
    renderPage();
    expect(await screen.findByText(/影像加载失败（服务端副本不可用/)).toBeInTheDocument();
  });
});
