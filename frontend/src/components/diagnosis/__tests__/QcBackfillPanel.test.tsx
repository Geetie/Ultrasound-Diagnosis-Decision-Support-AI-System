import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import QcBackfillPanel, { FINAL_DIAGNOSIS_SOURCE_LABELS } from '../QcBackfillPanel';

/**
 * A.133 / G42 + G38：质控回填面板。
 * 核心口径：**草稿期不得显示金标准**（锚定偏倚），话术必须标注"不属于正式报告"。
 */
describe('QcBackfillPanel', () => {
  it('草稿期：只有话术、没有最终诊断', () => {
    const { container } = render(
      <QcBackfillPanel
        reportStatus="draft"
        finalDiagnosis="恶性"
        finalDiagnosisSource="pathology"
        patientExplanation="您的结节需要进一步检查。"
      />,
    );
    expect(container.querySelector('[data-region="final-diagnosis"]')).toBeNull();
    expect(screen.getByText(/进一步检查/)).toBeInTheDocument();
  });

  it('已签发：显示最终诊断 + 来源中文 + 说明', () => {
    const { container } = render(
      <QcBackfillPanel
        reportStatus="signed"
        finalDiagnosis="恶性"
        finalDiagnosisSource="pathology"
        finalDiagnosisAt="2026-11-01T10:00:00Z"
      />,
    );
    const block = container.querySelector('[data-region="final-diagnosis"]');
    expect(block).not.toBeNull();
    expect(block!.textContent).toContain('恶性');
    expect(block!.textContent).toContain(FINAL_DIAGNOSIS_SOURCE_LABELS.pathology);
    expect(block!.textContent).toContain('不参与本次判读');
  });

  it('话术必须标注"不属于正式报告内容"', () => {
    render(<QcBackfillPanel reportStatus="signed" patientExplanation="话术正文" />);
    expect(screen.getByText(/不属于正式报告内容/)).toBeInTheDocument();
  });

  it('两者都没有 → 整块不渲染', () => {
    const { container } = render(<QcBackfillPanel reportStatus="signed" />);
    expect(container.querySelector('[data-region="qc-backfill"]')).toBeNull();
  });

  it('未知来源码原样露出（不猜语义）', () => {
    render(
      <QcBackfillPanel reportStatus="pushed" finalDiagnosis="良性" finalDiagnosisSource="mystery" />,
    );
    expect(screen.getByText('mystery')).toBeInTheDocument();
  });
});
