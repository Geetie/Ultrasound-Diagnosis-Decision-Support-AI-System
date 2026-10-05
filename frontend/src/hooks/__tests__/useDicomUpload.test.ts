import { renderHook, waitFor, act } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useDicomUpload } from '../useDicomUpload';
import { fetchDicomForDisplay, uploadDicom } from '@/services/imagingService';

/**
 * DICOM 装载 hook —— 判读主链的入口。
 *
 * 这里钉的是几件容易悄悄坏掉的事：
 *   1. **回调必须给后端 image_id，不能给本地 fileManager 的 id** —— 两者都是"影像 id"，
 *      传错的话请求 200、轮询也有响应，但管线取不到任何字节，结论恒为空。
 *   2. **没有会话就不发请求** —— upload 端点以 session_id 为外键，
 *      白发一次只是拿 404，还会让医生以为"影像坏了"。
 *   3. **上传失败必须落到 error 而不是静默** —— 静默失败在临床上等于
 *      "系统说上传了但没上传"，比报错危险得多。
 *   4. **显示优先用服务端副本**（同管线同源），但取回失败必须回落本地副本
 *      —— 显示是增强，不能因为回取失败把刚上传成功的影像弄没（2026-10-06）。
 */

vi.mock('@/services/imagingService', () => ({
  uploadDicom: vi.fn(),
  fetchDicomForDisplay: vi.fn(),
}));

// cornerstone 在 import 期就摸 WebGL/canvas，jsdom 下必然抛。
// 这里让它抛，正是"本地注册失败不许阻断上传"这条分支的验证条件。
vi.mock('@/components/dicom/cornerstone', () => {
  throw new Error('cornerstone 在 jsdom 下不可用');
});

const file = () => new File([new Uint8Array([0, 1, 2, 3])], 'thyroid.dcm');

describe('useDicomUpload', () => {
  beforeEach(() => {
    vi.mocked(uploadDicom).mockReset();
    vi.mocked(fetchDicomForDisplay).mockReset();
  });

  it('上传成功后用后端 image_id 回调（不是本地 fileManager id）', async () => {
    vi.mocked(uploadDicom).mockResolvedValue({
      image_id: 'img_20261005_abcdef12',
      session_id: 'sess-1',
      metadata: {
        patient_id: 'P******',
        modality: 'US',
        body_part: 'THYROID',
        acquisition_date: '20261005',
      },
      status: 'accepted',
    });
    const onUploaded = vi.fn();
    const { result } = renderHook(() => useDicomUpload('sess-1', onUploaded));

    await act(async () => {
      await result.current.handleFile(file());
    });

    await waitFor(() => expect(result.current.state.phase).toBe('uploaded'));
    expect(uploadDicom).toHaveBeenCalledWith(expect.any(File), 'sess-1', 1);
    expect(result.current.state.imageId).toBe('img_20261005_abcdef12');
    expect(onUploaded).toHaveBeenCalledWith('img_20261005_abcdef12');
  });

  it('没有会话时根本不发请求，并说明原因', async () => {
    const { result } = renderHook(() => useDicomUpload(undefined));

    await act(async () => {
      await result.current.handleFile(file());
    });

    expect(uploadDicom).not.toHaveBeenCalled();
    expect(result.current.state.phase).toBe('error');
    expect(result.current.state.error).toContain('尚未选择检查会话');
  });

  it('上传失败时进入 error 并保留后端给的原因', async () => {
    vi.mocked(uploadDicom).mockRejectedValue({
      response: { data: { error_code: 'E1012', error_message: 'DICOM 缺少必需标签' } },
    });
    const onUploaded = vi.fn();
    const { result } = renderHook(() => useDicomUpload('sess-1', onUploaded));

    await act(async () => {
      await result.current.handleFile(file());
    });

    await waitFor(() => expect(result.current.state.phase).toBe('error'));
    expect(result.current.state.error).toBe('DICOM 缺少必需标签');
    // 失败绝不能触发判读：没有字节入库时发起判读等于对着空气推理
    expect(onUploaded).not.toHaveBeenCalled();
    expect(result.current.state.imageId).toBeUndefined();
  });

  it('本地影像注册失败（jsdom / 无 WebGL）不阻断上传', async () => {
    vi.mocked(uploadDicom).mockResolvedValue({
      image_id: 'img_x',
      session_id: 'sess-1',
      metadata: {
        patient_id: 'P******',
        modality: 'US',
        body_part: 'THYROID',
        acquisition_date: '20261005',
      },
      status: 'accepted',
    });
    const { result } = renderHook(() => useDicomUpload('sess-1'));

    await act(async () => {
      await result.current.handleFile(file());
    });

    await waitFor(() => expect(result.current.state.phase).toBe('uploaded'));
    // cornerstone 已被 mock 成必抛，所以 localImageId 必然为空 —— 上传仍成功即为通过
    expect(result.current.state.localImageId).toBeUndefined();
    expect(result.current.state.imageId).toBe('img_x');
  });

  it('上传成功后取回服务端副本作为显示优先源（同管线同源）', async () => {
    vi.mocked(uploadDicom).mockResolvedValue({
      image_id: 'img_x',
      session_id: 'sess-1',
      metadata: {
        patient_id: 'P******',
        modality: 'US',
        body_part: 'THYROID',
        acquisition_date: '20261005',
      },
      status: 'accepted',
    });
    vi.mocked(fetchDicomForDisplay).mockResolvedValue('wadouri:server-copy');

    const { result } = renderHook(() => useDicomUpload('sess-1'));

    await act(async () => {
      await result.current.handleFile(file());
    });

    await waitFor(() => expect(result.current.state.phase).toBe('uploaded'));
    expect(fetchDicomForDisplay).toHaveBeenCalledWith('img_x');
    expect(result.current.state.serverImageId).toBe('wadouri:server-copy');
  });

  it('服务端副本取回失败时回落本地副本，上传不受影响', async () => {
    vi.mocked(uploadDicom).mockResolvedValue({
      image_id: 'img_x',
      session_id: 'sess-1',
      metadata: {
        patient_id: 'P******',
        modality: 'US',
        body_part: 'THYROID',
        acquisition_date: '20261005',
      },
      status: 'accepted',
    });
    vi.mocked(fetchDicomForDisplay).mockRejectedValue(new Error('503'));

    const { result } = renderHook(() => useDicomUpload('sess-1'));

    await act(async () => {
      await result.current.handleFile(file());
    });

    await waitFor(() => expect(result.current.state.phase).toBe('uploaded'));
    expect(result.current.state.serverImageId).toBeUndefined();
    expect(result.current.state.error).toBeNull();
  });

  it('reset 清空状态，便于重选', async () => {
    vi.mocked(uploadDicom).mockResolvedValue({
      image_id: 'img_x',
      session_id: 'sess-1',
      metadata: {
        patient_id: 'P******',
        modality: 'US',
        body_part: 'THYROID',
        acquisition_date: '20261005',
      },
      status: 'accepted',
    });
    const { result } = renderHook(() => useDicomUpload('sess-1'));

    await act(async () => {
      await result.current.handleFile(file());
    });
    await waitFor(() => expect(result.current.state.phase).toBe('uploaded'));

    act(() => result.current.reset());
    expect(result.current.state.phase).toBe('idle');
    expect(result.current.state.imageId).toBeUndefined();
  });
});
