import { create } from 'zustand';
import type { DicomImageState, DicomTool, FrameIndexMapping } from '@/types/dicom';
import { getViewport as csGetViewport, setViewport as csSetViewport } from '@/components/dicom/cornerstone';

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
        const csViewport = csGetViewport(element);
        if (csViewport) {
          if (viewport.windowWidth !== undefined) csViewport.voi.windowWidth = viewport.windowWidth;
          if (viewport.windowCenter !== undefined) csViewport.voi.windowCenter = viewport.windowCenter;
          if (viewport.scale !== undefined) csViewport.scale = viewport.scale;
          if (viewport.translationX !== undefined) csViewport.translation.x = viewport.translationX;
          if (viewport.translationY !== undefined) csViewport.translation.y = viewport.translationY;
          if (viewport.invert !== undefined) csViewport.invert = viewport.invert;
          csSetViewport(element, csViewport);
        }
      } catch {
        // silent-ok: 元素未 enable 时同步必失败属正常分支（store 侧值已落，view 会在就绪后重读）
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
