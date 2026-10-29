/**
 * 确认动作区 —— 节点 2:390「确认动作区-仅有待确认项时出现」
 *
 * 契约 §1：**仅存在待确认项时渲染**。无待确认项时它不存在（见 04 / 07 屏）；
 * 存在时结论区 260→200，而**影像区仍是 600 不变**。
 * 10 屏是全文唯一带底部动作条的状态（height_sum: 40+600+200+60 = 900，恰满无溢出）。
 *
 * P1/P3 依据：这是「默认沉默」的典型样例；按 P3 口径它也是净减法 ——
 * 它换来的是「确认」这个动作有信用额度。
 * P2 依据：整条只放一个待选动作，不出现并列的第二选择。
 *
 * ⚠️ 回读文案（2026-10-28 修）：原先是**写死**的「已回读：TI-RADS 3，考虑良性可能大」，
 * 不随真实结论变 —— 真机实测同一路径的结论是 **TR2**，界面却回读 TR3，等于让医生
 * 对一个错误的分级按下"确认"。现改为由调用方传 `readback`（真实结论原文），
 * 传空时退到「本次结论」泛称，**绝不编造分级/病种**。
 */
export default function WorkspaceActionBar({
  onConfirm,
  readback,
  confirming,
  confirmedAt,
  confirmError,
}: {
  /** 确认结论的回调。契约要求三选一等效：点它／说「确认」／踩脚踏。 */
  onConfirm?: () => void;
  /**
   * 系统回读的真实结论（调用方传 `view.headline`，即后端 `fusion_result.content`）。
   * 不传 = 结论正文尚未落地 → 只回读「本次结论」，不编造分级/病种。
   */
  readback?: string | null;
  /** 确认回写进行中（P0-4）：按钮禁用防连点。 */
  confirming?: boolean;
  /** 服务端已记录的确认时间（ISO 串）。有值 = 已确认。 */
  confirmedAt?: string | null;
  /** 确认失败原因。必须显示：静默失败等于医生以为记下了其实没有。 */
  confirmError?: string | null;
}) {
  // 只取结论首句：整段正文含「分级/总分/理由/证据」四段，塞进 60px 高的动作条会溢出
  // （10 屏 height_sum 恰满 900）。首句足够让医生确认"回读的是哪一条"。
  const readbackText = (readback ?? '').trim();
  const firstSentence = readbackText.split(/[。；\n]/)[0]?.trim() ?? '';
  const confirmed = Boolean(confirmedAt);
  return (
    <footer className="ws-actionbar" data-region="actionbar">
      {/* 2:391 回读文案 —— 系统回读**真实**结论，医生给一个确认动作 */}
      <span className="ws-actionbar__readback" data-region="actionbar-readback">
        {firstSentence
          ? `已回读：${firstSentence} —— 请给一个确认动作`
          : '已回读：本次结论 —— 请给一个确认动作'}
      </span>

      {/* 2:392 右侧动作组 */}
      <div className="ws-actionbar__group">
        {/* 确认失败要说出来，否则医生以为系统记下了 */}
        {confirmError ? (
          <span className="ws-actionbar__error" role="alert">
            {confirmError}
          </span>
        ) : null}
        {confirmed && !confirmError ? (
          <span className="ws-actionbar__confirmed" data-region="actionbar-confirmed">
            已确认
          </span>
        ) : null}

        {/* 2:393 等效说明 —— 脚踏不得为必需项，基层可能没有此硬件 */}
        {!confirmed ? (
          <span className="ws-actionbar__equivalent">或说「确认」／踩脚踏（三选一等效）</span>
        ) : null}

        {/* 2:394 确认结论-大按钮 —— 主按钮，色取 color-inverse / color-on-inverse */}
        <button
          type="button"
          className="ws-primary-button"
          onClick={onConfirm}
          disabled={confirming || confirmed || !onConfirm}
          title={
            confirmed
              ? '已确认（服务端已记录）'
              : confirming
                ? '确认中…'
                : onConfirm
                  ? undefined
                  : '确认通道未接线'
          }
        >
          {confirming ? '确认中…' : confirmed ? '已确认' : '确认结论'}
        </button>
      </div>
    </footer>
  );
}
