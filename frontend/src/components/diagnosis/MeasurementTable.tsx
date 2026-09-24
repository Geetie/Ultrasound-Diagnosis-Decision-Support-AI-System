import { Typography } from 'antd';
import { tokens } from '@/styles/tokens';
import type { Measurement } from '@/types/api';

const { Text } = Typography;

/**
 * M3 自动测量表。
 *
 * 关键设计：**尺寸来源必须显式标注**。
 * - ``dicom``：取自 DICOM PixelSpacing，可直接用于临床判断；
 * - ``default``：DICOM 缺 PixelSpacing，回退默认间距算出的是**估算值**，
 *   必须提示——把它当作真实尺寸会让医生基于看似精确的错误数字做判断；
 * - ``text``：来自报告文本表述（医生/LLM 写下的大小）。
 */
export const PIXEL_SPACING_SOURCE_LABELS: Record<string, { label: string; estimated: boolean }> = {
  dicom: { label: 'DICOM 像素间距', estimated: false },
  default: { label: '估算（缺 PixelSpacing）', estimated: true },
  text: { label: '报告文本', estimated: false },
};

interface MeasurementRow {
  key: string;
  label: string;
  value: number | null | undefined;
  unit: string;
  precision: number;
}

function buildRows(measurement: Measurement): MeasurementRow[] {
  return [
    { key: 'long', label: '长径', value: measurement.long_mm, unit: 'mm', precision: 1 },
    { key: 'short', label: '短径', value: measurement.short_mm, unit: 'mm', precision: 1 },
    {
      key: 'anteroposterior',
      label: '前后径',
      value: measurement.anteroposterior_mm,
      unit: 'mm',
      precision: 1,
    },
    {
      key: 'aspect',
      label: '纵横比',
      value: measurement.aspect_ratio,
      unit: '',
      precision: 2,
    },
    { key: 'volume', label: '体积', value: measurement.volume_mm3, unit: 'mm³', precision: 1 },
    {
      key: 'capsule',
      label: '距包膜',
      value: measurement.distance_to_capsule_mm,
      unit: 'mm',
      precision: 1,
    },
    {
      key: 'organ_edge',
      label: '距脏器边缘',
      value: measurement.distance_to_organ_edge_mm,
      unit: 'mm',
      precision: 1,
    },
    {
      key: 'skin',
      label: '距皮肤',
      value: measurement.distance_to_skin_mm,
      unit: 'mm',
      precision: 1,
    },
  ];
}

interface MeasurementTableProps {
  measurement: Measurement | null | undefined;
}

export default function MeasurementTable({ measurement }: MeasurementTableProps) {
  if (!measurement) {
    return null;
  }

  const sourceMeta = PIXEL_SPACING_SOURCE_LABELS[measurement.pixel_spacing_source] ?? {
    label: measurement.pixel_spacing_source,
    estimated: false,
  };
  const rows = buildRows(measurement).filter(
    (row) => row.value !== null && row.value !== undefined
  );

  if (rows.length === 0) {
    return null;
  }

  return (
    <div
      data-testid="measurement-table"
      data-source={measurement.pixel_spacing_source}
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: tokens.spacing.sm,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: tokens.spacing.sm }}>
        <Text
          style={{
            fontFamily: tokens.font.display,
            color: tokens.color.accent.secondary,
            fontSize: 12,
            letterSpacing: '0.05em',
          }}
        >
          AUTO MEASUREMENT
        </Text>
        {measurement.measurement_type && (
          <Text style={{ color: tokens.color.text.muted, fontSize: 11 }}>
            {measurement.measurement_type}
          </Text>
        )}
        <Text
          data-testid="measurement-source"
          style={{
            marginLeft: 'auto',
            fontSize: 10,
            fontFamily: tokens.font.mono,
            color: sourceMeta.estimated
              ? tokens.color.accent.warning
              : tokens.color.text.muted,
          }}
        >
          {sourceMeta.estimated ? '⚠ 估算值 · ' : ''}
          {sourceMeta.label}
        </Text>
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))',
          gap: tokens.spacing.sm,
        }}
      >
        {rows.map((row) => (
          <div
            key={row.key}
            data-testid={`measurement-row-${row.key}`}
            style={{
              background: tokens.color.bg.elevated,
              border: `1px solid ${tokens.color.border.default}`,
              borderRadius: tokens.radius.sm,
              padding: `${tokens.spacing.xs}px ${tokens.spacing.sm}px`,
            }}
          >
            <Text style={{ color: tokens.color.text.muted, fontSize: 10 }}>{row.label}</Text>
            <div>
              <Text
                style={{
                  color: tokens.color.text.primary,
                  fontSize: 14,
                  fontWeight: 600,
                  fontFamily: tokens.font.mono,
                }}
              >
                {row.value!.toFixed(row.precision)}
              </Text>
              {row.unit && (
                <Text style={{ color: tokens.color.text.secondary, fontSize: 10, marginLeft: 4 }}>
                  {row.unit}
                </Text>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
