import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { Measurement } from '@/types/api';
import MeasurementTable, { PIXEL_SPACING_SOURCE_LABELS } from '../MeasurementTable';

function _measurement(overrides: Partial<Measurement> = {}): Measurement {
  return {
    long_mm: 12.0,
    short_mm: 8.0,
    anteroposterior_mm: 6.0,
    aspect_ratio: 1.5,
    volume_mm3: 301.6,
    distance_to_capsule_mm: 3.0,
    distance_to_organ_edge_mm: null,
    distance_to_skin_mm: null,
    measurement_type: '三维',
    unit: 'mm',
    pixel_spacing_source: 'dicom',
    ...overrides,
  };
}

describe('MeasurementTable（M3 自动测量）', () => {
  it('无数据时不渲染（不展示空表格）', () => {
    const { container } = render(<MeasurementTable measurement={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('渲染各测量项与单位', () => {
    render(<MeasurementTable measurement={_measurement()} />);

    expect(screen.getByTestId('measurement-row-long')).toHaveTextContent('12.0');
    expect(screen.getByTestId('measurement-row-long')).toHaveTextContent('mm');
    expect(screen.getByTestId('measurement-row-short')).toHaveTextContent('8.0');
    expect(screen.getByTestId('measurement-row-anteroposterior')).toHaveTextContent('6.0');
    expect(screen.getByTestId('measurement-row-aspect')).toHaveTextContent('1.50');
    expect(screen.getByTestId('measurement-row-volume')).toHaveTextContent('301.6');
    expect(screen.getByTestId('measurement-row-capsule')).toHaveTextContent('3.0');
  });

  it('空值行不渲染', () => {
    render(<MeasurementTable measurement={_measurement()} />);

    expect(screen.queryByTestId('measurement-row-organ_edge')).not.toBeInTheDocument();
    expect(screen.queryByTestId('measurement-row-skin')).not.toBeInTheDocument();
  });

  it('DICOM 来源不加估算标记', () => {
    render(<MeasurementTable measurement={_measurement({ pixel_spacing_source: 'dicom' })} />);

    expect(screen.getByTestId('measurement-table')).toHaveAttribute('data-source', 'dicom');
    const source = screen.getByTestId('measurement-source');
    expect(source).toHaveTextContent('DICOM 像素间距');
    expect(source).not.toHaveTextContent('估算值');
  });

  it('回退默认间距时必须标注为估算值（避免医生误信）', () => {
    render(<MeasurementTable measurement={_measurement({ pixel_spacing_source: 'default' })} />);

    expect(screen.getByTestId('measurement-table')).toHaveAttribute('data-source', 'default');
    expect(screen.getByTestId('measurement-source')).toHaveTextContent('估算值');
  });

  it('文本来源单独标注', () => {
    render(<MeasurementTable measurement={_measurement({ pixel_spacing_source: 'text' })} />);

    expect(screen.getByTestId('measurement-source')).toHaveTextContent('报告文本');
  });

  it('展示测量类型（便于追溯切面）', () => {
    render(<MeasurementTable measurement={_measurement()} />);
    expect(screen.getByText('三维')).toBeInTheDocument();
  });

  it('全部字段为空时整块不渲染', () => {
    const { container } = render(
      <MeasurementTable
        measurement={_measurement({
          long_mm: null,
          short_mm: null,
          anteroposterior_mm: null,
          aspect_ratio: null,
          volume_mm3: null,
          distance_to_capsule_mm: null,
        })}
      />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('来源标签覆盖三种取值', () => {
    expect(PIXEL_SPACING_SOURCE_LABELS.dicom.estimated).toBe(false);
    expect(PIXEL_SPACING_SOURCE_LABELS.default.estimated).toBe(true);
    expect(PIXEL_SPACING_SOURCE_LABELS.text.estimated).toBe(false);
  });
});
