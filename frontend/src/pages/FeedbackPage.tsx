import { useCallback, useEffect, useMemo, useState } from 'react';
import { Card, Form, Input, Button, Typography, Radio, message, Select, Space, Progress } from 'antd';
import { SendOutlined } from '@ant-design/icons';
import apiClient from '@/services/api';
import { useSessionStore } from '@/stores/sessionStore';
import { tokens } from '@/styles/tokens';
import { useTouchAdaptation } from '@/hooks/useTouchAdaptation';
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
  const storeSessionId = useSessionStore((s) => s.sessionId);
  // A.62 批 3：会话 id 两级取 —— ① store（医生从工作区点侧边栏「反馈提交」进来时在）
  // ② URL 的 `?session_id=`（整页刷新/深链进来时 URL 才有）。
  // 为什么要兜底：反馈**必须挂在会话的判读上**，会话丢了就必然提交失败；
  // 而"整页加载后 store 清空"是真实场景（医生刷新页面、或从别处带链接进来）。
  const urlSessionId = useMemo(
    () => new URLSearchParams(window.location.search).get('session_id'),
    [],
  );
  const sessionId = storeSessionId || urlSessionId;
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
    // 没有会话就没有判读可挂 —— 直接说清并**不发请求**（发出去只会拿到 404「判读请求不存在」，
    // 医生看到的是一个莫名其妙的错误码；这是 A.62 批 3 真机联调踩到的形态）。
    if (!sessionId) {
      message.error('未关联会话：请从左侧「反馈提交」进入，或先在会话中完成一次判读');
      return;
    }
    setLoading(true);
    try {
      const payload: FeedbackRequest = {
        // A.62 批 3（医生视角联调踩到）：**不再自造 id**。
        // 原先用 `generateFeedbackId()`（`fb_…`）填 request_id，而服务端要求它必须是**真实判读 id**
        // → 真机上医生一点「提交反馈」就拿 404 `E1002 判读请求不存在`（实测复现）。留空即表示
        // 「按本会话关联最近一次判读」，服务端会在响应里回传**实际挂载**的 id，下面如实显示给医生。
        request_id: '',
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
      const resp = await apiClient.post<{ request_id?: string }>('/feedback', payload);
      const attached = resp.data?.request_id;
      // 把「挂到哪一次判读」显示出来：医生能核对，评测集的"正确口径"也才有据可查
      message.success(attached ? `反馈提交成功（已关联判读 ${attached}）` : '反馈提交成功');
      form.resetFields();
    } catch (err) {
      const raw = getApiErrorMessage(err, '反馈提交失败');
      // 会话里还没有判读结果时，给可行动的指引，而不是丢一个后端错误串
      message.error(
        raw.includes('判读请求不存在')
          ? '本会话还没有判读结果，请先完成一次判读（上传影像并出结论）再提交反馈'
          : `反馈提交失败：${raw}`,
      );
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
