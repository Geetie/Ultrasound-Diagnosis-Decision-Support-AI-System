import { useEffect } from 'react';
import { wsManager } from '@/websocket/DualWebSocketManager';
import { useSessionStore } from '@/stores/sessionStore';

const TOKEN_STORAGE_KEY = 'access_token';

/**
 * 建立/释放实时双通道连接（nav + dicom）。
 *
 * ## 为什么需要这个 hook
 *
 * 2026-09-28 接线审计发现：`wsManager.connect()` 在整个前端**没有任何调用点**
 * （`websocketStore.setUrls` 同样无调用），也就是说**双通道 WebSocket 从未建立**——
 * 页面上的实时导航叠加、诊断结果推送、会话终止通知全部不可用。
 * 单元测试全绿也发现不了：测试测的是"被调用的函数"，不是"有没有人调用"。
 *
 * 本 hook 是唯一调用点，挂在 `AppLayout` 上（覆盖所有需登录页面）。
 *
 * ## 连接条件
 *
 * 必须同时具备 `sessionId`、`deviceId`、`token`：
 * 后端 `/api/v1/realtime/{nav,dicom}` 的 `session_id`/`device_id` 都是 `Query(...)` 必填，
 * 缺任何一个都会在**握手阶段**被拒。缺条件时直接不连（而不是连上后无限重连），
 * 会话建立（`/session/new` 会带 device_id）后应自然满足条件。
 */
export function useRealtimeConnection(): void {
  const sessionId = useSessionStore((s) => s.sessionId);
  const deviceId = useSessionStore((s) => s.deviceId);

  useEffect(() => {
    const token = localStorage.getItem(TOKEN_STORAGE_KEY);
    if (!sessionId || !deviceId || !token) return;

    wsManager.connect(sessionId, token, deviceId);

    return () => {
      wsManager.disconnect();
    };
  }, [sessionId, deviceId]);
}
