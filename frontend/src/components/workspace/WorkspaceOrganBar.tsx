/**
 * 部位指示条 —— 05 的 2:202 / 14 的 4:60
 *
 * 契约 §1：**仅 target_organs 数 ≥ 2 时渲染**，单部位时它不存在（见 02_扫查态）。
 * 05 / 14 是这条规则的正面证据，02 是反面证据。
 *
 * 位置：影像区**内部底缘**，绝对 y=616，高 24，616+24=640 = 影像区下边界。
 * （05 记 y=576 是嵌套在影像区坐标系内，14 记 y=616 是根级绝对值，两者等价。）
 * 因此它**不改变影像区高度**——影像区恒定 600。
 *
 * 设计取舍：指示条**只承载二值 + 计数**；「需复核」是条上一个独立可点的警示点，
 * 不压在极细条里当纯文字。
 */
export default function WorkspaceOrganBar({
  onReviewClick,
}: {
  /** 展开该部位的复核明细（安全信息，可点）。 */
  onReviewClick?: () => void;
}) {
  return (
    <div className="ws-organ-bar" data-region="organ-bar">
      <span className="ws-organ-bar__count">已扫 2 / 3 · 未扫 1</span>
      <button
        type="button"
        className="ws-organ-bar__alert"
        onClick={onReviewClick}
      >
        需复核 1
      </button>
    </div>
  );
}
