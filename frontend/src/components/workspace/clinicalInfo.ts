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
