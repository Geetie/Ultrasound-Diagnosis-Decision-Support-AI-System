import cornerstone from 'cornerstone-core';
import cornerstoneTools from 'cornerstone-tools';
import cornerstoneWADOImageLoader from 'cornerstone-wado-image-loader';
import dicomParser from 'dicom-parser';

let coreInitialized = false;
let toolsInitialized = false;

/**
 * 只初始化 cornerstone core + WADO 影像加载器。
 *
 * 与工具栈拆开的原因：`cornerstoneTools.init()` 依赖页面上的 `Hammer`
 * （见 initCornerstoneTools），在没引入 hammer.js 的页面里会抛
 * `Cannot read properties of undefined (reading 'SUPPORT_POINTER_EVENTS')`。
 * 医生侧工作区的影像区是**只读展示**，不需要窗宽窗位/缩放/平移工具，
 * 因此那条路径只调 core，避免为看一眼图把整套 tools 栈也拉起来。
 */
export function initCornerstoneCore(): void {
  if (coreInitialized) return;

  cornerstoneWADOImageLoader.external.cornerstone = cornerstone;
  cornerstoneWADOImageLoader.external.dicomParser = dicomParser;

  cornerstoneWADOImageLoader.configure({
    useWebWorkers: true,
    decodeConfig: {
      convertFloatPixelDataToInt: false,
    },
  });

  coreInitialized = true;
}

/** 初始化交互工具栈（需要页面已引入 hammer.js）。 */
export function initCornerstoneTools(): void {
  if (toolsInitialized) return;

  const Hammer = (window as any).Hammer;
  if (!Hammer) {
    // 明确降级，而不是让 tools.init 抛一个难以定位的错
    console.warn(
      '[cornerstone] 未检测到 hammer.js，交互工具（窗宽窗位/缩放/平移）不启用；' +
      '只读影像显示不受影响。'
    );
    return;
  }

  cornerstoneTools.external.cornerstone = cornerstone;
  cornerstoneTools.external.Hammer = Hammer;

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

  toolsInitialized = true;
}

/** core + 工具栈一起初始化（交互式阅片器用）。 */
export function initCornerstone(): void {
  initCornerstoneCore();
  initCornerstoneTools();
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
