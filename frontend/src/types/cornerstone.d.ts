/**
 * cornerstone 系列库未随包发布类型声明（cornerstone-core / -tools /
 * -wado-image-loader 均为 UMD 打包且上游无 @types）。这里给出**最小可用**
 * 声明，覆盖本项目实际调用的 API，避免整份工程因隐式 any 无法编译；
 * 真实调用契约以各库文档为准（真机联调时若签名不符，以此文件为准修正）。
 */

/**
 * 由本项目的初始化代码挂到 window 上的全局（见 `components/dicom/cornerstone.ts`）。
 * 声明在这里而不是每次 `(window as any)`：把"这个全局长什么样"集中成一处契约，
 * 调用侧就不必靠断言绕过类型检查（`no-explicit-any` 也会因此不再报）。
 */
interface Window {
  cornerstone?: {
    getViewport(element: HTMLElement): import('cornerstone-core').CornerstoneViewport;
    setViewport(
      element: HTMLElement,
      viewport: import('cornerstone-core').CornerstoneViewport
    ): void;
    [key: string]: unknown;
  };
  /** cornerstone-tools 依赖的外部 Hammer 全局 */
  Hammer?: unknown;
}

declare module 'cornerstone-core' {
  export interface CornerstoneViewport {
    scale: number;
    translation: { x: number; y: number };
    voi: { windowWidth: number; windowCenter: number };
    [key: string]: unknown;
  }

  export interface CornerstoneImage {
    imageId: string;
    width: number;
    height: number;
    windowWidth?: number;
    windowCenter?: number;
    [key: string]: unknown;
  }

  // 参数与返回值按 any 处理（上游无类型；项目内既有代码也以宽松结构传递
  // image/viewport），避免为迁就声明而改动业务流程代码。
  const cornerstone: {
    enable(element: HTMLElement): void;
    disable(element: HTMLElement): void;
    loadImage(imageId: string): Promise<CornerstoneImage>;
    loadAndCacheImage(imageId: string): Promise<CornerstoneImage>;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    displayImage(element: HTMLElement, image: any, options?: Record<string, unknown>): void;
    getViewport(element: HTMLElement): CornerstoneViewport;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    setViewport(element: HTMLElement, viewport: any): void;
    updateImage(element: HTMLElement): void;
    resize(element: HTMLElement, width?: number, height?: number): void;
    reset(element: HTMLElement): void;
    fitToWindow(element: HTMLElement): void;
    getEnabledElement(element: HTMLElement): unknown;
    getEnabledElementByIds(enabledElementId: string, viewportId: string): unknown;
  };
  export default cornerstone;
}

declare module 'cornerstone-tools' {
  export interface ToolStateData {
    data: Array<Record<string, unknown>>;
  }

  const cornerstoneTools: {
    external: Record<string, unknown>;
    init(options?: Record<string, unknown>): void;
    addTool(tool: unknown): void;
    addToolForElement(element: HTMLElement, tool: unknown): void;
    setToolActive(toolName: string, options?: Record<string, unknown>): void;
    setToolPassive(toolName: string, options?: Record<string, unknown>): void;
    setToolDisabled(toolName: string, options?: Record<string, unknown>): void;
    getToolForElement(element: HTMLElement, toolName: string): unknown;
    clearToolState(element: HTMLElement, toolName: string): void;
    getToolState(element: HTMLElement, toolName: string): ToolStateData;
    [key: string]: unknown;
  };
  export default cornerstoneTools;
}

declare module 'cornerstone-wado-image-loader' {
  const cornerstoneWADOImageLoader: {
    external: Record<string, unknown>;
    configure(options: Record<string, unknown>): void;
    webWorkerManager: {
      initialize(options: Record<string, unknown>): void;
    };
    wadouri: {
      fileManager: {
        add(imageId: string, file: File): string;
      };
    };
    [key: string]: unknown;
  };
  export default cornerstoneWADOImageLoader;
}

declare module 'dicom-parser' {
  const dicomParser: Record<string, unknown>;
  export default dicomParser;
}
