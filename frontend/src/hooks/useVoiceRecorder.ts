import { useCallback, useEffect, useRef, useState } from 'react';
import { DEFAULT_SAMPLE_RATE, recognizeSpeech } from '@/services/voiceService';

/**
 * 录音与转写（T-GB-FE-02 的前端部分）。
 *
 * 采用 `MediaRecorder` 采集 → `decodeAudioData` 解码 → 重采样到 16kHz
 * 单声道 PCM → base64，而不是 ScriptProcessor/Worklet：后两者要么已废弃、
 * 要么需要额外打包 worklet 资源，而解码路径在所有现代浏览器一致。
 *
 * 容错（任务要求"识别失败给出可编辑的修正文本"）：
 * - 麦克风权限被拒 / 无可用设备 → 明确提示，不阻塞手动输入；
 * - 录音时长过短 → 直接提示重录（避免把静音送去识别）；
 * - 转写失败 → 保留失败原因，页面可重试或手工输入。
 */

export type RecorderState = 'idle' | 'requesting' | 'recording' | 'transcribing' | 'error';

export interface RecorderError {
  code: 'permission_denied' | 'no_device' | 'too_short' | 'unsupported' | 'transcribe_failed';
  message: string;
}

export interface UseVoiceRecorderOptions {
  /** 最短录音时长（毫秒），低于此值视为误触 */
  minDurationMs?: number;
  /** 最长录音时长（毫秒），到点自动结束，避免长时间占用麦克风 */
  maxDurationMs?: number;
  /** 转写实现，默认调用后端；测试可注入 */
  transcribe?: (audioBase64: string, sampleRate: number) => Promise<string>;
  /**
   * 转写完成回调（在事件回调里触发，便于调用方直接处理，
   * 无需再监听 transcript 状态做 effect 同步）。
   */
  onTranscribed?: (text: string) => void;
  /** 依赖注入点（测试用） */
  mediaDevices?: MediaDevices;
  audioContextFactory?: () => AudioContext;
}

export interface UseVoiceRecorderResult {
  state: RecorderState;
  /** 实时音量（0-1），用于波形/呼吸动效 */
  level: number;
  transcript: string;
  error: RecorderError | null;
  elapsedMs: number;
  isRecording: boolean;
  start: () => Promise<void>;
  stop: () => Promise<void>;
  reset: () => void;
}

/** Float32 [-1,1] → 16bit PCM → base64（可单测的纯函数）。 */
export function encodePcm16Base64(samples: Float32Array): string {
  const buffer = new ArrayBuffer(samples.length * 2);
  const view = new DataView(buffer);
  for (let i = 0; i < samples.length; i += 1) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(i * 2, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
  }
  let binary = '';
  const bytes = new Uint8Array(buffer);
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

/** 线性插值重采样（语音识别对插值质量不敏感，保持无第三方依赖）。 */
export function resampleLinear(input: Float32Array, fromRate: number, toRate: number): Float32Array {
  if (fromRate === toRate || input.length === 0) return input;
  const ratio = fromRate / toRate;
  const outputLength = Math.max(1, Math.round(input.length / ratio));
  const output = new Float32Array(outputLength);
  for (let i = 0; i < outputLength; i += 1) {
    const position = i * ratio;
    const lower = Math.floor(position);
    const upper = Math.min(lower + 1, input.length - 1);
    const weight = position - lower;
    output[i] = input[lower] * (1 - weight) + input[upper] * weight;
  }
  return output;
}

/** 多声道混合为单声道（语音识别只接受单声道）。 */
export function mixToMono(channels: Float32Array[]): Float32Array {
  if (channels.length === 0) return new Float32Array(0);
  if (channels.length === 1) return channels[0];
  const length = Math.max(...channels.map((channel) => channel.length));
  const mixed = new Float32Array(length);
  for (let i = 0; i < length; i += 1) {
    let sum = 0;
    for (const channel of channels) {
      sum += channel[i] ?? 0;
    }
    mixed[i] = sum / channels.length;
  }
  return mixed;
}

function toRecorderError(err: unknown): RecorderError {
  const name = (err as { name?: string })?.name ?? '';
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return { code: 'permission_denied', message: '麦克风权限被拒绝，请在浏览器设置中允许后重试' };
  }
  if (name === 'NotFoundError' || name === 'OverconstrainedError') {
    return { code: 'no_device', message: '未检测到可用麦克风，请检查设备连接' };
  }
  return { code: 'unsupported', message: '当前浏览器不支持录音，请改用手工输入' };
}

export function useVoiceRecorder(options: UseVoiceRecorderOptions = {}): UseVoiceRecorderResult {
  const {
    minDurationMs = 300,
    maxDurationMs = 30_000,
    transcribe,
    onTranscribed,
    mediaDevices,
    audioContextFactory,
  } = options;

  const [state, setState] = useState<RecorderState>('idle');
  const [level, setLevel] = useState(0);
  const [transcript, setTranscript] = useState('');
  const [error, setError] = useState<RecorderError | null>(null);
  const [elapsedMs, setElapsedMs] = useState(0);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const startedAtRef = useRef<number>(0);
  const rafRef = useRef<number | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const contextRef = useRef<AudioContext | null>(null);

  const cleanup = useCallback(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    if (timerRef.current !== null) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    analyserRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    recorderRef.current = null;
    void contextRef.current?.close?.();
    contextRef.current = null;
    setLevel(0);
  }, []);

  useEffect(() => cleanup, [cleanup]);

  const monitorLevel = useCallback(() => {
    const analyser = analyserRef.current;
    if (!analyser) return;
    const data = new Uint8Array(analyser.fftSize);
    const tick = () => {
      analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (let i = 0; i < data.length; i += 1) {
        const deviation = (data[i] - 128) / 128;
        sum += deviation * deviation;
      }
      setLevel(Math.min(1, Math.sqrt(sum / data.length) * 4));
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
  }, []);

  const decodeToPcmBase64 = useCallback(
    async (blob: Blob, targetRate: number): Promise<string> => {
      const factory =
        audioContextFactory ??
        (() => {
          const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
          if (!Ctor) throw new Error('AudioContext unsupported');
          return new Ctor();
        });
      const context = factory();
      const arrayBuffer = await blob.arrayBuffer();
      const decoded = await context.decodeAudioData(arrayBuffer);
      const channels: Float32Array[] = [];
      for (let i = 0; i < decoded.numberOfChannels; i += 1) {
        channels.push(decoded.getChannelData(i));
      }
      const mono = mixToMono(channels);
      const resampled = resampleLinear(mono, decoded.sampleRate, targetRate);
      return encodePcm16Base64(resampled);
    },
    [audioContextFactory]
  );

  const start = useCallback(async () => {
    if (state === 'recording' || state === 'requesting') return;
    setError(null);
    setTranscript('');
    setElapsedMs(0);
    setState('requesting');

    try {
      const devices = mediaDevices ?? navigator.mediaDevices;
      if (!devices?.getUserMedia || typeof MediaRecorder === 'undefined') {
        throw Object.assign(new Error('unsupported'), { name: 'NotSupportedError' });
      }
      const stream = await devices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const context = (() => {
        const factory =
          audioContextFactory ??
          (() => {
            const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
            if (!Ctor) throw new Error('AudioContext unsupported');
            return new Ctor();
          });
        return factory();
      })();
      contextRef.current = context;
      const source = context.createMediaStreamSource(stream);
      const analyser = context.createAnalyser();
      analyser.fftSize = 1024;
      source.connect(analyser);
      analyserRef.current = analyser;
      monitorLevel();

      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.start();
      recorderRef.current = recorder;
      startedAtRef.current = Date.now();
      setState('recording');

      timerRef.current = setInterval(() => {
        setElapsedMs(Date.now() - startedAtRef.current);
      }, 200);
    } catch (err) {
      cleanup();
      setError(toRecorderError(err));
      setState('error');
    }
  }, [audioContextFactory, cleanup, mediaDevices, monitorLevel, state]);

  const stop = useCallback(async () => {
    const recorder = recorderRef.current;
    if (!recorder || recorder.state === 'inactive') {
      cleanup();
      setState('idle');
      return;
    }

    const durationMs = Date.now() - startedAtRef.current;
    const blob: Blob = await new Promise((resolve) => {
      recorder.onstop = () => resolve(new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' }));
      recorder.stop();
    });

    cleanup();
    chunksRef.current = [];

    if (durationMs < minDurationMs) {
      setError({ code: 'too_short', message: '录音时间过短，请按住麦克风说完指令' });
      setState('error');
      return;
    }

    setState('transcribing');
    try {
      const audioBase64 = await decodeToPcmBase64(blob, DEFAULT_SAMPLE_RATE);
      const text = transcribe
        ? await transcribe(audioBase64, DEFAULT_SAMPLE_RATE)
        : (await recognizeSpeech(audioBase64, { sampleRate: DEFAULT_SAMPLE_RATE })).text;
      setTranscript(text ?? '');
      setState('idle');
      if (text) {
        onTranscribed?.(text);
      }
    } catch {
      setError({ code: 'transcribe_failed', message: '语音识别失败，可重试或手工输入指令' });
      setState('error');
    }
  }, [cleanup, decodeToPcmBase64, minDurationMs, onTranscribed, transcribe]);

  const reset = useCallback(() => {
    cleanup();
    setTranscript('');
    setError(null);
    setElapsedMs(0);
    setState('idle');
  }, [cleanup]);

  // 超时自动结束（避免医生忘记停止而长期占用麦克风）
  useEffect(() => {
    if (state !== 'recording') return;
    if (elapsedMs < maxDurationMs) return;
    void stop();
  }, [elapsedMs, maxDurationMs, state, stop]);

  return {
    state,
    level,
    transcript,
    error,
    elapsedMs,
    isRecording: state === 'recording',
    start,
    stop,
    reset,
  };
}
