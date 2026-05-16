export type ChannelStatus = 'connecting' | 'connected' | 'disconnected' | 'reconnecting';
export type ConnectionStatus = 'both_connected' | 'partial' | 'both_disconnected';

export interface WsNavigationMessage {
  type: 'navigation';
  frame_id: string;
  annotations: Array<{
    id: string;
    label: string;
    confidence: number;
    threshold: number;
    bbox: [number, number, number, number];
  }>;
  desensitization_status: 'full' | 'degraded';
  timestamp: number;
}

export interface WsNavigationRevocation {
  type: 'navigation_revocation';
  frame_id: string;
  annotation_ids: string[];
  reason: string;
  timestamp: number;
}

export interface WsDiagnosisNotification {
  type: 'diagnosis_result';
  request_id: string;
  session_id: string;
  status: string;
  timestamp: number;
}

export interface WsSessionTerminated {
  type: 'session_terminated';
  reason: 'safety_critical_update' | 'consent_withdrawn' | 'expired' | 'admin';
  affected_organs?: string[];
  kb_version?: string;
  timestamp: number;
}

export interface WsOrganSwitching {
  type: 'organ_switching';
  organ: string;
  switching: boolean;
  timestamp: number;
}

export interface WsHeartbeat {
  type: 'ping' | 'pong';
  timestamp: number;
}

export interface WsAuthMessage {
  type: 'auth';
  session_id: string;
  token: string;
}

export interface WsAuthResponse {
  type: 'auth_ok';
  session_ttl_remaining_ms?: number;
}

export interface WsReconnectAck {
  type: 'reconnect_ack';
  session_ttl_remaining_ms?: number;
}

export type NavChannelMessage =
  | WsNavigationMessage
  | WsNavigationRevocation
  | WsDiagnosisNotification
  | WsSessionTerminated
  | WsOrganSwitching
  | WsHeartbeat
  | WsAuthResponse
  | WsReconnectAck;

export interface DicomFrameMessage {
  frameIndex: number;
  flags: number;
  data: ArrayBuffer;
  crc32: number;
}

export interface WebSocketState {
  navChannelStatus: ChannelStatus;
  dicomChannelStatus: ChannelStatus;
  connectionStatus: ConnectionStatus;
  lastHeartbeatAt: number | null;
  reconnectAttempt: number;
  navChannelUrl: string;
  dicomChannelUrl: string;
}
