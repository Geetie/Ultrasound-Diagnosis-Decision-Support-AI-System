import { useEffect, useState } from 'react';
import WorkspaceStopAlert from './WorkspaceStopAlert';
import ResearchMetrics from '@/components/diagnosis/ResearchMetrics';
import type { WorkspaceState } from './states';
import type { ConclusionView } from '@/services/diagnosisAdapter';
import type { ReactNode } from 'react';
import { stripMarkdownEmphasis } from '@/utils/text';

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
 *
 * 【2026-10-28 两处修复】
 * · **P1-7 分级显示**：适配层 `extractTier()` 早就抽出了 `view.tier`，但**没有任何组件读它**
 *   —— 医生看到的一句话结论里混着「分级: TR2」，却看不到一个干净的分级标签。
 *   现在在一句话结论下方加分级徽标（有值才渲染，无值整块不出现）。
 * · **P1-6 展开依据**：原先是不可点的 `<p>`（`data-action="expand"` 但无 onClick）。
 *   现在改成真 `<button>` + 展开区，**只渲染后端真给了的东西**（`evidenceIds`），
 *   没有依据条目时按钮禁用并说明原因，不挂假按钮、不编造依据。
 */
function ConclusionBlock({
  expandText,
  view,
}: {
  expandText: string;
  view?: ConclusionView | null;
}) {
  const live = view ?? null;
  const [expanded, setExpanded] = useState(false);
  // 有真机数据时用数据，没有才回落到画布文案
  // A.58：结论正文是 LLM 自由文本，会带裸露的 `**`（真机截图实证）。适配层契约要求
  // **原样透传**（便于与后端对账），所以归一化只在这一刻做（显示层，不改数据）。
  const headline = stripMarkdownEmphasis(
    live?.headline ?? '考虑良性结节（TI-RADS 3），建议 6 个月后复查',
    { headings: true },
  );
  const attention = live ? live.attention : { reason: 'low_confidence' as const, text: 'AI 建议人工复核 · 置信度低于典型阈值' };
  const risk = live ? live.malignancyRisk : { level: '低', range: '约 2%–5%' };
  const advice = stripMarkdownEmphasis(
    live?.advice ? `建议：${live.advice}` : '建议：6 个月后复查超声，暂不需穿刺',
    { headings: true },
  );
  // P1-7：分级徽标。`tier` 由适配层从正文正则抽出，无值时不渲染整块。
  const tier = live?.tier;
  // P1-6：依据条目。仅 `evidenceIds` 里真实存在的条目；空数组 = 本次结论没有引用
  // 任何知识条目（后端 `evidence.knowledge_ids` 为 None 或正文无「证据:」段）。
  // 依据条目 + 鉴别诊断 + 跨模态对照（03-4/03-5/03-6 接线）。三段**独立门控**：
  // 后端真给了才有，空数组/缺字段整段不渲染 —— 不编造。
  const evidenceIds = live?.evidenceIds ?? [];
  const ddx = live?.differentialDiagnoses ?? [];
  const crossHints = live?.crossModalityHints ?? [];
  const canExpand = evidenceIds.length > 0 || ddx.length > 0 || crossHints.length > 0;

  return (
    <>
      {/* 一句话结论 —— 结论区一级只给三样 */}
      <p className="ws-conclusion__headline" data-node="2:19">
        {headline}
      </p>

      {/* P1-7：分级徽标 —— 后端抽得出分级时才出现 */}
      {tier && (
        <span className="ws-tier-chip" data-region="conclusion-tier">
          分级 {tier}
        </span>
      )}

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

      {/* 展开入口-唯一（P1-6 修复：真按钮 + 真展开区）
          契约要求「展开入口只能有一个，拆成三个并列按钮即违反 P2」——
          所以这里仍是**一个**按钮，展开后在一个区块内分段列出
          依据 → 鉴别诊断 → 跨模态对照，各段按后端数据独立门控。
          2026-10-30 更正：原注释「其余两段后端无字段」已过期 ——
          differential_diagnoses / cross_modality_hints 均已随结果接口返回。 */}
      <button
        type="button"
        className="ws-conclusion__expand"
        data-action="expand"
        aria-expanded={expanded}
        disabled={!canExpand}
        title={
          canExpand
            ? `展开${[
                evidenceIds.length > 0 ? `知识条目 ${evidenceIds.length} 条` : null,
                ddx.length > 0 ? `鉴别诊断 ${ddx.length} 条` : null,
                crossHints.length > 0 ? `跨模态对照 ${crossHints.length} 条` : null,
              ]
                .filter(Boolean)
                .join('、')}`
            : '本次结论没有可展开的依据 / 鉴别诊断 / 跨模态对照'
        }
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded ? '收起依据' : expandText}
      </button>

      {expanded && canExpand && (
        <div className="ws-conclusion__evidence" data-region="conclusion-evidence">
          {evidenceIds.length > 0 && (
            <>
              <span className="ws-conclusion__evidence-title">
                本次结论引用的知识条目（{evidenceIds.length} 条）
              </span>
              <ul className="ws-conclusion__evidence-list">
                {evidenceIds.map((id) => (
                  <li key={id} className="ws-conclusion__evidence-item">
                    {id}
                  </li>
                ))}
              </ul>
              <span className="ws-conclusion__evidence-note">
                条目 ID 可回知识库原文核对；界面暂未提供点击跳转。
              </span>
            </>
          )}

          {ddx.length > 0 && (
            <div className="ws-conclusion__ddx" data-region="conclusion-differential">
              <span className="ws-conclusion__evidence-title">
                鉴别诊断（按可能性排序，{ddx.length} 条）
              </span>
              <ul className="ws-conclusion__ddx-list">
                {ddx.map((d) => (
                  <li key={`${d.rank}-${d.disease_name}`} className="ws-conclusion__ddx-row">
                    <span className="ws-conclusion__ddx-rank">{d.rank}.</span>
                    <span className="ws-conclusion__ddx-name">{d.disease_name}</span>
                    <span className="ws-conclusion__ddx-conf">
                      {typeof d.confidence === 'number'
                        ? `${Math.round(d.confidence * 100)}%`
                        : '—'}
                    </span>
                  </li>
                ))}
              </ul>
              <span className="ws-conclusion__evidence-note">
                可能性为后端解析值；未给出的显示「—」，前端不反推数值。
              </span>
            </div>
          )}

          {crossHints.length > 0 && (
            <div className="ws-conclusion__cross" data-region="conclusion-cross-modality">
              <span className="ws-conclusion__evidence-title">
                跨模态对照（教科书级参考，不构成本次诊断依据）
              </span>
              <ul className="ws-conclusion__cross-list">
                {crossHints.map((h, i) => (
                  <li key={`${h.modality}-${i}`} className="ws-conclusion__cross-row">
                    <span className="ws-conclusion__cross-modality">{h.modality}</span>
                    <span className="ws-conclusion__cross-finding">
                      {h.clinical_value ? `${h.finding} —— ${h.clinical_value}` : h.finding}
                    </span>
                    {h.source_reference && (
                      <span className="ws-conclusion__cross-source">
                        出处：{h.source_reference}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* 06-5 科研量化指标：复用既有 ResearchMetrics（此前挂在无路由的 DiagnosisPage）。
          默认折叠 + 自带「科研用途，非临床决策依据」标注；后端合法缺席即整块不渲染；
          设计预览模式（view=null）同样不渲染。 */}
      {live?.researchMetrics && <ResearchMetrics metrics={live.researchMetrics} />}
    </>
  );
}

export default function WorkspaceConclusionBody({
  state,
  view,
  onSignNext,
  uploadSlot,
  multiOrganSlot,
  organCount,
  progressChars = 0,
  generationPreview = null,
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
  /**
   * 多部位结果插槽（T-GB-AGT-01）。仅在**有多器官数据**时由路由传入 → 默认沉默。
   * 单部位会话不传，DOM 里就不存在这块。
   */
  multiOrganSlot?: ReactNode;
  /** 多部位会话的部位数（0/省略 = 未知或单部位）。05 态文案按真实计数，不写死 3。 */
  organCount?: number;
  /** 生成进度（A.43）：processing 期间已生成字符数，0 = 无进度数据。 */
  progressChars?: number;
  /** 安全预览（A.46）：已过段落级 SG 的文本尾部；null = 无预览或 SG 拒绝。 */
  generationPreview?: string | null;
}) {
  // 安全网关显式要求转人工时，无论当前是哪个判读态都要显示 —— 契约 §4「不可削减」
  const blocked = view?.safetyBlocked;
  return (
    <>
      {blocked && (
        <WorkspaceStopAlert level="attention" message={blocked.message} code={blocked.errorCode} />
      )}
      <ConclusionSwitch state={state} view={view} onSignNext={onSignNext} uploadSlot={uploadSlot} organCount={organCount} progressChars={progressChars} generationPreview={generationPreview} />
      {/* 多部位结果的交代（每部位跑成没跑成 + 一致性提示），排在结论之后。
          它是补充信息，不抢结论区一级的三样（结论 / 恶性风险 / 建议动作）。 */}
      {multiOrganSlot}
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
function AnalyzingBody({
  progressChars,
  generationPreview,
}: {
  progressChars: number;
  generationPreview?: string | null;
}) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(timer);
  }, []);
  return (
    <>
      <p className="ws-conclusion__title ws-conclusion__title--progress">分析中</p>
      <p className="ws-conclusion__subtitle">
        已用时 {elapsed} 秒
        {/* 已生成字数（A.43 流式进度）：后端 SSE 写 Redis → 轮询带回。
            字数增长 = 生成确实在推进；0 = 该判读没有流式数据（只有秒数）。 */}
        {progressChars > 0 ? ` · 已生成 ${progressChars} 字` : ''} · 不用等它跑完，可继续扫查或先写报告
      </p>
      {/* 安全预览（A.46）：**已通过段落级 SG** 的生成文本（AWS 同步护栏模式：
          段落缓冲→检查→通过才显示；SG 拒绝 → null 不渲染，终态由全文 SG 兜底）。
          滚动区展示尾部，医生边等边看判读方向 —— 临床红线：未过 SG 的原始
          输出永远不上屏（NeMo/AWS 双标杆调研结论，A.46.1）。 */}
      {generationPreview && (
        <pre
          className="ws-conclusion__preview"
          style={{
            margin: '8px 0 0',
            maxHeight: 72,
            overflowY: 'auto',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            fontSize: 11,
            lineHeight: 1.5,
            color: 'var(--color-text-secondary)',
            textAlign: 'left',
          }}
        >
          {/* A.58：预览是**流式 LLM 原文**，同样会带裸露的 `**`；显示层去标记 */}
          {stripMarkdownEmphasis(generationPreview, { headings: true })}
        </pre>
      )}
    </>
  );
}

function ConclusionSwitch({
  state,
  view,
  onSignNext,
  uploadSlot,
  organCount = 0,
  progressChars,
  generationPreview,
}: {
  state: WorkspaceState;
  view?: ConclusionView | null;
  onSignNext?: () => void;
  uploadSlot?: ReactNode;
  /** 多部位会话的部位数（0 = 未知/单部位）。用于把"3 个部位"从写死改成真实计数。 */
  organCount?: number;
  progressChars?: number;
  generationPreview?: string | null;
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
      return (
        <AnalyzingBody progressChars={progressChars ?? 0} generationPreview={generationPreview} />
      );

    case 'multi-organ':
      return (
        <>
          <p className="ws-conclusion__title">多部位判读中</p>
          <p className="ws-conclusion__subtitle">
            {organCount > 0
              ? `本检查含 ${organCount} 个部位 · 结论按部位分别给出，逐部位状态见下方`
              : '结论按部位分别给出，部位指示条已出现在影像下方'}
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
          {/* 签发后唯一的主操作是「下一个病人」；点的同时清屏
           *
           * 2026-10-28 修（P0-3）：原先 `onClick={onSignNext}` 而 `onSignNext`
           * 由 `routes.tsx` **从未传入** → 按钮 `onClick={undefined}`，点了完全没反应
           * 且没有任何视觉提示（按钮不禁用、不变灰）。现在：
           *   · callback 缺失 → **禁用 + title 说明原因**，不再伪装成可点；
           *   · 正常路径（routes 传入清理函数）→ 可用。
           * 注意：本屏的「已推送 PACS」文案是**硬编码**，而 PACS 默认打 local_fake
           * 适配器（settings.pacs_adapter='local_fake'）—— 这条文案不准确，
           * 已登记到修复报告，需产品决策后改（不在本轮擅改文案）。 */}
          <div className="ws-sign-row" data-node="2:396">
            <span className="ws-sign-row__status">
              报告状态：已签发（HSM 数字签名）· 已推送 PACS
            </span>
            <button
              type="button"
              className="ws-primary-button"
              onClick={onSignNext}
              disabled={!onSignNext}
              title={onSignNext ? '回到工作列表并清屏' : '返回工作列表的通道未接线'}
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
