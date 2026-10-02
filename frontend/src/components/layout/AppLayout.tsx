import { useEffect } from 'react';
import { Outlet, useNavigate } from 'react-router-dom';
import { Layout } from 'antd';
import Sidebar from './Sidebar';
import TopBar from './TopBar';
import StatusBar from './StatusBar';
import VoiceControl from '@/components/voice/VoiceControl';
import { useUiStore } from '@/stores/uiStore';
import { useTouchAdaptation } from '@/hooks/useTouchAdaptation';
import { tokens } from '@/styles/tokens';

const { Sider, Header, Content, Footer } = Layout;

export default function AppLayout() {
  const { sidebarCollapsed, setSidebarCollapsed, isDarkRoomMode } = useUiStore();
  // T-GB-FE-04：触摸设备放大热区与字号（结果写入 <body> 的 touch-mode 类）
  const touch = useTouchAdaptation();
  const navigate = useNavigate();

  // 暗室模式同样以 body 类呈现，便于 CSS 统一压低亮度与提高对比
  useEffect(() => {
    document.body.classList.toggle('dark-room', isDarkRoomMode);
    return () => document.body.classList.remove('dark-room');
  }, [isDarkRoomMode]);

  const background = isDarkRoomMode ? tokens.color.darkroom.bgDeep : '#0a0e1a';

  return (
    <Layout className="h-screen" style={{ background }}>
      <Sider
        collapsible
        collapsed={sidebarCollapsed}
        onCollapse={setSidebarCollapsed}
        width={touch.isTouch ? 240 : 220}
        collapsedWidth={64}
        trigger={null}
        style={{
          background,
          borderRight: '1px solid #1e293b',
        }}
      >
        <Sidebar collapsed={sidebarCollapsed} />
      </Sider>
      <Layout style={{ background }}>
        <Header
          style={{
            background: '#111827',
            borderBottom: '1px solid #1e293b',
            padding: touch.isTouch ? '0 16px' : '0 24px',
            height: touch.isTouch ? 64 : 56,
            lineHeight: touch.isTouch ? '64px' : '56px',
            display: 'flex',
            alignItems: 'center',
          }}
        >
          <TopBar />
        </Header>
        <Content
          className="animate-fade-in"
          style={{
            background,
            padding: touch.isTouch ? 20 : 16,
            overflow: 'auto',
          }}
        >
          <Outlet />
        </Content>
        <Footer
          style={{
            background: '#111827',
            borderTop: '1px solid #1e293b',
            padding: '0 24px',
            height: 32,
            lineHeight: '32px',
          }}
        >
          <StatusBar />
        </Footer>
      </Layout>

      {/* T-GB-FE-02：语音控件常驻（发起诊断后跳检查工作区看结果。
          原跳 /diagnosis，该独立页已按 17-§5 裁定 4 删除，结论并入工作区结论区。 */}
      <VoiceControl
        onExecuted={(result) => {
          if (result.request_id) {
            navigate('/');
          }
        }}
      />
    </Layout>
  );
}
