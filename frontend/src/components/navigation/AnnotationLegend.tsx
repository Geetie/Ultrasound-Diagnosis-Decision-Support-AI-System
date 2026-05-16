import { ANNOTATION_STYLES } from '@/types/navigation';
import type { AnnotationValidationStatus } from '@/types/navigation';

const legendItems: Array<{ status: AnnotationValidationStatus; label: string }> = [
  { status: 'verified', label: '已校验' },
  { status: 'pending', label: '待校验' },
  { status: 'timeout', label: '校验超时' },
  { status: 'revoked', label: '校验移除' },
];

export default function AnnotationLegend() {
  return (
    <div
      style={{
        background: 'rgba(17, 24, 39, 0.9)',
        border: '1px solid #1e293b',
        borderRadius: 6,
        padding: '6px 10px',
        fontSize: 10,
        lineHeight: 1.8,
      }}
    >
      <div
        style={{
          fontFamily: "'Orbitron', sans-serif",
          fontSize: 9,
          color: '#64748b',
          letterSpacing: '0.05em',
          marginBottom: 2,
        }}
      >
        ANNOTATION STATUS
      </div>
      {legendItems.map((item) => {
        const style = ANNOTATION_STYLES[item.status];
        return (
          <div key={item.status} className="flex items-center gap-2">
            <span
              style={{
                display: 'inline-block',
                width: 16,
                height: 10,
                borderRadius: 2,
                border: `2px solid ${style.borderColor}`,
                background: style.borderColor,
                opacity: style.fillOpacity || 1,
                borderStyle: style.borderStyle === 'dashed' ? 'dashed' : 'solid',
              }}
            />
            <span style={{ color: '#94a3b8' }}>{item.label}</span>
          </div>
        );
      })}
      <div
        style={{
          marginTop: 4,
          paddingTop: 4,
          borderTop: '1px solid #1e293b',
          color: '#64748b',
          fontSize: 9,
        }}
      >
        置信度低于阈值时标注隐藏
      </div>
    </div>
  );
}
