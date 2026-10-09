/**
 * 标注存档服务（A.40）：测量注释随影像 image_id 存/取。
 *
 * 端点挂在 /api/v1/images/{image_id}/annotations（后端 JSON 文件存档，
 * 与 DICOM 目录同级、随影像备份迁移）。
 */
import apiClient from '@/services/api';

export interface StoredAnnotation {
  [key: string]: unknown;
  toolName?: string;
}

export async function fetchAnnotations(imageId: string): Promise<StoredAnnotation[]> {
  const response = await apiClient.get<{ image_id: string; annotations: StoredAnnotation[] }>(
    `/images/${encodeURIComponent(imageId)}/annotations`,
  );
  return response.data.annotations ?? [];
}

export async function saveAnnotations(
  imageId: string,
  annotations: StoredAnnotation[],
): Promise<number> {
  const response = await apiClient.put<{ saved: number }>(
    `/images/${encodeURIComponent(imageId)}/annotations`,
    { image_id: imageId, annotations },
  );
  return response.data.saved ?? 0;
}
