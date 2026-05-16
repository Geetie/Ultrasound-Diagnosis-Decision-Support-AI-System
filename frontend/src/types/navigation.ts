export interface NavigationAnnotation {
  id: string;
  label: string;
  confidence: number;
  threshold: number;
  bbox: [number, number, number, number];
}

export type AnnotationValidationStatus =
  | 'verified'
  | 'pending'
  | 'timeout'
  | 'revoked';

export interface AnnotationWithStatus {
  annotation: NavigationAnnotation;
  status: AnnotationValidationStatus;
  frameId: string;
  timestamp: number;
  revokeReason?: string;
}

export interface AnnotationStyleConfig {
  borderColor: string;
  borderStyle: 'solid' | 'dashed';
  borderWidth: number;
  fillOpacity: number;
  labelBgColor: string;
  labelTextColor: string;
  dashArray?: string;
  animation?: string;
}

export const ANNOTATION_STYLES: Record<AnnotationValidationStatus, AnnotationStyleConfig> = {
  verified: {
    borderColor: '#10b981',
    borderStyle: 'solid',
    borderWidth: 2,
    fillOpacity: 0.15,
    labelBgColor: '#10b981',
    labelTextColor: '#ffffff',
  },
  pending: {
    borderColor: '#f59e0b',
    borderStyle: 'dashed',
    borderWidth: 2,
    fillOpacity: 0.4,
    labelBgColor: '#f59e0b',
    labelTextColor: '#0f172a',
    dashArray: '8 4',
    animation: 'annotation-pending-blink 1.5s ease-in-out infinite',
  },
  timeout: {
    borderColor: '#ef4444',
    borderStyle: 'solid',
    borderWidth: 2,
    fillOpacity: 0,
    labelBgColor: '#ef4444',
    labelTextColor: '#ffffff',
  },
  revoked: {
    borderColor: '#ef4444',
    borderStyle: 'solid',
    borderWidth: 2,
    fillOpacity: 0,
    labelBgColor: '#ef4444',
    labelTextColor: '#ffffff',
    animation: 'flash-red 500ms ease-out',
  },
};

export interface NavigationState {
  currentFrameAnnotations: AnnotationWithStatus[];
  lowConfidenceFrameIds: Set<string>;
  isNavigationActive: boolean;
  organSwitching: boolean;
  currentOrgan: string | null;
}
