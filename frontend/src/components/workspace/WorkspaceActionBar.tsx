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
 */
export default function WorkspaceActionBar({
  onConfirm,
}: {
  /** 确认结论的回调。契约要求三选一等效：点它／说「确认」／踩脚踏。 */
  onConfirm?: () => void;
}) {
  return (
    <footer className="ws-actionbar" data-region="actionbar">
      {/* 2:391 回读文案 —— 系统回读指令，医生给一个确认动作 */}
      <span className="ws-actionbar__readback">
        已回读：TI-RADS 3，考虑良性可能大 —— 请给一个确认动作
      </span>

      {/* 2:392 右侧动作组 */}
      <div className="ws-actionbar__group">
        {/* 2:393 等效说明 —— 脚踏不得为必需项，基层可能没有此硬件 */}
        <span className="ws-actionbar__equivalent">或说「确认」／踩脚踏（三选一等效）</span>

        {/* 2:394 确认结论-大按钮 —— 主按钮，色取 color-inverse / color-on-inverse */}
        <button type="button" className="ws-primary-button" onClick={onConfirm}>
          确认结论
        </button>
      </div>
    </footer>
  );
}
