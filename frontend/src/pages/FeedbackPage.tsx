import { useCallback, useEffect, useState } from 'react';
import { Card, Form, Input, Button, Typography, Radio, message, Select, Space, Progress } from 'antd';
import { SendOutlined } from '@ant-design/icons';
import apiClient from '@/services/api';
import { useSessionStore } from '@/stores/sessionStore';
import { tokens } from '@/styles/tokens';
import { useTouchAdaptation } from '@/hooks/useTouchAdaptation';
import { generateFeedbackId } from '@/utils/idGenerator';
import { getApiErrorMessage } from '@/utils/apiError';
import VoiceDictationButton from '@/components/voice/VoiceDictationButton';
import { reportWarn } from '@/utils/errorReport';
import type { FeedbackRequest } from '@/types/api';

/** `GET /api/v1/eval-set/stats` 的响应（只读进度；后端契约见 app/api/v1/eval_set.py）。 */
interface EvalSetStats {
  available: boolean;
  reason?: string | null;
  generated_at?: string | null;
  source?: string | null;
  total: number;
  deduped: number;
  undedupable: number;
  skipped_no_image: number;
  patient_count: number;
  patient_id_missing: number;
  dedup_reason: Record<string, number>;
  doctor_labeled: number;
  targets: Record<string, number>;
  progress: Record<string, number>;
}

const { Title, Text } = Typography;
const { TextArea } = Input;

const feedbackTypeOptions = [
  { label: '纠正', value: 'correction' as const },
  { label: '错误报告', value: 'rule_change' as const },
  { label: '补充', value: 'supplement' as const },
];

/** 语音听写的目标字段（医生先选字段，再口述） */
type DictationTarget = 'original_value' | 'corrected_value' | 'reason';

const DICTATION_TARGETS: Array<{ value: DictationTarget; label: string }> = [
  { value: 'original_value', label: '原始值' },
  { value: 'corrected_value', label: '修正值' },
  { value: 'reason', label: '原因' },
];

interface FeedbackFormValues {
  feedback_level: 'L2' | 'L3';
  feedback_type: FeedbackRequest['feedback_type'];
  field: string;
  original_value: string;
  corrected_value: string;
  reason: string;
}

export default function FeedbackPage() {
  const [loading, setLoading] = useState(false);
  const [dictationTarget, setDictationTarget] = useState<DictationTarget>('reason');
  const [form] = Form.useForm<FeedbackFormValues>();
  const sessionId = useSessionStore((s) => s.sessionId);
  const touch = useTouchAdaptation();
  // A.61：评测集进度（只读）。医生看不到「还有多少没采」就只能靠口口相传 —— 进度必须上屏。
  const [evalStats, setEvalStats] = useState<EvalSetStats | null>(null);
  const [evalIssue, setEvalIssue] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    apiClient
      .get<EvalSetStats>('/eval-set/stats')
      .then((resp) => {
        if (alive) setEvalStats(resp.data);
      })
      .catch((err: unknown) => {
        if (!alive) return;
        // 零静默：读取失败要说出来（页面不崩，给可读原因）。
        // 固定加前缀：`getApiErrorMessage` 可能返回后端原文（形如 "boom"），
        // 单独显示它医生不知道是哪块出的问题。
        reportWarn('eval-set-stats', '评测集进度读取失败', err);
        setEvalIssue(`评测集进度读取失败：${getApiErrorMessage(err, '请稍后重试')}`);
      });
    return () => {
      alive = false;
    };
  }, []);

  // T-GB-FE-05：语音转写落到目标字段，文本保持可编辑
  const handleTranscript = useCallback(
    (text: string) => {
      if (!text) return;
      const current = (form.getFieldValue(dictationTarget) as string | undefined) ?? '';
      const merged = current ? `${current}${current.endsWith('。') ? '' : '。'}${text}` : text;
      form.setFieldsValue({ [dictationTarget]: merged } as Partial<FeedbackFormValues>);
      message.success('语音已转写，可直接编辑后提交');
    },
    [dictationTarget, form]
  );

  const handleSubmit = async (values: FeedbackFormValues) => {
    setLoading(true);
    try {
      const payload: FeedbackRequest = {
        request_id: generateFeedbackId(),
        session_id: sessionId || '',
        feedback_type: values.feedback_type,
        feedback_level: values.feedback_level,
        content: {
          field: values.field,
          original_value: values.original_value,
          corrected_value: values.corrected_value,
          reason: values.reason,
        },
      };
      await apiClient.post('/feedback', payload);
      message.success('反馈提交成功');
      form.resetFields();
    } catch (err) {
      message.error(getApiErrorMessage(err, '反馈提交失败'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="h-full overflow-auto animate-fade-in" style={{ padding: '0 48px' }}>
      <Title
        level={4}
        style={{
          fontFamily: tokens.font.display,
          color: tokens.color.accent.secondary,
          letterSpacing: '0.05em',
          marginBottom: 24,
        }}
      >
        FEEDBACK
      </Title>

      <div style={{ maxWidth: 640 }}>
        <Card
          style={{
            background: tokens.color.bg.secondary,
            border: `1px solid ${tokens.color.border.default}`,
          }}
        >
          <div style={{ marginBottom: 20 }}>
            <Text style={{ color: tokens.color.text.secondary, fontSize: 13 }}>
              提交对AI诊断结果的反馈，帮助改进系统准确性
            </Text>
          </div>

          <Form
            form={form}
            layout="vertical"
            onFinish={handleSubmit}
            initialValues={{ feedback_level: 'L2', feedback_type: 'correction' }}
          >
            <div style={{ display: 'flex', gap: 24, marginBottom: 8 }}>
              <Form.Item label="反馈级别" name="feedback_level" style={{ flex: 1, marginBottom: 16 }}>
                <Radio.Group>
                  <Radio value="L2">L2 医师复核</Radio>
                  <Radio value="L3">L3 知识库更新</Radio>
                </Radio.Group>
              </Form.Item>
            </div>

            <Form.Item label="反馈类型" name="feedback_type" style={{ marginBottom: 16 }}>
              <Radio.Group>
                {feedbackTypeOptions.map((opt) => (
                  <Radio key={opt.value} value={opt.value}>
                    {opt.label}
                  </Radio>
                ))}
              </Radio.Group>
            </Form.Item>

            {/* T-GB-FE-05：语音填写（先选字段，再口述；转写文本可编辑） */}
            <div
              data-testid="voice-fill-section"
              style={{
                border: `1px dashed ${tokens.color.border.accent}`,
                borderRadius: tokens.radius.md,
                padding: tokens.spacing.md,
                marginBottom: 16,
                background: tokens.color.accent.primaryAlpha20,
              }}
            >
              <div style={{ marginBottom: tokens.spacing.sm }}>
                <Text style={{ color: tokens.color.text.secondary, fontSize: 13 }}>语音填写</Text>
                <Text style={{ color: tokens.color.text.muted, fontSize: 11, marginLeft: 8 }}>
                  选择字段后口述，转写结果可继续编辑
                </Text>
              </div>
              <Space.Compact block>
                <Select<DictationTarget>
                  value={dictationTarget}
                  onChange={setDictationTarget}
                  style={{ width: 120 }}
                  options={DICTATION_TARGETS}
                  aria-label="语音填写目标字段"
                />
                <VoiceDictationButton
                  onTranscript={handleTranscript}
                  label="开始口述"
                  size={touch.isTouch ? 'large' : 'middle'}
                />
              </Space.Compact>
            </div>

            <Form.Item
              label="字段"
              name="field"
              rules={[{ required: true, message: '请输入字段名称' }]}
              style={{ marginBottom: 16 }}
            >
              <Input placeholder="输入相关字段名称" />
            </Form.Item>

            <Form.Item
              label="原始值"
              name="original_value"
              rules={[{ required: true, message: '请输入原始值' }]}
              style={{ marginBottom: 16 }}
            >
              <TextArea rows={2} placeholder="AI输出的原始内容" />
            </Form.Item>

            <Form.Item
              label="修正值"
              name="corrected_value"
              rules={[{ required: true, message: '请输入修正值' }]}
              style={{ marginBottom: 16 }}
            >
              <TextArea rows={2} placeholder="修正后的正确内容" />
            </Form.Item>

            <Form.Item
              label="原因"
              name="reason"
              rules={[{ required: true, message: '请输入原因' }]}
              style={{ marginBottom: 16 }}
            >
              <TextArea rows={3} placeholder="说明修正原因" />
            </Form.Item>

            <Form.Item style={{ marginBottom: 0 }}>
              <Button type="primary" htmlType="submit" icon={<SendOutlined />} loading={loading} block>
                提交反馈
              </Button>
            </Form.Item>
          </Form>
        </Card>

        {/* A.61：评测集进度（**只读**）—— 19 号计划要求「≥700 例进度条 / ≥50 例医生标注坏例」。
            数据来自 `GET /api/v1/eval-set/stats`（后端只读采集报告，不触发采集）。
            放在表单**之后**：本页主任务是提交反馈，进度是背景信息，不抢主操作。
            缺报告/读取失败都要给可读原因（零静默），而不是显示一个假的 0/700。 */}
        <Card
          data-region="eval-set-progress"
          style={{
            marginTop: 16,
            background: tokens.color.bg.secondary,
            border: `1px solid ${tokens.color.border.default}`,
          }}
        >
          <Text strong style={{ fontSize: 13 }}>
            评测集进度（双引擎）
          </Text>
          {evalIssue ? (
            <div style={{ marginTop: 8 }}>
              <Text style={{ color: tokens.color.text.secondary, fontSize: 12 }}>
                {evalIssue}（不影响反馈提交；进度由后端离线采集产生）
              </Text>
            </div>
          ) : !evalStats ? (
            <div style={{ marginTop: 8 }}>
              <Text style={{ color: tokens.color.text.secondary, fontSize: 12 }}>读取中…</Text>
            </div>
          ) : evalStats.available === false ? (
            <div style={{ marginTop: 8 }}>
              <Text style={{ color: tokens.color.text.secondary, fontSize: 12 }}>
                尚未采集：{evalStats.reason || '无采集报告'}
              </Text>
            </div>
          ) : (
            <div style={{ marginTop: 10, display: 'grid', gap: 8 }}>
              <div>
                <Text style={{ color: tokens.color.text.secondary, fontSize: 12 }}>
                  样本（去重后）：{evalStats.deduped} / {evalStats.targets?.total ?? 0}
                </Text>
                <Progress
                  percent={Math.round((evalStats.progress?.samples ?? 0) * 100)}
                  size="small"
                  strokeColor={tokens.color.accent.secondary}
                />
              </div>
              <div>
                <Text style={{ color: tokens.color.text.secondary, fontSize: 12 }}>
                  医生标注坏例（含正确口径）：{evalStats.doctor_labeled} /{' '}
                  {evalStats.targets?.doctor_labeled ?? 0}
                </Text>
                <Progress
                  percent={Math.round((evalStats.progress?.doctor_labeled ?? 0) * 100)}
                  size="small"
                  strokeColor={tokens.color.accent.secondary}
                />
              </div>
              <Text style={{ color: tokens.color.text.secondary, fontSize: 12 }}>
                判读总数 {evalStats.total} · 无法去重 {evalStats.undedupable} · 无影像跳过{' '}
                {evalStats.skipped_no_image}
                {evalStats.generated_at ? ` · 采集于 ${evalStats.generated_at}` : ''}
              </Text>
              {/* A.62 批 3：**不同患者数**才是「700 例」的真实刻度 —— 样本数可以靠跑探针涨，
                  独立病例涨不了；顺便把"去重是否已降级为按影像"摆到台面上（不藏）。 */}
              <Text style={{ color: tokens.color.text.secondary, fontSize: 12 }}>
                不同患者 {evalStats.patient_count} 人
                {evalStats.dedup_reason && Object.keys(evalStats.dedup_reason).length > 0
                  ? ` · 去重口径 ${Object.entries(evalStats.dedup_reason)
                      .map(([k, v]) => `${k}:${v}`)
                      .join(' ')}`
                  : ''}
                {evalStats.patient_id_missing > 0
                  ? `（${evalStats.patient_id_missing} 条缺患者 ID，去重已降级为按影像）`
                  : ''}
              </Text>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
