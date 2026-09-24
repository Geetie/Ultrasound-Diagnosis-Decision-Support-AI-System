import { create } from 'zustand';
import type { SessionState, OrganType, OrganStatus } from '@/types/session';

interface SessionActions {
  setSession: (session: Partial<SessionState>) => void;
  clearSession: () => void;
  updateOrganStatus: (organ: OrganType, status: OrganStatus) => void;
  setConsentStatus: (status: SessionState['consentStatus']) => void;
  setTtlRemaining: (ms: number | null) => void;
  /** 当前检查器官（顶栏选择器与语音指令共用同一处状态） */
  setCurrentOrgan: (organ: OrganType | null) => void;
}

const initialSessionState: SessionState = {
  sessionId: null,
  patientId: null,
  deviceId: null,
  status: 'pending',
  targetOrgans: [],
  organStatuses: {},
  knowledgeBaseVersion: null,
  consentStatus: null,
  createdAt: null,
  expiredAt: null,
  ttlRemainingMs: null,
  currentOrgan: null,
};

export const useSessionStore = create<SessionState & SessionActions>((set) => ({
  ...initialSessionState,
  setSession: (session) => set((state) => ({ ...state, ...session })),
  clearSession: () => set(initialSessionState),
  updateOrganStatus: (organ, status) =>
    set((state) => ({
      organStatuses: { ...state.organStatuses, [organ]: status },
    })),
  setConsentStatus: (consentStatus) => set({ consentStatus }),
  setTtlRemaining: (ttlRemainingMs) => set({ ttlRemainingMs }),
  setCurrentOrgan: (currentOrgan) => set({ currentOrgan }),
}));
