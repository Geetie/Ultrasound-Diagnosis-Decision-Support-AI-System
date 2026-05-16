import apiClient from './api';
import type { SessionInfo } from '@/types/api';

export async function createSession(data: {
  patient_id: string;
  device_id: string;
  target_organs: string[];
}): Promise<SessionInfo> {
  const response = await apiClient.post<SessionInfo>('/session', data);
  return response.data;
}

export async function getSession(sessionId: string): Promise<SessionInfo> {
  const response = await apiClient.get<SessionInfo>(`/session/${sessionId}`);
  return response.data;
}

export async function endSession(sessionId: string): Promise<void> {
  await apiClient.post(`/session/${sessionId}/end`);
}
