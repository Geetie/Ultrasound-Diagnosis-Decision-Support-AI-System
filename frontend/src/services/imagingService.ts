import apiClient from './api';
import type { ImagingUploadResponse } from '@/types/api';

/**
 * 影像接口。
 *
 * ⚠️ 上下行不对称，接线前务必知道：
 *
 * 【上行 —— 后端有端点】
 *   POST /api/v1/imaging/dicom/upload  multipart（下方 uploadDicom）
 *   POST /api/v1/imaging/dicom/stream  JSON { session_id, frame_data(base64),
 *                                           frame_index, is_key_frame, priority }
 *     → { session_id, frame_id, navigation_hint?, status }
 *   注意 stream 是**收帧**用的：设备把帧推给后端，后端校验并落盘后返回
 *   frame_id；它**不回传图像字节**。
 *
 * 【下行 —— 后端没有端点 ⚠️ 这是个真实的 API 缺口】
 *   浏览器要显示 DICOM 必须按 URL 取回字节（WADO 语义），但
 *   `app/api/v1/imaging.py` 只有 upload / stream 两个 POST，没有任何 GET。
 *   `DicomStorageService.retrieve(storage_path)` 有读回能力，但只在服务内部
 *   使用，没有暴露成 HTTP 接口。WebSocket `/api/v1/realtime/dicom` 只发 JSON
 *   控制/通知消息，不是字节流。
 *
 *   → 需要后端补一个端点，建议（包一层 retrieve 即可）：
 *       GET /api/v1/imaging/dicom/{frame_id}  →  application/dicom
 *
 *   在它存在之前，dev 用 vite 中间件 `/dev-dicom/*` 读本地 `dicom_storage/`
 *   （仅 dev，不进构建产物）。生产环境不要依赖它。
 */

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
 * 影像显示用的 URL —— **单一取值点**。
 *
 * 影像区只认这个函数产出的 URL，不关心字节从哪来；等后端补上 WADO 端点后，
 * 只需要改这里，界面侧零改动。
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
