import { useWebSocketStore } from '@/stores/websocketStore';
import { useSessionStore } from '@/stores/sessionStore';
import StatusIndicator from '@/components/common/StatusIndicator';

export default function StatusBar() {
  const { navChannelStatus, dicomChannelStatus } = useWebSocketStore();
  const { knowledgeBaseVersion, ttlRemainingMs, status } = useSessionStore();

  const formatTtl = (ms: number | null) => {
    if (ms === null) return '--:--';
    const totalSeconds = Math.floor(ms / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
  };

  return (
    <div
      className="w-full flex items-center justify-between"
      style={{ fontSize: 11, color: '#64748b' }}
    >
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          <span style={{ color: '#94a3b8' }}>导航通道</span>
          <StatusIndicator status={navChannelStatus} />
        </div>
        <div className="flex items-center gap-2">
          <span style={{ color: '#94a3b8' }}>影像通道</span>
          <StatusIndicator status={dicomChannelStatus} />
        </div>
      </div>
      <div className="flex items-center gap-4">
        <span>
          会话状态:
          <span style={{ color: status === 'active' ? '#10b981' : '#94a3b8', marginLeft: 4 }}>
            {status || '未连接'}
          </span>
        </span>
        <span>
          TTL:
          <span
            style={{
              fontFamily: "'JetBrains Mono', monospace",
              color: ttlRemainingMs !== null && ttlRemainingMs < 60000 ? '#f59e0b' : '#94a3b8',
              marginLeft: 4,
            }}
          >
            {formatTtl(ttlRemainingMs)}
          </span>
        </span>
        <span>
          知识库:
          <span style={{ fontFamily: "'JetBrains Mono', monospace", color: '#94a3b8', marginLeft: 4 }}>
            {knowledgeBaseVersion || '--'}
          </span>
        </span>
      </div>
    </div>
  );
}
