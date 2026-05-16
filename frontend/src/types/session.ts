export type SessionStatus = 'pending' | 'active' | 'partial' | 'completed' | 'needs_review' | 'failed' | 'terminated';
export type OrganStatus = 'pending' | 'in_progress' | 'completed' | 'failed' | 'skipped' | 'needs_review';
export type OrganType = 'thyroid' | 'liver' | 'cardiac';

export interface SessionState {
  sessionId: string | null;
  patientId: string | null;
  deviceId: string | null;
  status: SessionStatus;
  targetOrgans: OrganType[];
  organStatuses: Partial<Record<OrganType, OrganStatus>>;
  knowledgeBaseVersion: string | null;
  consentStatus: 'granted' | 'denied' | 'withdrawn' | 'pending' | null;
  createdAt: string | null;
  expiredAt: string | null;
  ttlRemainingMs: number | null;
}
