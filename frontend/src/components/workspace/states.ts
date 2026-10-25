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

/**
 * 生产不可达（仅 dev `?state=` 后门）的状态，以及**为什么**保持不可达。
 *
 * 这份清单是 T7 的结论沉淀：把"没接"和"不该接"分开写清楚，
 * 免得下一位 Agent 又来"顺手修可达性"然后造出伪造的临床界面。
 */
export const DEV_ONLY_STATES = {
  /**
   * 13_诊室遮蔽态。**保持 dev-only**：全仓没有任何"隐私模式/患者可见"开关，
   * 也没有来自后端的模式字段。为了"可达"去造一个没人用的开关，等于凭空发明
   * 一个临床工作流（医生不知道何时该按它，按了也没有任何后端语义）。
   * 真要接，前置条件是：设备级设置里先有"诊室有患者在场"的真实配置项。
   */
  masked: '需先有真实的隐私/患者在场开关（设备级设置），当前不存在 → 不造假入口',
  /**
   * 07_报告侧面板 / 12_签发后。
   *
   * **已改为生产可达**（T7）：显式进入时 `reportPanel` 有值，常驻条上有「报告面板」入口；
   * `signed` 仅由后端 `ReportResponse.signature_present` 驱动。
   *
   * 这里保留说明是因为它有一个**必须守住的前提**：面板正文只能显示后端
   * `rendered` 正文。组件里那段写死的「18.4 × 12.1 mm 低回声结节…」是画布示意文字，
   * 只在 dev `?state=` 后门（不传 `reportText`）时出现；生产入口查不到正文时
   * 必须如实说"尚无渲染正文"，**不得**回落到示意文字（那等于伪造超声所见）。
   */
  report: '正文须为后端 rendered 正文；无正文时如实说明，不回落到画布示意文字',
  signed: '同 07；signed 只由后端 signature_present 驱动，不由 UI 推测',
} as const;

/** 该状态是否仅 dev 可达（生产环境不得由数据驱动进入）。 */
export function isDevOnlyState(s: WorkspaceState): boolean {
  // `report` / `signed` 已于 T7 接通生产入口（带真实正文的前提见 DEV_ONLY_STATES），
  // 现在只剩遮蔽态是 dev-only。
  return s === 'masked';
}

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
