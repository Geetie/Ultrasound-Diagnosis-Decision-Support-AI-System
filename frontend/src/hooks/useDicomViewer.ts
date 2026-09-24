import { useEffect, useCallback } from 'react';
import {
  initCornerstone,
  enable,
  disable,
  loadAndCacheImage,
  displayImage,
  resize,
  activateTool,
  deactivateTool,
  resetViewport,
  getViewport,
} from '@/components/dicom/cornerstone';
import { useDicomStore } from '@/stores/dicomStore';

export function useDicomViewer(element: HTMLElement | null, imageId?: string) {
  const {
    isLoading,
    isLoaded,
    activeTool,
    setLoading,
    setLoaded,
    setError,
    setViewport,
  } = useDicomStore();

  useEffect(() => {
    initCornerstone();
  }, []);

  useEffect(() => {
    if (!element) return;

    enable(element);

    const handleResize = () => {
      if (element) resize(element);
    };

    const resizeObserver = new ResizeObserver(handleResize);
    resizeObserver.observe(element);

    return () => {
      resizeObserver.disconnect();
      disable(element);
    };
  }, [element]);

  useEffect(() => {
    if (!element || !imageId) return;

    let cancelled = false;
    setLoading(true);
    setLoaded(false);
    setError(null);

    loadAndCacheImage(imageId)
      .then((image) => {
        if (cancelled) return;
        displayImage(element, image);
        setLoaded(true);
        setLoading(false);

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
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setError(err.message);
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [element, imageId]);

  useEffect(() => {
    if (!element) return;

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
  }, [element, activeTool]);

  const handleReset = useCallback(() => {
    if (element) {
      resetViewport(element);
    }
  }, [element]);

  return {
    isLoading,
    isLoaded,
    handleReset,
  };
}
