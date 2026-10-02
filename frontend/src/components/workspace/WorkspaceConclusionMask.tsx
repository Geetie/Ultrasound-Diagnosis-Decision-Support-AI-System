/**
 * 结论区遮蔽态覆盖 —— 13 的 4:28
 *
 * 契约 §1：「患者可见态」**删除独立机制**，改为结论区上的「遮蔽 / 临时显示」开关。
 * 契约 §4：诊室可见性（默认遮蔽）是**不可削减**的一条 —— 保护的是医生：
 * 屏幕上「恶性风险：高」被患者先看到，是医患沟通事故。
 *
 * 画布上这是一层1440×260 的绝对定位覆盖（y=640）压在结论区之上，
 * 但diff_summary 明确：**实现时应理解为「未显式展开前不渲染结论内容」** ——
 * 所以本组件出现时，结论区内部**不渲染任何结论卡内容**（P1 默认沉默）。
 *
 * 主文案**不带任何语义色**：「结论就绪」是中性信息，不是告警。
 */
export default function WorkspaceConclusionMask({
  onReveal,
}: {
  /** 医生显式展开 → 露出结论区（本层消失）。 */
  onReveal?: () => void;
}) {
  return (
    <div
      className="ws-conclusion-overlay ws-conclusion-overlay--center"
      data-region="conclusion-mask"
    >
      <span className="ws-conclusion-mask__title">AI 结论已就绪</span>
      <span className="ws-conclusion-mask__desc">
        默认只对医生可见。诊室内有患者与家属，结论不会自动露出。
      </span>
      <button type="button" className="ws-primary-button" onClick={onReveal}>
        显示结论
      </button>
    </div>
  );
}
