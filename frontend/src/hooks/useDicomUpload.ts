import { useCallback, useState } from 'react';
import { uploadDicom } from '@/services/imagingService';
import { getApiErrorMessage } from '@/utils/apiError';

/**
 * DICOM 装载 —— 影像进入系统的**唯一入口**（2026-10-05 补，此前全站 file input=0）。
 *
 * 一次选择同时做两件**互相独立**的事，缺一不可：
 *
 *  1. **本地成像**：把 File 注册进 Cornerstone 的 `wadouri.fileManager`，拿到本地
 *     imageId 立刻显示。走本地是因为后端**没有 WADO 取回端点**
 *     （`app/api/v1/imaging.py` 只有 upload/stream 两个 POST），
 *     所以生产环境目前只能显示"医生刚选的那一份"，刷新后需要重选。
 *     这条是**尽力而为**：注册失败不许阻断上传（判读不依赖浏览器显示）。
 *  2. **上行后端**：`POST /api/v1/imaging/dicom/upload`（multipart：file/session_id/priority），
 *     拿回 `image_id`。**判读管线只认这个 id** —— 后端按它从 `dicom_storage` 取字节。
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
  /** 本地 Cornerstone fileManager 的 imageId（仅用于显示）。 */
  localImageId?: string;
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
        setState({
          phase: 'uploaded',
          localImageId,
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
