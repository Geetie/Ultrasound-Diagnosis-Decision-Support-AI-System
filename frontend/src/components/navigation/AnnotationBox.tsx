import type { NavigationAnnotation, AnnotationValidationStatus } from '@/types/navigation';
import { ANNOTATION_STYLES } from '@/types/navigation';

interface AnnotationBoxProps {
  annotation: NavigationAnnotation;
  status: AnnotationValidationStatus;
  revokeReason?: string;
}

export default function AnnotationBox({ annotation, status, revokeReason }: AnnotationBoxProps) {
  const [x, y, w, h] = annotation.bbox;
  const style = ANNOTATION_STYLES[status];
  const confidencePercent = Math.round(annotation.confidence * 100);

  if (status === 'revoked') {
    return (
      <g>
        <rect
          x={x}
          y={y}
          width={w}
          height={h}
          fill="none"
          stroke="#ef4444"
          strokeWidth={2}
          strokeDasharray="8 4"
          style={{ animation: 'flash-red 500ms ease-out' }}
        />
        <rect
          x={x}
          y={y - 22}
          width={Math.max(w, 200)}
          height={20}
          rx={3}
          fill="rgba(239, 68, 68, 0.9)"
        />
        <text
          x={x + 6}
          y={y - 8}
          fill="#ffffff"
          fontSize={10}
          fontFamily="'Inter', sans-serif"
        >
          导航标注经安全校验已移除{revokeReason ? `：${revokeReason}` : ''}
        </text>
      </g>
    );
  }

  if (status === 'timeout') {
    return (
      <g>
        <rect
          x={x}
          y={y}
          width={w}
          height={h}
          fill="none"
          stroke="#ef4444"
          strokeWidth={2}
          strokeDasharray="8 4"
        />
        <rect
          x={x}
          y={y - 22}
          width={Math.max(w, 140)}
          height={20}
          rx={3}
          fill="rgba(239, 68, 68, 0.9)"
        />
        <text
          x={x + 6}
          y={y - 8}
          fill="#ffffff"
          fontSize={10}
          fontFamily="'Inter', sans-serif"
        >
          导航服务中断
        </text>
      </g>
    );
  }

  const isPending = status === 'pending';

  return (
    <g>
      <rect
        x={x}
        y={y}
        width={w}
        height={h}
        fill={style.borderColor}
        fillOpacity={style.fillOpacity}
        stroke={style.borderColor}
        strokeWidth={style.borderWidth}
        strokeDasharray={style.dashArray}
        style={
          isPending
            ? { animation: 'annotation-pending-blink 1.5s ease-in-out infinite' }
            : undefined
        }
      />
      <rect
        x={x}
        y={y - 22}
        width={Math.max(w, 120)}
        height={20}
        rx={3}
        fill={style.labelBgColor}
      />
      <text
        x={x + 6}
        y={y - 8}
        fill={style.labelTextColor}
        fontSize={10}
        fontFamily="'Inter', sans-serif"
        fontWeight={600}
      >
        {annotation.label} {confidencePercent}%
      </text>
      {isPending && (
        <text
          x={x + 6}
          y={y + h + 14}
          fill="#f59e0b"
          fontSize={9}
          fontFamily="'Inter', sans-serif"
          style={{ animation: 'annotation-pending-blink 1.5s ease-in-out infinite' }}
        >
          待校验
        </text>
      )}
    </g>
  );
}
