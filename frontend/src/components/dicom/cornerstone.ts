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
    // 修复：WeakMap 此前只声明从未写入（disable 读它恒 undefined）。
    // 工作区同时只有一个 viewport，模块级 currentToolGroupId 兜得住，
    // 但按 element 定位才是正确语义 —— 多 viewport 时 activateTool 会作用错组。
    toolGroupByElement.set(element, `tg_${engineSeq}`);
  }
  eventTarget.addEventListener(
    CoreEnums.Events.IMAGE_RENDERED,
    ((evt: CustomEvent) => {
      const vpId = (evt.detail as { viewportId?: string })?.viewportId;
      if (vpId && vpId === viewportIdByElement.get(element)) {
        // 帧指示器（A.38）：滚动/键盘翻帧后渲染层要知道当前帧号。
        // 当前帧号从 viewport 读，经自定义事件 detail 传出。
        let imageIndex: number | undefined;
        try {
          const vp = engine.getViewports().find((v) => v.id === vpId);
          const stack = vp as Types.IStackViewport | undefined;
          imageIndex = stack?.getCurrentImageIdIndex?.();
        } catch {
          // 元素可能已分离
        }
        element.dispatchEvent(
          new CustomEvent('cornerstoneimagerendered', { detail: { imageIndex } }),
        );
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

/**
 * 显示单张或多张影像（A.38 多帧分期①）。
 *
 * 多张时走 StackViewport 原生多帧栈：setStack(全部 imageIds)，
 * 医生可用鼠标滚轮/本适配器 scrollStack 翻帧。VOI 全窗按**第一张**
 * 取值域设置 —— 16-bit US 无 VOI 窗位时 cornerstone 会把像素裁成全白
 * （A.35 验收发现）；同一次采集的帧值域一致，首帧窗位对全栈成立。
 * 兼容旧单张签名。
 */
export function displayImage(
  element: HTMLElement,
  image: { imageId?: string } | Array<{ imageId?: string }>,
): void {
  const engine = engineByElement.get(element);
  const viewportId = viewportIdByElement.get(element);
  const images = Array.isArray(image) ? image : [image];
  const ids = images.map((i) => i?.imageId).filter((id): id is string => Boolean(id));
  if (!engine || !viewportId || ids.length === 0) return;
  const vp = engine.getViewports().find((v) => v.id === viewportId);
  if (!vp) return;
  const stack = vp as Types.IStackViewport;
  void (async () => {
    // 整栈预缓存（A.38 真机探针教训）：只缓存首帧时，scroll 到未加载帧会
    // 静默不动（无报错、无渲染事件）。多帧帧体积小，全部 loadAndCacheImage。
    const imgs = await Promise.all(ids.map((id) => loadAndCacheImage(id)));
    const min = imgs[0]!.minPixelValue ?? 0;
    const max = imgs[0]!.maxPixelValue ?? 255;
    await stack.setStack(ids);
    stack.setProperties({
      voiRange: { lower: min, upper: max },
    });
  })().catch((err: unknown) => console.error('[cornerstone3d] display failed', err));
}

/**
 * 多帧滚动（A.38）：沿栈前进/后退 distance 帧，越界由 cornerstone 截断。
 * 返回滚动后的帧下标（0 起）；拿不到返回 null —— 调用方可直读更新指示器，
 * 不必等渲染事件（真机探针实测：渲染事件在个别路径上不可靠）。
 * 仅对 StackViewport 生效；单帧栈滚不动（无害）。
 */
export function scrollStack(element: HTMLElement, distance: number): number | null {
  try {
    const enabled = getEnabledElement(element as HTMLDivElement);
    if (!enabled) return null;
    (enabled.viewport as Types.IStackViewport).scroll(distance);
    const idx = (enabled.viewport as Types.IStackViewport).getCurrentImageIdIndex?.();
    return typeof idx === 'number' ? idx : null;
  } catch {
    return null;
  }
}

/** 当前帧下标（0 起）；拿不到返回 null（调用方自行兜底）。 */
export function getStackIndex(element: HTMLElement): number | null {
  try {
    const enabled = getEnabledElement(element as HTMLDivElement);
    if (!enabled) return null;
    const idx = (enabled.viewport as Types.IStackViewport).getCurrentImageIdIndex?.();
    return typeof idx === 'number' ? idx : null;
  } catch {
    return null;
  }
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
