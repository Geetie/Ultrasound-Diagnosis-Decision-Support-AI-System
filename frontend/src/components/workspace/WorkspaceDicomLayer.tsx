import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react';

/**
 * 影像区里的真实 DICOM 层（铺在影像区底部的 DICOM 容器）。
 *
 * 设计包 2:4 的 `impl_note` 写明：渐变只是占位，**真实实现放 DICOM 容器**，
 * 底色 `color-image-bg` 且不参与主题切换。叠加标注仍在本层之上 ——
 * 见 WorkspaceImageRegion。硬规则不变：影像区高度恒定 600，本层不参与高度计算。
 *
 * 为什么 cornerstone 是**动态 import**：
 *   1. cornerstone-core / wado-image-loader 合计约 2MB，只有真的要显示 DICOM 时才需要，
 *      不该让所有工作区状态都背着这个 chunk；
 *   2. cornerstone 在 import 期就会摸WebGL / canvas，静态 import 会让 jsdom 下的
 *      组件测试直接崩（两个 WorkspacePage 套件就是这么挂的）。
 *
 * 【多帧分期①（A.38）】本层从单张改为接收 imageIds 数组：
 *   · 数组 > 1 → StackViewport 原生多帧栈，右下角帧指示器「n/N ‹ ›」+
 *     键盘 ←→ 翻帧（默认沉默：单帧不渲染指示器 —— 单帧超声没有可翻的东西）；
 *   · 左上角浏览工具胶囊组（窗宽窗位/缩放/平移互斥单选）—— 工具早在
 *     cornerstone.ts 注册了（WindowLevel/Zoom/Pan），缺的只是激活入口，
 *     医生此前点不了任何浏览操作（阅片生命周期缺口，A.38 盘点）。
 */
type BrowseTool = 'windowLevel' | 'zoom' | 'pan';

/** cornerstone 工具名映射（与 useDicomViewer/DicomToolbar 同一套口径）。 */
const TOOL_ACTIVATION: Record<BrowseTool, string> = {
  windowLevel: 'WwwcTool',
  zoom: 'ZoomTool',
  pan: 'PanTool',
};

const TOOL_LABEL: Record<BrowseTool, string> = {
  windowLevel: '窗宽窗位',
  zoom: '缩放',
  pan: '平移',
};

export default function WorkspaceDicomLayer({ imageIds }: { imageIds: string[] }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  // 当前帧下标（0 起）。渲染事件带回（鼠标滚轮/拖动也走这里同步），
  // 指示器按钮/键盘则主动 scrollStack —— 单一事实源是 viewport 本身。
  const [frameIndex, setFrameIndex] = useState(0);
  const [activeTool, setActiveTool] = useState<BrowseTool>('windowLevel');
  // 数组身份每次渲染都可能变，effect 只关心内容 —— 用 join 做依赖。
  // 不能在渲染期写 imageIdsRef.current = imageIds（react-hooks error：
  // 渲染期访问 ref 会触发级联渲染），内容统一从 stackKey 派生。
  const stackKey = imageIds.join('\u0000');
  const frames = imageIds.length;
  const multiFrame = frames > 1;

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const stack = stackKey.split('\u0000').filter(Boolean);
    if (stack.length === 0) return;

    let cancelled = false;

    (async () => {
      try {
        const cornerstone = await import('@/components/dicom/cornerstone');
        if (cancelled) return;

        // feature 分支把 core-only 初始化换成统一入口 initCornerstone()：
        // 内部按需ensureInit（core + WADO 加载器 + 工具栈），幂等。
        // **必须 await**：init 是异步链（coreInit → loaderInit 注册 scheme → 工具），
        // 不等它就在这里 loadAndCacheImage 会撞上「loader 未注册」的竞态（A.35）。
        await cornerstone.initCornerstone();

        // displayImage 前必须先 enable，否则 cornerstone 抛 "element not enabled"。
        // 不静默吞异常 —— 吞掉之后的表现是 canvas 停在 300x150 默认尺寸，极难排查。
        cornerstone.enable(element);

        // imageId 按 scheme 分派，**不能**无脑补 wadouri: 前缀（2026-10-08 真机走查 P0）：
        //   · `wadouri:x`      —— 加载器走 xhrRequest（HTTP 取流），dev 后门 /dev-dicom/* 用这条
        //   · `dicomfile:n`    —— fileManager 的 Blob 路径（上传/回取注册产出的就是它），
        //     加前缀会变成 `wadouri:dicomfile:n`，加载器把 "dicomfile:n" 当 **URL** 向页面
        //     origin 发 XHR → 404 → 渲染层 catch 后只剩角落一行 11px 错误文本，
        //     影像区永远停留在渐变占位 —— 医生看到的就是"图像一直不出来"
        const ids = stack.map((id) =>
          id.startsWith('wadouri:') || id.startsWith('dicomfile:') ? id : `wadouri:${id}`,
        );
        // 首张先显式加载：把加载错误冒到本层 UI（displayImage 内部只 console）。
        // 缓存命中后 displayImage 不会重复取流。
        await cornerstone.loadAndCacheImage(ids[0]!);
        if (cancelled) return;

        // 影像区 1440×600，图像 300×225 —— cornerstone 按医学比例 letterbox、不拉伸，
        // 避免把真实组织纹理弄变形。多张时一次 setStack 成多帧栈。
        cornerstone.displayImage(
          element,
          ids.map((imageId) => ({ imageId })),
        );
        // displayImage 之后强制把 canvas 对齐元素尺寸，否则保留 300x150 默认值
        cornerstone.resize(element);
        if (!cancelled) {
          setLoaded(true);
          setFrameIndex(0);
        }
      } catch (e) {
        if (!cancelled) setError((e as Error)?.message || 'DICOM 加载失败');
      }
    })();

    const onResize = () => {
      // cornerstone 可能还没加载完，交给上面的 try 兜底
      import('@/components/dicom/cornerstone')
        .then((cs) => cs.resize(element))
        .catch(() => undefined);
    };
    window.addEventListener('resize', onResize);

    // 帧同步：任何来源的翻帧（滚轮/按钮/键盘）最终都触发重渲染事件，
    // 这里统一把当前帧号抄回 React 状态。
    const onRendered = (evt: Event) => {
      const idx = (evt as CustomEvent).detail?.imageIndex;
      if (typeof idx === 'number') setFrameIndex(idx);
    };
    element.addEventListener('cornerstoneimagerendered', onRendered);

    return () => {
      cancelled = true;
      window.removeEventListener('resize', onResize);
      element.removeEventListener('cornerstoneimagerendered', onRendered);
      import('@/components/dicom/cornerstone')
        .then((cs) => cs.disable(element))
        .catch(() => undefined);
    };
  }, [stackKey]);

  // 浏览工具激活：工具已在 ensureInit 注册，这里只切激活态。
  // 互斥单选 —— 激活一个、禁掉另外两个（与 useDicomViewer 同语义）。
  useEffect(() => {
    const element = ref.current;
    if (!element || !loaded) return;
    let cancelled = false;
    import('@/components/dicom/cornerstone')
      .then((cs) => {
        if (cancelled) return;
        for (const [key, name] of Object.entries(TOOL_ACTIVATION) as Array<[BrowseTool, string]>) {
          if (key === activeTool) cs.activateTool(name);
          else cs.deactivateTool(name);
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [activeTool, loaded]);

  const scrollBy = useCallback(
    (delta: number) => {
      const element = ref.current;
      if (!element || !multiFrame) return;
      import('@/components/dicom/cornerstone')
        .then((cs) => cs.scrollStack(element, delta))
        .catch(() => undefined);
    },
    [multiFrame],
  );

  // 键盘 ←→ 翻帧（多帧时）。医生在扫查态双手常在键盘上，
  // 比点按钮顺手；输入框聚焦时不劫持（写报告时打字不该翻片）。
  useEffect(() => {
    if (!multiFrame) return;
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
      if (e.key === 'ArrowLeft') {
        e.preventDefault();
        scrollBy(-1);
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        scrollBy(1);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [multiFrame, scrollBy]);

  return (
    <div
      ref={ref}
      data-region="dicom-layer"
      style={{ position: 'absolute', inset: 0, background: 'var(--color-image-bg)' }}
    >
      {error && (
        <span
          style={{
            position: 'absolute',
            left: 24,
            bottom: 12,
            fontSize: 11,
            color: 'var(--color-semantic-stop)',
          }}
        >
          DICOM: {error}
        </span>
      )}
      {!loaded && !error && frames > 0 && (
        <span
          style={{
            position: 'absolute',
            left: 24,
            bottom: 12,
            fontSize: 11,
            color: 'var(--color-text-secondary)',
          }}
        >
          影像加载中…
        </span>
      )}

      {/* 浏览工具胶囊组（左上，参数角标下方）。默认沉默：影像没起来不渲染。 */}
      {loaded && !error && (
        <div
          data-region="browse-tools"
          role="toolbar"
          aria-label="影像浏览工具"
          style={{
            position: 'absolute',
            left: 24,
            top: 44,
            display: 'flex',
            gap: 6,
            zIndex: 2,
          }}
        >
          {(Object.keys(TOOL_ACTIVATION) as BrowseTool[]).map((tool) => {
            const active = tool === activeTool;
            return (
              <button
                key={tool}
                type="button"
                aria-pressed={active}
                title={TOOL_LABEL[tool]}
                onClick={() => setActiveTool(tool)}
                style={{
                  height: 26,
                  padding: '0 10px',
                  fontSize: 11,
                  lineHeight: '26px',
                  borderRadius: 13,
                  cursor: 'pointer',
                  border: `1px solid ${active ? 'var(--color-attention)' : 'var(--color-border)'}`,
                  background: 'rgba(10, 14, 22, 0.72)',
                  color: active ? 'var(--color-attention)' : 'var(--color-text-secondary)',
                }}
              >
                {TOOL_LABEL[tool]}
              </button>
            );
          })}
        </div>
      )}

      {/* 帧指示器（右下）：仅多帧时渲染 —— 单帧没有可翻的东西（默认沉默）。 */}
      {multiFrame && loaded && !error && (
        <div
          data-region="frame-indicator"
          style={{
            position: 'absolute',
            right: 24,
            bottom: 12,
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            height: 26,
            padding: '0 10px',
            borderRadius: 13,
            background: 'rgba(10, 14, 22, 0.72)',
            border: '1px solid var(--color-border)',
            zIndex: 2,
          }}
        >
          <button
            type="button"
            aria-label="上一帧"
            onClick={() => scrollBy(-1)}
            style={frameBtnStyle}
          >
            ‹
          </button>
          <span
            style={{
              fontSize: 11,
              color: 'var(--color-text-primary)',
              minWidth: 34,
              textAlign: 'center',
            }}
          >
            {frameIndex + 1}/{frames}
          </span>
          <button
            type="button"
            aria-label="下一帧"
            onClick={() => scrollBy(1)}
            style={frameBtnStyle}
          >
            ›
          </button>
        </div>
      )}
    </div>
  );
}

const frameBtnStyle: CSSProperties = {
  width: 22,
  height: 22,
  padding: 0,
  fontSize: 14,
  lineHeight: '20px',
  textAlign: 'center',
  cursor: 'pointer',
  border: 'none',
  background: 'transparent',
  color: 'var(--color-text-primary)',
};
