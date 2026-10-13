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
import { reportError } from '@/utils/errorReport';

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
      // silent-ok: 元素尚无尺寸时 enable 会抛（随 ResizeObserver 就绪后重建），无用户可见影响
    }

    useDicomStore.getState().setCornerstoneElement(element);

    const handleResize = () => {
      if (element) {
        try { resize(element); } catch { /* silent-ok: 未 enable 时 resize 无意义（幂等，下次事件重试） */ }
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
        // silent-ok: 渲染事件里读 viewport 属同步式读（拿不到即跳过本次同步，下次事件重试）
      }
    };

    element.addEventListener('cornerstoneimagerendered', handleImageRendered);

    return () => {
      resizeObserver.disconnect();
      element.removeEventListener('cornerstoneimagerendered', handleImageRendered);
      useDicomStore.getState().setCornerstoneElement(null);
      try { disable(element); } catch { /* silent-ok: 卸载清理（幂等，元素已分离属预期） */ }
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
      .then((image) => {
        if (cancelled) return;
        try {
          displayImage(element, image);
        } catch (err) {
          // A.60：显示失败**必须说出来**。原先空块忽略，紧接着无条件 `setLoaded(true)`
          // → 医生看到的是"已加载"的黑屏/空白影像区、零提示（A.35 形态：占位被当成真实）。
          // 这里落进 store 的 error，由本组件的 IMAGE LOAD ERROR overlay 呈现。
          reportError('dicom-viewer', '影像渲染失败（像素数据可能不受支持）', err);
          setError('影像渲染失败，请重新选择检查');
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
          // silent-ok: 同渲染事件里的读（同步式读，失败跳过本次同步）
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
            // silent-ok: DICOM 元数据是只读展示增强（缺标签时 overlay 少几行，不影响影像与判读）
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
      // silent-ok: 工具切换在 cornerstone 未初始化时必抛（UI 会在初始化后重放本次 activeTool）
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
