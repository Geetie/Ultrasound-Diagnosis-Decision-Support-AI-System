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
  annotation,
  Enums as ToolsEnums,
  EllipticalROITool,
  LengthTool,
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
import { reportError, reportWarn } from '@/utils/errorReport';

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
  // 测量工具（A.40）：cornerstoneTools 现成，PixelSpacing 在 DICOM 里就绪，
  // 长度/面积换算 mm 由 cornerstone 注释管线承担，UI 只读结果
  Length: 'Length',
  EllipticalROI: 'EllipticalROI',
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
    addTool(LengthTool);
    addTool(EllipticalROITool);
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
    tg.addTool(LengthTool.toolName);
    tg.addTool(EllipticalROITool.toolName);
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
          // silent-ok: 元素已分离时读帧号属正常控制流（imageIndex 留 undefined，指示器回落不显示）
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
    // silent-ok: disable 是幂等清理（重复销毁 / 元素已分离属预期），无用户可见影响
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
  })().catch((err: unknown) => {
    // A.60 补处置（A.35 病灶的正主）：这条链 = 整栈预缓存 → setStack → setProperties。
    // 原先只打一行 console.error 就吞掉，而它把异常咽在 Promise 链里 —— 上层
    // WorkspaceDicomLayer 的 `unhandledrejection` 监听**接不到**，医生看到的是一块空白
    // 影像区、门禁全绿（A.35「空白活了 3 天」正是这个形态）。
    // 现在：统一出口留痕 + **重新抛出**，让上层可见出口（`dicom-load-error`）兜住。
    reportError('cornerstone3d', '影像显示失败（像素/帧数据未能装载，影像区可能空白）', err);
    throw err;
  });
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
    // silent-ok: null 是契约内语义（调用方直读+渲染事件双路兜底），探测式读无需留痕
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
    // silent-ok: 同上，未 enable / 已分离属正常分支，null 由调用方兜底
    return null;
  }
}

/** 当前帧的 imageId（A.50.6 E1）：帧级标注归属的锚。
 *
 * 帧级过滤必须用 imageId 而不是 UI frameIndex —— E1 真机探针实测：wadouri
 * fileManager 的 dicomfile:N 编号与 UI 帧序**存在偏移**（画在第 1 帧的注释
 * referencedImageId=dicomfile:1），CS3D 显示过滤器按 referencedImageId 匹配
 * currentImageId，frameIndex 锚定会导致「翻回原帧不恢复」。
 */
export function getCurrentImageId(element: HTMLElement): string | null {
  try {
    const enabled = getEnabledElement(element as HTMLDivElement);
    // CS3D 的类型签名允许 undefined（元素未 enable）——此前靠 tsc 增量缓存
    // 侥幸没报，清缓存重建即 TS18048。这里显式守卫，行为不变（无 viewport 时
    // 返回 null，正是 catch 分支的老语义）。
    const id = (enabled?.viewport as Types.IStackViewport | undefined)?.getCurrentImageId?.();
    return typeof id === 'string' ? id : null;
  } catch {
    // silent-ok: 未 enable / 已分离时拿不到 imageId 属正常（标注归属回落「不显示」）
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
    // silent-ok: 读窗口参数属探测式调用（viewport 未就绪即返回 null，调用方用默认值）
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
  } catch (err) {
    // A.60：**写**失败与"读不到"不同 —— 医生这次调的窗位/缩放可能没生效，必须留痕。
    // 不给弹窗：view 侧仍持有目标值，下一次渲染事件会重试；但零留痕会让"调了没反应"
    // 变成无法排查的怪象（本处原为 `catch {} // ignore`）。
    reportWarn('cornerstone3d', '窗位/相机写回影像失败（本次调节可能未生效）', err);
  }
}

export function resetViewport(element: HTMLElement): void {
  try {
    const enabled = getEnabledElement(element as HTMLDivElement);
    if (!enabled) return;
    enabled.viewport.resetCamera();
    enabled.viewport.resetProperties();
  } catch {
    // silent-ok: 复位是幂等操作（元素未就绪时无副作用，医生可再点一次）
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

/**
 * 读 DICOM NumberOfFrames（A.40 分期②）。
 *
 * 一次装载 = 一份完整多帧 DICOM（CAMUS cine 导出、机器 cine 存档），
 * 此前只显示首帧。失败/缺标签返回 1（单帧）——多帧是增强，不挡主链。
 */
export async function getNumberOfFrames(imageId: string): Promise<number> {
  try {
    const bare = imageId.split('?')[0]!;
    const dataSet = (await wadouri.dataSetCacheManager.load(
      bare,
      wadouri.loadFileRequest,
      bare,
    )) as { string?: (tag: string) => string | undefined };
    const n = parseInt(dataSet.string?.('x00280008') ?? '', 10);
    return Number.isFinite(n) && n > 1 ? n : 1;
  } catch {
    // silent-ok: 读 NumberOfFrames 属能力探测（缺标签/未装载即回落单帧，多帧是增强）
    return 1;
  }
}

/**
 * 单个 imageId → 显示栈条目。多帧文件扩展为 `id?frame=1..N`
 * （wadouri 帧寻址是 **1-based**：parseImageId 里 `frame - 1`），
 * 单帧原样返回。扩展后的帧复用同一份缓存 dataset，加载代价低。
 */
export async function expandStackId(imageId: string): Promise<string[]> {
  const n = await getNumberOfFrames(imageId);
  return n > 1 ? Array.from({ length: n }, (_, i) => `${imageId}?frame=${i + 1}`) : [imageId];
}

/** 医生画完一次测量（长度/椭圆）后回调的载荷。 */
export interface MeasureResult {
  tool: string;
  /** 长度 mm 或面积 mm²（cornerstone 按 DICOM PixelSpacing 换算）。 */
  value: number;
  unit: 'mm' | 'mm²';
  /** 注释原对象（标注持久化时序列化存档用）。 */
  annotation?: Record<string, unknown>;
}

/**
 * 监听测量完成（A.40）。cornerstone 注释管线在 ANNOTATION_COMPLETED 时
 * 已用 PixelSpacing 把像素换算成 mm —— UI 只读结果，不自己算。
 * 返回取消函数（组件卸载时必须调，eventTarget 是模块级单例）。
 */
export function onMeasureCompleted(handler: (m: MeasureResult) => void): () => void {
  const listener = ((evt: CustomEvent) => {
    // CS3D 5.11 真机形态（探针实测）：toolName 在 annotation.metadata.toolName
    // （顶层没有）；数值在 data.cachedStats[targetId].length/area。
    const ann = (evt.detail as {
      annotation?: {
        metadata?: { toolName?: string };
        data?: Record<string, unknown>;
      };
    })?.annotation;
    if (!ann) return;
    const toolName = ann.metadata?.toolName;
    const data = (ann.data ?? {}) as {
      length?: number;
      area?: number;
      cachedStats?: Record<string, { length?: number; area?: number; unit?: string }>;
    };
    const first = data.cachedStats?.[Object.keys(data.cachedStats ?? {})[0] ?? ''];
    const length = first?.length ?? data.length;
    const area = first?.area ?? data.area;
    if (toolName === LengthTool.toolName && typeof length === 'number') {
      handler({
        tool: toolName,
        value: length,
        unit: 'mm',
        annotation: ann as Record<string, unknown>,
      });
    } else if (toolName === EllipticalROITool.toolName && typeof area === 'number') {
      handler({
        tool: toolName,
        value: area,
        unit: 'mm²',
        annotation: ann as Record<string, unknown>,
      });
    }
  }) as EventListener;
  eventTarget.addEventListener(ToolsEnums.Events.ANNOTATION_COMPLETED, listener);
  return () => eventTarget.removeEventListener(ToolsEnums.Events.ANNOTATION_COMPLETED, listener);
}

/** 恢复到当前 viewport 的注释（标注持久化的载入侧）。失败逐条容忍。 */
/**
 * 清空已注册的全部注释（A.45 分期③：翻帧重恢复前必须清场，
 * 否则 CS3D 按同一 FrameOfReference 累积 —— 多帧栈里注释会叠在错误帧上）。
 * 工作区单 viewport，全量清安全；多 viewport 时需按 FoS 过滤。
 */
export function clearAnnotations(): void {
  try {
    const anns = annotation.state.getAllAnnotations() ?? [];
    for (const a of anns) {
      if (a.annotationUID) annotation.state.removeAnnotation(a.annotationUID);
    }
  } catch {
    // silent-ok: 清场是幂等操作（本帧无注释 / 元素已分离都属正常）
  }
}

export function restoreAnnotations(
  element: HTMLElement,
  annotations: Array<Record<string, unknown> & { toolName?: string }>,
): number {
  let restored = 0;
  let failed = 0;
  let firstError: unknown;
  const enabled = (() => {
    try {
      return getEnabledElement(element as HTMLDivElement);
    } catch {
      // silent-ok: 未 enable 的正常分支（随后 return 0，调用方按"本帧无标注"处理）
      return null;
    }
  })();
  if (!enabled) return 0;
  const tgId = toolGroupByElement.get(element);
  const tg = tgId ? ToolGroupManager.getToolGroup(tgId) : null;
  for (const ann of annotations) {
    // toolName 实际在 metadata.toolName（CS3D 序列化结构）——E1 真机探针实测：
    // 顶层检查导致恢复全部被跳过（restored=0），翻回原帧永远画不出来。
    const toolName = ann.toolName ?? (ann.metadata as { toolName?: string } | undefined)?.toolName;
    if (!toolName) continue;
    try {
      // CS3D 5.x：addAnnotation(annotation, groupSelector) 两参
      annotation.state.addAnnotation(ann as never, element as never);
      // 已存注释只渲染不响应绘制 —— 工具组 Passive（在组内渲染，但不吃鼠标）
      tg?.setToolPassive(toolName);
      restored += 1;
    } catch (err) {
      // 逐条容错（一条坏不拖累整批），但**必须留痕**：静默少一条 = 医生以为
      // 「这条标注当时没画/丢了」（A.60：恢复侧原先完全无声）。
      failed += 1;
      if (firstError === undefined) firstError = err;
    }
  }
  if (failed > 0) {
    reportWarn(
      'cornerstone3d',
      `本帧 ${failed}/${annotations.length} 条历史标注未能恢复（标注显示可能不完整）`,
      firstError,
    );
  }
  if (restored) {
    enabled.viewport.render();
  }
  return restored;
}

export { cornerstone, wadouri };

export type { Types };
