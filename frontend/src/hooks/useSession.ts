import { useEffect, useCallback, useRef } from 'react';
import { useSessionStore } from '@/stores/sessionStore';
import { createSession, getSession, endSession } from '@/services/sessionService';
import type { OrganType } from '@/types/session';

export function useSession() {
  const ttlIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const expiredAtRef = useRef<number | null>(null);

  const sessionId = useSessionStore((s) => s.sessionId);
  const status = useSessionStore((s) => s.status);
  const patientId = useSessionStore((s) => s.patientId);
  const targetOrgans = useSessionStore((s) => s.targetOrgans);
  const ttlRemainingMs = useSessionStore((s) => s.ttlRemainingMs);
  const consentStatus = useSessionStore((s) => s.consentStatus);

  const startTtlCountdown = useCallback((ttlMs: number) => {
    if (ttlIntervalRef.current) {
      clearInterval(ttlIntervalRef.current);
    }

    expiredAtRef.current = Date.now() + ttlMs;
    useSessionStore.getState().setTtlRemaining(ttlMs);

    ttlIntervalRef.current = setInterval(() => {
      if (expiredAtRef.current !== null) {
        const remaining = Math.max(0, expiredAtRef.current - Date.now());
        useSessionStore.getState().setTtlRemaining(remaining);
        if (remaining <= 0 && ttlIntervalRef.current) {
          clearInterval(ttlIntervalRef.current);
          ttlIntervalRef.current = null;
        }
      }
    }, 1000);
  }, []);

  useEffect(() => {
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible' && expiredAtRef.current !== null) {
        const remaining = Math.max(0, expiredAtRef.current - Date.now());
        useSessionStore.getState().setTtlRemaining(remaining);
      }
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange);
  }, []);

  const handleCreateSession = useCallback(
    async (patientId: string, deviceId: string, targetOrgans: OrganType[]) => {
      try {
        const session = await createSession({
          patient_id: patientId,
          device_id: deviceId,
          target_organs: targetOrgans,
        });

        useSessionStore.getState().setSession({
          sessionId: session.session_id,
          patientId: session.patient_id,
          deviceId: session.device_id,
          status: session.status,
          targetOrgans: session.target_organs as OrganType[],
          knowledgeBaseVersion: session.knowledge_base_version,
          consentStatus: session.consent_status_at_creation as any,
          createdAt: session.created_at,
          expiredAt: session.expired_at,
        });

        if (session.expired_at) {
          const ttl = new Date(session.expired_at).getTime() - Date.now();
          if (ttl > 0) startTtlCountdown(ttl);
        }
      } catch (error) {
        useSessionStore.getState().setSession({ status: 'failed' });
        throw error;
      }
    },
    [startTtlCountdown]
  );

  const handleEndSession = useCallback(async () => {
    const currentSessionId = useSessionStore.getState().sessionId;
    if (!currentSessionId) return;

    try {
      await endSession(currentSessionId);
    } catch (err) {
      console.warn('[Session] End session failed:', err);
    }

    if (ttlIntervalRef.current) {
      clearInterval(ttlIntervalRef.current);
      ttlIntervalRef.current = null;
    }
    expiredAtRef.current = null;
    useSessionStore.getState().clearSession();
  }, []);

  const refreshSession = useCallback(async () => {
    const currentSessionId = useSessionStore.getState().sessionId;
    if (!currentSessionId) return;

    try {
      const session = await getSession(currentSessionId);
      useSessionStore.getState().setSession({
        status: session.status,
        organStatuses: session.organ_statuses as any,
      });
    } catch (err) {
      console.warn('[Session] Refresh failed:', err);
    }
  }, []);

  useEffect(() => {
    return () => {
      if (ttlIntervalRef.current) {
        clearInterval(ttlIntervalRef.current);
      }
    };
  }, []);

  return {
    sessionId,
    status,
    patientId,
    targetOrgans,
    ttlRemainingMs,
    consentStatus,
    createSession: handleCreateSession,
    endSession: handleEndSession,
    refreshSession,
  };
}
