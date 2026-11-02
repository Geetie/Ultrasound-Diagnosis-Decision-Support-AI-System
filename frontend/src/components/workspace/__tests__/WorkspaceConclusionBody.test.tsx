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

/**
 * 禁忌命中明细（A.121 / G34）。
 * 此前 SG 只给一句"检测到 N 条禁忌"，rule_id 与 description 全丢在网关内部；
 * 现在如实列出命中的规则，供医生复核。**空数组整块不渲染**（不编造"没有禁忌"）。
 */
describe('WorkspaceConclusionBody · 禁忌命中明细（G34）', () => {
  const BASE: ConclusionView = { headline: '考虑良性结节（TI-RADS 3）', evidenceIds: [] };

  it('有命中：渲染描述与规则 id', () => {
    const { container } = render(
      <WorkspaceConclusionBody
        state="conclusion"
        view={{
          ...BASE,
          contraindicationMatches: [
            {
              ruleId: 'thyroid_pregnancy_fna',
              description: '妊娠期避免 FNA',
              severity: 'high',
              organ: 'thyroid',
            },
          ],
        }}
      />,
    );
    const block = container.querySelector('[data-node="2:27"]');
    expect(block).not.toBeNull();
    expect(screen.getByText('禁忌命中（需人工确认）')).toBeInTheDocument();
    expect(screen.getByText(/妊娠期避免 FNA/)).toBeInTheDocument();
    expect(screen.getByText(/thyroid_pregnancy_fna/)).toBeInTheDocument();
  });

  it('无命中：整块不渲染（不显示"没有禁忌"这种说法）', () => {
    const { container } = render(
      <WorkspaceConclusionBody state="conclusion" view={{ ...BASE, contraindicationMatches: [] }} />,
    );
    expect(container.querySelector('[data-node="2:27"]')).toBeNull();
    expect(screen.queryByText('禁忌命中（需人工确认）')).toBeNull();
  });

  it('只有 description 没有 ruleId 也能显示', () => {
    render(
      <WorkspaceConclusionBody
        state="conclusion"
        view={{ ...BASE, contraindicationMatches: [{ description: '凝血功能障碍避免穿刺' }] }}
      />,
    );
    expect(screen.getByText('凝血功能障碍避免穿刺')).toBeInTheDocument();
  });
});

/**
 * 转人工原因（A.122 / G24）：此前 review_reason 在工作区无读者，
 * 医生只看到"需复核"标记却不知道为什么。
 */
describe('WorkspaceConclusionBody · 转人工原因（G24）', () => {
  const BASE: ConclusionView = { headline: '考虑良性结节（TI-RADS 3）', evidenceIds: [] };

  it('有原因：显示"转人工原因 + 中文说明"', () => {
    const { container } = render(
      <WorkspaceConclusionBody
        state="conclusion"
        view={{ ...BASE, reviewReason: '一致性校验未通过', reviewReasonCode: 'consistency_failed' }}
      />,
    );
    expect(container.querySelector('[data-region="review-reason"]')).not.toBeNull();
    expect(screen.getByText('转人工原因')).toBeInTheDocument();
    expect(screen.getByText('一致性校验未通过')).toBeInTheDocument();
  });

  it('无原因：整块不渲染', () => {
    const { container } = render(<WorkspaceConclusionBody state="conclusion" view={BASE} />);
    expect(container.querySelector('[data-region="review-reason"]')).toBeNull();
    expect(screen.queryByText('转人工原因')).toBeNull();
  });
});

/** 处置建议降级提示（A.123 / G33）：特征不足时医生必须知道建议被缩水了。 */
describe('WorkspaceConclusionBody · 处置建议降级（G33）', () => {
  const BASE: ConclusionView = { headline: '考虑良性结节（TI-RADS 3）', evidenceIds: [] };

  it('actionsDegraded=true → 在建议后追加降级说明', () => {
    const { container } = render(
      <WorkspaceConclusionBody
        state="conclusion"
        view={{ ...BASE, advice: '6 个月复查', actionsDegraded: true }}
      />,
    );
    const block = container.querySelector('[data-region="advice-degraded"]');
    expect(block).not.toBeNull();
    expect(screen.getByText(/处置建议已降级/)).toBeInTheDocument();
  });

  it('无标记 → 不出现降级说明（不制造噪音）', () => {
    const { container } = render(
      <WorkspaceConclusionBody state="conclusion" view={{ ...BASE, advice: '6 个月复查' }} />,
    );
    expect(container.querySelector('[data-region="advice-degraded"]')).toBeNull();
  });
});

/** 结构化处置建议 / 测量迁移（A.125 / G38）：此前只在死页渲染。 */
describe('WorkspaceConclusionBody · 处置建议与测量（G38）', () => {
  const BASE: ConclusionView = { headline: '考虑良性结节（TI-RADS 3）', evidenceIds: [] };

  it('有处置建议：逐条渲染说明/科室/紧急度/出处', () => {
    const { container } = render(
      <WorkspaceConclusionBody
        state="conclusion"
        view={{
          ...BASE,
          clinicalActions: [
            {
              actionType: 'fna',
              department: '超声科',
              urgency: '紧急',
              description: '建议超声引导下细针穿刺',
              reference: 'ACR TI-RADS 2017',
            },
          ],
        }}
      />,
    );
    expect(container.querySelector('[data-region="clinical-actions"]')).not.toBeNull();
    expect(screen.getByText(/细针穿刺/)).toBeInTheDocument();
    expect(screen.getByText(/ACR TI-RADS 2017/)).toBeInTheDocument();
  });

  it('无处置建议：整块不渲染', () => {
    const { container } = render(<WorkspaceConclusionBody state="conclusion" view={BASE} />);
    expect(container.querySelector('[data-region="clinical-actions"]')).toBeNull();
  });

  it('测量：渲染 mm 数值；default 像素间距时标注估算', () => {
    const { container } = render(
      <WorkspaceConclusionBody
        state="conclusion"
        view={{ ...BASE, measurementView: { longMm: 12.5, shortMm: 8, pixelSpacingSource: 'default' } }}
      />,
    );
    const block = container.querySelector('[data-region="measurement"]');
    expect(block).not.toBeNull();
    expect(block!.textContent).toContain('长径 12.5 mm');
    expect(block!.textContent).toContain('短径 8 mm');
    expect(block!.textContent).toContain('估算值');
  });

  it('测量：dicom 像素间距时不标估算', () => {
    const { container } = render(
      <WorkspaceConclusionBody
        state="conclusion"
        view={{ ...BASE, measurementView: { longMm: 10, pixelSpacingSource: 'dicom' } }}
      />,
    );
    expect(container.querySelector('[data-region="measurement"]')!.textContent).not.toContain('估算');
  });
});

/** 证据来源（A.126 / G23）：显示"文档·章节（chunk_id）"，缺来源回落裸 ID。 */
describe('WorkspaceConclusionBody · 证据来源（G23）', () => {
  const BASE: ConclusionView = { headline: '考虑良性结节（TI-RADS 3）', evidenceIds: ['c1', 'c2'] };
  const EXPAND = '展开依据 / 鉴别诊断 / 诊断过程';

  it('有来源：主行显示文档·章节，次要行是 chunk_id', () => {
    render(
      <WorkspaceConclusionBody
        state="conclusion"
        view={{
          ...BASE,
          evidenceSources: { c1: { docId: 'acr_ti_rads_2017', sectionPath: '第4章/4.2' } },
        }}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: EXPAND }));
    expect(screen.getByText(/acr_ti_rads_2017 · 第4章\/4\.2/)).toBeInTheDocument();
    expect(screen.getByText(/（c1）/)).toBeInTheDocument();
  });

  it('无来源：回落裸 ID，并保留"暂未提供点击跳转"说明', () => {
    render(<WorkspaceConclusionBody state="conclusion" view={BASE} />);
    fireEvent.click(screen.getByRole('button', { name: EXPAND }));
    expect(screen.getByText('c1')).toBeInTheDocument();
    expect(screen.getByText(/暂未提供点击跳转/)).toBeInTheDocument();
  });
});

/** 规则 ID（A.127 / G31）：处置建议可追溯回规则条目。 */
describe('WorkspaceConclusionBody · 规则 ID（G31）', () => {
  const BASE: ConclusionView = { headline: '考虑良性结节（TI-RADS 3）', evidenceIds: [] };

  it('有 ruleId：渲染并带 data-rule-id', () => {
    const { container } = render(
      <WorkspaceConclusionBody
        state="conclusion"
        view={{
          ...BASE,
          clinicalActions: [{ actionType: 'fna', ruleId: 'thyroid:fna', description: '穿刺' }],
        }}
      />,
    );
    const el = container.querySelector('[data-rule-id="thyroid:fna"]');
    expect(el).not.toBeNull();
    expect(el!.textContent).toBe('thyroid:fna');
  });

  it('无 ruleId：不渲染该标记', () => {
    const { container } = render(
      <WorkspaceConclusionBody
        state="conclusion"
        view={{ ...BASE, clinicalActions: [{ actionType: 'fna', description: '穿刺' }] }}
      />,
    );
    expect(container.querySelector('[data-rule-id]')).toBeNull();
  });
});
