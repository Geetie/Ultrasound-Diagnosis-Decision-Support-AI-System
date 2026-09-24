import { useCallback, useMemo, useState } from 'react';
import { Modal, Typography, Input, Select, Radio, Button, Tag, Space, Alert, message } from 'antd';
import { CheckOutlined, CloseOutlined, ReloadOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { tokens } from '@/styles/tokens';
import { useTouchAdaptation } from '@/hooks/useTouchAdaptation';
import {
  COMMAND_LABELS,
  GRADE_LABELS,
  NAV_ACTION_LABELS,
  ORGAN_LABELS,
  isCommandExecutable,
  parseVoiceCommand,
} from '@/services/voiceCommand';
import type {
  GradeTarget,
  NavAction,
  ParsedVoiceCommand,
  TargetOrgan,
} from '@/services/voiceCommand';
import { confirmVoiceCommand } from '@/services/voiceService';
import type { VoiceConfirmResult } from '@/services/voiceService';

const { Text } = Typography;
const { TextArea } = Input;

export interface VoiceConfirmDialogProps {
  open: boolean;
  command: ParsedVoiceCommand | null;
  sessionId?: string | null;
  onClose: () => void;
  /** 指令已执行（含发起诊断返回 request_id） */
  onExecuted?: (result: VoiceConfirmResult) => void;
}

/**
 * 语音指令确认层（T-GB-FE-02）。
 *
 * 职责边界：**只做确认，不猜测**。解析出的指令、参数与识别文本全部可编辑；
 * 参数缺失时禁用执行按钮并提示补全；取消会显式上报 `confirmed=false`
 * （后端记为 voice_confirm_rejected，属安全审计信号）。
 */
export default function VoiceConfirmDialog({
  open,
  command,
  sessionId,
  onClose,
  onExecuted,
}: VoiceConfirmDialogProps) {
  const touch = useTouchAdaptation();
  // 初始值直接取自 command；父组件以 key 重挂载本弹层，新命令自然会重置状态
  const [transcript, setTranscript] = useState(() => command?.transcript ?? '');
  const [organ, setOrgan] = useState<TargetOrgan | undefined>(() => command?.organ);
  const [grade, setGrade] = useState<GradeTarget | undefined>(() => command?.gradeTarget);
  const [navAction, setNavAction] = useState<NavAction | undefined>(() => command?.navAction);
  const [submitting, setSubmitting] = useState(false);

  const effective = useMemo<ParsedVoiceCommand | null>(() => {
    if (!command) return null;
    return {
      ...command,
      organ,
      gradeTarget: grade,
      navAction,
      needsClarification:
        command.commandType === 'diagnosis_trigger' ? !organ : command.needsClarification && !grade && !navAction,
    };
  }, [command, grade, navAction, organ]);

  const executable = isCommandExecutable(effective) && Boolean(sessionId);

  const handleReparse = useCallback(() => {
    const parsed = parseVoiceCommand(transcript);
    if (!parsed) {
      message.warning('未能从当前文本中解析出指令，请手工选择参数');
      return;
    }
    setOrgan(parsed.organ);
    setGrade(parsed.gradeTarget);
    setNavAction(parsed.navAction);
    message.success('已按当前文本重新解析');
  }, [transcript]);

  const submit = useCallback(
    async (confirmed: boolean) => {
      if (!effective || !sessionId) return;
      setSubmitting(true);
      try {
        const result = await confirmVoiceCommand({
          sessionId,
          transcript,
          commandType: effective.commandType,
          confirmed,
          organ,
          gradeTarget: grade,
          navAction,
        });
        if (confirmed) {
          message.success(`指令已执行：${result.action_taken ?? '完成'}`);
          onExecuted?.(result);
        } else {
          message.info('已取消本次语音指令');
        }
        onClose();
      } catch (err) {
        const detail = (err as { response?: { data?: { error_message?: string } } })?.response?.data?.error_message;
        message.error(detail || '指令提交失败，请重试');
      } finally {
        setSubmitting(false);
      }
    },
    [effective, grade, navAction, onClose, onExecuted, organ, sessionId, transcript]
  );

  const renderParameters = () => {
    if (!effective) return null;
    if (effective.commandType === 'diagnosis_trigger') {
      return (
        <Space orientation="vertical" size={4} style={{ width: '100%' }}>
          <Text style={{ color: tokens.color.text.secondary, fontSize: 12 }}>目标器官</Text>
          <Select<TargetOrgan>
            value={organ}
            placeholder="请选择要检查的器官"
            onChange={setOrgan}
            style={{ width: '100%' }}
            options={(Object.keys(ORGAN_LABELS) as TargetOrgan[]).map((key) => ({
              value: key,
              label: ORGAN_LABELS[key],
            }))}
          />
        </Space>
      );
    }
    if (effective.commandType === 'grade_change') {
      return (
        <Space orientation="vertical" size={4} style={{ width: '100%' }}>
          <Text style={{ color: tokens.color.text.secondary, fontSize: 12 }}>目标档位</Text>
          <Radio.Group value={grade} onChange={(event) => setGrade(event.target.value)}>
            {(Object.keys(GRADE_LABELS) as GradeTarget[]).map((key) => (
              <Radio key={key} value={key}>
                {GRADE_LABELS[key]}
              </Radio>
            ))}
          </Radio.Group>
        </Space>
      );
    }
    return (
      <Space direction="vertical" size={4} style={{ width: '100%' }}>
        <Text style={{ color: tokens.color.text.secondary, fontSize: 12 }}>导航动作</Text>
        <Select<NavAction>
          value={navAction}
          placeholder="请选择导航动作"
          onChange={setNavAction}
          style={{ width: '100%' }}
          options={(Object.keys(NAV_ACTION_LABELS) as NavAction[]).map((key) => ({
            value: key,
            label: NAV_ACTION_LABELS[key],
          }))}
        />
      </Space>
    );
  };

  return (
    <Modal
      open={open}
      title={
        <Space size={8}>
          <ThunderboltOutlined style={{ color: tokens.color.voice.idle }} />
          <span style={{ fontFamily: tokens.font.display, letterSpacing: '0.05em' }}>语音指令确认</span>
        </Space>
      }
      onCancel={() => void submit(false)}
      mask={{ closable: false }}
      destroyOnHidden
      width={touch.isTouch ? 560 : 480}
      footer={[
        <Button
          key="reject"
          icon={<CloseOutlined />}
          onClick={() => void submit(false)}
          loading={submitting}
          size={touch.isTouch ? 'large' : 'middle'}
        >
          取消指令
        </Button>,
        <Button
          key="confirm"
          type="primary"
          icon={<CheckOutlined />}
          onClick={() => void submit(true)}
          disabled={!executable}
          loading={submitting}
          size={touch.isTouch ? 'large' : 'middle'}
        >
          确认执行
        </Button>,
      ]}
    >
      <Space orientation="vertical" size={12} style={{ width: '100%' }}>
        {effective?.needsClarification && (
          <Alert
            type="warning"
            showIcon
            title="指令信息不完整"
            description="为避免误执行，请先补全下方参数再确认。"
          />
        )}

        <div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <Text style={{ color: tokens.color.text.secondary, fontSize: 12 }}>识别文本（可编辑）</Text>
            <Button type="link" size="small" icon={<ReloadOutlined />} onClick={handleReparse}>
              重新解析
            </Button>
          </div>
          <TextArea
            value={transcript}
            onChange={(event) => setTranscript(event.target.value)}
            autoSize={{ minRows: 2, maxRows: 4 }}
            placeholder="未识别到内容时可直接输入指令文本"
          />
        </div>

        <Space size={8} wrap>
          <Tag color="cyan" style={{ fontFamily: tokens.font.mono }}>
            {effective ? COMMAND_LABELS[effective.commandType] : '未识别'}
          </Tag>
          {effective && (
            <Text style={{ color: tokens.color.text.muted, fontSize: 11, fontFamily: tokens.font.mono }}>
              解析可信度 {(effective.confidence * 100).toFixed(0)}%
              {effective.matchedKeywords.length > 0 ? ` · 命中 ${effective.matchedKeywords.join('、')}` : ''}
            </Text>
          )}
        </Space>

        {renderParameters()}

        {!sessionId && (
          <Text style={{ color: tokens.color.accent.warning, fontSize: 12 }}>
            当前没有活跃检查会话，请先在检查页创建会话
          </Text>
        )}
      </Space>
    </Modal>
  );
}
