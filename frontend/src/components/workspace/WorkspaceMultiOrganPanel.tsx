import type { CrossOrganBlock, OrganDiagnosisStatus } from '@/types/api';
import { toCrossOrganView, toOrganRows } from './multiOrganView';

/**
 * 多部位结果面板 —— 05/14 多部位态的真实数据区（T-GB-AGT-01 前端接线）。
 *
 * 它出现的前提：会话 `target_organs` ≥ 2 且本次判读走了多器官端点。
 * 单部位会话里这个组件**根本不渲染**（默认沉默，契约 §1）。
 *
 * 两条口径写死在渲染里：
 *   1. 每个部位的状态逐条如实列出（失败/未执行**不清零、不折叠**），
 *      失败带可读原因 + 「单独重跑该部位」入口；
 *   2. 多部位一致性只是**提示**，未核对就明说未核对 —— 见 multiOrganView.toCrossOrganView。
 */
export default function WorkspaceMultiOrganPanel({
  results,
  crossOrgan,
  onRerunOrgan,
  rerunningOrgan,
}: {
  results: OrganDiagnosisStatus[];
  crossOrgan?: CrossOrganBlock | null;
  /** 单独重跑某部位（失败/未执行时才有意义）。 */
  onRerunOrgan?: (organ: string) => void;
  /** 正在重跑的部位代码；null = 空闲（用于禁用按钮 + 文案）。 */
  rerunningOrgan?: string | null;
}) {
  if (results.length === 0) return null;
  const rows = toOrganRows(results);
  const cross = toCrossOrganView(crossOrgan);

  return (
    <div className="ws-multi-organ" data-region="multi-organ-results">
      <ul className="ws-multi-organ__list" aria-label="各部位判读状态">
        {rows.map((row) => (
          <li
            key={row.organ}
            className="ws-multi-organ__item"
            data-organ={row.organ}
            data-status={row.status}
          >
            <span className="ws-multi-organ__organ">{row.label}</span>
            <span className="ws-multi-organ__status">{row.statusText}</span>
            {row.reasonText && (
              <span className="ws-multi-organ__reason">{row.reasonText}</span>
            )}
            {row.canRerun && onRerunOrgan && (
              <button
                type="button"
                className="ws-multi-organ__rerun"
                onClick={() => onRerunOrgan(row.organ)}
                disabled={rerunningOrgan === row.organ}
              >
                {rerunningOrgan === row.organ ? '重跑中…' : '单独重跑该部位'}
              </button>
            )}
          </li>
        ))}
      </ul>

      {/* 多部位一致性提示 —— 只作提示，不替代医生判断（文案里明确写出这句话） */}
      <div
        className="ws-multi-organ__cross"
        data-region="cross-organ-hint"
        data-tone={cross.tone}
      >
        <span className="ws-multi-organ__cross-title">{cross.title}</span>
        {cross.lines.map((line) => (
          <span key={line} className="ws-multi-organ__cross-line">
            {line}
          </span>
        ))}
        <span className="ws-multi-organ__cross-disclaimer">
          仅为系统提示，不替代医生判断；最终结论由医生确认。
        </span>
      </div>
    </div>
  );
}
