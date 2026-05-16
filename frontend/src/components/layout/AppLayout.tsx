import { Outlet } from 'react-router-dom';
import { Layout } from 'antd';
import Sidebar from './Sidebar';
import TopBar from './TopBar';
import StatusBar from './StatusBar';
import { useUiStore } from '@/stores/uiStore';

const { Sider, Header, Content, Footer } = Layout;

export default function AppLayout() {
  const { sidebarCollapsed, setSidebarCollapsed } = useUiStore();

  return (
    <Layout className="h-screen" style={{ background: '#0a0e1a' }}>
      <Sider
        collapsible
        collapsed={sidebarCollapsed}
        onCollapse={setSidebarCollapsed}
        width={220}
        collapsedWidth={64}
        trigger={null}
        style={{
          background: '#0a0e1a',
          borderRight: '1px solid #1e293b',
        }}
      >
        <Sidebar collapsed={sidebarCollapsed} />
      </Sider>
      <Layout style={{ background: '#0a0e1a' }}>
        <Header
          style={{
            background: '#111827',
            borderBottom: '1px solid #1e293b',
            padding: '0 24px',
            height: 56,
            lineHeight: '56px',
            display: 'flex',
            alignItems: 'center',
          }}
        >
          <TopBar />
        </Header>
        <Content
          className="animate-fade-in"
          style={{
            background: '#0a0e1a',
            padding: 16,
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
    </Layout>
  );
}
