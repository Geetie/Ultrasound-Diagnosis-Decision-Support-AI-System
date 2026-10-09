import { useEffect, useState } from 'react';
import WorkspaceStopAlert from './WorkspaceStopAlert';
import type { WorkspaceState } from './states';
import type { ConclusionView } from '@/services/diagnosisAdapter';
import type { ReactNode } from 'react';

/**
 * 结论区内容 —— 按视图状态切换
 *
 * 一个组件 + 一套视图状态，不是 11 个页面（17帧里只有 3 个真页面，
 * 见 design_data_for_agents/_页面分类.md）。状态定义与渲染判定在 ./states.ts。
 *
 * 契约要点：
 *   · 结论区一级只给三样：一句话结论 + 恶性风险 + 建议动作（14-文档 §3.1）
 *   · 「看一眼」档＝中性青灰描边 + 图标 + 文字；静态、不发声、不闪烁
 *   · 原因必须人话化，且实现时须带兜底分支 —— 后端 review_reason
 *     存在集合外自由串（16-报告 M-02）
 *   · 展开入口只能有一个，拆成三个并列按钮即违反 P2「一次决策」
 *   · masked / organ-tags 两态**不渲染任何结论卡内容**（画布上是覆盖层，
 *     但按 P1 应理解为「未显式展开前不渲染」），覆盖层由页面单独渲染
 */

/** 「看一眼」档图标。用 currentColor 跟主题，不烘焙色值。 */
function AttentionIcon() {
  return (
    <svg
      className="ws-attention-chip__icon"
      viewBox="0 0 16 16"
      role="img"
      aria-label="需留意"
    >
      {/* Ellipse 13x13 — 圆环 */}
      <circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
      {/* Vector — 「i」的竖笔 */}
      <path d="M8 7.2V11.2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      {/* Ellipse 1.8x1.8 — 「i」的点 */}
      <circle cx="8" cy="4.9" r="0.9" fill="currentColor" />
    </svg>
  );
}

/**
 * 判读态结论的公共部分 —— 04 / 07 / 10 / 12 / 13 / 14 的结论区基底完全一致，
 * 只有「展开入口」文案在12（已签发）时不同。逐字相同，故抽成一段。
 *
 * 【两种渲染模式】
 * · `view` 为 null → **设计预览模式**：渲染画布上的示意文案（无后端数据时用）
 * · `view` 有值   → **真机数据模式**：逐字段按 P1 渲染 ——
 *   view 里该字段为 undefined 时**整行不渲染**，不是显示占位。
 *   画布上「低 · 约 2%–5%」是示意文案，后端并没有恶性概率字段，
 *   所以真机模式下这一行必然消失 —— 那是正确行为，不是缺件。
 */
function ConclusionBlock({
  expandText,
  view,
}: {
  expandText: string;
  view?: ConclusionView | null;
}) {
  const live = view ?? null;
  // 有真机数据时用数据，没有才回落到画布文案
  const headline = live?.headline ?? '考虑良性结节（TI-RADS 3），建议 6 个月后复查';
  const attention = live ? live.attention : { reason: 'low_confidence' as const, text: 'AI 建议人工复核 · 置信度低于典型阈值' };
  const risk = live ? live.malignancyRisk : { level: '低', range: '约 2%–5%' };
  const advice = live?.advice
    ? `建议：${live.advice}`
    : '建议：6 个月后复查超声，暂不需穿刺';

  return (
    <>
      {/* 一句话结论 —— 结论区一级只给三样 */}
      <p className="ws-conclusion__headline" data-node="2:19">
        {headline}
      </p>

      {/* 看一眼档-标记行 —— P1：view.attention 不存在即整行不渲染 */}
      {attention && (
        <span className="ws-attention-chip" data-node="2:20" data-reason={attention.reason}>
          <AttentionIcon />
          <span className="ws-attention-chip__text">{attention.text}</span>
        </span>
      )}

      {/* 恶性风险行 —— P1：后端没给区间口径即整行不渲染，不编造数字 */}
      {risk && (
        <div className="ws-risk-row" data-node="2:26">
          <span className="ws-risk-row__label">恶性风险</span>
          <span className="ws-risk-row__value">
            {risk.level} · {risk.range}
          </span>
        </div>
      )}

      {/* 建议动作 */}
      <p className="ws-conclusion__advice" data-node="2:29">
        {advice}
      </p>

      {/* 展开入口-唯一
          数据里 type 是 TEXT、actions 标注“就地展开三级：依据 → 鉴别 → AI 过程”。
          三级折叠内容（证据溯源 / 鉴别诊断 / 既往对比，契约 §1 列为「折叠」）
          未包含在任何一屏的导出数据中，故按数据还原为文本、不挂假按钮；
          待折叠区数据落地后由本节点升级为 button 并接管 actions。 */}
      <p className="ws-conclusion__expand" data-action="expand">
        {expandText}
      </p>
    </>
  );
}

export default function WorkspaceConclusionBody({
  state,
  view,
  onSignNext,
  uploadSlot,
}: {
  state: WorkspaceState;
  /**
   * 真机数据视图模型（由 services/diagnosisAdapter 从 API 响应映射而来）。
   * 省略 = 设计预览模式（用画布示意文案）；传入 = 真机模式（按 P1 逐字段渲染）。
   */
  view?: ConclusionView | null;
  /** 12 的「下一个病人」：回 01_工作列表并清屏（上一位患者零残留）。 */
  onSignNext?: () => void;
  /** 影像装载入口插槽（WorkspaceImageUpload）。仅 02 扫查态渲染 —— 见组件内说明。 */
  uploadSlot?: ReactNode;
}) {
  // 安全网关显式要求转人工时，无论当前是哪个判读态都要显示 —— 契约 §4「不可削减」
  const blocked = view?.safetyBlocked;
  return (
    <>
      {blocked && (
        <WorkspaceStopAlert level="attention" message={blocked.message} code={blocked.errorCode} />
      )}
      <ConclusionSwitch state={state} view={view} onSignNext={onSignNext} uploadSlot={uploadSlot} />
    </>
  );
}

/**
 * 「分析中」态（A.38）：加已用时实时秒数。
 *
 * 为什么偏离画布「进度只用两个词」：判读实测 4~33s，医生盯着静止的
 * 「分析中」分不清系统在算还是死了 —— 秒数走动本身就是"活着"的信号，
 * 这是实测事实而不是编造的预估（百分比/预计剩余时间是编造，仍然不给，
 * 该口径保留）。计时器随本组件挂载（=判读发起）起走，出结论即卸载。
 */
function AnalyzingBody() {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(timer);
  }, []);
  return (
    <>
      <p className="ws-conclusion__title ws-conclusion__title--progress">分析中</p>
      <p className="ws-conclusion__subtitle">
        已用时 {elapsed} 秒 · 不用等它跑完，可继续扫查或先写报告
      </p>
    </>
  );
}

function ConclusionSwitch({
  state,
  view,
  onSignNext,
  uploadSlot,
}: {
  state: WorkspaceState;
  view?: ConclusionView | null;
  onSignNext?: () => void;
  uploadSlot?: ReactNode;
}) {
  switch (state) {
    case 'scanning':
      return (
        <>
          {/* 文案偏离设计稿一处：画布原文「等待冻结取图 / 冻结当前帧即自动发起判读」
              预设了设备端冻结键，浏览器部署里没有这个键，照抄会让医生找不存在的东西。*/}
          <p className="ws-conclusion__title">等待装载影像</p>
          <p className="ws-conclusion__subtitle">
            装载当前帧即自动发起判读，不需要点击任何按钮
          </p>
          {uploadSlot}
        </>
      );

    case 'progress':
      return <AnalyzingBody />;

    case 'multi-organ':
      return (
        <>
          <p className="ws-conclusion__title">等待冻结取图</p>
          <p className="ws-conclusion__subtitle">
            本检查含 3 个部位 · 结论按部位分别给出，部位指示条已出现在影像下方
          </p>
        </>
      );

    case 'conclusion':
    case 'report':
    case 'actionbar':
      return <ConclusionBlock expandText="展开依据 / 鉴别诊断 / 诊断过程" view={view} />;

    case 'ai-unavailable':
      return (
        <>
          {/* 用「看一眼」档而不是红色 —— 不伪装成正常结论，也不当故障吓人 */}
          <p className="ws-conclusion__headline ws-conclusion__headline--attention">
            AI 辅助暂不可用
          </p>
          {/* AI 不可用时没有复核理由、没有恶性风险值 —— 对应节点 visible:false，此处不渲染 */}
          {/* 保住手工路径 —— AI 不可用不等于工作流中断 */}
          <p className="ws-conclusion__advice">
            请完全依赖人工判断。你仍可继续扫查、手工书写报告并签发。
          </p>
          <p className="ws-conclusion__expand">
            连接恢复后会自动重试，不需要你操作
          </p>
        </>
      );

    case 'stopped':
      return (
        <>
          <WorkspaceStopAlert />
          {/* 结论仍然可见（降一级字号）—— 挡住的是签发，不是信息 */}
          <p className="ws-conclusion__headline ws-conclusion__headline--degraded">
            考虑良性结节（TI-RADS 3），建议 6 个月后复查
          </p>
          <div className="ws-sign-row" data-node="2:394">
            <span className="ws-sign-row__note">需先处理禁忌命中</span>
            <button type="button" className="ws-primary-button" disabled>
              签发（已禁用）
            </button>
          </div>
        </>
      );

    case 'signed':
      return (
        <>
          <ConclusionBlock
            expandText="报告已签发并推送 PACS。若再修改正文，需要重新签发。"
            view={view}
          />
          {/* 签发后唯一的主操作是「下一个病人」；点的同时清屏 */}
          <div className="ws-sign-row" data-node="2:396">
            <span className="ws-sign-row__status">
              报告状态：已签发（HSM 数字签名）· 已推送 PACS
            </span>
            <button
              type="button"
              className="ws-primary-button"
              onClick={onSignNext}
            >
              下一个病人
            </button>
          </div>
        </>
      );

    case 'masked':
    case 'organ-tags':
      // 覆盖层独占结论区，内容不渲染（见组件顶部说明）
      return null;
  }
}
