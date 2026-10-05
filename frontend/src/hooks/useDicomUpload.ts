import { useCallback, useState } from 'react';
import { fetchDicomForDisplay, uploadDicom } from '@/services/imagingService';
import { getApiErrorMessage } from '@/utils/apiError';

/**
 * DICOM 装载 —— 影像进入系统的**唯一入口**（2026-10-05 补，此前全站 file input=0）。
 *
 * 显示的取值优先级（2026-10-06 引入"服务端副本"）：
 *   1. **服务端副本**：上传成功后按 `image_id` 取回字节（axios 带 Bearer →
 *      `wadouri.fileManager.add`）。优先它，因为医生看到的与 AI 管线用的是
 *      **同一份**（已 PHI 剥离），且它证明"上传的字节真的取得回来"。
 *   2. **浏览器本地副本**：取回失败时回落 —— 显示是增强，绝不能因为回取失败
 *      而把刚上传成功的影像弄没。
 *
 * 上行后端：`POST /api/v1/imaging/dicom/upload`，拿回 `image_id`。
 * **判读管线只认这个 id** —— 后端按它从 `dicom_storage` 取字节。
 *
 * ⚠️ 本地 fileManager 的 imageId 与后端 image_id 是**两套 id**，绝不能混用：
 *    把本地 id 当 `image_ids` 发给 `/diagnosis/request`，后端解析不到任何字节。
 *
 * 顺序说明：先显示再上传（医生不等网络），但**只有上传成功才回调 onUploaded** ——
 * 回调就是"发起判读"的触发点，在字节还没入库时发起判读等于对着空气推理。
 */

export type DicomUploadPhase = 'idle' | 'uploading' | 'uploaded' | 'error';

export interface DicomUploadState {
  phase: DicomUploadPhase;
  /** 浏览器本地副本的 Cornerstone imageId（上传瞬间立即可显示）。 */
  localImageId?: string;
  /** 服务端副本的 Cornerstone imageId（上传成功后回取注册；显示优先用它）。 */
  serverImageId?: string;
  /** 后端 image_id（判读用）。 */
  imageId?: string;
  fileName: string | null;
  error: string | null;
}

export interface UseDicomUpload {
  state: DicomUploadState;
  /** 拿到 File 就绪处理（由 <input type=file> 的 onChange 调用）。 */
  handleFile: (file: File) => Promise<void>;
  reset: () => void;
}

/** 后端优先级：0=急诊 1=常规 2=教学。UI 不暴露选择权，医生端恒为常规。 */
const DEFAULT_PRIORITY: 0 | 1 | 2 = 1;

/**
 * 把 File 注册进 Cornerstone 本地文件管理器。
 *
 * 动态 import 有两个原因（与 WorkspaceDicomLayer 一致）：
 *   1. cornerstone 约 2MB，不该让所有工作区状态都背这个 chunk；
 *   2. 它在 import 期就摸 WebGL/canvas，静态 import 会让 jsdom 下的组件测试直接崩。
 *
 * 任何异常都被吞掉并返回 undefined：显示是增强，上传才是主线。
 */
async function registerLocalImage(file: File): Promise<string | undefined> {
  try {
    const cornerstone = await import('@/components/dicom/cornerstone');
    return cornerstone.wadouri.fileManager.add(file) as string;
  } catch {
    return undefined;
  }
}

/**
 * 上传成功后，按后端 image_id 取回服务端副本并注册（见模块顶部的优先级说明）。
 * 失败返回 undefined —— 回落本地副本，绝不因此把影像弄没。
 */
async function registerServerImage(imageId: string): Promise<string | undefined> {
  try {
    return await fetchDicomForDisplay(imageId);
  } catch {
    return undefined;
  }
}

/**
 * @param sessionId 上传所归属的检查会话。**为空时 handleFile 不发请求** ——
 *   后端 upload 端点以 session_id 为外键且会校验其存在（`imaging.py` 抛 NotFound），
 *   与其白发一次拿 404，不如让调用方先把会话准备好。
 * @param onUploaded 上传成功后回调，参数是后端 image_id。判读由调用方在这里发起。
 */
export function useDicomUpload(
  sessionId: string | undefined,
  onUploaded?: (imageId: string) => void | Promise<void>
): UseDicomUpload {
  const [state, setState] = useState<DicomUploadState>({
    phase: 'idle',
    fileName: null,
    error: null,
  });

  const handleFile = useCallback(
    async (file: File) => {
      if (!sessionId) {
        setState({
          phase: 'error',
          fileName: file.name,
          error: '尚未选择检查会话，无法装载影像',
        });
        return;
      }

      setState({ phase: 'uploading', fileName: file.name, error: null });

      // 先显示：不等网络。注册失败也不影响后续上传。
      const localImageId = await registerLocalImage(file);

      try {
        const resp = await uploadDicom(file, sessionId, DEFAULT_PRIORITY);
        // 升级为服务端副本（同管线同源）。失败回落本地副本 —— 显示是增强。
        const serverImageId = await registerServerImage(resp.image_id);
        setState({
          phase: 'uploaded',
          localImageId,
          serverImageId,
          imageId: resp.image_id,
          fileName: file.name,
          error: null,
        });
        await onUploaded?.(resp.image_id);
      } catch (e: unknown) {
        setState({
          phase: 'error',
          localImageId,
          fileName: file.name,
          error: getApiErrorMessage(e, '影像上传失败'),
        });
      }
    },
    [sessionId, onUploaded]
  );

  const reset = useCallback(() => {
    setState({ phase: 'idle', fileName: null, error: null });
  }, []);

  return { state, handleFile, reset };
}
