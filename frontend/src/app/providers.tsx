import { type ReactNode } from 'react';
import { ConfigProvider, theme as antdTheme } from 'antd';
import { BrowserRouter } from 'react-router-dom';
import darkMedicalTheme from '@/styles/theme';
import ErrorBoundary from '@/components/common/ErrorBoundary';

interface ProvidersProps {
  children: ReactNode;
}

export default function Providers({ children }: ProvidersProps) {
  return (
    <ErrorBoundary>
      <ConfigProvider
        theme={{
          ...darkMedicalTheme,
          algorithm: antdTheme.darkAlgorithm,
        }}
      >
        <BrowserRouter>
          {children}
        </BrowserRouter>
      </ConfigProvider>
    </ErrorBoundary>
  );
}
