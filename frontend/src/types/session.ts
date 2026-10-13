export type SessionStatus = 'pending' | 'active' | 'partial' | 'completed' | 'needs_review' | 'failed' | 'terminated';
export type OrganStatus = 'pending' | 'in_progress' | 'completed' | 'failed' | 'skipped' | 'needs_review';
/**
 * 检查部位。
 *
 * ⚠️ 部位集与设计包的对齐说明（2026-10-13 更新）：
 *
 * 设计稿 09_新建检查会话（2:286/2:288/2:290）与 14_多部位标签排（4:64）列的是
 * **甲状腺 / 颈部淋巴结 / 肝脏** 三项；而 `cardiac` 是后端契约里真实存在、
 * 且**临床上必须有**的部位 —— 装载心脏超声图（凸阵探头 / 多帧心超序列）却只能
 * 声明甲状腺时，系统会按甲状腺给出"结节 TI-RADS 分级 + 病灶面积"压在心脏图上，
 * 属**错误器官语义**的输出（比不输出更危险，见 A.40 登记同类隐患）。
 *
 * 因此本处**有意偏离设计稿**：把 `cardiac` 补进预设列表（第 4 项）。该偏离已登记
 * 到 16 号附录 A.56，下次设计走查需与设计方同步；若要回退到 3 项，必须同时给出
 * 「影像与声明部位不符时不输出该部位结论」的替代护栏。
 *
 * 显示名一律走 ORGAN_LABEL，不要在组件里硬编码中文 —— 否则设计与代码
 * 会再次走散（这正是本次要修的那类不一致）。
 */
export type OrganType = 'thyroid' | 'cervical_ln' | 'liver' | 'cardiac';

export const ORGAN_LABEL: Record<OrganType, string> = {
  thyroid: '甲状腺',
  cervical_ln: '颈部淋巴结',
  liver: '肝脏',
  cardiac: '心脏',
};

/**
 * 部位预设顺序（甲状腺默认预选）。
 *
 * 设计稿 09 只列前三项；`cardiac` 为**临床补充**（见文件头"有意偏离"说明，
 * 16 号附录 A.56）—— 没有它，心脏检查无法声明部位，只能落默认甲状腺。
 */
export const ORGAN_PRESETS: OrganType[] = ['thyroid', 'cervical_ln', 'liver', 'cardiac'];

/** 显示名 → OrganType；遇到未知显示名返回 undefined 而不是静默兜底。 */
export function organTypeFromLabel(label: string): OrganType | undefined {
  return (Object.keys(ORGAN_LABEL) as OrganType[]).find(
    (k) => ORGAN_LABEL[k] === label
  );
}

export interface SessionState {
  sessionId: string | null;
  patientId: string | null;
  deviceId: string | null;
  status: SessionStatus;
  targetOrgans: OrganType[];
  organStatuses: Partial<Record<OrganType, OrganStatus>>;
  knowledgeBaseVersion: string | null;
  consentStatus: 'granted' | 'denied' | 'withdrawn' | 'pending' | null;
  createdAt: string | null;
  expiredAt: string | null;
  ttlRemainingMs: number | null;
  /** 当前选中的检查器官（顶栏选择器 / 语音指令共用） */
  currentOrgan: OrganType | null;
}
