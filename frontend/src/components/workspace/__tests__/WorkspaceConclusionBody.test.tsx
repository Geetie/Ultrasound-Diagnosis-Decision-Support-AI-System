import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import WorkspaceConclusionBody from '../WorkspaceConclusionBody';
import type { ConclusionView } from '@/services/diagnosisAdapter';

/**
 * 「分析中」进度显示（A.43）：已用时秒数 + 已生成字数（后端流式写 Redis →
 * 轮询带回 → 四层透传到此）。字数是实测事实（SSE 累积），不是编造的百分比。
 */
describe('WorkspaceConclusionBody · 分析中进度', () => {
  it('progress 态显示已用时秒数（无流式数据时只有秒数）', () => {
    render(<WorkspaceConclusionBody state="progress" progressChars={0} />);
    expect(screen.getByText(/已用时 0 秒/)).toBeInTheDocument();
    expect(screen.queryByText(/已生成/)).toBeNull();
  });

  it('有流式数据时显示「已生成 N 字」', () => {
    render(<WorkspaceConclusionBody state="progress" progressChars={528} />);
    expect(screen.getByText(/已生成 528 字/)).toBeInTheDocument();
  });

  it('progressChars 缺省（undefined）不显示字数 —— 默认沉默', () => {
    render(<WorkspaceConclusionBody state="progress" />);
    expect(screen.queryByText(/已生成/)).toBeNull();
  });

  it('安全预览（A.46）：已过段落级 SG 的文本显示在滚动区', () => {
    render(
      <WorkspaceConclusionBody
        state="progress"
        progressChars={528}
        generationPreview="甲状腺右叶见低回声结节，边界清。"
      />,
    );
    expect(screen.getByText(/甲状腺右叶见低回声结节/)).toBeInTheDocument();
  });

  it('SG 拒绝（generationPreview=null）预览不渲染 —— 临床红线：SG 前文本不上屏', () => {
    render(<WorkspaceConclusionBody state="progress" progressChars={528} />);
    expect(screen.queryByText(/甲状腺右叶/)).toBeNull();
  });
});

/**
 * 展开区新三段（03-4 鉴别诊断 / 03-6 跨模态对照 / 06-5 科研指标）。
 * 核心契约：**后端真给了才渲染，空段整段不出现**（不编造、无占位文案）。
 */
describe('WorkspaceConclusionBody · 展开区新三段（03-4/03-6/06-5）', () => {
  const BASE_VIEW: ConclusionView = { headline: '考虑良性结节（TI-RADS 3）', evidenceIds: [] };

  const DDX_VIEW: ConclusionView = {
    ...BASE_VIEW,
    differentialDiagnoses: [
      { rank: 1, disease_name: '乳头状癌', confidence: null, supporting_evidence: ['微钙化'], excluding_evidence: [] },
      { rank: 2, disease_name: '结节性甲状腺肿', confidence: 0.3, supporting_evidence: [], excluding_evidence: [] },
    ],
  };

  const CROSS_VIEW: ConclusionView = {
    ...BASE_VIEW,
    crossModalityHints: [{ modality: 'CT', finding: '多发砂砾样钙化', source_reference: 'ACR TI-RADS 2017' }],
  };

  const EXPAND_NAME = '展开依据 / 鉴别诊断 / 诊断过程';

  it('有鉴别诊断：展开后渲染逐条 rank/病名/可能性（null 显示 — 不显示 %）', () => {
    const { container } = render(<WorkspaceConclusionBody state="conclusion" view={DDX_VIEW} />);
    fireEvent.click(screen.getByRole('button', { name: EXPAND_NAME }));
    expect(container.querySelector('[data-region="conclusion-differential"]')).not.toBeNull();
    expect(container.querySelectorAll('[data-region="conclusion-differential"] li')).toHaveLength(2);
    expect(screen.getByText('乳头状癌')).toBeInTheDocument();
    expect(screen.getByText('—')).toBeInTheDocument();          // confidence=null → 禁止出现 %
    expect(screen.getByText('30%')).toBeInTheDocument();         // 0.3 → 30%
  });

  it('无鉴别诊断：展开后整段不渲染（反证判据：不渲染空段）', () => {
    const { container } = render(<WorkspaceConclusionBody state="conclusion" view={BASE_VIEW} />);
    fireEvent.click(screen.getByRole('button', { name: EXPAND_NAME }));
    expect(container.querySelector('[data-region="conclusion-differential"]')).toBeNull();
  });

  it('只有鉴别没有依据：展开按钮仍可用，且证据小节（0 条标题）不出现', () => {
    const { container } = render(<WorkspaceConclusionBody state="conclusion" view={DDX_VIEW} />);
    const btn = screen.getByRole('button', { name: EXPAND_NAME });
    expect(btn).not.toBeDisabled();
    fireEvent.click(btn);
    expect(container.textContent).not.toContain('本次结论引用的知识条目');
  });

  it('三类数据都没有：展开按钮禁用且不挂假展开区', () => {
    const { container } = render(<WorkspaceConclusionBody state="conclusion" view={BASE_VIEW} />);
    expect(screen.getByRole('button', { name: EXPAND_NAME })).toBeDisabled();
    expect(container.querySelector('[data-region="conclusion-evidence"]')).toBeNull();
  });

  it('有跨模态对照：modality/finding/出处可见，带「不构成…依据」定位文案', () => {
    const { container } = render(<WorkspaceConclusionBody state="conclusion" view={CROSS_VIEW} />);
    fireEvent.click(screen.getByRole('button', { name: EXPAND_NAME }));
    const region = container.querySelector('[data-region="conclusion-cross-modality"]');
    expect(region).not.toBeNull();
    expect(region?.textContent).toContain('CT');
    expect(region?.textContent).toContain('多发砂砾样钙化');
    expect(region?.textContent).toContain('ACR TI-RADS 2017');   // 出处必须显示
    expect(region?.textContent).toContain('不构成本次诊断依据');
  });

  it('无跨模态对照：整段不渲染（不得出现占位文案）', () => {
    const { container } = render(<WorkspaceConclusionBody state="conclusion" view={BASE_VIEW} />);
    fireEvent.click(screen.getByRole('button', { name: EXPAND_NAME }));
    expect(container.querySelector('[data-region="conclusion-cross-modality"]')).toBeNull();
  });

  it('researchMetrics 有值时渲染 ResearchMetrics（data-testid），无值时不渲染', () => {
    const { container } = render(
      <WorkspaceConclusionBody state="conclusion" view={{ ...BASE_VIEW, researchMetrics: { ai_features: { feature_vector_hash: 'h1' } } }} />,
    );
    expect(container.querySelector('[data-testid="research-metrics"]')).not.toBeNull();
    const { container: c2 } = render(<WorkspaceConclusionBody state="conclusion" view={BASE_VIEW} />);
    expect(c2.querySelector('[data-testid="research-metrics"]')).toBeNull();
  });
});
