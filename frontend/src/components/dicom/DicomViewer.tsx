import { useEffect, useRef } from 'react';
import { useDicomStore } from '@/stores/dicomStore';
import {
  initCornerstone,
  enable,
  disable,
  loadAndCacheImage,
  displayImage,
  resize,
  activateTool,
  deactivateTool,
  getViewport,
} from './cornerstone';
import DicomToolbar from './DicomToolbar';
import DicomOverlay from './DicomOverlay';
import LoadingOverlay from '@/components/common/LoadingOverlay';

interface DicomViewerProps {
  imageId?: string;
  onFrameChange?: (frameIndex: number) => void;
}

export default function DicomViewer({ imageId, onFrameChange }: DicomViewerProps) {
  const elementRef = useRef<HTMLDivElement>(null);
  const isLoading = useDicomStore((s) => s.isLoading);
  const isLoaded = useDicomStore((s) => s.isLoaded);
  const error = useDicomStore((s) => s.error);
  const currentFrameIndex = useDicomStore((s) => s.currentFrameIndex);
  const showOverlay = useDicomStore((s) => s.showOverlay);
  const activeTool = useDicomStore((s) => s.activeTool);

  useEffect(() => {
    initCornerstone();
  }, []);

  useEffect(() => {
    const element = elementRef.current;
    if (!element) return;

    try {
      enable(element);
    } catch {
      // cornerstone.enable may fail if element has no dimensions yet
    }

    useDicomStore.getState().setCornerstoneElement(element);

    const handleResize = () => {
      if (element) {
        try { resize(element); } catch { /* ignore */ }
      }
    };

    const resizeObserver = new ResizeObserver(handleResize);
    resizeObserver.observe(element);

    const handleImageRendered = () => {
      try {
        const viewport = getViewport(element);
        if (viewport) {
          useDicomStore.setState({
            viewport: {
              windowWidth: viewport.voi.windowWidth as number,
              windowCenter: viewport.voi.windowCenter as number,
              scale: viewport.scale as number,
              translationX: viewport.translation?.x ?? 0,
              translationY: viewport.translation?.y ?? 0,
              invert: viewport.invert as boolean,
            },
          });
        }
      } catch {
        // viewport read failed
      }
    };

    (element as any).addEventListener('cornerstoneimagerendered', handleImageRendered);

    return () => {
      resizeObserver.disconnect();
      (element as any).removeEventListener('cornerstoneimagerendered', handleImageRendered);
      useDicomStore.getState().setCornerstoneElement(null);
      try { disable(element); } catch { /* ignore */ }
    };
  }, []);

  useEffect(() => {
    const element = elementRef.current;
    if (!element || !imageId) return;

    let cancelled = false;
    const { setLoading, setLoaded, setError, setViewport, setMetadata } = useDicomStore.getState();
    setLoading(true);
    setLoaded(false);
    setError(null);

    loadAndCacheImage(imageId)
      .then((image: any) => {
        if (cancelled) return;
        try {
          displayImage(element, image);
        } catch {
          // display may fail if element is not ready
        }
        setLoaded(true);
        setLoading(false);

        try {
          const viewport = getViewport(element);
          if (viewport) {
            setViewport({
              windowWidth: viewport.voi.windowWidth as number,
              windowCenter: viewport.voi.windowCenter as number,
              scale: viewport.scale as number,
              translationX: viewport.translation?.x ?? 0,
              translationY: viewport.translation?.y ?? 0,
              invert: viewport.invert as boolean,
            });
          }
        } catch {
          // viewport read failed
        }

        if (image.data) {
          try {
            const patientId = image.data.string('x00100020') || '';
            const patientName = image.data.string('x00100010') || '';
            const studyInstanceUid = image.data.string('x0020000d') || '';
            const seriesInstanceUid = image.data.string('x0020000e') || '';
            const sopInstanceUid = image.data.string('x00080018') || '';
            const modality = image.data.string('x00080060') || '';
            const acquisitionDate = image.data.string('x00080020') || '';
            const institutionName = image.data.string('x00080080') || '';
            const rows = image.data.uint16('x00280010') || 0;
            const columns = image.data.uint16('x00280011') || 0;
            const bitsAllocated = image.data.uint16('x00280100') || 0;

            setMetadata({
              patientId,
              patientName,
              studyInstanceUid,
              seriesInstanceUid,
              sopInstanceUid,
              modality,
              acquisitionDate,
              institutionName,
              rows,
              columns,
              bitsAllocated,
            });
          } catch {
            // metadata extraction failed, non-critical
          }
        }
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setError(err.message);
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [imageId]);

  useEffect(() => {
    try {
      if (activeTool === 'windowLevel') {
        activateTool('WwwcTool');
        deactivateTool('ZoomTool');
        deactivateTool('PanTool');
      } else if (activeTool === 'zoom') {
        deactivateTool('WwwcTool');
        activateTool('ZoomTool');
        deactivateTool('PanTool');
      } else if (activeTool === 'pan') {
        deactivateTool('WwwcTool');
        deactivateTool('ZoomTool');
        activateTool('PanTool');
      }
    } catch {
      // tool activation may fail if cornerstone is not initialized
    }
  }, [activeTool]);

  return (
    <div
      className="relative"
      style={{
        width: '100%',
        height: '100%',
        background: '#000',
        borderRadius: 10,
        overflow: 'hidden',
        border: '1px solid #1e293b',
      }}
    >
      <div
        ref={elementRef}
        className="cornerstone-enabled-element"
        style={{
          width: '100%',
          height: '100%',
        }}
        onContextMenu={(e) => e.preventDefault()}
      />

      {showOverlay && isLoaded && <DicomOverlay />}

      <LoadingOverlay visible={isLoading} tip="加载DICOM影像..." />

      {error && (
        <div
          className="absolute inset-0 flex items-center justify-center"
          style={{ background: 'rgba(10, 14, 26, 0.9)', zIndex: 100 }}
        >
          <div className="text-center">
            <div
              style={{
                fontFamily: "'Orbitron', sans-serif",
                color: '#ef4444',
                fontSize: 14,
                marginBottom: 8,
              }}
            >
              IMAGE LOAD ERROR
            </div>
            <div style={{ color: '#94a3b8', fontSize: 12 }}>{error}</div>
          </div>
        </div>
      )}

      {isLoaded && (
        <DicomToolbar onFrameChange={onFrameChange} currentFrame={currentFrameIndex} />
      )}
    </div>
  );
}
