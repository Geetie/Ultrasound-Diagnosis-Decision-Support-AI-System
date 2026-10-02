import { useState } from 'react';
import { Card, Form, Input, Button, Typography, message } from 'antd';
import { LoginOutlined } from '@ant-design/icons';
import apiClient from '@/services/api';
import { getApiErrorMessage } from '@/utils/apiError';

const { Title, Text } = Typography;

export default function LoginPage() {
  const [loading, setLoading] = useState(false);
  const [form] = Form.useForm();

  const handleLogin = async (values: { username: string; password: string }) => {
    setLoading(true);
    try {
      const response = await apiClient.post('/auth/token', {
        username: values.username,
        password: values.password,
      });
      const { access_token, refresh_token } = response.data;
      if (access_token) {
        localStorage.setItem('access_token', access_token);
        if (refresh_token) {
          localStorage.setItem('refresh_token', refresh_token);
        }
        window.location.href = '/';
      }
    } catch (err: unknown) {
      message.error(getApiErrorMessage(err, '登录失败'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="h-screen flex items-center justify-center" style={{ background: '#0a0e1a' }}>
      <Card
        style={{
          width: 400,
          background: '#111827',
          border: '1px solid #1e293b',
        }}
      >
        <div className="text-center" style={{ marginBottom: 24 }}>
          <Title
            level={3}
            style={{
              fontFamily: "'Orbitron', sans-serif",
              color: '#00d4ff',
              letterSpacing: '0.05em',
              marginBottom: 4,
            }}
          >
            US-DX AI
          </Title>
          <Text style={{ color: '#64748b', fontSize: 12 }}>超声诊疗决策支持系统</Text>
        </div>
        <Form form={form} layout="vertical" onFinish={handleLogin}>
          <Form.Item label="用户名" name="username" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input placeholder="输入用户名" />
          </Form.Item>
          <Form.Item label="密码" name="password" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password placeholder="输入密码" />
          </Form.Item>
          <Form.Item>
            <Button
              type="primary"
              htmlType="submit"
              icon={<LoginOutlined />}
              loading={loading}
              block
            >
              登录
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}
