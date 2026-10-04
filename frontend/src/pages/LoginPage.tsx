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
      // 2026-10-05 医生视角浏览器走查抓到：这里原来打的是 `/auth/token`。
      // 该端点用 `OAuth2PasswordRequestForm = Depends()`，**只接受
      // application/x-www-form-urlencoded**（OAuth2 密码流规范），而 axios 发的是
      // JSON —— FastAsyncAPI 校验失败返回 422，于是**登录永远失败**（实测
      // JSON→/auth/token = 422，form→/auth/token = 200）。
      //
      // 改用 `/auth/login`：它收 JSON 的 `LoginRequest`，与本文件其余调用方式一致，
      // 不必把 axios 全局改成 form 序列化。
      const response = await apiClient.post('/auth/login', {
        username: values.username,
        password: values.password,
      });
      const { access_token, refresh_token } = response.data;
      if (access_token) {
        localStorage.setItem('access_token', access_token);
        if (refresh_token) {
          localStorage.setItem('refresh_token', refresh_token);
        }
        // 2026-10-05 走查抓到：原来写的是 `window.location.href = '/'`，但 `/` 是
        // **后端**的根 JSON（`{"message": "..."}`），不是前端页面 —— 登录成功后
        // 医生看到的是一串 JSON，界面直接断掉。
        // 前端产物挂在 `/ui`（base 由 VITE_BASE 注入），工作区路由就是 base 本身，
        // 所以用 BASE_URL：生产='/ui/'，dev(不加 VITE_BASE)='/'，两边都对。
        window.location.href = import.meta.env.BASE_URL;
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
