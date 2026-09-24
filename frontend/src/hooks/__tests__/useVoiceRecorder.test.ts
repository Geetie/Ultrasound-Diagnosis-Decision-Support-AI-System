import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  encodePcm16Base64,
  mixToMono,
  resampleLinear,
  useVoiceRecorder,
} from '../useVoiceRecorder';

/* ============================================================
   纯函数：编码 / 重采样 / 混音
   ============================================================ */

function decodeInt16(base64: string): Int16Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new Int16Array(bytes.buffer);
}

describe('encodePcm16Base64', () => {
  it('按小端序编码 16bit PCM', () => {
    const samples = new Float32Array([0, 1, -1]);
    const decoded = decodeInt16(encodePcm16Base64(samples));
    expect(decoded[0]).toBe(0);
    expect(decoded[1]).toBe(0x7fff);
    expect(decoded[2]).toBe(-0x8000);
  });

  it('越界样本被削顶而不溢出', () => {
    const decoded = decodeInt16(encodePcm16Base64(new Float32Array([2.5, -3.2])));
    expect(decoded[0]).toBe(0x7fff);
    expect(decoded[1]).toBe(-0x8000);
  });
});

describe('resampleLinear', () => {
  it('同采样率原样返回', () => {
    const input = new Float32Array([1, 2, 3]);
    expect(resampleLinear(input, 16000, 16000)).toBe(input);
  });

  it('48k → 16k 长度变为 1/3', () => {
    const input = new Float32Array(4800).fill(0.5);
    const output = resampleLinear(input, 48000, 16000);
    expect(output.length).toBe(1600);
    expect(output[800]).toBeCloseTo(0.5, 5);
  });

  it('空输入不抛异常', () => {
    expect(resampleLinear(new Float32Array(0), 48000, 16000).length).toBe(0);
  });
});

describe('mixToMono', () => {
  it('多声道取平均', () => {
    const mixed = mixToMono([new Float32Array([1, 0]), new Float32Array([0, 1])]);
    expect(mixed[0]).toBeCloseTo(0.5, 5);
    expect(mixed[1]).toBeCloseTo(0.5, 5);
  });

  it('单声道直接返回', () => {
    const single = new Float32Array([0.3]);
    expect(mixToMono([single])).toBe(single);
  });
});

/* ============================================================
   Hook：录音 → 解码 → 转写
   ============================================================ */

class FakeAnalyser {
  fftSize = 1024;
  getByteTimeDomainData(array: Uint8Array) {
    array.fill(140);
  }
}

class FakeAudioContext {
  createMediaStreamSource() {
    return { connect: () => undefined };
  }
  createAnalyser() {
    return new FakeAnalyser() as unknown as AnalyserNode;
  }
  async decodeAudioData() {
    return {
      numberOfChannels: 1,
      sampleRate: 48000,
      getChannelData: () => new Float32Array(48000),
    } as unknown as AudioBuffer;
  }
  close = vi.fn().mockResolvedValue(undefined);
}

class FakeMediaRecorder {
  static instances: FakeMediaRecorder[] = [];
  state: 'inactive' | 'recording' = 'inactive';
  mimeType = 'audio/webm';
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  stream: unknown;

  constructor(stream: unknown) {
    this.stream = stream;
    FakeMediaRecorder.instances.push(this);
  }

  start() {
    this.state = 'recording';
    this.ondataavailable?.({ data: new Blob(['chunk'], { type: 'audio/webm' }) });
  }

  stop() {
    this.state = 'inactive';
    this.onstop?.();
  }
}

function fakeStream() {
  return { getTracks: () => [{ stop: vi.fn() }] } as unknown as MediaStream;
}

describe('useVoiceRecorder', () => {
  const originalMediaRecorder = globalThis.MediaRecorder;

  beforeEach(() => {
    FakeMediaRecorder.instances = [];
    (globalThis as unknown as { MediaRecorder: unknown }).MediaRecorder = FakeMediaRecorder;
  });

  afterEach(() => {
    (globalThis as unknown as { MediaRecorder: unknown }).MediaRecorder = originalMediaRecorder;
    vi.restoreAllMocks();
  });

  it('录音后转写成功', async () => {
    const mediaDevices = { getUserMedia: vi.fn().mockResolvedValue(fakeStream()) } as unknown as MediaDevices;
    const transcribe = vi.fn().mockResolvedValue('开始甲状腺诊断');

    const { result } = renderHook(() =>
      useVoiceRecorder({
        minDurationMs: 0,
        mediaDevices,
        audioContextFactory: () => new FakeAudioContext() as unknown as AudioContext,
        transcribe,
      })
    );

    await act(async () => {
      await result.current.start();
    });
    expect(result.current.state).toBe('recording');

    await act(async () => {
      await result.current.stop();
    });

    await waitFor(() => expect(result.current.transcript).toBe('开始甲状腺诊断'));
    expect(transcribe).toHaveBeenCalledTimes(1);
    expect(result.current.state).toBe('idle');
    expect(result.current.error).toBeNull();
  });

  it('麦克风权限被拒时给出明确错误', async () => {
    const mediaDevices = {
      getUserMedia: vi.fn().mockRejectedValue(Object.assign(new Error('denied'), { name: 'NotAllowedError' })),
    } as unknown as MediaDevices;

    const { result } = renderHook(() => useVoiceRecorder({ mediaDevices }));

    await act(async () => {
      await result.current.start();
    });

    expect(result.current.state).toBe('error');
    expect(result.current.error?.code).toBe('permission_denied');
    expect(result.current.error?.message).toContain('权限');
  });

  it('无可用设备时提示检查设备', async () => {
    const mediaDevices = {
      getUserMedia: vi.fn().mockRejectedValue(Object.assign(new Error('none'), { name: 'NotFoundError' })),
    } as unknown as MediaDevices;

    const { result } = renderHook(() => useVoiceRecorder({ mediaDevices }));
    await act(async () => {
      await result.current.start();
    });

    expect(result.current.error?.code).toBe('no_device');
  });

  it('录音过短直接提示重录，不调用转写', async () => {
    const mediaDevices = { getUserMedia: vi.fn().mockResolvedValue(fakeStream()) } as unknown as MediaDevices;
    const transcribe = vi.fn();

    const { result } = renderHook(() =>
      useVoiceRecorder({
        minDurationMs: 5000,
        mediaDevices,
        audioContextFactory: () => new FakeAudioContext() as unknown as AudioContext,
        transcribe,
      })
    );

    await act(async () => {
      await result.current.start();
    });
    await act(async () => {
      await result.current.stop();
    });

    expect(result.current.error?.code).toBe('too_short');
    expect(transcribe).not.toHaveBeenCalled();
  });

  it('转写失败进入 error 且可重试（reset 回到 idle）', async () => {
    const mediaDevices = { getUserMedia: vi.fn().mockResolvedValue(fakeStream()) } as unknown as MediaDevices;
    const transcribe = vi.fn().mockRejectedValue(new Error('asr down'));

    const { result } = renderHook(() =>
      useVoiceRecorder({
        minDurationMs: 0,
        mediaDevices,
        audioContextFactory: () => new FakeAudioContext() as unknown as AudioContext,
        transcribe,
      })
    );

    await act(async () => {
      await result.current.start();
    });
    await act(async () => {
      await result.current.stop();
    });

    await waitFor(() => expect(result.current.state).toBe('error'));
    expect(result.current.error?.code).toBe('transcribe_failed');

    act(() => result.current.reset());
    expect(result.current.state).toBe('idle');
    expect(result.current.error).toBeNull();
    expect(result.current.transcript).toBe('');
  });

  it('浏览器不支持录音时给出 unsupported', async () => {
    (globalThis as unknown as { MediaRecorder: unknown }).MediaRecorder = undefined;
    const mediaDevices = { getUserMedia: vi.fn() } as unknown as MediaDevices;

    const { result } = renderHook(() => useVoiceRecorder({ mediaDevices }));
    await act(async () => {
      await result.current.start();
    });

    expect(result.current.error?.code).toBe('unsupported');
  });
});
