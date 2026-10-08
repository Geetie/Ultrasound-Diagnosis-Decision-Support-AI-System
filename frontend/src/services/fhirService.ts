import apiClient from './api';

/**
 * FHIR 患者资源接口 —— 「新建检查会话」页患者校验/建档的数据源。
 *
 * 后端挂载在 **`/fhir`**（`app/main.py`: `include_router(fhir_router, prefix="/fhir")`），
 * **不带 `/api/v1` 前缀**；而 `apiClient` 的全局 baseURL 是 `/api/v1`。
 * 所以这里每个请求都带 `baseURL: ''` 覆盖 —— axios 合并配置时非 undefined 的
 * 请求级值优先生效，空串 = 直接用相对 URL，否则会打到 `/api/v1/fhir/*`（404）。
 *
 * 两个端点都只要求 `get_current_active_user`（fhir.py:130,158）——
 * 医生角色可以调用；这与 consent 端点（医生 403）不同。
 * 「同意校验」由后端在 GET 时顺带做：未签署/已撤回 → 403（E1008/E1010），
 * 同意有效但 patients 表无行 → 404。前端据此分流（见 SessionCreatePage）。
 */

export interface FhirPatientResource {
  resourceType: 'Patient';
  id: string;
  name: { text: string }[];
  active: boolean;
}

export interface CreateFhirPatientInput {
  patient_id: string;
  /** 姓名。可省（库字段可空）；人口学信息由管理员后续补录。 */
  name?: string;
  /** YYYY-MM-DD。 */
  birth_date?: string;
  gender?: 'male' | 'female' | 'other' | 'unknown';
}

/** 校验患者：存在且知情同意有效 → 200 资源；未同意 → 403 E1008；撤回 → 403 E1010。 */
export async function getFhirPatient(patientId: string): Promise<FhirPatientResource> {
  const response = await apiClient.get<FhirPatientResource>(
    `/fhir/Patient/${encodeURIComponent(patientId)}`,
    { baseURL: '' },
  );
  return response.data;
}

/** 医生侧建档（patients 表无行时用）。patient_id 已存在 → 400 E1001。 */
export async function createFhirPatient(data: CreateFhirPatientInput): Promise<FhirPatientResource> {
  const response = await apiClient.post<FhirPatientResource>('/fhir/Patient', data, {
    baseURL: '',
  });
  return response.data;
}
