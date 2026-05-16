import type {
  ChannelStatus,
  ConnectionStatus,
  NavChannelMessage,
  WsNavigationMessage,
  WsNavigationRevocation,
  WsDiagnosisNotification,
  WsSessionTerminated,
  WsOrganSwitching,
  WsAuthResponse,
} from '@/types/websocket';
import { useWebSocketStore } from '@/stores/websocketStore';

type EventHandler<T> = (data: T) => void;

function deriveConnectionStatus(nav: ChannelStatus, dicom: ChannelStatus): ConnectionStatus {
  const navConnected = nav === 'connected';
  const dicomConnected = dicom === 'connected';
  if (navConnected && dicomConnected) return 'both_connected';
  if (navConnected || dicomConnected) return 'partial';
  return 'both_disconnected';
}

function buildWsUrl(path: string): string {
  const store = useWebSocketStore.getState();
  const configuredUrl = path === '/api/v1/realtime/nav' ? store.navChannelUrl : store.dicomChannelUrl;
  const url = configuredUrl || path;
  if (url.startsWith('ws')) return url;
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}${url}`;
}

export class DualWebSocketManager {
  private navWs: WebSocket | null = null;
  private dicomWs: WebSocket | null = null;
  private sessionId: string | null = null;
  private token: string | null = null;

  private heartbeatInterval: ReturnType<typeof setInterval> | null = null;
  private navReconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  private dicomReconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  private missedHeartbeats = 0;
  private maxMissedHeartbeats = 3;
  private heartbeatIntervalMs = 5000;

  private navReconnectAttempt = 0;
  private dicomReconnectAttempt = 0;
  private maxReconnectDelayMs = 30000;
  private baseReconnectDelayMs = 1000;
  private maxReconnectAttempts = 20;

  private isNavReconnecting = false;
  private isDicomReconnecting = false;

  private ttlCheckTimeout: ReturnType<typeof setTimeout> | null = null;
  private consecutiveDebounceCount = 0;
  private maxDebounceCount = 5;

  private handlers = {
    navigationMessage: [] as EventHandler<WsNavigationMessage>[],
    dicomFrame: [] as EventHandler<ArrayBuffer>[],
    diagnosisResult: [] as EventHandler<WsDiagnosisNotification>[],
    sessionTerminated: [] as EventHandler<WsSessionTerminated>[],
    connectionStatusChange: [] as EventHandler<ConnectionStatus>[],
    navigationRevocation: [] as EventHandler<WsNavigationRevocation>[],
    organSwitching: [] as EventHandler<WsOrganSwitching>[],
  };

  connect(sessionId: string, token: string): void {
    this.sessionId = sessionId;
    this.token = token;
    this.isNavReconnecting = false;
    this.isDicomReconnecting = false;
    this.navReconnectAttempt = 0;
    this.dicomReconnectAttempt = 0;
    this.connectNavChannel();
  }

  disconnect(): void {
    this.clearHeartbeat();
    this.clearReconnectTimers();
    this.clearTtlCheck();
    this.isNavReconnecting = false;
    this.isDicomReconnecting = false;

    if (this.navWs) {
      this.navWs.close(1000, 'client_disconnect');
      this.navWs = null;
    }
    if (this.dicomWs) {
      this.dicomWs.close(1000, 'client_disconnect');
      this.dicomWs = null;
    }

    this.updateStoreStatus('disconnected', 'disconnected');
  }

  onNavigationMessage(handler: EventHandler<WsNavigationMessage>): () => void {
    this.handlers.navigationMessage.push(handler);
    return () => {
      this.handlers.navigationMessage = this.handlers.navigationMessage.filter((h) => h !== handler);
    };
  }

  onDicomFrame(handler: EventHandler<ArrayBuffer>): () => void {
    this.handlers.dicomFrame.push(handler);
    return () => {
      this.handlers.dicomFrame = this.handlers.dicomFrame.filter((h) => h !== handler);
    };
  }

  onDiagnosisResult(handler: EventHandler<WsDiagnosisNotification>): () => void {
    this.handlers.diagnosisResult.push(handler);
    return () => {
      this.handlers.diagnosisResult = this.handlers.diagnosisResult.filter((h) => h !== handler);
    };
  }

  onSessionTerminated(handler: EventHandler<WsSessionTerminated>): () => void {
    this.handlers.sessionTerminated.push(handler);
    return () => {
      this.handlers.sessionTerminated = this.handlers.sessionTerminated.filter((h) => h !== handler);
    };
  }

  onConnectionStatusChange(handler: EventHandler<ConnectionStatus>): () => void {
    this.handlers.connectionStatusChange.push(handler);
    return () => {
      this.handlers.connectionStatusChange = this.handlers.connectionStatusChange.filter((h) => h !== handler);
    };
  }

  onNavigationRevocation(handler: EventHandler<WsNavigationRevocation>): () => void {
    this.handlers.navigationRevocation.push(handler);
    return () => {
      this.handlers.navigationRevocation = this.handlers.navigationRevocation.filter((h) => h !== handler);
    };
  }

  onOrganSwitching(handler: EventHandler<WsOrganSwitching>): () => void {
    this.handlers.organSwitching.push(handler);
    return () => {
      this.handlers.organSwitching = this.handlers.organSwitching.filter((h) => h !== handler);
    };
  }

  getNavChannelStatus(): ChannelStatus {
    return useWebSocketStore.getState().navChannelStatus;
  }

  getDicomChannelStatus(): ChannelStatus {
    return useWebSocketStore.getState().dicomChannelStatus;
  }

  private connectNavChannel(): void {
    if (!this.sessionId) return;

    const wsUrl = buildWsUrl('/api/v1/realtime/nav');
    this.updateStoreStatus('connecting', useWebSocketStore.getState().dicomChannelStatus);

    try {
      this.navWs = new WebSocket(wsUrl);
    } catch {
      this.scheduleNavReconnect();
      return;
    }

    this.navWs.onopen = () => {
      this.navWs?.send(
        JSON.stringify({
          type: 'auth',
          session_id: this.sessionId,
          token: this.token,
        })
      );
    };

    this.navWs.onmessage = (event) => {
      this.handleNavMessage(event);
    };

    this.navWs.onclose = (event) => {
      if (event.code !== 1000) {
        this.scheduleNavReconnect();
      } else {
        this.updateStoreStatus('disconnected', useWebSocketStore.getState().dicomChannelStatus);
      }
    };

    this.navWs.onerror = () => {
      // onerror is always followed by onclose, which handles reconnect
      // only update status if not already reconnecting
      if (!this.isNavReconnecting) {
        this.updateStoreStatus('disconnected', useWebSocketStore.getState().dicomChannelStatus);
      }
    };
  }

  private connectDicomChannel(): void {
    if (!this.sessionId || !this.token) return;

    const wsUrl = buildWsUrl('/api/v1/realtime/dicom');
    this.updateStoreStatus(useWebSocketStore.getState().navChannelStatus, 'connecting');

    try {
      this.dicomWs = new WebSocket(wsUrl);
    } catch {
      this.scheduleDicomReconnect();
      return;
    }

    this.dicomWs.binaryType = 'arraybuffer';

    this.dicomWs.onopen = () => {
      this.dicomWs?.send(
        JSON.stringify({
          type: 'auth',
          session_id: this.sessionId,
          token: this.token,
        })
      );
    };

    this.dicomWs.onmessage = (event) => {
      this.handleDicomMessage(event);
    };

    this.dicomWs.onclose = (event) => {
      if (event.code !== 1000) {
        this.scheduleDicomReconnect();
      } else {
        this.updateStoreStatus(useWebSocketStore.getState().navChannelStatus, 'disconnected');
      }
    };

    this.dicomWs.onerror = () => {
      if (!this.isDicomReconnecting) {
        this.updateStoreStatus(useWebSocketStore.getState().navChannelStatus, 'disconnected');
      }
    };
  }

  private handleNavMessage(event: MessageEvent): void {
    try {
      const msg = JSON.parse(event.data) as NavChannelMessage;

      switch (msg.type) {
        case 'auth_ok':
          this.updateStoreStatus('connected', useWebSocketStore.getState().dicomChannelStatus);
          this.navReconnectAttempt = 0;
          this.isNavReconnecting = false;
          this.startHeartbeat();
          this.connectDicomChannel();
          if ((msg as WsAuthResponse).session_ttl_remaining_ms !== undefined) {
            useWebSocketStore.getState().setLastHeartbeat(Date.now());
          }
          break;

        case 'reconnect_ack':
          this.updateStoreStatus('connected', useWebSocketStore.getState().dicomChannelStatus);
          this.navReconnectAttempt = 0;
          this.isNavReconnecting = false;
          this.startHeartbeat();
          this.scheduleTtlCheck();
          break;

        case 'navigation':
          this.handlers.navigationMessage.forEach((h) => h(msg as WsNavigationMessage));
          break;

        case 'navigation_revocation':
          this.handlers.navigationRevocation.forEach((h) => h(msg as WsNavigationRevocation));
          break;

        case 'diagnosis_result':
          this.handlers.diagnosisResult.forEach((h) => h(msg as WsDiagnosisNotification));
          break;

        case 'session_terminated':
          this.handlers.sessionTerminated.forEach((h) => h(msg as WsSessionTerminated));
          this.disconnect();
          break;

        case 'organ_switching':
          this.handlers.organSwitching.forEach((h) => h(msg as WsOrganSwitching));
          break;

        case 'pong':
          this.missedHeartbeats = 0;
          useWebSocketStore.getState().setLastHeartbeat(Date.now());
          break;
      }
    } catch {
      // ignore parse errors
    }
  }

  private handleDicomMessage(event: MessageEvent): void {
    if (typeof event.data === 'string') {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'auth_ok') {
          this.updateStoreStatus(useWebSocketStore.getState().navChannelStatus, 'connected');
          this.dicomReconnectAttempt = 0;
          this.isDicomReconnecting = false;
        }
      } catch {
        // ignore
      }
      return;
    }

    if (event.data instanceof ArrayBuffer) {
      this.handlers.dicomFrame.forEach((h) => h(event.data));
    }
  }

  private startHeartbeat(): void {
    this.clearHeartbeat();
    this.missedHeartbeats = 0;

    this.heartbeatInterval = setInterval(() => {
      if (this.navWs?.readyState === WebSocket.OPEN) {
        if (this.missedHeartbeats >= this.maxMissedHeartbeats) {
          this.clearHeartbeat();
          this.scheduleNavReconnect();
          return;
        }
        this.navWs.send(JSON.stringify({ type: 'ping', timestamp: Date.now() }));
        this.missedHeartbeats++;
      }
    }, this.heartbeatIntervalMs);
  }

  private clearHeartbeat(): void {
    if (this.heartbeatInterval) {
      clearInterval(this.heartbeatInterval);
      this.heartbeatInterval = null;
    }
  }

  private getReconnectDelay(attempt: number): number {
    return Math.min(
      this.baseReconnectDelayMs * Math.pow(2, attempt),
      this.maxReconnectDelayMs
    );
  }

  private scheduleNavReconnect(): void {
    if (this.navReconnectTimeout) return;
    if (this.navReconnectAttempt >= this.maxReconnectAttempts) {
      this.updateStoreStatus('disconnected', useWebSocketStore.getState().dicomChannelStatus);
      return;
    }

    this.isNavReconnecting = true;
    this.updateStoreStatus('reconnecting', useWebSocketStore.getState().dicomChannelStatus);
    useWebSocketStore.getState().incrementReconnectAttempt();

    const delay = this.getReconnectDelay(this.navReconnectAttempt);
    this.navReconnectAttempt++;

    this.navReconnectTimeout = setTimeout(() => {
      this.navReconnectTimeout = null;
      if (this.navWs) {
        this.navWs.close();
        this.navWs = null;
      }
      this.connectNavChannel();
    }, delay);
  }

  private scheduleDicomReconnect(): void {
    if (this.dicomReconnectTimeout) return;
    if (this.dicomReconnectAttempt >= this.maxReconnectAttempts) {
      this.updateStoreStatus(useWebSocketStore.getState().navChannelStatus, 'disconnected');
      return;
    }

    this.isDicomReconnecting = true;
    this.updateStoreStatus(useWebSocketStore.getState().navChannelStatus, 'reconnecting');

    const delay = this.getReconnectDelay(this.dicomReconnectAttempt);
    this.dicomReconnectAttempt++;

    this.dicomReconnectTimeout = setTimeout(() => {
      this.dicomReconnectTimeout = null;
      if (this.dicomWs) {
        this.dicomWs.close();
        this.dicomWs = null;
      }
      this.connectDicomChannel();
    }, delay);
  }

  private clearReconnectTimers(): void {
    if (this.navReconnectTimeout) {
      clearTimeout(this.navReconnectTimeout);
      this.navReconnectTimeout = null;
    }
    if (this.dicomReconnectTimeout) {
      clearTimeout(this.dicomReconnectTimeout);
      this.dicomReconnectTimeout = null;
    }
  }

  private scheduleTtlCheck(): void {
    this.clearTtlCheck();
    this.consecutiveDebounceCount++;

    if (this.consecutiveDebounceCount > this.maxDebounceCount) {
      this.consecutiveDebounceCount = 0;
      this.forceTtlQuery();
      return;
    }

    this.ttlCheckTimeout = setTimeout(() => {
      this.ttlCheckTimeout = null;
      this.consecutiveDebounceCount = 0;
      this.forceTtlQuery();
    }, 2000);
  }

  private clearTtlCheck(): void {
    if (this.ttlCheckTimeout) {
      clearTimeout(this.ttlCheckTimeout);
      this.ttlCheckTimeout = null;
    }
  }

  private forceTtlQuery(): void {
    // In a real implementation, this would call the session API
    // to check TTL. For now, it's a placeholder.
  }

  private updateStoreStatus(navStatus: ChannelStatus, dicomStatus: ChannelStatus): void {
    const connectionStatus = deriveConnectionStatus(navStatus, dicomStatus);
    useWebSocketStore.setState({ navChannelStatus: navStatus, dicomChannelStatus: dicomStatus, connectionStatus });
    this.handlers.connectionStatusChange.forEach((h) => h(connectionStatus));
  }
}

export const wsManager = new DualWebSocketManager();
