import type { ImageQuality, PlaneAssessment, RiskFlag, SuggestedAction } from './api';

export type ChannelStatus = 'connecting' | 'connected' | 'disconnected' | 'reconnecting';
export type ConnectionStatus = 'both_connected' | 'partial' | 'both_disconnected';

export interface WsNavigationMessage {
  type: 'navigation';
  frame_id: string;
  annotations: Array<{
    /** 后端 NavigationAnnotation 不含 id（可选）：前端按帧内下标派生稳定 id */
    id?: string;
    label: string;
    confidence: number;
    threshold: number;
    bbox: [number, number, number, number];
    /** M2 B+ 级高危标记（后端 NavigationAnnotation.risk_flag） */
    risk_flag?: RiskFlag | null;
    suggested_action?: SuggestedAction | null;
  }>;
  desensitization_status: 'full' | 'degraded';
  /** M1 切面标准性评估（帧级，后端 NavigationMessage.plane_assessment） */
  plane_assessment?: PlaneAssessment | null;
  /** M6 图像质量评估（帧级，后端 NavigationMessage.image_quality） */
  image_quality?: ImageQuality | null;
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
