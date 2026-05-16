import { useEffect, useCallback } from 'react';
import { wsManager } from '@/websocket/DualWebSocketManager';
import { handleNavigationMessage, handleNavigationRevocation, handleDiagnosisNotification, handleSessionTerminated, handleOrganSwitching } from '@/websocket/navChannel';
import { useNavigationStore } from '@/stores/navigationStore';
import type { AnnotationValidationStatus } from '@/types/navigation';

export function useNavigationAnnotations() {
  const store = useNavigationStore();

  useEffect(() => {
    const unsubNav = wsManager.onNavigationMessage((msg) => {
      handleNavigationMessage(msg);
    });

    const unsubRevocation = wsManager.onNavigationRevocation((msg) => {
      handleNavigationRevocation(msg);
    });

    const unsubDiagnosis = wsManager.onDiagnosisResult((msg) => {
      handleDiagnosisNotification(msg);
    });

    const unsubTerminated = wsManager.onSessionTerminated((msg) => {
      handleSessionTerminated(msg);
    });

    const unsubOrgan = wsManager.onOrganSwitching((msg) => {
      handleOrganSwitching(msg);
    });

    return () => {
      unsubNav();
      unsubRevocation();
      unsubDiagnosis();
      unsubTerminated();
      unsubOrgan();
    };
  }, []);

  const updateAnnotationStatus = useCallback(
    (frameId: string, label: string, status: AnnotationValidationStatus, reason?: string) => {
      store.updateAnnotationStatus(frameId, label, status, reason);
    },
    [store]
  );

  return {
    ...store,
    updateAnnotationStatus,
  };
}
