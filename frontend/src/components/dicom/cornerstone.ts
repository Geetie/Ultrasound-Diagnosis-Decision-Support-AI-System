/**
 * Cornerstone3D 适配器（@cornerstonejs/* 5.x），迁移自 cornerstone-core 2.x。
 * 对外签名与旧适配器一致，消费方零改动；工具名内部映射（WwwcTool→WindowLevel）。
 * 并发：init 异步 fire-and-forget；engine/toolGroup 按 element 缓存。
 */
import {
  RenderingEngine,
  Enums as CoreEnums,
  eventTarget,
  getEnabledElement,
  imageLoader,
  init as coreInit,
  type Types,
} from '@cornerstonejs/core';
import {
  Enums as ToolsEnums,
  PanTool,
  ToolGroupManager,
  WindowLevelTool,
  ZoomTool,
  init as toolsInit,
  addTool,
} from '@cornerstonejs/tools';
import cornerstone, {
  init as loaderInit,
  wadouri,
} from '@cornerstonejs/dicom-image-loader';

let initPromise: Promise<void> | null = null;
const engineByElement = new WeakMap<HTMLElement, RenderingEngine>();
const viewportIdByElement = new WeakMap<HTMLElement, string>();
const toolGroupByElement = new WeakMap<HTMLElement, string>();
let currentToolGroupId: string | null = null;
let engineSeq = 0;

const TOOL_MAP: Record<string, string> = {
  WwwcTool: 'WindowLevel',
  ZoomTool: 'Zoom',
  PanTool: 'Pan',
};

/**
 * 初始化**必须可等待**：coreInit → loaderInit（注册 wadouri/dicomfile scheme）
 * → tools 注册。此前是 fire-and-forget，`loadAndCacheImage` 与之赛跑 ——
 * 生产构建下 init 链没走完时加载先跑，报 "No image loader found for scheme
 * 'dicomfile'"，工具也全部未注册（2026-10-08 走查实测）。
 * 失败要响：console.error + 向上抛，绝不静默吞（吞掉的表现是影像区永远占位）。
 */
function ensureInit(): Promise<void> {
  if (initPromise) return initPromise;
  initPromise = (async () => {
    await coreInit();
    await loaderInit();
    toolsInit();
    addTool(WindowLevelTool);
    addTool(ZoomTool);
    addTool(PanTool);
  })().catch((err: unknown) => {
    // 允许重试：清掉缓存的 promise，下次调用重新初始化
    initPromise = null;
    console.error('[cornerstone3d] init failed', err);
    throw err;
  });
  return initPromise;
}

export function initCornerstone(): Promise<void> {
  return ensureInit();
}

export function enable(element: HTMLElement): void {
  ensureInit();
  if (engineByElement.has(element)) return;
  engineSeq += 1;
  const engineId = `engine_${engineSeq}`;
  const viewportId = `vp_${engineSeq}`;
  const engine = new RenderingEngine(engineId);
  engine.enableElement({
    viewportId,
    element: element as HTMLDivElement,
    type: CoreEnums.ViewportType.STACK,
  });
  engineByElement.set(element, engine);
  viewportIdByElement.set(element, viewportId);
  const tg = ToolGroupManager.createToolGroup(`tg_${engineSeq}`);
  if (tg) {
    tg.addTool(WindowLevelTool.toolName);
    tg.addTool(ZoomTool.toolName);
    tg.addTool(PanTool.toolName);
    tg.addViewport(viewportId, engineId);
    tg.setToolActive('WindowLevel', {
      bindings: [{ mouseButton: ToolsEnums.MouseBindings.Primary }],
    });
    currentToolGroupId = `tg_${engineSeq}`;
  }
  eventTarget.addEventListener(
    CoreEnums.Events.IMAGE_RENDERED,
    ((evt: CustomEvent) => {
      const vpId = (evt.detail as { viewportId?: string })?.viewportId;
      if (vpId && vpId === viewportIdByElement.get(element)) {
        element.dispatchEvent(new CustomEvent('cornerstoneimagerendered'));
      }
    }) as EventListener,
  );
}

export function disable(element: HTMLElement): void {
  const engine = engineByElement.get(element);
  if (!engine) return;
  try {
    const tgId = toolGroupByElement.get(element);
    if (tgId) ToolGroupManager.destroyToolGroup(tgId);
    engine.destroy();
  } catch {
    // already detached
  }
  engineByElement.delete(element);
  viewportIdByElement.delete(element);
  toolGroupByElement.delete(element);
  if (currentToolGroupId && !ToolGroupManager.getToolGroup(currentToolGroupId)) {
    currentToolGroupId = null;
  }
}

/** wadouri image 的 .data：dicomParser.DataSet 的最小接口（元数据提取用）。 */
export interface DicomDataSet {
  string(tag: string): string | undefined;
  uint16(tag: string): number | undefined;
}

export function loadAndCacheImage(
  imageId: string,
): Promise<Types.IImage & { data?: DicomDataSet }> {
  // wadouri 加载的 image 运行时带 dicomParser DataSet（.data），
  // cs3d 的 IImage 类型未声明——消费方（DicomViewer 元数据提取）依赖它。
  return imageLoader.loadAndCacheImage(imageId) as Promise<
    Types.IImage & { data?: DicomDataSet }
  >;
}

export function displayImage(element: HTMLElement, image: { imageId?: string }): void {
  const engine = engineByElement.get(element);
  const viewportId = viewportIdByElement.get(element);
  if (!engine || !viewportId || !image?.imageId) return;
  const vp = engine.getViewports().find((v) => v.id === viewportId);
  if (!vp) return;
  (vp as Types.IStackViewport).setStack([image.imageId]).catch(() => undefined);
}

export function getViewport(element: HTMLElement) {
  try {
    const enabled = getEnabledElement(element as HTMLDivElement);
    if (!enabled) return null;
    const viewport = enabled.viewport;
    const props = viewport.getProperties() as {
      voi?: { windowWidth?: number; windowCenter?: number };
      invert?: boolean;
    };
    const cam = viewport.getCamera();
    return {
      voi: {
        windowWidth: (props.voi?.windowWidth as number | undefined) ?? 1,
        windowCenter: (props.voi?.windowCenter as number | undefined) ?? 0,
      },
      scale: cam.parallelScale as number,
      translation: {
        x: cam.focalPoint?.[0] ?? 0,
        y: cam.focalPoint?.[1] ?? 0,
      },
      invert: Boolean(props.invert),
    };
  } catch {
    return null;
  }
}


export function setViewport(element: HTMLElement, viewport: Record<string, unknown>): void {
  try {
    const enabled = getEnabledElement(element as HTMLDivElement);
    if (!enabled) return;
    const vp = enabled.viewport as Types.IStackViewport;
    // voi {windowWidth, windowCenter} → voiRange {lower, upper}
    const voi = (viewport.voi ?? {}) as { windowWidth?: number; windowCenter?: number };
    const ww = voi.windowWidth ?? 0;
    const wc = voi.windowCenter ?? 0;
    if (ww > 0) {
      vp.setProperties({ voiRange: { lower: wc - ww / 2, upper: wc + ww / 2 } });
    }
    // invert / scale / translation：与 getViewport 读出口对称，旧签名完整兼容
    if (typeof viewport.invert === 'boolean') {
      vp.setProperties({ invert: viewport.invert });
    }
    const cam = vp.getCamera();
    let camChanged = false;
    if (typeof viewport.scale === 'number' && viewport.scale > 0) {
      cam.parallelScale = viewport.scale;
      camChanged = true;
    }
    const tr = viewport.translation as { x?: number; y?: number } | undefined;
    if (tr && (typeof tr.x === 'number' || typeof tr.y === 'number')) {
      const focal = [...(cam.focalPoint ?? [0, 0, 0])] as [number, number, number];
      if (typeof tr.x === 'number') focal[0] = tr.x;
      if (typeof tr.y === 'number') focal[1] = tr.y;
      cam.focalPoint = focal;
      camChanged = true;
    }
    if (camChanged) vp.setCamera(cam);
  } catch {
    // ignore
  }
}

export function resetViewport(element: HTMLElement): void {
  try {
    const enabled = getEnabledElement(element as HTMLDivElement);
    if (!enabled) return;
    enabled.viewport.resetCamera();
    enabled.viewport.resetProperties();
  } catch {
    // ignore
  }
}

export function resize(element: HTMLElement): void {
  engineByElement.get(element)?.resize();
}

function currentToolGroup() {
  return currentToolGroupId
    ? ToolGroupManager.getToolGroup(currentToolGroupId)
    : null;
}

export function activateTool(toolName: string): void {
  currentToolGroup()?.setToolActive(TOOL_MAP[toolName] ?? toolName, {
    bindings: [{ mouseButton: ToolsEnums.MouseBindings.Primary }],
  });
}

export function deactivateTool(toolName: string): void {
  currentToolGroup()?.setToolDisabled(TOOL_MAP[toolName] ?? toolName);
}

export { cornerstone, wadouri };

export type { Types };
