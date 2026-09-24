import { useState } from 'react';
import { Card, Form, Input, Select, Button, Typography, message } from 'antd';
import { getApiErrorMessage } from '@/utils/apiError';
import { PlusOutlined } from '@ant-design/icons';
import { useSession } from '@/hooks/useSession';
import { useNavigate } from 'react-router-dom';
import type { OrganType } from '@/types/session';

const { Title } = Typography;

interface SessionFormValues {
  patient_id: string;
  device_id: string;
  target_organs: OrganType[];
}

export default function SessionCreatePage() {
  const [loading, setLoading] = useState(false);
  const { createSession } = useSession();
  const navigate = useNavigate();
  const [form] = Form.useForm<SessionFormValues>();

  const handleFinish = async (values: SessionFormValues) => {
    setLoading(true);
    try {
      await createSession(values.patient_id, values.device_id, values.target_organs);
      message.success('检查会话创建成功');
      navigate('/');
    } catch (err: unknown) {
      message.error(getApiErrorMessage(err, '创建会话失败'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="h-full flex items-center justify-center animate-fade-in">
      <Card
        style={{
          width: 480,
          background: '#111827',
          border: '1px solid #1e293b',
        }}
      >
        <Title
          level={4}
          style={{
            fontFamily: "'Orbitron', sans-serif",
            color: '#00d4ff',
            letterSpacing: '0.05em',
            marginBottom: 24,
          }}
        >
          NEW SESSION
        </Title>
        <Form form={form} layout="vertical" onFinish={handleFinish}>
          <Form.Item label="患者ID" name="patient_id" rules={[{ required: true, message: '请输入患者ID' }]}>
            <Input placeholder="输入患者ID" />
          </Form.Item>
          <Form.Item label="设备ID" name="device_id" rules={[{ required: true, message: '请输入设备ID' }]}>
            <Input placeholder="输入设备ID" />
          </Form.Item>
          <Form.Item label="目标器官" name="target_organs" rules={[{ required: true, message: '请选择至少一个目标器官' }]}>
            <Select
              mode="multiple"
              placeholder="选择目标器官"
              options={[
                { value: 'thyroid', label: '甲状腺' },
                { value: 'liver', label: '肝脏' },
                { value: 'cardiac', label: '心脏' },
              ]}
            />
          </Form.Item>
          <Form.Item>
            <Button
              type="primary"
              htmlType="submit"
              icon={<PlusOutlined />}
              loading={loading}
              block
            >
              创建检查会话
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}
