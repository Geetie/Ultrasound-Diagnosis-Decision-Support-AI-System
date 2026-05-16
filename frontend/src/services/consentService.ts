import apiClient from './api';
import type { ConsentStatus } from '@/types/api';

export interface ConsentActionData {
  patient_id: string;
  consent_version: string;
  operator_id: string;
}

export async function getConsentStatus(patientId: string): Promise<ConsentStatus> {
  const response = await apiClient.get<ConsentStatus>(`/consent/${patientId}`);
  return response.data;
}

async function consentAction(data: ConsentActionData, action: 'grant' | 'withdraw'): Promise<ConsentStatus> {
  const response = await apiClient.post<ConsentStatus>('/consent', { ...data, action });
  return response.data;
}

export const grantConsent = (data: ConsentActionData) => consentAction(data, 'grant');
export const withdrawConsent = (data: ConsentActionData) => consentAction(data, 'withdraw');
