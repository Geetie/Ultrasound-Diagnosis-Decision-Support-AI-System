import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import WorkspaceConclusionBody from '../WorkspaceConclusionBody';

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
