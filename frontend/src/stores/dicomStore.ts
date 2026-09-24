import { create } from 'zustand';
import type { DicomImageState, DicomTool, FrameIndexMapping } from '@/types/dicom';

type DicomStoreState = DicomImageState & { activeTool: DicomTool; frameMappings: FrameIndexMapping[]; cornerstoneElement: HTMLElement | null };

interface DicomActions {
  setLoading: (loading: boolean) => void;
  setLoaded: (loaded: boolean) => void;
  setError: (error: string | null) => void;
  setCurrentFrame: (index: number) => void;
  setTotalFrames: (total: number) => void;
  setViewport: (viewport: Partial<DicomImageState['viewport']>) => void;
  setMetadata: (metadata: DicomImageState['metadata']) => void;
  setActiveTool: (tool: DicomTool) => void;
  toggleOverlay: () => void;
  addFrameMapping: (mapping: FrameIndexMapping) => void;
  setCornerstoneElement: (element: HTMLElement | null) => void;
  reset: () => void;
}

const initialDicomState: DicomStoreState = {
  isLoading: false,
  isLoaded: false,
  error: null,
  currentFrameIndex: 0,
  totalFrames: 0,
  viewport: {
    windowWidth: 400,
    windowCenter: 40,
    scale: 1,
    translationX: 0,
    translationY: 0,
    invert: false,
  },
  metadata: null,
  showOverlay: true,
  activeTool: 'windowLevel',
  frameMappings: [],
  cornerstoneElement: null,
};

export const useDicomStore = create<DicomStoreState & DicomActions>((set, get) => ({
  ...initialDicomState,
  setLoading: (isLoading) => set({ isLoading }),
  setLoaded: (isLoaded) => set({ isLoaded }),
  setError: (error) => set({ error }),
  setCurrentFrame: (currentFrameIndex) => set({ currentFrameIndex }),
  setTotalFrames: (totalFrames) => set({ totalFrames }),
  setViewport: (viewport) => {
    const newViewport = { ...get().viewport, ...viewport };
    set({ viewport: newViewport });
    const element = get().cornerstoneElement;
    if (element) {
      try {
        const csViewport = window.cornerstone?.getViewport(element);
        if (csViewport) {
          if (viewport.windowWidth !== undefined) csViewport.voi.windowWidth = viewport.windowWidth;
          if (viewport.windowCenter !== undefined) csViewport.voi.windowCenter = viewport.windowCenter;
          if (viewport.scale !== undefined) csViewport.scale = viewport.scale;
          if (viewport.translationX !== undefined) csViewport.translation.x = viewport.translationX;
          if (viewport.translationY !== undefined) csViewport.translation.y = viewport.translationY;
          if (viewport.invert !== undefined) csViewport.invert = viewport.invert;
          window.cornerstone?.setViewport(element, csViewport);
        }
      } catch {
        // cornerstone sync failed
      }
    }
  },
  setMetadata: (metadata) => set({ metadata }),
  setActiveTool: (activeTool) => set({ activeTool }),
  toggleOverlay: () => set((state) => ({ showOverlay: !state.showOverlay })),
  addFrameMapping: (mapping) =>
    set((state) => ({ frameMappings: [...state.frameMappings, mapping] })),
  setCornerstoneElement: (cornerstoneElement) => set({ cornerstoneElement }),
  reset: () => set({ ...initialDicomState, cornerstoneElement: null }),
}));
