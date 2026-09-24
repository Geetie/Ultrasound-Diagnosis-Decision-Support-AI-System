import { useCallback, useEffect } from 'react';
import { Button, Tooltip, message } from 'antd';
import { AudioOutlined, LoadingOutlined, StopOutlined } from '@ant-design/icons';
import { tokens } from '@/styles/tokens';
import { useVoiceRecorder } from '@/hooks/useVoiceRecorder';
import type { UseVoiceRecorderOptions } from '@/hooks/useVoiceRecorder';

export interface VoiceDictationButtonProps {
  /** 转写完成回调（文本可继续编辑） */
  onTranscript: (text: string) => void;
  label?: string;
  disabled?: boolean;
  block?: boolean;
  /** 触摸屏适配时由调用方放大（与 antd Button 尺寸一致） */
  size?: 'small' | 'middle' | 'large';
  /** 依赖注入（测试用），透传给 useVoiceRecorder */
  recorderOptions?: UseVoiceRecorderOptions;
}

/**
 * 语音听写按钮（T-GB-FE-05：反馈页语音输入）。
 *
 * 与 VoiceControl 的区别：听写只是把语音变成**可编辑文本**，不解析指令、
 * 不自动执行，因此不需要确认弹层。识别失败时保留重试与手工输入两条路径。
 */
export default function VoiceDictationButton({
  onTranscript,
  label = '语音填写',
  disabled = false,
  block = false,
  size = 'middle',
  recorderOptions,
}: VoiceDictationButtonProps) {
  // 转写完成即回调（避免监听状态做 effect 同步导致多余渲染）
  const recorder = useVoiceRecorder({ ...recorderOptions, onTranscribed: onTranscript });

  useEffect(() => {
    if (recorder.error) {
      message.error(recorder.error.message);
    }
  }, [recorder.error]);

  const handleClick = useCallback(async () => {
    if (recorder.isRecording) {
      await recorder.stop();
      return;
    }
    if (recorder.state === 'transcribing' || recorder.state === 'requesting') return;
    await recorder.start();
  }, [recorder]);

  const busy = recorder.state === 'transcribing' || recorder.state === 'requesting';
  const text = recorder.isRecording
    ? `结束录音 ${(recorder.elapsedMs / 1000).toFixed(1)}s`
    : busy
      ? '识别中...'
      : label;

  return (
    <Tooltip title={recorder.isRecording ? '点击结束并识别' : '点击开始口述'}>
      <Button
        icon={
          busy ? <LoadingOutlined /> : recorder.isRecording ? <StopOutlined /> : <AudioOutlined />
        }
        onClick={() => void handleClick()}
        disabled={disabled}
        block={block}
        size={size}
        danger={recorder.isRecording}
        data-testid="voice-dictation-button"
        style={{
          borderColor: recorder.isRecording ? tokens.color.voice.recording : tokens.color.border.accent,
          color: recorder.isRecording ? tokens.color.voice.recording : tokens.color.accent.primary,
          background: recorder.isRecording ? `${tokens.color.voice.recording}14` : tokens.color.accent.primaryAlpha20,
        }}
      >
        {text}
      </Button>
    </Tooltip>
  );
}
