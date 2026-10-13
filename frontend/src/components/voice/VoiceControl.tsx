import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button, Tooltip, message, Typography } from 'antd';
import { AudioOutlined, LoadingOutlined, StopOutlined } from '@ant-design/icons';
import { tokens } from '@/styles/tokens';
import { useTouchAdaptation } from '@/hooks/useTouchAdaptation';
import { useUiStore } from '@/stores/uiStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useVoiceRecorder } from '@/hooks/useVoiceRecorder';
import { parseVoiceCommand } from '@/services/voiceCommand';
import type { ParsedVoiceCommand } from '@/services/voiceCommand';
import type { VoiceConfirmResult } from '@/services/voiceService';
import VoiceConfirmDialog from './VoiceConfirmDialog';

const { Text } = Typography;

export interface VoiceControlProps {
  /** 指令执行完成（例如发起诊断后跳转诊断页） */
  onExecuted?: (result: VoiceConfirmResult) => void;
  /**
   * 当前会话 id 的**显式覆盖值**（A.58：主流程页面级挂载用）。
   *
   * 不传 = 回落到全局 `sessionStore.sessionId`（遗留 AppLayout 页面的既有行为）。
   *
   * 为什么要有这个覆盖：主流程四页（工作区/工作列表/建会话/设置）**不套 AppLayout**，
   * 它们的会话来自 URL（`?session_id=`）而不是全局 store。若为了语音去往全局 store
   * 写 `sessionId/deviceId`，会**顺手触发别的消费者**——`AppLayout` 的
   * `useRealtimeConnection` 在 `sessionId+deviceId` 齐备时会去连 nav WebSocket
   * （真机 p26 的 T0-err 实测抓到：隧道下必然连接失败并报 console error）。
   * 显式传参只影响语音确认弹层的可执行性，不动全局状态。
   */
  sessionId?: string | null;
}

/**
 * 常驻语音控件（T-GB-FE-02）。
 *
 * 交互：右下角常驻麦克风按钮 → 录音（呼吸式脉冲 + 实时音量）→ 转写 →
 * 指令确认弹层（可编辑、必须确认）→ 执行。
 *
 * 无需助手式"自由对话"：临床场景下每个指令都要经医生确认，因此这里只做
 * "采集 + 解析 + 确认"，不替医生做决定。
 */
export default function VoiceControl({ onExecuted, sessionId: sessionIdProp }: VoiceControlProps) {
  const touch = useTouchAdaptation();
  const storeSessionId = useSessionStore((s) => s.sessionId);
  // 显式传入优先（主流程页面级挂载）；未传时回落全局 store（AppLayout 既有用法）
  const sessionId = sessionIdProp ?? storeSessionId;
  const isDarkRoomMode = useUiStore((s) => s.isDarkRoomMode);
  const [pending, setPending] = useState<ParsedVoiceCommand | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);

  // 转写完成 → 解析 → 打开确认层。解析失败时也开，医生可手工输入再解析。
  const handleTranscribed = useCallback((text: string) => {
    const parsed = parseVoiceCommand(text);
    if (!parsed) {
      message.warning('未能从语音中解析出指令，可手工输入文本后重新解析');
      setPending(null);
      setDialogOpen(true);
      return;
    }
    setPending(parsed);
    setDialogOpen(true);
  }, []);

  const recorder = useVoiceRecorder({ onTranscribed: handleTranscribed });

  const size = touch.isTouch ? tokens.touch.hitTargetLarge : 56;
  const edge = touch.isTouch ? tokens.touch.edgeOffsetLarge : tokens.touch.edgeOffset;

  const accentColor = recorder.isRecording
    ? tokens.color.voice.recording
    : recorder.state === 'transcribing'
      ? tokens.color.voice.processing
      : tokens.color.voice.idle;

  const tooltip = useMemo(() => {
    if (recorder.isRecording) return '录音中，点击结束';
    if (recorder.state === 'transcribing') return '正在识别语音...';
    if (recorder.state === 'requesting') return '正在请求麦克风权限...';
    return '语音指令（点击开始）';
  }, [recorder.isRecording, recorder.state]);

  // 录音异常：明确提示并给出"手工输入"的出口（容错）
  useEffect(() => {
    if (recorder.state !== 'error' || !recorder.error) return;
    message.error(recorder.error.message);
  }, [recorder.error, recorder.state]);

  const handleClick = useCallback(async () => {
    if (recorder.isRecording) {
      await recorder.stop();
      return;
    }
    if (recorder.state === 'transcribing' || recorder.state === 'requesting') return;
    await recorder.start();
  }, [recorder]);

  // 手工输入入口：打开确认层并清空解析结果，由医生直接在文本框中输入
  const handleManualInput = useCallback(() => {
    recorder.reset();
    setPending(null);
    setDialogOpen(true);
  }, [recorder]);

  const closeDialog = useCallback(() => {
    setDialogOpen(false);
    recorder.reset();
  }, [recorder]);

  const levelScale = 1 + Math.min(0.35, recorder.level * 0.5);

  return (
    <>
      <div
        style={{
          position: 'fixed',
          right: edge,
          bottom: edge + (touch.isTouch ? 12 : 0),
          zIndex: 1100,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: tokens.spacing.sm,
        }}
      >
        {recorder.isRecording && (
          <Text
            style={{
              color: tokens.color.voice.recording,
              fontSize: 11,
              fontFamily: tokens.font.mono,
              letterSpacing: '0.05em',
              background: `${tokens.color.bg.secondary}d9`,
              padding: '2px 8px',
              borderRadius: tokens.radius.sm,
              border: `1px solid ${tokens.color.voice.recording}55`,
            }}
          >
            REC {(recorder.elapsedMs / 1000).toFixed(1)}s
          </Text>
        )}

        <div style={{ position: 'relative', width: size, height: size }}>
          {recorder.isRecording && (
            <>
              <span
                className="animate-voice-ring"
                style={{
                  position: 'absolute',
                  inset: 0,
                  borderRadius: '50%',
                  border: `2px solid ${tokens.color.voice.recording}`,
                }}
              />
              <span
                className="animate-voice-ring"
                style={{
                  position: 'absolute',
                  inset: 0,
                  borderRadius: '50%',
                  border: `2px solid ${tokens.color.voice.recording}`,
                  animationDelay: '600ms',
                }}
              />
            </>
          )}

          <Tooltip title={tooltip} placement="left">
            <Button
              type="primary"
              shape="circle"
              aria-label={tooltip}
              data-testid="voice-control-button"
              className={recorder.isRecording ? 'animate-voice-breathe' : undefined}
              onClick={() => void handleClick()}
              onContextMenu={(event) => {
                // 右键/长按 → 手工输入（触摸屏上单手可达的备用入口）
                event.preventDefault();
                handleManualInput();
              }}
              icon={
                recorder.state === 'transcribing' || recorder.state === 'requesting' ? (
                  <LoadingOutlined />
                ) : recorder.isRecording ? (
                  <StopOutlined />
                ) : (
                  <AudioOutlined />
                )
              }
              style={{
                width: size,
                height: size,
                background: `${accentColor}26`,
                border: `1.5px solid ${accentColor}`,
                color: accentColor,
                boxShadow: recorder.isRecording
                  ? `0 0 18px ${tokens.color.voice.glowRecording}`
                  : `0 0 14px ${tokens.color.voice.glow}`,
                transform: recorder.isRecording ? `scale(${levelScale})` : 'scale(1)',
                transition: 'transform 120ms ease-out, box-shadow 200ms ease-out',
              }}
            />
          </Tooltip>
        </div>

        {!touch.isTouch && (
          <Text
            style={{
              color: isDarkRoomMode ? tokens.color.darkroom.textHigh : tokens.color.text.muted,
              fontSize: 10,
              fontFamily: tokens.font.mono,
            }}
          >
            语音
          </Text>
        )}
      </div>

      <VoiceConfirmDialog
        // 每次新的识别结果/手工输入都重挂载，使弹层内部状态从新命令初始化
        key={pending?.transcript ?? 'manual'}
        open={dialogOpen}
        command={pending}
        sessionId={sessionId}
        onClose={closeDialog}
        onExecuted={onExecuted}
      />
    </>
  );
}
