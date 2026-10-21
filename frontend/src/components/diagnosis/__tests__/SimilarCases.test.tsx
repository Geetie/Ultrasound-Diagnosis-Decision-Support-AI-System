import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import SimilarCases from '../SimilarCases';

/**
 * A.82 守卫：相似病例必须**始终**带「参考」标注与免责声明，且不得透出病患标识。
 *
 * 这三条是临床安全约束，不是样式偏好：
 * 1. 标题用后端 label（含"参考"），前端不得改写；
 * 2. 免责声明必须可见（不做折叠默认隐藏）；
 * 3. 只显示 date / image_id / score。
 */
describe('SimilarCases', () => {
  const block = {
    label: '本人历史影像（参考）',
    disclaimer: '以下为同一患者既往检查影像的相似度对照，仅供回顾参考，不构成诊断依据。',
    status: 'ok',
    count: 2,
    scope: 'self',
    cases: [
      { image_id: 'img_20261009_a', score: 0.9759, date: '2026-10-09' },
      { image_id: 'img_20261003_b', score: 0.6383, date: '2026-10-03' },
    ],
  };

  it('渲染标题、免责声明与逐条记录', () => {
    render(<SimilarCases block={block} />);
    expect(screen.getByTestId('similar-cases')).toBeInTheDocument();
    expect(screen.getByText('本人历史影像（参考）')).toBeInTheDocument();
    expect(screen.getByTestId('similar-cases-disclaimer')).toHaveTextContent('不构成诊断依据');
    expect(screen.getByText('img_20261009_a')).toBeInTheDocument();
    expect(screen.getByText('2026-10-09')).toBeInTheDocument();
    expect(screen.getByText('相似度 0.976')).toBeInTheDocument();
  });

  it('日期缺失时显示"日期未知"而不是空白', () => {
    render(
      <SimilarCases
        block={{ ...block, cases: [{ image_id: 'img_x', score: 0.5 }], count: 1 }}
      />,
    );
    expect(screen.getByText('日期未知')).toBeInTheDocument();
  });

  it('status 非 ok 时整块不渲染（不写"未检出"）', () => {
    const { container } = render(
      <SimilarCases block={{ ...block, status: 'unavailable', reason: 'InferenceError' }} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('没有病例时整块不渲染', () => {
    const { container } = render(<SimilarCases block={{ ...block, cases: [], count: 0 }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('block 为 null 时不崩', () => {
    const { container } = render(<SimilarCases block={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
