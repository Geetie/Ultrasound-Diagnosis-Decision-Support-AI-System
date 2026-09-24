import apiClient from './api';
import type { VoiceCommandType } from './voiceCommand';

/**
 * 语音服务（T-GB-FE-02 / T-GB-FE-05）。
 *
 * - 转写：`POST /api/v1/speech/recognize`（16kHz 单声道 PCM，base64）
 * - 指令确认：`POST /api/v1/voice/confirm`（医生确认后才执行，含拒绝上报）
 */

export interface TerminologyMatch {
  term?: string;
  matched?: string;
  [key: string]: unknown;
}

export interface SpeechRecognitionResult {
  text: string;
  language: string;
  confidence: number;
  latency_ms: number;
  request_id?: string | null;
  terminology_matches: TerminologyMatch[];
}

export interface RecognizeOptions {
  sampleRate?: number;
  language?: string;
  requestId?: string;
  signal?: AbortSignal;
}

export const DEFAULT_SAMPLE_RATE = 16000;

/** 语音转写；失败时抛出，由调用方转成面向医生的提示。 */
export async function recognizeSpeech(
  audioBase64: string,
  options: RecognizeOptions = {}
): Promise<SpeechRecognitionResult> {
  const response = await apiClient.post<SpeechRecognitionResult>(
    '/speech/recognize',
    {
      audio_data: audioBase64,
      sample_rate: options.sampleRate ?? DEFAULT_SAMPLE_RATE,
      language: options.language ?? 'zh',
      request_id: options.requestId ?? null,
    },
    { signal: options.signal, timeout: 20000 }
  );
  return response.data;
}

export interface VoiceConfirmPayload {
  sessionId: string;
  transcript: string;
  commandType: VoiceCommandType;
  confirmed: boolean;
  organ?: 'thyroid' | 'liver' | 'cardiac';
  gradeTarget?: 'realtime' | 'standard' | 'full';
  navAction?: 'start' | 'pause' | 'resume' | 'stop';
  timestamp?: number;
}

export interface VoiceConfirmResult {
  session_id: string;
  executed: boolean;
  action_taken?: string | null;
  request_id?: string | null;
}

/**
 * 提交指令确认结果。
 *
 * `confirmed=false` 也会上报（后端记为 `voice_confirm_rejected` 审计），
 * 因为"医生拒绝了哪条指令"本身是安全审计的重要信号。
 */
export async function confirmVoiceCommand(
  payload: VoiceConfirmPayload
): Promise<VoiceConfirmResult> {
  const response = await apiClient.post<VoiceConfirmResult>('/voice/confirm', {
    session_id: payload.sessionId,
    transcript: payload.transcript,
    command_type: payload.commandType,
    confirmed: payload.confirmed,
    timestamp: payload.timestamp ?? Date.now(),
    organ: payload.organ ?? null,
    grade_target: payload.gradeTarget ?? null,
    nav_action: payload.navAction ?? null,
  });
  return response.data;
}
