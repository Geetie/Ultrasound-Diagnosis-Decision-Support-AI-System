import { Switch, Select, Space } from 'antd';
import { BulbOutlined, UserOutlined } from '@ant-design/icons';
import { useUiStore } from '@/stores/uiStore';
import { useSessionStore } from '@/stores/sessionStore';

const organOptions = [
  { value: 'thyroid', label: '甲状腺' },
  { value: 'liver', label: '肝脏' },
  { value: 'cardiac', label: '心脏' },
];

export default function TopBar() {
  const { isDarkRoomMode, toggleDarkRoomMode } = useUiStore();
  const { currentOrgan, targetOrgans } = useSessionStore();

  return (
    <div className="w-full flex items-center justify-between">
      <div className="flex items-center gap-4">
        <h1
          style={{
            fontFamily: "'Orbitron', sans-serif",
            fontSize: 16,
            fontWeight: 600,
            color: '#e2e8f0',
            letterSpacing: '0.05em',
            margin: 0,
          }}
        >
          超声诊疗决策支持系统
        </h1>
        <span
          style={{
            fontFamily: "'Orbitron', sans-serif",
            fontSize: 10,
            color: '#64748b',
            letterSpacing: '0.1em',
          }}
        >
          v0.1.0-alpha
        </span>
      </div>
      <div className="flex items-center gap-4">
        <Select
          value={currentOrgan || undefined}
          placeholder="选择器官"
          options={organOptions.filter((o) =>
            targetOrgans.length > 0 ? targetOrgans.includes(o.value as 'thyroid' | 'liver' | 'cardiac') : true
          )}
          size="small"
          style={{ width: 120 }}
          disabled={targetOrgans.length === 0}
        />
        <Space size={4} align="center">
          <BulbOutlined style={{ color: isDarkRoomMode ? '#f59e0b' : '#64748b', fontSize: 14 }} />
          <Switch
            size="small"
            checked={isDarkRoomMode}
            onChange={toggleDarkRoomMode}
            checkedChildren="暗室"
            unCheckedChildren="标准"
          />
        </Space>
        <Space size={4} align="center">
          <UserOutlined style={{ color: '#94a3b8', fontSize: 14 }} />
          <span style={{ color: '#94a3b8', fontSize: 13 }}>操作员</span>
        </Space>
      </div>
    </div>
  );
}
