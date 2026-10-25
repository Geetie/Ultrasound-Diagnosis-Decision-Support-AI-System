/**
 * 临床信息的值模型与工具函数（A.36 ⑤）。
 *
 * 独立成文件是 react-refresh 的硬性要求：组件文件只能导出组件，
 * 常量/函数混在组件文件里会让 Fast Refresh 失效（lint error）。
 * 消费方：ClinicalInfoPanel（渲染）、routes.tsx（拼判读请求）。
 */

export interface ClinicalInfoValue {
  symptoms: string;
  history: string;
  labs: string;
  /** 安全网关 patient_conditions 的前端勾选形态。 */
  conditions: string[];
}

export const EMPTY_CLINICAL_INFO: ClinicalInfoValue = {
  symptoms: '',
  history: '',
  labs: '',
  conditions: [],
};

/**
 * 勾选标签 → 安全网关规范码的**唯一**映射表（只此一处，别处不得再写一份）。
 *
 * 为什么必须发码、不能发中文标签
 * ------------------------------
 * 禁忌判定在 `safety_gateway/contraindication.py`：`_CONDITION_ALIASES` 的键与别名
 * 全是英文/规范码（pregnancy / gestation / pregnant、contrast_allergy /
 * iodine_allergy、anticoagulant_therapy / warfarin / heparin …），`_match_condition`
 * 的比对是 `any(c in aliases for c in patient_conditions)` —— **中文串永远不在别名表里**。
 * 所以把面板上的「妊娠」原样送上去的命中数是 0：医生勾了禁忌，系统照样按"无禁忌"
 * 放行。这是**反向 fail-open**（勾了也不触发，比直接报错危险得多）。
 *
 * 结论：界面继续显示中文，**上行一律用这里的码**。
 *
 * 新增勾选项时**必须**在这里补码；`__tests__/clinicalInfo.test.ts` 会扫描
 * ClinicalInfoPanel 的 CONDITION_OPTIONS，漏码即红。
 */
export const CONDITION_CODES = {
  '妊娠': 'pregnancy',
  '造影剂过敏': 'contrast_allergy',
  '抗凝治疗': 'anticoagulant_therapy',
} as const;

export type ConditionLabel = keyof typeof CONDITION_CODES;

/**
 * 勾选标签 → 规范码。未知标签**直接丢弃**（不猜、不退化成本地化字符串：
 * 猜错的码会命中错误的禁忌，比不命中更糟）。
 *
 * ⚠️ 空输入返回 `[]`，**不是 `undefined`**。这条决定临床行为（见
 * `app/models/schemas.py:193-197` 的语义约定）：
 *   · `None`（未提供）→ 请求里不转发该键，SG 走保守分支「缺数据即判命中」
 *     → 每次判读都 requires_manual_review，等于自动结论永远出不来；
 *   · `[]`（已确认无条件）→ 如实转发空列表，SG 正常评估 → 不命中。
 * 面板上「一项都没勾」是医生的**确认**（确实没有这三类情况），不是"没填"，
 * 所以必须送 `[]`。
 */
export function toConditionCodes(labels: readonly string[]): string[] {
  const codes: string[] = [];
  for (const label of labels) {
    if (!Object.hasOwn(CONDITION_CODES, label)) continue; // 未知标签丢弃（理由见上）
    const code = CONDITION_CODES[label as ConditionLabel];
    if (!codes.includes(code)) codes.push(code);
  }
  return codes;
}

export function isClinicalInfoFilled(v: ClinicalInfoValue): boolean {
  return (
    v.symptoms.trim() !== '' ||
    v.history.trim() !== '' ||
    v.labs.trim() !== '' ||
    v.conditions.length > 0
  );
}

/** 拼判读请求的 clinical_text（症状/病史/化验逐行拼接）。 */
export function toClinicalText(v: ClinicalInfoValue): string {
  return [
    v.symptoms.trim() && `症状：${v.symptoms.trim()}`,
    v.history.trim() && `病史：${v.history.trim()}`,
    v.labs.trim() && `化验：${v.labs.trim()}`,
  ]
    .filter(Boolean)
    .join('\n');
}
