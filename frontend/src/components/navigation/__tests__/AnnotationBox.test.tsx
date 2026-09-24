import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { NavigationAnnotation } from '@/types/navigation';
import { HIGH_RISK_ACTION_TEXT } from '@/types/navigation';
import AnnotationBox from '../AnnotationBox';

function _annotation(overrides: Partial<NavigationAnnotation> = {}): NavigationAnnotation {
  return {
    id: 'f1:0',
    label: 'nodule',
    confidence: 0.82,
    threshold: 0.5,
    bbox: [0.1, 0.2, 0.3, 0.4],
    risk_flag: null,
    suggested_action: null,
    ...overrides,
  };
}

describe('AnnotationBox（M2 B+ 级高危标记视觉区分）', () => {
  it('普通标注不显示高危标记', () => {
    render(<AnnotationBox annotation={_annotation()} status="verified" />);

    const box = screen.getByTestId('annotation-box');
    expect(box).toHaveAttribute('data-high-risk', 'false');
    expect(box.textContent).toContain('nodule');
    expect(box.textContent).not.toContain(HIGH_RISK_ACTION_TEXT);
  });

  it('B+ 级标注用橙色脉冲并提示已启动深度分析', () => {
    render(
      <AnnotationBox
        annotation={_annotation({
          label: 'malignant_suspected',
          risk_flag: 'high',
          suggested_action: 'start_standard_evaluation',
        })}
        status="verified"
      />
    );

    const box = screen.getByTestId('annotation-box');
    expect(box).toHaveAttribute('data-high-risk', 'true');
    expect(box.textContent).toContain(HIGH_RISK_ACTION_TEXT);
    expect(box.textContent).toContain('malignant_suspected');
    // 高危标注用橙色（#f97316），与校验态琥珀色、普通标注蓝色区分开
    expect(box.querySelector('rect[stroke="#f97316"]')).not.toBeNull();
  });

  it('高危标记的脉冲动画生效（状态显性化，不只看颜色）', () => {
    render(
      <AnnotationBox annotation={_annotation({ risk_flag: 'high' })} status="verified" />
    );

    const rect = screen.getByTestId('annotation-box').querySelector('rect');
    expect(rect?.getAttribute('style')).toContain('annotation-high-risk-pulse');
  });

  it('已撤销的标注不展示高危样式（撤销优先）', () => {
    render(
      <AnnotationBox
        annotation={_annotation({ risk_flag: 'high' })}
        status="revoked"
        revokeReason="安全校验未通过"
      />
    );

    // 撤销分支有独立渲染，不含高危提示
    expect(screen.queryByTestId('annotation-box')).not.toBeInTheDocument();
    expect(screen.getByText(/安全校验未通过/)).toBeInTheDocument();
  });

  it('超时状态同样优先于高危样式', () => {
    render(<AnnotationBox annotation={_annotation({ risk_flag: 'high' })} status="timeout" />);

    expect(screen.queryByTestId('annotation-box')).not.toBeInTheDocument();
    expect(screen.getByText('导航服务中断')).toBeInTheDocument();
  });
});
