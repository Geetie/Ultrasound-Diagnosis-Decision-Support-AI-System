import { Card, Switch, Form, Typography, Divider } from 'antd';
import { useUiStore } from '@/stores/uiStore';

const { Title, Text } = Typography;

export default function SettingsPage() {
  const { isDarkRoomMode, toggleDarkRoomMode } = useUiStore();

  return (
    <div className="h-full overflow-auto animate-fade-in" style={{ padding: '0 48px' }}>
      <Title
        level={4}
        style={{
          fontFamily: "'Orbitron', sans-serif",
          color: '#00d4ff',
          letterSpacing: '0.05em',
          marginBottom: 24,
        }}
      >
        SETTINGS
      </Title>
      <Card
        style={{
          background: '#111827',
          border: '1px solid #1e293b',
          maxWidth: 600,
        }}
      >
        <Form layout="vertical">
          <Form.Item label="暗室模式" extra="降低界面亮度，减少环境光干扰">
            <Switch
              checked={isDarkRoomMode}
              onChange={toggleDarkRoomMode}
              checkedChildren="开"
              unCheckedChildren="关"
            />
          </Form.Item>
          <Divider style={{ borderColor: '#1e293b' }} />
          <Text style={{ color: '#64748b', fontSize: 12 }}>
            更多设置将在后续阶段开放
          </Text>
        </Form>
      </Card>
    </div>
  );
}
