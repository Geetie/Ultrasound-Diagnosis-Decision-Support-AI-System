import { useNavigationStore } from '@/stores/navigationStore';
import { useDicomStore } from '@/stores/dicomStore';
import { useWebSocketStore } from '@/stores/websocketStore';
import AnnotationBox from './AnnotationBox';
import AnnotationLegend from './AnnotationLegend';

export default function NavigationOverlay() {
  const currentFrameAnnotations = useNavigationStore((s) => s.currentFrameAnnotations);
  const organSwitching = useNavigationStore((s) => s.organSwitching);
  const isNavigationActive = useNavigationStore((s) => s.isNavigationActive);
  const lowConfidenceFrameIds = useNavigationStore((s) => s.lowConfidenceFrameIds);
  const currentFrameIndex = useDicomStore((s) => s.currentFrameIndex);
  const navChannelStatus = useWebSocketStore((s) => s.navChannelStatus);

  const isNavDown = navChannelStatus === 'disconnected' || navChannelStatus === 'reconnecting';
  const isLowConfidenceFrame = lowConfidenceFrameIds.has(String(currentFrameIndex));

  return (
    <div
      className="absolute inset-0 pointer-events-none"
      style={{ zIndex: 20 }}
    >
      {organSwitching && (
        <div
          className="absolute inset-0 flex items-center justify-center"
          style={{
            background: 'rgba(10, 14, 26, 0.6)',
          }}
        >
          <div
            style={{
              fontFamily: "'Orbitron', sans-serif",
              fontSize: 14,
              color: '#f59e0b',
              letterSpacing: '0.05em',
              animation: 'blink-warning 1s ease-in-out infinite',
            }}
          >
            器官模型切换中，导航暂停
          </div>
        </div>
      )}

      {isNavDown && isNavigationActive && !organSwitching && (
        <div
          className="absolute top-2 left-1/2 -translate-x-1/2"
          style={{
            background: 'rgba(239, 68, 68, 0.15)',
            border: '1px solid rgba(239, 68, 68, 0.4)',
            borderRadius: 6,
            padding: '4px 12px',
            display: 'flex',
            alignItems: 'center',
            gap: 6,
          }}
        >
          <span
            style={{
              width: 6,
              height: 6,
              borderRadius: '50%',
              background: '#ef4444',
              animation: 'blink-warning 0.8s ease-in-out infinite',
            }}
          />
          <span style={{ color: '#ef4444', fontSize: 11 }}>导航服务中断</span>
        </div>
      )}

      {isLowConfidenceFrame && !organSwitching && (
        <div
          className="absolute top-2 right-4"
          style={{
            background: 'rgba(245, 158, 11, 0.15)',
            border: '1px dashed rgba(245, 158, 11, 0.5)',
            borderRadius: 6,
            padding: '4px 12px',
          }}
        >
          <span style={{ color: '#f59e0b', fontSize: 11 }}>低置信度帧 — 标注已隐藏</span>
        </div>
      )}

      <svg
        className="absolute inset-0"
        width="100%"
        height="100%"
        style={{ overflow: 'visible' }}
      >
        {isLowConfidenceFrame
          ? currentFrameAnnotations.map((item) => {
              const [x, y, w, h] = item.annotation.bbox;
              return (
                <rect
                  key={item.annotation.id}
                  x={x}
                  y={y}
                  width={w}
                  height={h}
                  fill="none"
                  stroke="#f59e0b"
                  strokeWidth={1}
                  strokeDasharray="4 4"
                  opacity={0.5}
                />
              );
            })
          : currentFrameAnnotations.map((item) => (
              <AnnotationBox
                key={item.annotation.id}
                annotation={item.annotation}
                status={item.status}
                revokeReason={item.revokeReason}
              />
            ))}
      </svg>

      <div className="absolute bottom-12 right-4">
        <AnnotationLegend />
      </div>
    </div>
  );
}
