/**
 * 影像区 600 —— 节点 2:4「影像区-600-尺寸恒定」
 *
 * 逐条对应 02_扫查态/design_data.json：
 *   2:5  标注-未校验-虚线   220x170 @ (360,170)  2px dashed color-attention
 *   2:6  标注-已校验-实线   160x130 @ (830,300)  2px solid  color-text-primary
 *   2:97 影像叠加层-成像视口  1440x600 @ (0,0)
 *     2:98  参数角标  234x16  @ (24,20)     font-micro  text-secondary
 *     2:99  时间角标  160x13  @ (1256,20)   font-micro  text-secondary  right
 *     2:100 深度刻度  48x576  @ (1368,28)   font-micro  lineHeight 96   right
 *     2:101 测量框-冻结态 160x150 @ (540,250) 1px dashed text-primary
 *     2:102 测量值    83x15   @ (540,408)  font-tiny   Inter Medium
 *     2:103 标注标签-未校验 81x16 @ (360,146) font-micro color-attention
 *     2:104 标注标签-已校验 81x16 @ (830,276) font-micro text-primary
 *
 * 硬规则：高度恒定 600，不因结论卡 / 报告面板出现而缩放。
 * 渐变是占位（本环境无法生成真实超声图）；接真实 DICOM 时替换 .ws-image 的
 * background-image 为 DICOM 容器，底色 var(--color-image-bg) 不参与主题切换。
 *
 * 【坐标约定】design_data 里本区子节点的 x/y 是**相对影像区左上角**的坐标，
 * 绝对位置 = y + 常驻条高度(40)。因此所有子元素都直接用数据里的原始数值定位，
 * 由本组件自身承担偏移，不要再加 40。已用 参考图/S02-扫查态.png 逐像素核验：
 *   2:5虚线框 数据 y170..340 → 实测绝对 210..380
 *   2:6 实线框 数据 y300..430 → 实测绝对 340..470
 *   2:101测量框数据 y250..400 → 实测绝对 290..440
 *   深度刻度6 行行距 数据 lineHeight 96 → 实测 96.0，间隔精确一致
 */
import { useState } from 'react';
import type { ReactNode } from 'react';
import WorkspaceDicomLayer from './WorkspaceDicomLayer';
import type { SegmentationView } from '@/services/diagnosisAdapter';

/** available=false 的降级原因 → 医生能看懂的一句话（未知原因原文透传）。 */
const SEG_REASON_LABEL: Record<string, string> = {
  not_enabled: '分割旁路未启用（管理员可在启动配置开启）',
  no_image_ids: '本次判读未关联影像',
  image_not_indexed: '影像索引缺失',
  image_bytes_unavailable: '影像字节不可读',
};

/**
 * A.51 帧级归属判定：分割结果是否属于"正在看的这一帧"。
 *
 * 规则（与后端 selection_policy 对应）：
 *  - 未收到渲染事件（liveFrame=null）→ 视为首帧，允许渲染（避免首帧前闪烁空白）；
 *  - 分割自带 image_id 且与当前帧的 image_id 不同 → 不属于（多影像栈）；
 *  - 单张多帧（totalFrames>1）→ 必须 frameIndex 相等（择优帧只在那一帧呈现）；
 *  - 其余（单帧影像）→ 属于。
 */
export function segBelongsToLiveFrame(
  seg: SegmentationView | undefined,
  liveFrame: { index: number; imageId: string } | null,
): boolean {
  if (!seg?.available) return false;
  if (liveFrame === null) return true;
  if (seg.imageId && liveFrame.imageId && seg.imageId !== liveFrame.imageId) return false;
  if (seg.totalFrames != null && seg.totalFrames > 1) {
    return seg.frameIndex === liveFrame.index;
  }
  return true;
}

export default function WorkspaceImageRegion({
  organBar,
  dicomImageId,
  dicomImageIds,
  dicomFrameOwners,
  segmentation,
  annotationArchiveKey,
}: {
  /** 部位指示条插槽：仅部位数 ≥ 2 时传入（05 / 14）。它落在影像区内部底缘，
   *  不改变影像区高度 —— 影像区恒定 600。 */
  organBar?: ReactNode;
  /** 真实 DICOM 的 URL（单张，兼容旧签名）。给了就铺在渐变占位之上（渐变作为底色兜底），
   *  对应 2:4 的 impl_note：真实实现放 DICOM 容器。 */
  dicomImageId?: string;
  /** 多帧（A.38 分期①）：本会话已装载的 imageIds 栈（顺序=上传顺序）。
   *  传入时优先于 dicomImageId；空/省略回落单张语义。 */
  dicomImageIds?: string[];
  /**
   * A.52 帧归属：与 dicomImageIds **逐位对齐**的后端 image_id。
   *
   * 显示栈里是 Cornerstone 句柄（`dicomfile:N?frame=k`），分割自带的是后端 `img_...`，
   * 拿句柄比后端 id 永不相等 → 掩膜任何帧都不显示（A.52 走查实测）。缺省时退化为
   * 只比帧号（原语义），不阻塞旧调用方。
   */
  dicomFrameOwners?: string[];
  /** 分割旁路视图（来自诊断结果的 nodule_segmentation）。 */
  segmentation?: SegmentationView;
  /** 测量注释存档 key（A.40：后端 image_id，img_/frame_ 前缀）。
   *  undefined = 不持久化（预览/无后端 id 场景）。 */
  annotationArchiveKey?: string;
}) {
  const stack = dicomImageIds && dicomImageIds.length > 0
    ? dicomImageIds
    : dicomImageId
      ? [dicomImageId]
      : [];
  // A.52：与 stack 同长同序的「这一位属于哪张后端影像」。栈来自 dicomImageIds 时
  // 直接取对齐数组；单张语义时取首元素（并只对首位有效——多帧展开缺失时退化为空串，
  // 门控只比帧号，不会误判为"不归属"而彻底不显示）。
  const frameOwners = dicomImageIds && dicomImageIds.length > 0
    ? (dicomFrameOwners ?? [])
    : (dicomFrameOwners?.[0] ? [dicomFrameOwners[0]] : []);
  // 设计稿 2:5/2:6/2:101 的「结节A/B、18.4×12.1mm」是**写死的示意数据**。
  // 真实影像显示时必须隐藏 —— 假框叠在真片子上等于伪造标注（2026-10-08 走查发现）。
  const showMock = stack.length === 0;
  const seg = stack.length > 0 ? segmentation : undefined;
  // A.51 帧级归属：当前正在看的（帧号, image_id）由 DICOM 层上抛。
  // 分割覆盖层只在"它所属的那个影像/帧"上渲染，翻到别的帧自动隐藏——
  // 否则多帧栈里会出现"这一帧根本没跑分割，却叠了上一帧的框"的错标。
  const [liveFrame, setLiveFrame] = useState<{ index: number; imageId: string } | null>(null);
  const segFrameMatches = segBelongsToLiveFrame(seg, liveFrame);
  // A.52 分期②：分割所属帧的「直达」目标。nonce 递增 —— 同一帧再点一次也要生效。
  const [targetFrame, setTargetFrame] = useState<{ index: number; nonce: number } | undefined>();
  const bbox = seg?.available ? seg.bbox : null;
  const areaLabel = seg?.available
    ? seg.area_mm2 != null
      ? `${seg.area_mm2.toFixed(1)} mm²`
      : seg.area_px != null
        ? `${seg.area_px} px²`
        : null
    : null;

  return (
    <section className="ws-image" data-region="image" aria-label="实时影像">
      {/* 真实 DICOM 层（在叠加标注之下）。渐变仍留在 CSS 里当底色与加载兜底。
          多帧时 DicomLayer 内部出帧指示器与浏览工具（默认沉默口径在层内）。 */}
      {stack.length > 0 && (
        <WorkspaceDicomLayer
          imageIds={stack}
          frameOwners={frameOwners}
          archiveKey={annotationArchiveKey}
          targetFrame={targetFrame}
          onFrameChange={(index, imageId) => setLiveFrame({ index, imageId })}
        />
      )}

      {/* 分割旁路标注：bbox 画在 SVG 上，viewBox = 分割帧像素尺寸。
          Cornerstone fit 与 SVG meet 的 letterbox 数学一致，坐标因此对齐显示的影像。
          A.51：仅当当前帧/影像 == 分割所属帧/影像时渲染（segFrameMatches）。 */}
      {seg?.available && segFrameMatches && seg.width != null && seg.height != null && (
        <svg
          className="ws-image__seg"
          viewBox={`0 0 ${seg.width} ${seg.height}`}
          preserveAspectRatio="xMidYMid meet"
          data-region="segmentation-overlay"
          aria-label="病灶分割标注"
        >
          {bbox && (
            <rect
              x={bbox[0]}
              y={bbox[1]}
              width={bbox[2] - bbox[0]}
              height={bbox[3] - bbox[1]}
              fill="none"
              stroke="var(--color-overlay-attention)"
              strokeWidth={2}
              strokeDasharray="8 5"
              vectorEffect="non-scaling-stroke"
            />
          )}
          {areaLabel && bbox && (
            <text
              x={bbox[0]}
              y={Math.max(18, bbox[1] - 8)}
              fill="var(--color-overlay-attention)"
              fontSize={16}
              vectorEffect="non-scaling-stroke"
            >
              {`病灶 ${areaLabel}${seg.mean_prob != null ? ` · ${Math.round(seg.mean_prob * 100)}%` : ''}`}
            </text>
          )}
        </svg>
      )}

      {/* A.51 帧级归属提示：分割存在但不在当前帧 → 告诉医生去哪里看，
          避免误以为"这一帧没有分割结果"。 */}
      {seg?.available && !segFrameMatches && stack.length > 0 && (
        <span
          className="ws-image__text"
          style={{ left: 24, bottom: 12, width: 460, display: 'flex', gap: 8, alignItems: 'center' }}
          data-region="segmentation-off-frame"
        >
          {`分割结果在第 ${(seg.frameIndex ?? 0) + 1}/${seg.totalFrames ?? 1} 帧（${
            seg.selectionPolicy === 'best_mean_prob' ? '多候选择优' : '单帧'
          }）`}
          {/* A.52 分期②：多帧时给「一键直达该帧」——医生不必手动翻几十帧去找病灶帧 */}
          {(seg.totalFrames ?? 1) > 1 && (
            <button
              type="button"
              data-region="segmentation-jump"
              aria-label={`跳到第 ${(seg.frameIndex ?? 0) + 1} 帧`}
              onClick={() =>
                setTargetFrame((prev) => ({
                  index: seg.frameIndex ?? 0,
                  nonce: (prev?.nonce ?? 0) + 1,
                }))
              }
              style={{
                height: 22,
                padding: '0 10px',
                fontSize: 11,
                lineHeight: '22px',
                borderRadius: 11,
                cursor: 'pointer',
                border: '1px solid var(--color-overlay-attention)',
                background: 'rgba(10, 14, 22, 0.72)',
                color: 'var(--color-overlay-attention)',
              }}
            >
              跳到该帧
            </button>
          )}
        </span>
      )}

      {/* 后端契约：available=false 必须显示「未做分割：<原因>」，不能显示成
          「未检出结节」—— 缺权重/未启用与"没病"是两回事（schemas.py 原文）。 */}
      {seg && !seg.available && stack.length > 0 && (
        <span
          className="ws-image__text"
          style={{ left: 24, bottom: 12, width: 400 }}
          data-region="segmentation-unavailable"
        >
          未做分割：{SEG_REASON_LABEL[seg.reason ?? ''] ?? seg.reason ?? '原因未提供'}
        </span>
      )}

      {/* 2:5 未校验标注：虚线＝未经人工校验（用线型而非颜色表达）。
          仅设计预览态（无真实影像）渲染 —— 数据驱动标注见上方 SVG。 */}
      {showMock && (
        <>
          <div
            className="ws-anno ws-anno--unverified"
            style={{ left: 360, top: 170, width: 220, height: 170 }}
            data-node="2:5"
          />
          <span
            className="ws-image__text"
            style={{ left: 360, top: 146, width: 81 }}
            data-node="2:103"
          >
            结节 A · 未校验
          </span>

          {/* 2:6 已校验标注：实线＝已人工校验 */}
          <div
            className="ws-anno ws-anno--verified"
            style={{ left: 830, top: 300, width: 160, height: 130 }}
            data-node="2:6"
          />
          <span
            className="ws-image__text"
            style={{ left: 830, top: 276, width: 81 }}
            data-node="2:104"
          >
            结节 B · 已校验
          </span>
        </>
      )}

      {/* 2:97 影像叠加层-成像视口 */}
      <div className="ws-image__overlay" data-node="2:97">
        <span
          className="ws-image__text"
          style={{ left: 24, top: 20, width: 234 }}
          data-node="2:98"
        >
          甲状腺 · 线阵 12 MHz · 增益 62 · 深度 10 cm
        </span>

        <span
          className="ws-image__text ws-image__text--numeric"
          style={{ left: 1256, top: 20, width: 160 }}
          data-node="2:99"
        >
          2026-10-02  14:26:41
        </span>

        {/* 6 行刻度，行高 96px = 576 */}
        <span
          className="ws-image__scale"
          style={{ left: 1368, top: 28, width: 48, height: 576 }}
          data-node="2:100"
        >
          {'0 cm\n2 cm\n4 cm\n6 cm\n8 cm\n10 cm'}
        </span>

        {/* 2:101 测量框-冻结态＝测量态（设计稿示意，真实测量待数据驱动） */}
        {showMock && (
          <>
            <div
              className="ws-measure"
              style={{ left: 540, top: 250, width: 160, height: 150 }}
              data-node="2:101"
            />
            <span
              className="ws-measure__value"
              style={{ left: 540, top: 408 }}
              data-node="2:102"
            >
              18.4 × 12.1 mm
            </span>
          </>
        )}
      </div>

      {/* 部位指示条：仅部位数 ≥ 2 时出现，锚在影像区内部底缘 */}
      {organBar}
    </section>
  );
}
