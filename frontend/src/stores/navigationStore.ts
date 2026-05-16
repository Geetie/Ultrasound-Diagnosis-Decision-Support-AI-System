import { create } from 'zustand';
import type { AnnotationWithStatus, AnnotationValidationStatus } from '@/types/navigation';

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
}

export const useNavigationStore = create<NavigationActions & {
  currentFrameAnnotations: AnnotationWithStatus[];
  isNavigationActive: boolean;
  organSwitching: boolean;
  currentOrgan: string | null;
  lowConfidenceFrameIds: Set<string>;
}>((set) => ({
  currentFrameAnnotations: [],
  isNavigationActive: false,
  organSwitching: false,
  currentOrgan: null,
  lowConfidenceFrameIds: new Set<string>(),

  setAnnotations: (currentFrameAnnotations) => set({ currentFrameAnnotations }),
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
  clearAnnotations: () => set({ currentFrameAnnotations: [] }),
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
