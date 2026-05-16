import { LoadingOutlined } from '@ant-design/icons';

interface LoadingOverlayProps {
  tip?: string;
  visible?: boolean;
  fullscreen?: boolean;
}

export default function LoadingOverlay({ tip = '加载中...', visible = true, fullscreen = false }: LoadingOverlayProps) {
  if (!visible) return null;

  if (fullscreen) {
    return (
      <div
        className="fixed inset-0 flex items-center justify-center"
        style={{
          background: '#0a0e1a',
          zIndex: 9999,
        }}
      >
        <div className="flex flex-col items-center gap-3">
          <LoadingOutlined style={{ fontSize: 40, color: '#00d4ff' }} spin />
          <span style={{ color: '#94a3b8', fontSize: 13 }}>{tip}</span>
        </div>
      </div>
    );
  }

  return (
    <div
      className="absolute inset-0 flex items-center justify-center"
      style={{
        background: 'rgba(10, 14, 26, 0.85)',
        zIndex: 1000,
      }}
    >
      <div className="flex flex-col items-center gap-3">
        <LoadingOutlined style={{ fontSize: 32, color: '#00d4ff' }} spin />
        <span style={{ color: '#94a3b8', fontSize: 12 }}>{tip}</span>
      </div>
    </div>
  );
}
