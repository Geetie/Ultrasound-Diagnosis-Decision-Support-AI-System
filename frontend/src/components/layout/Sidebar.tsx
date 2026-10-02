import { Menu } from 'antd';
import {
  ScanOutlined,
  FileSearchOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
  BarChartOutlined,
  BookOutlined,
  AuditOutlined,
  FormOutlined,
  FileTextOutlined,
} from '@ant-design/icons';
import { useNavigate, useLocation } from 'react-router-dom';

interface SidebarProps {
  collapsed: boolean;
}

const menuItems = [
  { key: '/', icon: <ScanOutlined />, label: '检查主界面' },
  { key: '/diagnosis', icon: <FileSearchOutlined />, label: '诊断结果' },
  { key: '/consent', icon: <AuditOutlined />, label: '知情同意' },
  { key: '/feedback', icon: <FormOutlined />, label: '反馈提交' },
  { key: '/session/new', icon: <SafetyCertificateOutlined />, label: '新建会话' },
  { key: '/report', icon: <FileTextOutlined />, label: '报告签发' },
  { key: '/qc-report', icon: <BarChartOutlined />, label: '质控报告' },
  { key: '/kb-management', icon: <BookOutlined />, label: '知识库管理' },
  { key: '/settings', icon: <SettingOutlined />, label: '设置' },
];

export default function Sidebar({ collapsed }: SidebarProps) {
  const navigate = useNavigate();
  const location = useLocation();

  return (
    <div className="h-full flex flex-col">
      <div
        className="flex items-center justify-center"
        style={{
          height: 56,
          borderBottom: '1px solid #1e293b',
        }}
      >
        {!collapsed && (
          <span
            className="text-display"
            style={{
              fontFamily: "'Orbitron', sans-serif",
              fontSize: 14,
              fontWeight: 700,
              color: '#00d4ff',
              letterSpacing: '0.05em',
            }}
          >
            US-DX AI
          </span>
        )}
        {collapsed && (
          <span
            style={{
              fontFamily: "'Orbitron', sans-serif",
              fontSize: 16,
              fontWeight: 700,
              color: '#00d4ff',
            }}
          >
            U
          </span>
        )}
      </div>
      <Menu
        mode="inline"
        selectedKeys={[location.pathname]}
        items={menuItems}
        onClick={({ key }) => navigate(key)}
        style={{
          background: 'transparent',
          borderRight: 'none',
          flex: 1,
        }}
        theme="dark"
      />
    </div>
  );
}
