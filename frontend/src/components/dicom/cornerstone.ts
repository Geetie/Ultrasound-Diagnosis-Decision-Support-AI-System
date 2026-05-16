import cornerstone from 'cornerstone-core';
import cornerstoneTools from 'cornerstone-tools';
import cornerstoneWADOImageLoader from 'cornerstone-wado-image-loader';
import dicomParser from 'dicom-parser';

let initialized = false;

export function initCornerstone(): void {
  if (initialized) return;

  cornerstoneWADOImageLoader.external.cornerstone = cornerstone;
  cornerstoneWADOImageLoader.external.dicomParser = dicomParser;

  cornerstoneWADOImageLoader.configure({
    useWebWorkers: true,
    decodeConfig: {
      convertFloatPixelDataToInt: false,
    },
  });

  cornerstoneTools.external.cornerstone = cornerstone;
  cornerstoneTools.external.Hammer = (window as any).Hammer;

  cornerstoneTools.init({
    mouseEnabled: true,
    touchEnabled: true,
    globalToolSyncEnabled: false,
    showSVGCursors: true,
  });

  const WwwcTool = cornerstoneTools.WwwcTool;
  const ZoomTool = cornerstoneTools.ZoomTool;
  const PanTool = cornerstoneTools.PanTool;

  if (WwwcTool) cornerstoneTools.addTool(WwwcTool);
  if (ZoomTool) cornerstoneTools.addTool(ZoomTool);
  if (PanTool) cornerstoneTools.addTool(PanTool);

  initialized = true;
}

export function activateTool(toolName: string): void {
  cornerstoneTools.setToolActive(toolName, { mouseButtonMask: 1 });
}

export function deactivateTool(toolName: string): void {
  cornerstoneTools.setToolPassive(toolName);
}

export function resetViewport(element: HTMLElement): void {
  cornerstone.reset(element);
}

export function getViewport(element: HTMLElement) {
  return cornerstone.getViewport(element);
}

export function setViewport(element: HTMLElement, viewport: Record<string, unknown>) {
  cornerstone.setViewport(element, viewport);
}

export function enable(element: HTMLElement): void {
  try {
    cornerstone.enable(element);
  } catch {
    // element may not have dimensions yet
  }
}

export function disable(element: HTMLElement): void {
  try {
    cornerstone.disable(element);
  } catch {
    // element may not have been enabled
  }
}

export function loadAndCacheImage(imageId: string) {
  return cornerstone.loadAndCacheImage(imageId);
}

export function displayImage(element: HTMLElement, image: Record<string, unknown>, viewport?: Record<string, unknown>) {
  cornerstone.displayImage(element, image, viewport);
}

export function updateImage(element: HTMLElement) {
  cornerstone.updateImage(element);
}

export function resize(element: HTMLElement) {
  cornerstone.resize(element);
}

export { cornerstone, cornerstoneTools, cornerstoneWADOImageLoader };
