import { create } from 'zustand';
import type { AnnotationWithStatus, AnnotationValidationStatus } from '@/types/navigation';
import type { ImageQuality, PlaneAssessment } from '@/types/api';

interface NavigationActions {
  setAnnotations: (annotations: AnnotationWithStatus[]) => void;
  addAnnotation: (annotation: AnnotationWithStatus) => void;
  updateAnnotationStatus: (frameId: string, label: string, status: AnnotationValidationStatus, reason?: string) => void;
  removeAnnotations: (frameId: string, annotationIds: string[]) => void;
  clearAnnotations: () => void;
  setNavigationActive: (active: boolean) => void;
  setOrganSwitching: (switching: boolean) => void;
  setCurrentOrgan: (organ: string | null) => void;
  addLowConfidenceFrame: (frameId: string) => void;
  /** M1/M6：设置本帧的切面评估与图像质量（帧级属性） */
  setFrameAttributes: (planeAssessment: PlaneAssessment | null, imageQuality: ImageQuality | null) => void;
}

export const useNavigationStore = create<NavigationActions & {
  currentFrameAnnotations: AnnotationWithStatus[];
  isNavigationActive: boolean;
  organSwitching: boolean;
  currentOrgan: string | null;
  lowConfidenceFrameIds: Set<string>;
  currentFramePlaneAssessment: PlaneAssessment | null;
  currentFrameImageQuality: ImageQuality | null;
}>((set) => ({
  currentFrameAnnotations: [],
  isNavigationActive: false,
  organSwitching: false,
  currentOrgan: null,
  lowConfidenceFrameIds: new Set<string>(),
  currentFramePlaneAssessment: null,
  currentFrameImageQuality: null,

  setAnnotations: (currentFrameAnnotations) => set({ currentFrameAnnotations }),
  setFrameAttributes: (currentFramePlaneAssessment, currentFrameImageQuality) =>
    set({ currentFramePlaneAssessment, currentFrameImageQuality }),
  addAnnotation: (annotation) =>
    set((state) => ({
      currentFrameAnnotations: [...state.currentFrameAnnotations, annotation],
    })),
  updateAnnotationStatus: (frameId, label, status, reason) =>
    set((state) => ({
      currentFrameAnnotations: state.currentFrameAnnotations.map((a) =>
        a.frameId === frameId && a.annotation.label === label
          ? { ...a, status, revokeReason: reason }
          : a
      ),
    })),
  removeAnnotations: (frameId, annotationIds) =>
    set((state) => ({
      currentFrameAnnotations: state.currentFrameAnnotations.filter(
        (a) => !(a.frameId === frameId && annotationIds.includes(a.annotation.id))
      ),
    })),
  clearAnnotations: () =>
    set({
      currentFrameAnnotations: [],
      currentFramePlaneAssessment: null,
      currentFrameImageQuality: null,
    }),
  setNavigationActive: (isNavigationActive) => set({ isNavigationActive }),
  setOrganSwitching: (organSwitching) => set({ organSwitching }),
  setCurrentOrgan: (currentOrgan) => set({ currentOrgan }),
  addLowConfidenceFrame: (frameId) =>
    set((state) => {
      const updated = new Set(state.lowConfidenceFrameIds);
      updated.add(frameId);
      return { lowConfidenceFrameIds: updated };
    }),
}));
