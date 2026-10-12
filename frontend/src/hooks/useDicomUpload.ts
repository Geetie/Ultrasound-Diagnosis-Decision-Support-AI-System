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

  /** 本批已上传的后端 image_id（多图顺序上传，A.37 多图分期①）。 */

  imageIds?: string[];
  phase: DicomUploadPhase;
  /** 浏览器本地副本的 Cornerstone imageId（上传瞬间立即可显示）。 */
  localImageId?: string;
  /** 服务端副本的 Cornerstone imageId（上传成功后回取注册；显示优先用它）。 */
  serverImageId?: string;
  /** 多帧显示栈·本地副本（A.38 分期①：与 imageIds 同序，逐张注册）。 */
  localImageIds: string[];
  /** 多帧显示栈·服务端副本（同序；某张回取失败时该位缺省）。 */
  serverImageIds: string[];
  /**
   * A.52 帧归属：与 localImageIds **逐位对齐**的后端 image_id 数组。
   *
   * 为什么需要：显示栈里是 Cornerstone 句柄（`dicomfile:N?frame=k`），而分割结果
   * 自带的是后端 `img_...` —— 前端帧级门控若拿句柄去比，**永远不相等**，掩膜在
   * 任何帧都不显示（A.52 浏览器走查实测：提示一直挂着、翻到第 2/7 帧也不出覆盖层）。
   * 这里把「这一位显示条目属于哪张后端影像」并行带出去，门控改比后端 id。
   */
  localFrameOwners: string[];
  /** 同上，与 serverImageIds 逐位对齐。 */
  serverFrameOwners: string[];
  /** 后端 image_id（判读用）。 */
  imageId?: string;
  fileName: string | null;
  error: string | null;
}

export interface UseDicomUpload {
  state: DicomUploadState;
  /** 拿到 File 就绪处理（由 <input type=file multiple> 的 onChange 调用）。 */
  handleFile: (files: File[]) => Promise<void>;
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
 * 一个注册 imageId → 显示栈条目（A.40 分期②）：多帧 DICOM（NumberOfFrames>1）
 * 展开为 `?frame=1..N`（wadouri 帧寻址 1-based），单帧原样。
 */
async function expandStackId(imageId: string): Promise<string[]> {
  try {
    const cornerstone = await import('@/components/dicom/cornerstone');
    return await cornerstone.expandStackId(imageId);
  } catch {
    return [imageId];
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
  onUploaded?: (imageIds: string[]) => void | Promise<void>
): UseDicomUpload {
  const [state, setState] = useState<DicomUploadState>({
    phase: 'idle',
    fileName: null,
    error: null,
    imageIds: [],
    localImageIds: [],
    serverImageIds: [],
    localFrameOwners: [],
    serverFrameOwners: [],
  });

  const handleFiles = useCallback(
    async (files: File[]) => {
      if (!sessionId) {
        setState({
          phase: 'error',
          fileName: null,
          error: '尚未选择检查会话，无法装载影像',
          localImageIds: [],
          serverImageIds: [],
          localFrameOwners: [],
          serverFrameOwners: [],
        });
        return;
      }

      const ids: string[] = [];
      // A.38 多帧分期①：显示栈逐张累计（旧实现只留最后一张，前几张被丢弃
      // —— 上传了 3 张却只能看 1 张，帧指示器没有数据源）。
      const localIds: string[] = [];
      const serverIds: string[] = [];
      // A.52 帧归属：与两条显示栈逐位对齐的后端 image_id（见 DicomUploadState 注释）。
      // 本地栈每张占多少位（多帧展开后 = 帧数）在上传前就要记下来 —— 后端 id 要等
      // 上传成功才知道，末尾再统一回填。
      const localFrameCounts: number[] = [];
      const serverOwners: string[] = [];

      let lastLocal: string | undefined;

      let lastServer: string | undefined;
      // 多图顺序上传：每张上传成功即推进 imageIds；最后一张作为显示源。
      // 单张失败不中断整批（错误信息显示在面板，医生可看到第几张失败）。
      for (const file of Array.from(files)) {
        setState({
          phase: 'uploading',
          fileName: file.name,
          error: null,
          imageIds: ids,
          localImageIds: localIds,
          serverImageIds: serverIds,
          localFrameOwners: ids.flatMap((id, i) => Array(localFrameCounts[i] ?? 1).fill(id)),
          serverFrameOwners: [...serverOwners],
        });

        // 先显示：不等网络。注册失败也不影响后续上传。
        const localImageId = await registerLocalImage(file).catch(() => undefined);

        lastLocal = localImageId;
        // 分期②：多帧 DICOM（单文件 NumberOfFrames>1）展开为帧栈
        const localFrames = localImageId ? await expandStackId(localImageId) : [];
        const localCount = Math.max(1, localFrames.length);
        localFrameCounts.push(localCount);
        if (localImageId) localIds.push(...localFrames);

        try {
          const resp = await uploadDicom(file, sessionId, DEFAULT_PRIORITY);
          ids.push(resp.image_id);
          // 升级为服务端副本（同管线同源）。失败回落本地副本 —— 显示是增强。
          const serverImageId = await registerServerImage(resp.image_id).catch(
            () => undefined,
          );
          lastServer = serverImageId;
          if (serverImageId) {
            const serverFrames = await expandStackId(serverImageId);
            serverIds.push(...serverFrames);
            // 这些显示位都属于同一张后端影像（多帧时逐位重复同一个 image_id）
            for (let k = 0; k < Math.max(1, serverFrames.length); k += 1) {
              serverOwners.push(resp.image_id);
            }
          }
          setState({
            phase: 'uploading',
            localImageId,
            serverImageId,
            imageIds: ids,
            localImageIds: localIds,
            serverImageIds: serverIds,
            localFrameOwners: ids.flatMap((id, i) =>
              Array(localFrameCounts[i] ?? 1).fill(id),
            ),
            serverFrameOwners: [...serverOwners],
            fileName: file.name,
            error: null,
          });
        } catch (e: unknown) {
          setState({
            phase: 'error',
            localImageId,
            fileName: file.name,
            error: getApiErrorMessage(e, '影像上传失败'),
            imageIds: ids,
            localImageIds: localIds,
            serverImageIds: serverIds,
            localFrameOwners: ids.flatMap((id, i) => Array(localFrameCounts[i] ?? 1).fill(id)),
            serverFrameOwners: [...serverOwners],
          });
          return;
        }
      }

      setState({
        phase: 'uploaded',
        imageIds: ids,
        imageId: ids[ids.length - 1],
        localImageId: lastLocal,
        serverImageId: lastServer,
        localImageIds: localIds,
        serverImageIds: serverIds,
        localFrameOwners: ids.flatMap((id, i) => Array(localFrameCounts[i] ?? 1).fill(id)),
        serverFrameOwners: [...serverOwners],
        fileName: null,
        error: null,
      });
      await onUploaded?.(ids);
    },
    [sessionId, onUploaded]
  );

  const handleFile = useCallback((files: File[]) => handleFiles(files), [handleFiles]);

  const reset = useCallback(() => {
    setState({
      phase: 'idle',
      fileName: null,
      error: null,
      localImageIds: [],
      serverImageIds: [],
      localFrameOwners: [],
      serverFrameOwners: [],
    });
  }, []);

  return { state, handleFile, reset };
}
