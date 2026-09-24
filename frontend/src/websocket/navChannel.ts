import type { WsNavigationMessage, WsNavigationRevocation, WsDiagnosisNotification, WsSessionTerminated, WsOrganSwitching } from '@/types/websocket';
import { useNavigationStore } from '@/stores/navigationStore';
import { useSessionStore } from '@/stores/sessionStore';
import type { AnnotationValidationStatus } from '@/types/navigation';

export function handleNavigationMessage(msg: WsNavigationMessage): void {
  const store = useNavigationStore.getState();
  const annotations = msg.annotations.map((a, index) => ({
    annotation: {
      // 后端 NavigationAnnotation 不带 id，只有前端契约要求 id（撤销消息按 id
      // 匹配）。缺失时会得到 undefined，导致撤销/去重静默失效，因此这里用
      // 帧内下标派生稳定 id。
      id: a.id ?? `${msg.frame_id}:${index}`,
      label: a.label,
      confidence: a.confidence,
      threshold: a.threshold,
      bbox: a.bbox,
      // M2：B+ 级高危标记随标注透传
      risk_flag: a.risk_flag ?? null,
      suggested_action: a.suggested_action ?? null,
    },
    status: 'pending' as AnnotationValidationStatus,
    frameId: msg.frame_id,
    timestamp: msg.timestamp,
  }));

  store.setAnnotations(annotations);
  // M1/M6：帧级切面与质量属性（后端未部署评估头时为 undefined → null）
  store.setFrameAttributes(msg.plane_assessment ?? null, msg.image_quality ?? null);

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
