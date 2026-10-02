import apiClient from './api';
import type { SessionInfo, SessionListResponse } from '@/types/api';

/**
 * 会话接口。
 *
 * 端点以前缀 `/api/v1` + `app/api/v1/session.py` 的 router 注册为准
 * （`app/main.py`: `include_router(session_router, prefix="/api/v1/sessions")`），
 * 所以路径是 **`/sessions` 复数**。
 *
 * ⚠️ 修正记录（原先三处全错，结束会话必 404）：
 *   1. 建会话写的是 `/session` → 后端是 `POST /api/v1/sessions`
 *   2. 查会话写的是 `/session/{id}` → 后端是 `GET /api/v1/sessions/{id}`
 *   3. 结束会话写的是 `POST /session/{id}/end` → **后端没有 `/end` 端点**。
 *      `session.py` 只有 POST "" / GET {id} / PATCH {id}；
 *      结束会话 = `PATCH /api/v1/sessions/{id}` 带 `{ status }`，
 *      入参 schema 是 `SessionUpdateRequest { status?, organ_statuses? }`。
 */

export async function createSession(data: {
  patient_id: string;
  device_id: string;
  target_organs: string[];
}): Promise<SessionInfo> {
  const response = await apiClient.post<SessionInfo>('/sessions', data);
  return response.data;
}

export async function getSession(sessionId: string): Promise<SessionInfo> {
  const response = await apiClient.get<SessionInfo>(`/sessions/${sessionId}`);
  return response.data;
}

/**
 * 结束会话：没有 `/end` 端点，用 PATCH 改状态。
 * @param status terminated=中止、completed=正常完成（对应后端 SessionStatus）
 */
export async function endSession(
  sessionId: string,
  status: 'terminated' | 'completed' = 'terminated'
): Promise<SessionInfo> {
  const response = await apiClient.patch<SessionInfo>(`/sessions/${sessionId}`, { status });
  return response.data;
}

/** 更新部位状态（多器官联合诊断用）。 */
export async function updateSession(
  sessionId: string,
  data: { status?: string; organ_statuses?: Record<string, string> }
): Promise<SessionInfo> {
  const response = await apiClient.patch<SessionInfo>(`/sessions/${sessionId}`, data);
  return response.data;
}

/**
 * 工作列表 —— 设计稿 01_工作列表 / 15_工作列表浅色 的数据源。
 *
 * 这个端点是 2026-10-02 补的：原先只有 POST "" / GET {id} / PATCH {id}，
 * **没有列表**，所以工作列表只能写死三行假数据。已真机验证通过。
 *
 * 分组在服务端做完（in_progress / pending_today），前端只渲染 ——
 * 不要在前端按七种状态再分类一次，那会与契约 P2「一次决策」打架。
 */
export async function listSessions(limit = 20): Promise<SessionListResponse> {
  const response = await apiClient.get<SessionListResponse>('/sessions', {
    params: { limit },
  });
  return response.data;
}
