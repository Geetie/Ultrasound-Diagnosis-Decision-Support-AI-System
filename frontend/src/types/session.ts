export type SessionStatus = 'pending' | 'active' | 'partial' | 'completed' | 'needs_review' | 'failed' | 'terminated';
export type OrganStatus = 'pending' | 'in_progress' | 'completed' | 'failed' | 'skipped' | 'needs_review';
/**
 * 检查部位。
 *
 * ⚠️ 部位集与设计包对齐过：设计稿的 09_新建检查会话（2:286/2:288/2:290）与
 * 14_多部位标签排（4:64）用的都是 **甲状腺 / 颈部淋巴结 / 肝脏**，
 * 而原类型只有 thyroid/liver/cardiac —— 缺 `cervical_ln`、多 `cardiac`。
 * 现补齐 `cervical_ln`；`cardiac` 保留（后端契约与既有代码仍在用），
 * 只是不出现在 09 的预设列表里。
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

/** 设计稿 09 的部位预设顺序（甲状腺默认预选）。 */
export const ORGAN_PRESETS: OrganType[] = ['thyroid', 'cervical_ln', 'liver'];

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
