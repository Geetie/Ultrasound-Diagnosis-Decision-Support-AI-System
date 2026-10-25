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
 * 现在支持传 `reportText`（后端 `rendered` 正文）：**传了就显示真数据**，
 * 不传才回落到画布示意文案（仅 dev 预览用）。生产入口必须传 `reportText`。
 */
export default function WorkspaceReportPanel({
  signed = false,
  reportText,
  statusNote,
  onCollapse,
  onStructuredProofread,
  onSign,
}: {
  /** signed=true 对应 12_签发后；false 对应 07 的 draft 态。 */
  signed?: boolean;
  /**
   * 后端报告正文（`ReportResponse.rendered`）。**有值 = 生产模式**，逐字显示；
   * 无值 = dev 画布预览（示意文字）。
   */
  reportText?: string | null;
  /** 真实状态备注（如「尚未生成报告草稿」）。有值时替代画布那句"系统预生成"。 */
  statusNote?: string | null;
  onCollapse?: () => void;
  onStructuredProofread?: () => void;
  onSign?: () => void;
}) {
  // 生产模式：有真实正文就只显示真实正文，绝不掺画布示意文字
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
        <>
          {/* 所见 —— 草稿由 AI 预填（零转录），医生只改要改的句子 */}
          <p className="ws-report-panel__text">
            所见：甲状腺右叶中极见 18.4 × 12.1 mm 低回声结节，形态规则，边界清晰，纵横比小于 1；
            内部回声均匀，未见微钙化，后方回声无明显衰减。CDFI：结节内部未见明显血流信号。
            左叶及峡部未见异常。双侧颈部未见肿大淋巴结。
          </p>

          <p className="ws-report-panel__text">
            印象：甲状腺右叶结节，TI-RADS 3 类，考虑良性可能大。建议 6 个月后复查超声；
            若结节增大或出现可疑征象，建议细针穿刺活检。
          </p>
        </>
      )}

      {/* 弹性占位：把页脚推到底部 */}
      <div className="ws-report-panel__spacer" />

      <p className="ws-report-panel__note">
        {statusNote ?? '草稿由系统预生成，你只需改要改的句子'}
      </p>

      {/* 面板脚 */}
      <div className="ws-report-panel__foot">
        <button
          type="button"
          className="ws-report-panel__action"
          onClick={onStructuredProofread}
        >
          结构化校对
        </button>
        <button
          type="button"
          className="ws-primary-button"
          onClick={onSign}
          disabled={signed}
        >
          {signed ? '已签发' : '签发'}
        </button>
      </div>
    </aside>
  );
}
