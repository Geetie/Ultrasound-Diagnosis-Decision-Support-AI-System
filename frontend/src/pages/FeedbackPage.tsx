import { useState } from 'react';
import { Card, Form, Input, Button, Typography, Radio, message } from 'antd';
import { SendOutlined } from '@ant-design/icons';
import apiClient from '@/services/api';
import { useSessionStore } from '@/stores/sessionStore';
import { tokens } from '@/styles/tokens';
import { generateFeedbackId } from '@/utils/idGenerator';
import type { FeedbackRequest } from '@/types/api';

const { Title, Text } = Typography;
const { TextArea } = Input;

const feedbackTypeOptions = [
  { label: '纠正', value: 'correction' as const },
  { label: '错误报告', value: 'rule_change' as const },
  { label: '补充', value: 'supplement' as const },
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
  const [form] = Form.useForm<FeedbackFormValues>();
  const sessionId = useSessionStore((s) => s.sessionId);

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
    } catch (err: any) {
      message.error(err?.response?.data?.error_message || '反馈提交失败');
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
      </div>
    </div>
  );
}
