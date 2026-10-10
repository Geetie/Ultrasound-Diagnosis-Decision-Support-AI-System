import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react';
// 仅类型：编译期擦除，不会把 cornerstone 拉进 jsdom 测试（运行时仍是动态 import）
import type { MeasureResult } from '@/components/dicom/cornerstone';
import type { StoredAnnotation } from '@/services/annotationsService';

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
 *   · 左上角浏览工具胶囊组（窗宽窗位/缩放/平移/长度/椭圆互斥单选）—— 浏览
 *     工具在 cornerstone.ts 注册已久（A.38 补激活入口）；测量工具 A.40 补齐，
 *     读数（mm/mm²）由 cornerstone 按 DICOM PixelSpacing 换算，UI 只显示。
 */
type BrowseTool = 'windowLevel' | 'zoom' | 'pan' | 'length' | 'ellipse';

/** cornerstone 工具名映射（与 useDicomViewer/DicomToolbar 同一套口径）。 */
const TOOL_ACTIVATION: Record<BrowseTool, string> = {
  windowLevel: 'WwwcTool',
  zoom: 'ZoomTool',
  pan: 'PanTool',
  // 测量（A.40）：画完自动出 mm 读数（cornerstone 按 PixelSpacing 换算）
  length: 'Length',
  ellipse: 'EllipticalROI',
};

const TOOL_LABEL: Record<BrowseTool, string> = {
  windowLevel: '窗宽窗位',
  zoom: '缩放',
  pan: '平移',
  length: '长度',
  ellipse: '椭圆',
};

export default function WorkspaceDicomLayer({
  imageIds,
  archiveKey,
}: {
  imageIds: string[];
  /** 测量注释存档 key（后端 image_id）。undefined = 不持久化（预览场景）。 */
  archiveKey?: string;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  // 当前帧下标（0 起）。渲染事件带回（鼠标滚轮/拖动也走这里同步），
  // 指示器按钮/键盘则主动 scrollStack —— 单一事实源是 viewport 本身。
  const [frameIndex, setFrameIndex] = useState(0);
  const [activeTool, setActiveTool] = useState<BrowseTool>('windowLevel');
  // 最近一次测量读数（长度 mm / 面积 mm²）。带 stackKey 标识：换会话/换影像后
  // 渲染期对不上就当 null（旧读数不属于新影像），不搞 effect 里同步 setState。
  // 只显示最近一条 —— 历史测量是标注持久化的事（feature_list 远期项）。
  const [measure, setMeasure] = useState<{ key: string; text: string } | null>(null);
  // 测量注释存档（A.40）：画完一条立即 PUT（含此前画的所有 —— 全量覆盖语义，
  // 服务端原子写）。恢复的注释不计入（再次 PUT 会把恢复的原样写回，无害）。
  const [savedAnnotations, setSavedAnnotations] = useState<StoredAnnotation[]>([]);
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

  // 浏览工具激活 + 测量读数注册（A.40）：工具已在 ensureInit 注册，这里只切激活态。
  // 互斥单选 —— 激活一个、禁掉其余（与 useDicomViewer 同语义）。
  // 测量监听挂同一个模块实例上（eventTarget 是单例，取消函数必须调）。
  useEffect(() => {
    const element = ref.current;
    if (!element || !loaded) return;
    let cancelled = false;
    let off: (() => void) | null = null;
    import('@/components/dicom/cornerstone')
      .then((cs) => {
        if (cancelled) return;
        for (const [key, name] of Object.entries(TOOL_ACTIVATION) as Array<[BrowseTool, string]>) {
          if (key === activeTool) cs.activateTool(name);
          else cs.deactivateTool(name);
        }
        // cornerstone 已按 PixelSpacing 把测量换算成 mm —— UI 只显示数值。
        // stackKey 进闭包：读数带归属标识，换影像后派生为不显示。
        const key = stackKey;
        off = cs.onMeasureCompleted((m: MeasureResult) => {
          setMeasure({
            key,
            text: `${m.tool === 'Length' ? '长度' : '面积'} ${m.value.toFixed(1)} ${m.unit}`,
          });
          // 标注持久化（A.40）+ 帧级归属（A.45 分期③）：注释原对象序列化进存档
          // 列表并带上当前帧号 —— 多帧栈里翻帧只恢复当前帧的注释
          if (m.annotation) {
            const serialized = JSON.parse(JSON.stringify(m.annotation)) as StoredAnnotation;
            serialized.frame_index = frameIndex;
            setSavedAnnotations((prev) => [...prev, serialized]);
          }
        });
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
      off?.();
    };
    // frameIndex 进依赖：闭包里的帧号必须跟手 —— 否则画的注释全记成旧帧
  }, [activeTool, loaded, stackKey, frameIndex]);

  // 存档写入：有 archiveKey 且有未保存注释时 PUT 全量（服务端原子覆盖）。
  // 失败静默 —— 标注是增强，不该因网络抖动打断阅片；重画一次即重试。
  useEffect(() => {
    if (!archiveKey || savedAnnotations.length === 0) return;
    import('@/services/annotationsService')
      .then((svc) => svc.saveAnnotations(archiveKey, savedAnnotations))
      .catch(() => undefined);
  }, [archiveKey, savedAnnotations]);

  // 存档恢复：装载完成且有 archiveKey 时 GET 并渲染**当前帧**的历史标注
  // （Passive 态）。帧级归属（A.45 分期③；E1 真机探针修正锚点）：过滤锚用
  // **CS3D referencedImageId === 当前 imageId**，不是 UI frameIndex ——
  // wadouri 的 dicomfile:N 编号与帧序有偏移，frameIndex 锚会导致翻回原帧
  // 不恢复（CS3D 显示过滤器只认 referencedImageId）。旧存档无
  // referencedImageId 时退回 frame_index（无则第 0 帧）兜底。
  useEffect(() => {
    const element = ref.current;
    if (!element || !loaded || !archiveKey) return;
    let cancelled = false;
    import('@/services/annotationsService')
      .then((svc) => svc.fetchAnnotations(archiveKey))
      .then((anns) => {
        // 空存档：不恢复也不清场（旧行为 —— restoreAnnotations 空调用没有意义）
        if (cancelled || anns.length === 0) return;
        import('@/components/dicom/cornerstone')
          .then((cs) => {
            if (cancelled) return;
            cs.clearAnnotations();
            const curImgId = cs.getCurrentImageId(element);
            const frameAnns = anns.filter((a) => {
              const refId = (a.metadata as { referencedImageId?: string } | undefined)
                ?.referencedImageId;
              if (refId) return refId === curImgId;
              return ((a.frame_index as number | undefined) ?? 0) === frameIndex;
            });
            if (frameAnns.length === 0) {
              setSavedAnnotations(anns);
              return;
            }
            cs.restoreAnnotations(element, frameAnns);
            // 恢复的注释进存档缓存：医生再画一条时 PUT 会把整批写回（全量覆盖）
            setSavedAnnotations(anns);
          })
          .catch(() => undefined);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [archiveKey, loaded, stackKey, frameIndex]);

  const scrollBy = useCallback(
    (delta: number) => {
      const element = ref.current;
      if (!element || !multiFrame) return;
      import('@/components/dicom/cornerstone')
        .then((cs) => {
          const idx = cs.scrollStack(element, delta);
          // 渲染事件同步是后备，这里直读返回值先更新指示器（真机探针：
          // 个别路径上渲染事件不可靠，指示器会停在旧帧号）
          if (typeof idx === 'number') setFrameIndex(idx);
        })
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
          {/* 最近一次测量读数：医生画完一条线/一个椭圆立刻看得见数值。
              默认沉默 —— 没画过不占位；读数带 stackKey 归属，换影像派生为不显示。 */}
          {measure && measure.key === stackKey && (
            <span
              data-region="measure-readout"
              style={{
                height: 26,
                padding: '0 10px',
                fontSize: 11,
                lineHeight: '26px',
                borderRadius: 13,
                background: 'rgba(10, 14, 22, 0.72)',
                color: 'var(--color-text-primary)',
              }}
            >
              {measure.text}
            </span>
          )}
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
