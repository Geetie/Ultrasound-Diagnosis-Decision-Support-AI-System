import apiClient from './api';

/**
 * 管理面只读查询（A.58）。
 *
 * 目前只有**设备台账**：`GET /api/v1/admin/device`（A.51③ 端点，`app/api/v1/device.py`）。
 * 契约（真机 p16 实测）：
 *   · 查询参数 `status / device_type / offset / limit`（limit 上限 200）；
 *   · 权限 `RoleChecker(allowed_roles=["SYSTEM_ADMIN"])` → **医生 token 403**、无 token 401；
 *   · 响应 `{ items, total, offset, limit }`，条目字段见下；**响应不含 api_key**（刻意）。
 *
 * 为什么放在前端服务层：设置页要如实呈现"设备台账属管理员工作面"这一口径
 * （A.58 口径落地）——403 不是故障而是权限边界，界面必须说清楚而不是静默缺行。
 */
export interface DeviceListItem {
  device_id: string;
  device_name: string;
  device_type: string;
  location: string | null;
  status: string;
  jetpack_version: string | null;
  created_at: string;
  updated_at: string;
}

export interface DeviceListResponse {
  items: DeviceListItem[];
  total: number;
  offset: number;
  limit: number;
}

export async function listDevices(
  params: { limit?: number; offset?: number; status?: string } = {}
): Promise<DeviceListResponse> {
  const response = await apiClient.get<DeviceListResponse>('/admin/device', { params });
  return response.data;
}
