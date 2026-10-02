import { useEffect, useRef, useState } from 'react';

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
 */
export default function WorkspaceDicomLayer({ imageId }: { imageId: string }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;

    let cancelled = false;

    (async () => {
      try {
        const cornerstone = await import('@/components/dicom/cornerstone');
        if (cancelled) return;

        // feature 分支把 core-only 初始化换成统一入口 initCornerstone()：
        // 内部按需ensureInit（core + WADO 加载器 + 工具栈），幂等。
        cornerstone.initCornerstone();

        // displayImage 前必须先 enable，否则 cornerstone 抛 "element not enabled"。
        // 不静默吞异常 —— 吞掉之后的表现是 canvas 停在 300x150 默认尺寸，极难排查。
        cornerstone.enable(element);

        const id = imageId.startsWith('wadouri:') ? imageId : `wadouri:${imageId}`;
        const image = await cornerstone.loadAndCacheImage(id);
        if (cancelled) return;

        // 影像区 1440×600，图像 300×225 —— cornerstone 按医学比例 letterbox、不拉伸，
        // 避免把真实组织纹理弄变形。
        cornerstone.displayImage(element, image);
        // displayImage 之后强制把 canvas 对齐元素尺寸，否则保留 300x150 默认值
        cornerstone.resize(element);
        if (!cancelled) setLoaded(true);
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

    return () => {
      cancelled = true;
      window.removeEventListener('resize', onResize);
      import('@/components/dicom/cornerstone')
        .then((cs) => cs.disable(element))
        .catch(() => undefined);
    };
  }, [imageId]);

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
      {!loaded && !error && (
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
    </div>
  );
}
