import type { WsNavigationMessage, WsNavigationRevocation, WsDiagnosisNotification, WsSessionTerminated, WsOrganSwitching } from '@/types/websocket';
import { useNavigationStore } from '@/stores/navigationStore';
import { useSessionStore } from '@/stores/sessionStore';
import type { AnnotationValidationStatus } from '@/types/navigation';

export function handleNavigationMessage(msg: WsNavigationMessage): void {
  const store = useNavigationStore.getState();
  const annotations = msg.annotations.map((a) => ({
    annotation: {
      id: a.id,
      label: a.label,
      confidence: a.confidence,
      threshold: a.threshold,
      bbox: a.bbox,
    },
    status: 'pending' as AnnotationValidationStatus,
    frameId: msg.frame_id,
    timestamp: msg.timestamp,
  }));

  store.setAnnotations(annotations);

  if (msg.annotations.length === 0) {
    store.addLowConfidenceFrame(msg.frame_id);
  }
}

export function handleNavigationRevocation(msg: WsNavigationRevocation): void {
  const store = useNavigationStore.getState();
  store.removeAnnotations(msg.frame_id, msg.annotation_ids);
}

export function handleDiagnosisNotification(msg: WsDiagnosisNotification): void {
  // Diagnosis result notification - polling for full result is handled by the component
  console.info('[NavChannel] Diagnosis notification received:', msg.request_id);
}

export function handleSessionTerminated(msg: WsSessionTerminated): void {
  const sessionStore = useSessionStore.getState();
  sessionStore.setSession({ status: 'terminated' });

  const navStore = useNavigationStore.getState();
  navStore.clearAnnotations();
  navStore.setNavigationActive(false);

  if (msg.reason === 'consent_withdrawn') {
    sessionStore.setConsentStatus('withdrawn');
  }
}

export function handleOrganSwitching(msg: WsOrganSwitching): void {
  const store = useNavigationStore.getState();
  store.setOrganSwitching(msg.switching);
  if (!msg.switching) {
    store.setCurrentOrgan(msg.organ);
  }
}
