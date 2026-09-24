import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'path';

// 前端测试基建（T-GB-FE-01/02/04/05）：
// 与 Vite 同源，沿用 @ 别名，避免重复维护一套构建配置。
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    // 组件测试依赖 antd 的 CSS-in-JS，串行更稳定且体量很小
    pool: 'threads',
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/**/*.d.ts',
        'src/test/**',
        'src/main.tsx',
        'src/**/__tests__/**',
        'src/components/dicom/cornerstone.ts',
      ],
    },
  },
});
