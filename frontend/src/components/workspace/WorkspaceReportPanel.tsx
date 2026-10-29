/**
 * 报告侧面板 —— 07 的 2:182 / 12 同构（signed 变体）
 *
 * 480×860，绝对定位 x=960 / y=40 —— 贴右边缘与底边，是**滑出面板而非页面**。
 * 设计理由：报告是检查的**收尾动作**，不是目的地。
 *
 * ⚠️ 这是全稿唯一允许阴影的地方（面板带 DROP_SHADOW，向左投射）。
 * 02 屏的 shadow_nodes = 0，其余区域一律不加阴影。
 *
 * 契约 §1：**报告状态行仅在非 draft 时渲染**。
 *   · 07（draft）→ 不渲染状态行，面板标题「报告草稿」，签发按钮可用
 *   · 12（signed）→ 渲染状态行（在结论区），面板标题「已签发」，签发按钮**禁用**
 * 签发是法律责任节点；存在禁忌命中时它也会被禁用（见 06）。
 *
 * ⚠️ 数据口径（T7）：本组件原先只渲染设计画布上的**示意文字**（写死的
 * 「18.4 × 12.1 mm 低回声结节…」），因此它只能在 dev 的 `?state=` 后门出现 ——
 * 生产放开等于在真实患者界面上显示伪造的超声所见。
 *
 * **2026-10-28 修（P1-8）**：删掉整段画布示意文字。原实现是"没传 reportText 就回落
 * 到假所见"，而生产入口在「报告还没生成」时**恰恰就是没正文**（这是极常见的状态）
 * —— 于是医生会在真实患者界面上看到「18.4 × 12.1 mm 低回声结节」。现在：
 *   · 有正文 → 显示正文；
 *   · 无正文 → 显示**空态提示**（"尚无渲染正文"，文案由 `statusNote` 或默认串给），
 *     一个字的临床内容都不编。
 * 代价：dev `?state=report` 后门不再有画布预览效果 —— 这是刻意的（那条后门的价值
 * 远低于"生产上永不可能显示假所见"）。
 */
export default function WorkspaceReportPanel({
  signed = false,
  reportText,
  statusNote,
  onCollapse,
  onStructuredProofread,
  onSign,
  signing = false,
}: {
  /** signed=true 对应 12_签发后；false 对应 07 的 draft 态。 */
  signed?: boolean;
  /**
   * 后端报告正文（`ReportResponse.rendered`）。**有值 = 生产模式**，逐字显示；
   * 无值 = 无正文空态（**不再回落画布示意文字**）。
   */
  reportText?: string | null;
  /** 真实状态备注（如「尚未生成报告草稿」）。无正文时作为空态说明。 */
  statusNote?: string | null;
  onCollapse?: () => void;
  onStructuredProofread?: () => void;
  onSign?: () => void;
  /** 签发请求进行中（按钮禁用 + 文案切换），防止连点产生重复签名请求。 */
  signing?: boolean;
}) {
  // 生产模式：有真实正文就只显示真实正文
  const live = typeof reportText === 'string' && reportText.trim() !== '';
  return (
    <aside className="ws-report-panel" data-region="report-panel" aria-label="报告侧面板">
      {/* 2:183/184 面板头 */}
      <div className="ws-report-panel__head">
        <span className="ws-report-panel__title">{signed ? '已签发' : '报告草稿'}</span>
        <button type="button" className="ws-report-panel__action" onClick={onCollapse}>
          收起
        </button>
      </div>

      {live ? (
        <pre className="ws-report-panel__text ws-report-panel__text--live" data-region="report-body">
          {reportText}
        </pre>
      ) : (
        /* 空态：**只说没有正文**，不渲染任何临床内容（不编造超声所见）。
           有 statusNote 时用它（它承载真实原因，如 404 / 缺段），否则用默认串。
           ⚠️ 面板底部 note 不再重复同一句（下面按 live 分支取不同文案）。 */
        <p
          className="ws-report-panel__text ws-report-panel__text--empty"
          data-region="report-empty"
        >
          {statusNote ?? '尚无渲染正文'}
        </p>
      )}

      {/* 弹性占位：把页脚推到底部 */}
      <div className="ws-report-panel__spacer" />

      <p className="ws-report-panel__note">
        {live ? '正文来自后端渲染，签发即对这段文字负责' : '请到「报告」页生成草稿'}
      </p>

      {/* 面板脚 —— 两个按钮都必须真的能点（2026-10-28 修）：
          原实现 onStructuredProofread / onSign 由路由层从未传入 → onClick={undefined}，
          点了完全没反应，且**没有任何视觉提示**（按钮不禁用、不变灰）。
          现在：回调缺失时**禁用并说明原因**，宁可看得见"不可用"也不要假活的按钮。 */}
      <div className="ws-report-panel__foot">
        <button
          type="button"
          className="ws-report-panel__action"
          onClick={onStructuredProofread}
          disabled={!onStructuredProofread}
          title={onStructuredProofread ? '切换到结构化校对视图' : '结构化校对暂不可用'}
        >
          结构化校对
        </button>
        <button
          type="button"
          className="ws-primary-button"
          onClick={onSign}
          disabled={signed || signing || !onSign || !live}
          title={
            signed
              ? '已签发'
              : !live
                ? '尚无渲染正文，不能签发'
                : !onSign
                  ? '签发通道未接线'
                  : '签发报告（RSA 数字签名）'
          }
        >
          {signed ? '已签发' : signing ? '签发中…' : '签发'}
        </button>
      </div>
    </aside>
  );
}
