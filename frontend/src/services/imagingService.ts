import apiClient from './api';
import type { ImagingUploadResponse } from '@/types/api';

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
