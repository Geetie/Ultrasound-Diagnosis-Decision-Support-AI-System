import { renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useRealtimeConnection } from '@/hooks/useRealtimeConnection';
import { useSessionStore } from '@/stores/sessionStore';
import { wsManager } from '@/websocket/DualWebSocketManager';

/**
 * 实时双通道接线守卫。
 *
 * 背景（2026-09-28 接线审计）：`wsManager.connect()` 此前**没有任何调用点**，
 * 双通道 WebSocket 从未建立；即使建立，URL 也缺后端必填的 `session_id`/`device_id`
 * query，且前端 switch 没有 `ping` 分支（不回 pong 会被服务端心跳判死）。
 * 本文件把这三件事都钉住。
 */

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  static OPEN = 1;

  url: string;
  readyState = 1;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.readyState = 3;
  }
}

function sentTypes(ws: FakeWebSocket): string[] {
  return ws.sent.map((raw) => (JSON.parse(raw) as { type: string }).type);
}

describe('useRealtimeConnection', () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket as unknown as typeof WebSocket);
    localStorage.setItem('access_token', 'test-token');
    useSessionStore.setState({ sessionId: null, deviceId: null });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
    wsManager.disconnect();
  });

  it('不满足连接条件时不发起连接', () => {
    renderHook(() => useRealtimeConnection());

    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it('sessionId+deviceId+token 齐备时建立 nav 通道，并带上后端必填的 query 参数', () => {
    useSessionStore.setState({ sessionId: 'sess-1', deviceId: 'dev-1' });

    renderHook(() => useRealtimeConnection());

    expect(FakeWebSocket.instances).toHaveLength(1);
    const url = FakeWebSocket.instances[0].url;
    // 后端 nav.py 的 session_id/device_id 是 Query(...) 必填：缺失会在握手阶段被拒
    expect(url).toContain('session_id=sess-1');
    expect(url).toContain('device_id=dev-1');
    expect(url).toContain('/api/v1/realtime/nav');
  });

  it('连接建立后发送 auth 消息', () => {
    useSessionStore.setState({ sessionId: 'sess-1', deviceId: 'dev-1' });

    renderHook(() => useRealtimeConnection());
    FakeWebSocket.instances[0].onopen?.();

    expect(sentTypes(FakeWebSocket.instances[0])).toContain('auth');
  });

  it('收到服务端 ping 时回 pong（否则连续 3 次未回会被判死关闭）', () => {
    useSessionStore.setState({ sessionId: 'sess-1', deviceId: 'dev-1' });

    renderHook(() => useRealtimeConnection());
    const nav = FakeWebSocket.instances[0];
    nav.onmessage?.({ data: JSON.stringify({ type: 'ping' }) });

    expect(sentTypes(nav)).toContain('pong');
  });

  it('nav 通道 auth_ok 后才建立 dicom 通道，且同样带 query 参数', () => {
    useSessionStore.setState({ sessionId: 'sess-1', deviceId: 'dev-1' });

    renderHook(() => useRealtimeConnection());
    FakeWebSocket.instances[0].onmessage?.({ data: JSON.stringify({ type: 'auth_ok' }) });

    const dicom = FakeWebSocket.instances.find((ws) => ws.url.includes('/realtime/dicom'));
    expect(dicom).toBeDefined();
    expect(dicom?.url).toContain('session_id=sess-1');
    expect(dicom?.url).toContain('device_id=dev-1');
  });

  it('卸载时断开设连接（避免旧连接继续往新会话推送）', () => {
    useSessionStore.setState({ sessionId: 'sess-1', deviceId: 'dev-1' });

    const { unmount } = renderHook(() => useRealtimeConnection());
    const nav = FakeWebSocket.instances[0];

    unmount();

    expect(nav.readyState).toBe(3);
  });
});
