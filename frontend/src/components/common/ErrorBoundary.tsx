import { Component, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  render() {
    if (this.state.hasError) {
      return (
        <div
          className="flex items-center justify-center"
          style={{
            width: '100vw',
            height: '100vh',
            background: '#0a0e1a',
            color: '#e2e8f0',
            flexDirection: 'column',
            gap: 16,
          }}
        >
          <div
            style={{
              fontFamily: "'Orbitron', sans-serif",
              fontSize: 24,
              color: '#ef4444',
              letterSpacing: '0.05em',
            }}
          >
            SYSTEM ERROR
          </div>
          <div style={{ color: '#94a3b8', fontSize: 14, maxWidth: 480, textAlign: 'center' }}>
            {this.state.error?.message || '系统发生未知错误'}
          </div>
          <button
            onClick={() => window.location.reload()}
            style={{
              marginTop: 16,
              padding: '8px 24px',
              background: '#1e2745',
              border: '1px solid #00d4ff40',
              borderRadius: 10,
              color: '#00d4ff',
              cursor: 'pointer',
              fontFamily: "'Inter', sans-serif",
              fontSize: 14,
            }}
          >
            重新加载
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
