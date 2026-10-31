import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import T2aChip from '../T2aChip';
import type { T2aView } from '@/services/diagnosisAdapter';

/**
 * 02-1 T2a 影像分类芯片（辅助参考）。
 *
 * 红线：available=false 必须显示「未做影像分类：<原因>」，**不能**显示成
 * "未检出恶性"（缺权重/未启用与"没病"是两回事）；展示必须带「辅助参考」标注。
 *
 * 本文件刻意不引入 cornerstone 链（组件零 cornerstone 依赖）——
 * 影像区整件的集成测试见 WorkspaceImageRegion.test.tsx（高负载环境
 * 可能 OOM/超慢，A.100.4）。
 */
const T2A_OK: T2aView = {
  available: true,
  predicted: 'Benign',
  probs: { Benign: 0.82, Malignant: 0.12, Normal: 0.06 },
  malignantProb: 0.12,
};

describe('T2aChip · 02-1 影像分类（辅助参考）', () => {
  it('available=true：显示中文类别 + 恶性概率 + 辅助参考标注', () => {
    const { container } = render(<T2aChip t2a={T2A_OK} />);
    expect(container.querySelector('[data-region="t2a-aux"]')).not.toBeNull();
    expect(screen.getByText(/辅助参考，不参与结论/)).toBeInTheDocument();
    expect(screen.getByText('良性')).toBeInTheDocument();
    expect(screen.getByText(/恶性概率 12%/)).toBeInTheDocument();
  });

  it('Malignant 原文映射为「恶性」', () => {
    render(<T2aChip t2a={{ ...T2A_OK, predicted: 'Malignant', malignantProb: 0.63 }} />);
    expect(screen.getByText('恶性')).toBeInTheDocument();
    expect(screen.getByText(/恶性概率 63%/)).toBeInTheDocument();
  });

  it('available=false：显示「未做影像分类：<原因>」，不得出现类别或概率', () => {
    render(<T2aChip t2a={{ available: false, reason: 'model_missing' }} />);
    expect(screen.getByText(/未做影像分类：分类服务未加载模型/)).toBeInTheDocument();
    expect(screen.queryByText(/恶性概率/)).toBeNull();
  });

  it('未知 reason 原样兜底，不编造', () => {
    render(<T2aChip t2a={{ available: false, reason: 'some_new_reason' }} />);
    expect(screen.getByText(/未做影像分类：原因未提供/)).toBeInTheDocument();
  });

  it('malignant_prob=null 显示类别但不显示概率（后端没给就不反推）', () => {
    render(<T2aChip t2a={{ ...T2A_OK, malignantProb: null }} />);
    expect(screen.getByText('良性')).toBeInTheDocument();
    expect(screen.queryByText(/恶性概率/)).toBeNull();
  });
});
