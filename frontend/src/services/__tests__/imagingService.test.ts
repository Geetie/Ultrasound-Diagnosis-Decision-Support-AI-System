import { beforeEach, describe, expect, it, vi } from 'vitest';

import apiClient from '../api';
import { fetchDicomForDisplay, listSessionImages } from '../imagingService';

/**
 * 影像回取（2026-10-06 闭合的 API 缺口）。
 *
 * 钉两件事：
 *  1. 取回字节走 **axios**（自动带 Bearer）而不是 Cornerstone 加载器的内部 XHR
 *     —— 后者拿不到鉴权头，这就是 A.28 之前「显示 URL 单一取值点」方案被否的原因。
 *  2. 取回的字节注册进 fileManager，返回的 imageId 可直接用于显示。
 */

vi.mock('../api', () => ({
  default: { get: vi.fn() },
}));

// cornerstone 在 import 期就摸 WebGL/canvas，jsdom 下不可用 —— mock 出 fileManager
const addMock = vi.fn((file: File) => `wadouri:${file.name}`);
vi.mock('@/components/dicom/cornerstone', () => ({
  wadouri: { fileManager: { add: (file: File) => addMock(file) } },
}));

describe('listSessionImages', () => {
  beforeEach(() => {
    vi.mocked(apiClient.get).mockReset();
  });

  it('按 session_id 查询并返回索引列表', async () => {
    const images = [{ image_id: 'img_1', session_id: 'sess_1' }];
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { session_id: 'sess_1', count: 1, images },
    });

    const resp = await listSessionImages('sess_1');

    expect(apiClient.get).toHaveBeenCalledWith('/imaging/dicom', {
      params: { session_id: 'sess_1' },
    });
    expect(resp.count).toBe(1);
    expect(resp.images[0].image_id).toBe('img_1');
  });
});

describe('fetchDicomForDisplay', () => {
  beforeEach(() => {
    vi.mocked(apiClient.get).mockReset();
    addMock.mockClear();
  });

  it('以 blob 取回字节并注册进 fileManager', async () => {
    const bytes = new Blob([new Uint8Array([1, 2, 3])]);
    vi.mocked(apiClient.get).mockResolvedValue({ data: bytes });

    const imageId = await fetchDicomForDisplay('img_20261006_abc');

    expect(apiClient.get).toHaveBeenCalledWith('/imaging/dicom/img_20261006_abc', {
      responseType: 'blob',
      timeout: 60_000,
    });
    expect(addMock).toHaveBeenCalledTimes(1);
    const registered = addMock.mock.calls[0][0] as File;
    expect(registered.name).toBe('img_20261006_abc.dcm');
    expect(registered.type).toBe('application/dicom');
    expect(imageId).toBe('wadouri:img_20261006_abc.dcm');
  });

  it('取回失败向上抛 —— 由调用方决定回落策略（上传场景回落本地副本）', async () => {
    vi.mocked(apiClient.get).mockRejectedValue(new Error('404'));
    await expect(fetchDicomForDisplay('img_missing')).rejects.toThrow('404');
    expect(addMock).not.toHaveBeenCalled();
  });
});
