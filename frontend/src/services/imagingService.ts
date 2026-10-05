import apiClient from './api';
import type { ImagingUploadResponse } from '@/types/api';

/**
 * 影像接口。
 *
 * 【上行】POST /api/v1/imaging/dicom/upload（下方 uploadDicom）
 *         POST /api/v1/imaging/dicom/stream（收帧，不回传图像字节）
 *
 * 【下行 —— 2026-10-06 闭合了 A.28 时的缺口】
 *   GET  /api/v1/imaging/dicom?session_id=…   → 影像索引列表（无字节）
 *   GET  /api/v1/imaging/dicom/{image_id}     → application/dicom 字节
 *
 * 显示路径**刻意不走 Cornerstone 的远程加载**（wadouri:http://… 需要给加载器
 * 配鉴权头，而加载器内部的 XHR 拿不到 axios 的 Bearer 注入）。改用：
 *   axios 取字节（自动带 Bearer）→ `wadouri.fileManager.add(blob)`
 *   → 走已验证的本地显示路径（{@link fetchDicomForDisplay}）。
 * 这样鉴权天然一致，也不依赖加载器内部实现；且医生看到的与 AI 管线用的
 * 是**同一份**服务端字节（已 PHI 剥离）。
 */

export interface SessionImageMeta {
  image_id: string;
  session_id: string;
  modality?: string | null;
  width?: number | null;
  height?: number | null;
  frame_count?: number | null;
  size_bytes?: number | null;
  original_filename?: string | null;
  created_at?: string | null;
}

export interface SessionImageListResponse {
  session_id: string;
  count: number;
  images: SessionImageMeta[];
}

/** 按 session 列出影像索引（最早优先；**不含**字节）。 */
export async function listSessionImages(sessionId: string): Promise<SessionImageListResponse> {
  const response = await apiClient.get<SessionImageListResponse>('/imaging/dicom', {
    params: { session_id: sessionId },
  });
  return response.data;
}

/**
 * 取回一份 DICOM 字节并注册进 Cornerstone 本地文件管理器，返回可显示的 imageId。
 *
 * 失败抛异常，由调用方决定回落策略（上传场景回落到浏览器本地副本）。
 */
export async function fetchDicomForDisplay(imageId: string): Promise<string> {
  const response = await apiClient.get<Blob>(`/imaging/dicom/${encodeURIComponent(imageId)}`, {
    responseType: 'blob',
    timeout: 60_000,
  });
  const blob = response.data;
  const file = new File([blob], `${imageId}.dcm`, { type: 'application/dicom' });
  const { wadouri } = await import('@/components/dicom/cornerstone');
  return wadouri.fileManager.add(file) as string;
}

export async function uploadDicom(
  file: File,
  sessionId: string,
  priority: 0 | 1 | 2 = 1
): Promise<ImagingUploadResponse> {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('session_id', sessionId);
  formData.append('priority', String(priority));

  const response = await apiClient.post<ImagingUploadResponse>(
    '/imaging/dicom/upload',
    formData,
    {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    }
  );
  return response.data;
}

/** 帧流上行：把一帧 DICOM 以 base64 推给后端，换frame_id。 */
export async function streamDicomFrame(data: {
  session_id: string;
  frame_data: string; // base64
  frame_index: number;
  is_key_frame?: boolean;
  priority?: number;
}): Promise<{ session_id: string; frame_id: string; status: string }> {
  const response = await apiClient.post('/imaging/dicom/stream', data, {
    timeout: 120000,
  });
  return response.data;
}

/**
 * 影像显示用的 URL —— **已废弃，勿用于生产**。
 *
 * 这是 A.28 之前的设计：让 Cornerstone 直接按 `wadouri:/api/v1/imaging/dicom/{id}`
 * 远程取字节。没采用的原因见模块顶部 —— 加载器内部的 XHR 拿不到 axios 的
 * Bearer 注入，鉴权过不去。实际实现改走 {@link fetchDicomForDisplay}
 * （axios 取字节 → fileManager.add）。
 *
 * 保留这个函数只是为了让"显示 URL 单一取值点"的历史注释不误导后来者；
 * 若有新代码要用它，先解决鉴权头问题，否则 401。
 */
export function dicomImageIdFromFrame(frameId: string): string {
  return `wadouri:/api/v1/imaging/dicom/${encodeURIComponent(frameId)}`;
}

/**
 * dev 用：从 vite 中间件取本地 DICOM。仅 `import.meta.env.DEV` 下可用。
 * @param relativePath 相对 `dicom_storage/` 的路径，如 `dev-us365k/us365k_sample.dcm`
 */
export function devDicomImageId(relativePath: string): string | undefined {
  if (!import.meta.env.DEV) return undefined;
  return `wadouri:/dev-dicom/${relativePath.split('/').map(encodeURIComponent).join('/')}`;
}
