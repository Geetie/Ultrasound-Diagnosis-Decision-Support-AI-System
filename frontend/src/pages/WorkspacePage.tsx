import WorkspaceImageRegion from '@/components/workspace/WorkspaceImageRegion';
import WorkspaceConclusion from '@/components/workspace/WorkspaceConclusion';
import WorkspaceConclusionBody from '@/components/workspace/WorkspaceConclusionBody';
import {
  hasOrganBar,
  hasReportPanel,
  isCenteredState,
  isCompactState,
  overlayOf,
  SCREEN_OF,
  type WorkspaceState,
} from '@/components/workspace/states';
import WorkspaceActionBar from '@/components/workspace/WorkspaceActionBar';
import WorkspaceOrganBar from '@/components/workspace/WorkspaceOrganBar';
import WorkspaceReportPanel from '@/components/workspace/WorkspaceReportPanel';
import WorkspaceConclusionMask from '@/components/workspace/WorkspaceConclusionMask';
import WorkspaceOrganTagRow from '@/components/workspace/WorkspaceOrganTagRow';
import type { ConclusionView } from '@/services/diagnosisAdapter';
import type { ReactNode } from 'react';

/**
 * 检查工作区 —— 路由 `/`
 *
 * 一个组件 + **11 个视图状态**，不是 11 个页面（17 帧里只有 3 个真页面，
 * 见 design_data_for_agents/_页面分类.md）。
 *
 * 骨架（合计 900，硬规则不得改动）：
 *   常驻条 40 / 影像区 600（恒定） / 结论区 260（→200） / 确认动作区 60（仅有待确认项时）
 *   无动作区 40+600+260 = 900；有动作区 40+600+200+60 = 900。两种路径都恰满。
 *
 * 四条硬规则（AGENT_TASKS §六）：
 *   1. 默认沉默 —— 不满足条件**不渲染**（不是 display:none）
 *   2. 影像尺寸恒定 —— 永远 600，被压缩的从来不是它
 *   3. 语义色两级 —— 只有 semantic-stop（停，挡签发）与 attention（看一眼，静态）
 *   4. 主题设备级一次设置；--color-image-bg 不参与切换
 *
 * 全稿按契约**故意不渲染**的元素（补上任何一个都算返工）：
 *   · 同意状态、连接状态 —— §1：仅非 granted / 仅异常时渲染
 *   · 确认动作区          —— §1：仅存在待确认项时渲染
 *   · 部位指示条 / 标签排 —— §1：仅部位数 ≥ 2 时渲染
 *   · 报告状态行          —— §1：仅非 draft 时渲染（12_签发后 才出现）
 *   · 「看一眼」标记行 / 恶性风险行 —— 11 屏 AI 不可用时 visible:false
 *   · 置信度 / 一致性 / Self-RAG 明细 —— 二级折叠且后端字段未接通（16-报告 N-01）
 *   · 区域编号 1~4        —— 画布上的评阅用标注（annotation_note），非界面
 */
export default function WorkspacePage({
  state = 'scanning',
  view,
  onConfirm,
  onReviewDetail,
  onCollapseReport,
  onStructuredProofread,
  onSign,
  onSignNext,
  onRevealConclusion,
  dicomImageId,
  dicomImageIds,
  dicomFrameOwners,
  /** 测量注释存档 key（A.40：后端 image_id）。undefined = 不持久化。 */
  annotationArchiveKey,
  uploadSlot,
  navItems,
  /** 脱敏身份行（张** · 女 · 52 岁）。undefined = 数据不可用 → 显示『患者信息不可用』。 */
  identity,
  /** 临床信息面板插槽（常驻条正下方；判读发起后锁定，A.36 ⑤）。 */
  clinicalSlot,
  /** 生成进度（A.43）：processing 期间已生成字符数，0 = 无进度数据。 */
  progressChars,
  /** 安全预览（A.46）：已过段落级 SG 的文本尾部；null = 无预览或 SG 拒绝。 */
  generationPreview,
  declaredOrgan,
  sessionStartedAt,
  imageLoadIssue,
  multiOrganSlot,
  organCount,
  reportText,
  reportStatusNote,
}: {
  state?: WorkspaceState;
  /**
   * 真机数据视图模型（services/diagnosisAdapter 从 API 响应映射）。
   * 省略 = 设计预览模式；传入 = 真机模式，结论区按 P1 逐字段渲染。
   */
  view?: ConclusionView | null;
  onConfirm?: () => void;
  onReviewDetail?: () => void;
  onCollapseReport?: () => void;
  onStructuredProofread?: () => void;
  onSign?: () => void;
  onSignNext?: () => void;
  onRevealConclusion?: () => void;
  /** 真实 DICOM 的 URL。给了就替换影像区的渐变占位（2:4 impl_note）。 */
  dicomImageId?: string;
  /** 多帧栈（A.38 分期①）：本会话已装载的 imageIds（顺序=上传顺序）。 */
  dicomImageIds?: string[];
  /** A.52：与 dicomImageIds 逐位对齐的后端 image_id（帧级分割门控用）。 */
  dicomFrameOwners?: string[];
  /** 测量注释存档 key（A.40：后端 image_id）。undefined = 不持久化。 */
  annotationArchiveKey?: string;
  /** 影像装载入口插槽（仅 02 扫查态渲染，见 WorkspaceConclusionBody）。 */
  uploadSlot?: ReactNode;
  /**
   * 常驻条右侧的导航出口。
   *
   * 工作区是全站唯一不套 AppLayout 的页面（通栏 900 骨架放不下侧栏），
   * 2026-10-05 真浏览器走查实测 `btn=[]` —— 医生登录后就困在这里，一个页面都去不了。
   * 放在常驻条内而不是加侧栏：条高 40 不变，900 骨架不受影响。
   * 不传就不渲染（默认沉默）。
   */
  navItems?: { key: string; label: string; onClick: () => void }[];
  identity?: string;
  clinicalSlot?: ReactNode;
  progressChars?: number;
  generationPreview?: string | null;
  /** 影像区角标的**声明部位**中文名（会话 `target_organs[0]`）；不传 = 部位未声明。
   *  参数角标不得写死器官 —— 写死「甲状腺」会让心脏会话显示错误部位（A.57）。 */
  declaredOrgan?: string | null;
  /** 影像区时间角标：会话起始时间（ISO）。影像采集时间未解析，故用它并如实标注。 */
  sessionStartedAt?: string | null;
  /** 影像字节/列表取回失败的可读原因（A.58 零静默：失败必须说出来，不许沉默占位）。 */
  imageLoadIssue?: string | null;
  /**
   * 多部位结果插槽（T-GB-AGT-01）：会话部位 ≥ 2 且走了多器官端点时由路由传入。
   * 不传 = 单部位会话，DOM 里不存在这块（默认沉默，契约 §1）。
   */
  multiOrganSlot?: ReactNode;
  /** 多部位会话的部位数（05 态文案按真实计数，不写死 3）。 */
  organCount?: number;
  /**
   * 报告正文（后端 `ReportResponse.rendered`）。传给报告侧面板 → 显示真数据。
   * 不传 = dev 预览（画布示意文字，只在 `?state=report` 后门出现）。
   */
  reportText?: string | null;
  /** 报告状态备注（如「尚未生成报告草稿」），有值时替代面板底部的画布文案。 */
  reportStatusNote?: string | null;
}) {
  const overlay = overlayOf(state);

  return (
    <div className="ws-root" data-screen={SCREEN_OF[state]}>
      <header className="ws-bar" data-region="bar">
        {/* A.36 P0：身份必须数据驱动（写死「张**」= 患者身份错显）。由路由按
            session 传入脱敏人口学；缺失时如实显示不可用，不猜、不占位假人。 */}
        <span className="ws-bar__identity">{identity ?? '患者信息不可用'}</span>
        {navItems && navItems.length > 0 && (
          <nav className="ws-bar__nav" aria-label="页面导航">
            {navItems.map((item) => (
              <button
                key={item.key}
                type="button"
                className="ws-bar__nav-item"
                onClick={item.onClick}
              >
                {item.label}
              </button>
            ))}
          </nav>
        )}
      </header>
      {clinicalSlot}

      {/* 影像区在 11 个状态间是同一份拷贝（04 的 2:15、10 的 2:299 等都标注
          shared_copy_of: 02_扫查态/2:4），逐像素相同，故直接复用，不重复实现。
          部位指示条锚在它内部底缘，高度不受影响 —— 影像区恒定 600。 */}
      <WorkspaceImageRegion
        organBar={hasOrganBar(state) ? <WorkspaceOrganBar onReviewClick={onReviewDetail} /> : undefined}
        dicomImageId={dicomImageId}
        dicomImageIds={dicomImageIds}
        dicomFrameOwners={dicomFrameOwners}
        annotationArchiveKey={annotationArchiveKey}
        segmentation={view?.segmentation}
        declaredOrgan={declaredOrgan}
        sessionStartedAt={sessionStartedAt}
        imageLoadIssue={imageLoadIssue}
      />

      <WorkspaceConclusion
        centered={isCenteredState(state)}
        compact={isCompactState(state)}
      >
        <WorkspaceConclusionBody
          state={state}
          view={view}
          onSignNext={onSignNext}
          uploadSlot={uploadSlot}
          multiOrganSlot={multiOrganSlot}
          organCount={organCount}
          progressChars={progressChars}
          generationPreview={generationPreview}
        />
      </WorkspaceConclusion>

      {/* 仅存在待确认项时渲染；其余状态这个组件根本不出现在 DOM 里 */}
      {isCompactState(state) && <WorkspaceActionBar onConfirm={onConfirm} />}

      {/* 报告侧面板：滑出面板而非页面，可收起、可钉住。
          reportText/statusNote 有值时显示**后端真实正文**（生产入口必须传）；
          都没有才是 dev 画布预览（见 WorkspaceReportPanel 的数据口径说明）。 */}
      {hasReportPanel(state) && (
        <WorkspaceReportPanel
          signed={state === 'signed'}
          reportText={reportText}
          statusNote={reportStatusNote}
          onCollapse={onCollapseReport}
          onStructuredProofread={onStructuredProofread}
          onSign={onSign}
        />
      )}

      {/* 覆盖层与结论区内容互斥：出现覆盖层时结论卡不渲染（P1） */}
      {overlay === 'mask' && <WorkspaceConclusionMask onReveal={onRevealConclusion} />}
      {overlay === 'organ-tags' && <WorkspaceOrganTagRow />}
    </div>
  );
}
