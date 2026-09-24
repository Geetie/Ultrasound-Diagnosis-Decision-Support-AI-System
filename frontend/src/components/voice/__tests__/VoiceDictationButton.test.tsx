import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import VoiceDictationButton from '../VoiceDictationButton';

class FakeAnalyser {
  fftSize = 1024;
  getByteTimeDomainData(array: Uint8Array) {
    array.fill(130);
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
      getChannelData: () => new Float32Array(4800),
    } as unknown as AudioBuffer;
  }
  close = vi.fn().mockResolvedValue(undefined);
}

class FakeMediaRecorder {
  state: 'inactive' | 'recording' = 'inactive';
  mimeType = 'audio/webm';
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  start() {
    this.state = 'recording';
    this.ondataavailable?.({ data: new Blob(['x']) });
  }
  stop() {
    this.state = 'inactive';
    this.onstop?.();
  }
}

describe('VoiceDictationButton（T-GB-FE-05 语音填写）', () => {
  const originalMediaRecorder = globalThis.MediaRecorder;

  beforeEach(() => {
    (globalThis as unknown as { MediaRecorder: unknown }).MediaRecorder = FakeMediaRecorder;
  });

  afterEach(() => {
    (globalThis as unknown as { MediaRecorder: unknown }).MediaRecorder = originalMediaRecorder;
    vi.restoreAllMocks();
  });

  it('录音结束后把转写文本交给调用方（文本保持可编辑）', async () => {
    const user = userEvent.setup();
    const onTranscript = vi.fn();
    const mediaDevices = {
      getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: vi.fn() }] }),
    } as unknown as MediaDevices;

    render(
      <VoiceDictationButton
        onTranscript={onTranscript}
        label="开始口述"
        recorderOptions={{
          minDurationMs: 0,
          mediaDevices,
          audioContextFactory: () => new FakeAudioContext() as unknown as AudioContext,
          transcribe: vi.fn().mockResolvedValue('结节边界不清'),
        }}
      />
    );

    const button = screen.getByRole('button', { name: /开始口述/ });
    await user.click(button);
    await waitFor(() => expect(button.textContent).toContain('结束录音'));

    await user.click(button);
    await waitFor(() => expect(onTranscript).toHaveBeenCalledWith('结节边界不清'));
  });

  it('录音失败时给出可读提示（容错：可重试或手工输入）', async () => {
    const user = userEvent.setup();
    const mediaDevices = {
      getUserMedia: vi
        .fn()
        .mockRejectedValue(Object.assign(new Error('denied'), { name: 'NotAllowedError' })),
    } as unknown as MediaDevices;

    render(
      <VoiceDictationButton
        onTranscript={vi.fn()}
        label="开始口述"
        recorderOptions={{ mediaDevices }}
      />
    );

    await user.click(screen.getByRole('button', { name: /开始口述/ }));

    await waitFor(() =>
      expect(document.body.textContent).toContain('麦克风权限被拒绝')
    );
  });
});
