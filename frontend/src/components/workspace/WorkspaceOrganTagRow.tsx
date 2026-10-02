import { ORGAN_LABEL } from '@/types/session';

/**
 * 部位标签排 + 当前部位结论 —— 14 的 4:63 / 4:64
 * 契约 §1：**仅部位数 ≥ 2 时渲染**（见 04_判读态结论，单部位时不存在）。
 * 承担「进度总览」职责，替代独立的进度页；当前部位的结论在下方展开。
 *
 * 与 13 的遮蔽同理：画布上这层是绝对定位覆盖在结论区之上，
 * 但**同一时刻只渲染这一层**，04 的结论卡内容不出现（P1 默认沉默）。
 *
 * 三个标签态用**形状与字重**区分，不靠颜色（也不引入第三个语义色）：
 *   已完成 = 有底色 + 青灰描边 + Regular
 *   进行中 = 有底色 + 无描边 + Medium
 *   待扫   = 无底色 + 灰描边 + 次要色
 *
 * 结论卡**全局只有一张**，按部位切换内容，不做竖向堆叠。
 */
export default function WorkspaceOrganTagRow() {
  return (
    <div
      className="ws-conclusion-overlay"
      data-region="conclusion-organ-tags"
    >
      <div className="ws-organ-tags">
        <span className="ws-organ-tag">{ORGAN_LABEL.thyroid} · 已完成</span>
        <span className="ws-organ-tag ws-organ-tag--active">
          {ORGAN_LABEL.cervical_ln} · 进行中
        </span>
        <span className="ws-organ-tag ws-organ-tag--pending">
          {ORGAN_LABEL.liver} · 待扫
        </span>
      </div>
      <p className="ws-organ-tags__current">
        {`当前部位（${ORGAN_LABEL.cervical_ln}）：考虑反应性增生可能，建议结合临床随访。`}
      </p>
    </div>
  );
}
