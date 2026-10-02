/**
 * 停档红实心提示 —— 06 的 2:40
 *
 * **全稿唯一的打断式提示**，也是全稿唯一允许「一次性声光」的地方。
 * 红实心 + 白字 + 图标 + 规则原文；静态、不闪烁。
 * 不要给别的提示加声音/闪烁（impl_note）。
 *
 * 契约 §4：禁忌命中的强制勾选是**不可削减**的一条 —— 挡住的是签发，不是信息，
 * 所以同一屏里「一句话结论」仍然可见（降一级字号）。
 */

/** 警示图标（三角 + 感叹号）。currentColor 跟主题，不烘焙色值。 */
function WarningIcon() {
  return (
    <svg className="ws-stop-alert__icon" viewBox="0 0 20 20" role="img" aria-label="警示">
      {/* 三角主体 */}
      <path
        d="M10 2.2 18.6 17H1.4L10 2.2Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      {/* 感叹号竖笔 */}
      <path d="M10 8v4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      {/* 感叹号点 */}
      <circle cx="10" cy="14.6" r="0.9" fill="currentColor" />
    </svg>
  );
}

export default function WorkspaceStopAlert({
  level = 'stop',
  message,
  code,
}: {
  /**
   * `stop`     = 06 禁忌命中。**全稿唯一用红 + 一次性声光的地方**。
   * `attention`= 安全网关要求转人工等「看一眼」档。用青灰，**不占用红**。
   *              理由：语义色只有两级，而红专属于"禁忌命中"这一条规则
   *              （契约 §2 + 全稿唯一红点）。把红色扩到别的含义上，
   *              就等于让医生分不清"规则禁止"和"系统建议"。
   */
  level?: 'stop' | 'attention';
  /** 规则原文 / 转人工原因。不给默认文案之外的东西 —— 调用方必须显式传。 */
  message?: string;
  /** 机器可读错误码（如 E4005），供追溯，不进医生可见文案。 */
  code?: string;
}) {
  if (level === 'attention') {
    return (
      <div className="ws-stop-alert ws-stop-alert--attention" data-node="2:40" role="status">
        <WarningIcon />
        <div className="ws-stop-alert__body">
          <span className="ws-stop-alert__title">需人工确认后继续</span>
          {message && <span className="ws-stop-alert__rule">{message}</span>}
          {code && <span className="ws-stop-alert__code">错误码 {code}</span>}
        </div>
      </div>
    );
  }

  return (
    <div className="ws-stop-alert" data-node="2:40" role="alert">
      <WarningIcon />
      <div className="ws-stop-alert__body">
        <span className="ws-stop-alert__title">禁忌命中，不得签发</span>
        {/* 给出规则原文 —— 不静默阻断，医生要知道是哪条规则拦住他 */}
        <span className="ws-stop-alert__rule">
          {message ??
            '规则：妊娠期禁用该造影剂路径（《禁忌规则》§3.2）。请改用替代方案后重新判读。'}
        </span>
      </div>
    </div>
  );
}
