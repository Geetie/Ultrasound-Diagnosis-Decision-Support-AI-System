import { create } from 'zustand';
import type { ChannelStatus, ConnectionStatus } from '@/types/websocket';

interface WebSocketActions {
  setNavChannelStatus: (status: ChannelStatus) => void;
  setDicomChannelStatus: (status: ChannelStatus) => void;
  setLastHeartbeat: (timestamp: number | null) => void;
  incrementReconnectAttempt: () => void;
  resetReconnectAttempt: () => void;
  setUrls: (navUrl: string, dicomUrl: string) => void;
}

export const useWebSocketStore = create<WebSocketActions & {
  navChannelStatus: ChannelStatus;
  dicomChannelStatus: ChannelStatus;
  connectionStatus: ConnectionStatus;
  lastHeartbeatAt: number | null;
  reconnectAttempt: number;
  navChannelUrl: string;
  dicomChannelUrl: string;
}>((set) => ({
  navChannelStatus: 'disconnected',
  dicomChannelStatus: 'disconnected',
  connectionStatus: 'both_disconnected',
  lastHeartbeatAt: null,
  reconnectAttempt: 0,
  navChannelUrl: '',
  dicomChannelUrl: '',

  setNavChannelStatus: (navChannelStatus) => set({ navChannelStatus }),
  setDicomChannelStatus: (dicomChannelStatus) => set({ dicomChannelStatus }),
  setLastHeartbeat: (lastHeartbeatAt) => set({ lastHeartbeatAt }),
  incrementReconnectAttempt: () =>
    set((state) => ({ reconnectAttempt: state.reconnectAttempt + 1 })),
  resetReconnectAttempt: () => set({ reconnectAttempt: 0 }),
  setUrls: (navChannelUrl, dicomChannelUrl) => set({ navChannelUrl, dicomChannelUrl }),
}));
