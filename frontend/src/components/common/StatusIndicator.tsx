import type { ChannelStatus } from '@/types/websocket';

interface StatusIndicatorProps {
  status: ChannelStatus;
  size?: number;
}

const statusConfig: Record<ChannelStatus, { color: string; className: string; title: string }> = {
  connected: { color: '#10b981', className: 'animate-pulse-glow', title: '已连接' },
  connecting: { color: '#f59e0b', className: 'animate-blink-warning', title: '连接中' },
  reconnecting: { color: '#f59e0b', className: 'animate-blink-warning', title: '重连中' },
  disconnected: { color: '#ef4444', className: '', title: '已断开' },
};

export default function StatusIndicator({ status, size = 8 }: StatusIndicatorProps) {
  const config = statusConfig[status];

  return (
    <span
      className={config.className}
      title={config.title}
      style={{
        display: 'inline-block',
        width: size,
        height: size,
        borderRadius: '50%',
        backgroundColor: config.color,
        boxShadow: status === 'connected' ? `0 0 8px ${config.color}80` : 'none',
      }}
    />
  );
}
