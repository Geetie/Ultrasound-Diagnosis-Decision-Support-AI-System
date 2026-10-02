/**
 * 检查工作区的视图状态定义与「该不该渲染」判定。
 *
 * 单独成文件的原因：react-refresh 要求一个文件要么只导出组件、要么只导出常量，
 * 状态判定函数与组件混在一起会破坏 HMR。
 *
 * 全部判定都来自契约 §1 的「条件渲染」列——不满足条件时**不渲染**（不是隐藏）。
 */
export type WorkspaceState =
  | 'scanning'        // 02 扫查态
  | 'progress'        // 03 等待分析中
  | 'conclusion'      // 04 判读态结论
  | 'multi-organ'     // 05 多部位态
  | 'stopped'         // 06 停档禁忌命中
  | 'report'          // 07 报告侧面板
  | 'actionbar'       // 10 确认动作区
  | 'ai-unavailable'  // 11 AI 不可用态
  | 'signed'// 12 签发后
  | 'masked'          // 13 诊室遮蔽态
  | 'organ-tags';     // 14 多部位标签排

export const WORKSPACE_STATES: readonly WorkspaceState[] = [
  'scanning', 'progress', 'conclusion', 'multi-organ', 'stopped',
  'report', 'actionbar', 'ai-unavailable', 'signed', 'masked', 'organ-tags',
];

/** 空态 / 等待态：两轴居中（02 / 03 / 05） */
export const isCenteredState = (s: WorkspaceState) =>
  s === 'scanning' || s === 'progress' || s === 'multi-organ';

/** 确认动作区出现时结论区收至 200（仅 10） */
export const isCompactState = (s: WorkspaceState) => s === 'actionbar';

/** 部位指示条：仅部位数 ≥ 2（05 / 14） */
export const hasOrganBar = (s: WorkspaceState) =>
  s === 'multi-organ' || s === 'organ-tags';

/** 报告侧面板（07 / 12） */
export const hasReportPanel = (s: WorkspaceState) => s === 'report' || s === 'signed';

/** 结论区覆盖层：13 遮蔽态 / 14 标签排。二者都让结论卡内容不渲染。 */
export const overlayOf = (s: WorkspaceState) =>
  s === 'masked' ? 'mask' : s === 'organ-tags' ? 'organ-tags' : null;

/** 每态对应哪个画布帧 —— 挂 data-screen 便于比对与排查。 */
export const SCREEN_OF: Record<WorkspaceState, string> = {
  scanning: '02_扫查态',
  progress: '03_等待分析中',
  conclusion: '04_判读态结论',
  'multi-organ': '05_多部位态',
  stopped: '06_停档禁忌命中',
  report: '07_报告侧面板',
  actionbar: '10_确认动作区',
  'ai-unavailable': '11_AI不可用态',
  signed: '12_签发后',
  masked: '13_诊室遮蔽态',
  'organ-tags': '14_多部位标签排',
};

/**
 * 判读流程相位 → 视图状态。
 *
 * 放在这里而不是页面里：相位到画布帧的映射是**契约级**对应关系，
 * 独立成纯函数才好单测，也免得散落在各个容器里走散。
 *
 * 注意 timeout / error 都映射到 03 等待态 —— 因为 03 的副文案本来就写着
 * 「不用等它跑完，可继续扫查或先写报告」（契约§3.1），超时不该把医生困住。
 * `ai-unavailable` 留给「AI 明确不可用」而非「这一次请求失败」。
 */
export function stateForPhase(
  phase: 'idle' | 'scanning' | 'analyzing' | 'concluded' | 'timeout' | 'error'
): WorkspaceState {
  switch (phase) {
    case 'concluded':
      return 'conclusion';
    case 'analyzing':
    case 'timeout':
    case 'error':
      return 'progress';
    case 'scanning':
    case 'idle':
    default:
      return 'scanning';
  }
}
